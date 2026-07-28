from django.conf import settings
from django.db import transaction
from django.http import Http404

from core.observability import current_context
from core.services import household_for

from .catalog import sync_starter_catalog
from .models import (
    CanonicalIngredient,
    IngredientCategory,
    IngredientCategoryExample,
    InventoryEvent,
    InventoryItem,
    PantryCategorizationJob,
)
from .semantic import (
    cosine_similarity,
    embed_with_diagnostics,
    embedding_needs_refresh,
    normalized_text,
    rank_ingredients,
    update_embedding,
)


class PlannedIngredientInUse(Exception):
    """A manual transition away from `in_stock` would affect upcoming planned meals."""

    def __init__(self, slots):
        self.slots = list(slots)
        super().__init__("planned_ingredient_in_use")


def locked_item(household, ingredient):
    return InventoryItem.objects.select_for_update().get_or_create(
        household=household,
        ingredient=ingredient,
        defaults={"status": InventoryItem.Status.UNKNOWN},
    )


def write_status(*, item, status, actor, origin, meal_slot=None):
    previous_status = item.status
    item.status = status
    item.version += 1
    item.save(update_fields=["status", "version", "updated_at"])
    InventoryEvent.objects.create(
        item=item,
        previous_status=previous_status,
        new_status=status,
        actor=actor,
        origin=origin,
        meal_slot=meal_slot,
    )
    return item


def removes_planned_stock(previous_status, new_status):
    return (
        previous_status == InventoryItem.Status.IN_STOCK
        and new_status != InventoryItem.Status.IN_STOCK
    )


@transaction.atomic
def change_inventory_status(*, user, ingredient_id, status, version, confirm_planned_use=False):
    """Manual availability change. Returns `None` when the supplied version is stale."""

    household = household_for(user)
    ingredient = CanonicalIngredient.objects.filter(id=ingredient_id, household=household).first()
    if not ingredient:
        raise Http404
    item, created = locked_item(household, ingredient)
    if not created and item.version != version:
        return None
    if not confirm_planned_use and removes_planned_stock(item.status, status):
        from planning.services import upcoming_slots_using_ingredient

        slots = upcoming_slots_using_ingredient(household=household, ingredient=ingredient)
        if slots:
            raise PlannedIngredientInUse(slots)
    return write_status(item=item, status=status, actor=user, origin=InventoryEvent.Origin.MANUAL)


def record_purchase(*, user, household, ingredient):
    """Runs inside the shopping purchase transaction; never asks for confirmation."""

    item, _ = locked_item(household, ingredient)
    return write_status(
        item=item,
        status=InventoryItem.Status.IN_STOCK,
        actor=user,
        origin=InventoryEvent.Origin.PURCHASE,
    )


def record_cooking_change(*, user, household, ingredient, status, version, meal_slot):
    """Runs inside the mark-cooked transaction. Returns `None` on a stale version."""

    item, created = locked_item(household, ingredient)
    if version is not None and not created and item.version != version:
        return None
    return write_status(
        item=item,
        status=status,
        actor=user,
        origin=InventoryEvent.Origin.COOK_RECIPE,
        meal_slot=meal_slot,
    )


@transaction.atomic
def merge_ingredients(*, user, source_id, target_id):
    household = household_for(user)
    source = (
        CanonicalIngredient.objects.select_for_update()
        .filter(id=source_id, household=household)
        .first()
    )
    target = (
        CanonicalIngredient.objects.select_for_update()
        .filter(id=target_id, household=household)
        .first()
    )
    if not source or not target or source == target:
        raise Http404
    from recipes.models import RecipeIngredient

    RecipeIngredient.objects.filter(canonical_ingredient=source).update(canonical_ingredient=target)
    source_item = InventoryItem.objects.filter(household=household, ingredient=source).first()
    target_item = InventoryItem.objects.filter(household=household, ingredient=target).first()
    if source_item and not target_item:
        source_item.ingredient = target
        source_item.version += 1
        source_item.save()
    elif source_item:
        source_item.delete()
    source.active = False
    source.merged_into = target
    source.save(update_fields=["active", "merged_into"])
    return target


def similar_ingredient_recommendations(*, user, minimum_score=0.96):
    """Return high-confidence pairs for a user to review; never merge automatically."""

    household = household_for(user)
    ingredients = list(CanonicalIngredient.objects.filter(household=household, active=True))
    retained = []
    recommendations = []
    for ingredient in ingredients:
        candidates = rank_ingredients(retained, ingredient.name)
        if candidates and candidates[0][1] >= minimum_score:
            target, score, semantic = candidates[0]
            recommendations.append(
                {
                    "source": ingredient,
                    "target": target,
                    "score_percent": round(score * 100),
                    "semantic": semantic,
                }
            )
        retained.append(ingredient)
    return recommendations


def ensure_suggested_categories(household, *, job_id=None, correlation_id=None):
    sync_starter_catalog(household=household)
    refresh_category_example_embeddings(
        household=household,
        job_id=job_id,
        correlation_id=correlation_id,
    )
    return list(
        IngredientCategory.objects.filter(household=household)
        .prefetch_related("examples")
        .order_by("sort_order", "name")
    )


def refresh_category_example_embeddings(
    *, household, job_id=None, correlation_id=None, force=False
):
    """Refresh active example vectors that are missing or use another deployment."""

    updated = 0
    examples = IngredientCategoryExample.objects.filter(household=household, active=True)
    for example in examples:
        if not force and not embedding_needs_refresh(example.embedding, example.embedding_model):
            continue
        vector = embed_with_diagnostics(
            example.text,
            household_id=household.id,
            job_id=job_id,
            operation="category_example_embedding",
        ).vector
        if vector is not None:
            example.embedding = vector
            example.embedding_model = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
            example.save(update_fields=["embedding", "embedding_model"])
            updated += 1
    return updated


