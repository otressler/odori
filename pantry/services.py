from django.conf import settings
from django.db import transaction
from django.http import Http404

from core.services import household_for

from .models import (
    CanonicalIngredient,
    IngredientCategory,
    InventoryEvent,
    InventoryItem,
)
from .semantic import (
    cosine_similarity,
    embed,
    fuzzy_similarity,
    rank_ingredients,
    update_embedding,
)

CATEGORY_SUGGESTIONS = (
    ("Obst & Gemüse", 10, ("tomate", "gemüse", "obst", "salat", "kartoffel", "zwiebel")),
    ("Bäckerei", 20, ("brot", "brötchen", "mehl", "kuchen", "backen")),
    ("Kühlregal", 30, ("milch", "joghurt", "käse", "butter", "sahne", "tofu")),
    ("Fleisch & Fisch", 40, ("fleisch", "huhn", "rind", "fisch", "lachs", "wurst")),
    ("Trockenwaren", 50, ("pasta", "reis", "linsen", "bohnen", "mehl", "konserve")),
    ("Gewürze & Öl", 60, ("salz", "pfeffer", "gewürz", "öl", "essig", "kräuter")),
    ("Getränke", 70, ("wasser", "saft", "kaffee", "tee", "wein", "bier")),
    ("Haushalt & Hygiene", 80, ("seife", "shampoo", "toilettenpapier", "spülmittel", "reiniger")),
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


def _category_text(name):
    for category_name, _, keywords in CATEGORY_SUGGESTIONS:
        if category_name == name:
            return " ".join((category_name, *keywords))
    return name


def ensure_suggested_categories(household):
    categories = []
    for name, sort_order, _ in CATEGORY_SUGGESTIONS:
        category, _ = IngredientCategory.objects.get_or_create(
            household=household, name=name, defaults={"sort_order": sort_order}
        )
        if category.sort_order != sort_order:
            category.sort_order = sort_order
            category.save(update_fields=["sort_order"])
        if not category.embedding:
            vector = embed(_category_text(category.name))
            if vector is not None:
                category.embedding = vector
                category.embedding_model = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
                category.save(update_fields=["embedding", "embedding_model"])
        categories.append(category)
    return categories


def category_suggestions(*, user, minimum_score=0.72):
    """Seed store categories and assign only high-confidence ingredient suggestions."""

    household = household_for(user)
    categories = ensure_suggested_categories(household)
    updated = 0
    for ingredient in CanonicalIngredient.objects.filter(
        household=household, active=True, category__isnull=True
    ):
        if not ingredient.embedding:
            update_embedding(ingredient)
        candidates = []
        for category in categories:
            score = cosine_similarity(ingredient.embedding, category.embedding)
            if score is None:
                _, _, keywords = next(
                    item for item in CATEGORY_SUGGESTIONS if item[0] == category.name
                )
                score = max([fuzzy_similarity(ingredient.name, category.name)] + [
                    fuzzy_similarity(ingredient.name, keyword) for keyword in keywords
                ])
            candidates.append((score, category))
        score, category = max(candidates, key=lambda result: result[0])
        if score >= minimum_score:
            ingredient.category = category
            ingredient.save(update_fields=["category"])
            updated += 1
    return updated