def _compatible_similarity(*, ingredient_embedding, ingredient_model, example):
    if (
        not ingredient_embedding
        or not example.embedding
        or ingredient_model != example.embedding_model
    ):
        return None
    return cosine_similarity(ingredient_embedding, example.embedding)


def _category_candidate(*, category, ingredient_texts, ingredient_embedding, ingredient_model):
    normalized_ingredient_texts = {normalized_text(text) for text in ingredient_texts if text}
    active_examples = [example for example in category.examples.all() if example.active]
    exact_examples = [
        example
        for example in active_examples
        if example.normalized_text in normalized_ingredient_texts
    ]
    if exact_examples:
        return {
            "category": category,
            "score": 1.0,
            "exact_match": True,
            "embedding_score": None,
            "best_example": exact_examples[0],
            "matched_examples": exact_examples[:1],
        }

    scored_examples = [
        (score, example)
        for example in active_examples
        if (
            score := _compatible_similarity(
                ingredient_embedding=ingredient_embedding,
                ingredient_model=ingredient_model,
                example=example,
            )
        )
        is not None
    ]
    if not scored_examples:
        return {
            "category": category,
            "score": None,
            "exact_match": False,
            "embedding_score": None,
            "best_example": None,
            "matched_examples": [],
        }
    scored_examples.sort(key=lambda result: (-result[0], result[1].normalized_text))
    top_examples = scored_examples[:3]
    score = sum(item[0] for item in top_examples) / len(top_examples)
    return {
        "category": category,
        "score": score,
        "exact_match": False,
        "embedding_score": score,
        "best_example": top_examples[0][1],
        "matched_examples": [item[1] for item in top_examples],
    }


def classify_category(
    *, name, aliases=(), ingredient_embedding=None, ingredient_embedding_model="", categories
):
    candidates = [
        _category_candidate(
            category=category,
            ingredient_texts=(name, *aliases),
            ingredient_embedding=ingredient_embedding,
            ingredient_model=ingredient_embedding_model,
        )
        for category in categories
    ]
    scored_candidates = [candidate for candidate in candidates if candidate["score"] is not None]
    if not scored_candidates:
        return {
            "state": "no_vectors",
            "category": None,
            "runner_up": None,
            "score": None,
            "runner_up_score": None,
            "margin": None,
            "candidate": None,
            "candidates": candidates,
        }

    scored_candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    candidate = scored_candidates[0]
    runner_up = scored_candidates[1] if len(scored_candidates) > 1 else None
    runner_up_score = runner_up["score"] if runner_up else 0.0
    margin = candidate["score"] - runner_up_score
    category = candidate["category"]
    minimum_similarity = (
        category.minimum_similarity
        if category.minimum_similarity is not None
        else settings.CATEGORY_CLASSIFIER_MIN_SIMILARITY
    )
    minimum_margin = (
        category.minimum_margin
        if category.minimum_margin is not None
        else settings.CATEGORY_CLASSIFIER_MIN_MARGIN
    )
    assigned = candidate["exact_match"] or (
        candidate["score"] >= minimum_similarity and margin >= minimum_margin
    )
    return {
        "state": "assigned" if assigned else "review_required",
        "category": category,
        "runner_up": runner_up["category"] if runner_up else None,
        "score": candidate["score"],
        "runner_up_score": runner_up_score,
        "margin": margin,
        "minimum_similarity": minimum_similarity,
        "minimum_margin": minimum_margin,
        "candidate": candidate,
        "candidates": candidates,
    }


def category_score_details(*, name, ingredient_embedding, category, ingredient_embedding_model=""):
    candidate = _category_candidate(
        category=category,
        ingredient_texts=(name,),
        ingredient_embedding=ingredient_embedding,
        ingredient_model=ingredient_embedding_model,
    )
    return {
        "text_score": float(candidate["exact_match"]),
        "embedding_score": candidate["embedding_score"],
        "score": candidate["score"] or 0.0,
        "best_example": candidate["best_example"],
        "matched_examples": candidate["matched_examples"],
    }


def categorize_household(*, household, job_id=None, correlation_id=None):
    categories = ensure_suggested_categories(
        household, job_id=job_id, correlation_id=correlation_id
    )
    updated = 0
    ingredients = list(CanonicalIngredient.objects.filter(household=household, active=True))
    for ingredient in ingredients:
        if embedding_needs_refresh(ingredient.embedding, ingredient.embedding_model):
            update_embedding(ingredient, job_id=job_id, correlation_id=correlation_id)
    for ingredient in (ingredient for ingredient in ingredients if ingredient.category_id is None):
        classification = classify_category(
            name=ingredient.name,
            aliases=ingredient.aliases,
            ingredient_embedding=ingredient.embedding,
            ingredient_embedding_model=ingredient.embedding_model,
            categories=categories,
        )
        if classification["state"] == "assigned":
            ingredient.category = classification["category"]
            ingredient.save(update_fields=["category"])
            updated += 1
    return updated


@transaction.atomic
def queue_category_suggestions(*, user):
    household = household_for(user)
    active_job = (
        PantryCategorizationJob.objects.select_for_update()
        .filter(
            household=household,
            state__in=[
                PantryCategorizationJob.State.QUEUED,
                PantryCategorizationJob.State.RUNNING,
            ],
        )
        .first()
    )
    if active_job:
        return active_job, False
    return (
        PantryCategorizationJob.objects.create(
            household=household,
            correlation_id=current_context().get("request_id"),
        ),
        True,
    )


def category_suggestions(*, user):
    """Synchronous categorization for worker and local callers."""

    return categorize_household(household=household_for(user))
