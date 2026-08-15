from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from core.services import household_for
from pantry.models import InventoryItem
from planning.models import CookEvent, MealSlot

from .models import Recipe, RecommendationOutcome, RecommendationRun

SCORING_VERSION = "2026-08-1"
RECENT_COOK_DAYS = 21


@dataclass(frozen=True)
class Recommendation:
    recipe: Recipe
    score: float
    matched_ingredients: list[str]
    missing_ingredients: list[str]
    reasons: list[str]
    score_components: dict[str, float]


@dataclass(frozen=True)
class RecommendationResult:
    run: RecommendationRun
    suggestions: list[Recommendation]


def _candidate_limit():
    return max(1, min(settings.RECOMMENDATION_CANDIDATE_LIMIT, 250))


def _snapshot(*, recipes, inventory, duplicate_counts, recently_cooked, feedback):
    return {
        "candidateLimit": _candidate_limit(),
        "candidateRecipes": [
            {"id": str(recipe.id), "version": recipe.version} for recipe in recipes
        ],
        "inventory": [
            {"ingredientId": str(item["ingredient_id"]), "status": item["status"]}
            for item in sorted(inventory, key=lambda item: str(item["ingredient_id"]))
        ][:500],
        "plannedRecipeIds": sorted(
            str(recipe_id) for recipe_id, count in duplicate_counts.items() if count > 1
        )[: _candidate_limit()],
        "recentlyCookedRecipeIds": sorted(str(recipe_id) for recipe_id in recently_cooked)[
            : _candidate_limit()
        ],
        "feedbackRecipeIds": sorted(str(recipe_id) for recipe_id in feedback)[: _candidate_limit()],
    }


def _latest_feedback(*, household, recipe_ids):
    latest = {}
    outcomes = (
        RecommendationOutcome.objects.filter(household=household, recipe_id__in=recipe_ids)
        .order_by("recipe_id", "-created_at")
        .values("recipe_id", "outcome")
    )
    for outcome in outcomes:
        latest.setdefault(outcome["recipe_id"], outcome["outcome"])
    return latest


def _score_recipe(*, recipe, inventory, duplicate_counts, recently_cooked, feedback, favorite_ids):
    ingredients = {}
    unmapped = []
    for line in recipe.ingredients.all():
        if line.canonical_ingredient_id:
            ingredients.setdefault(line.canonical_ingredient_id, line.canonical_ingredient.name)
        else:
            unmapped.append(line.source_text)

    matched = []
    missing = []
    unknown_count = 0
    for ingredient_id, name in ingredients.items():
        status = inventory.get(ingredient_id)
        if status == InventoryItem.Status.AVAILABLE:
            matched.append(name)
        else:
            missing.append(name)
            unknown_count += status == InventoryItem.Status.UNKNOWN
    missing.extend(unmapped)

    total = len(ingredients) + len(unmapped)
    coverage = (len(matched) + (unknown_count * 0.4)) / total if total else 0.0
    favorite = recipe.id in favorite_ids
    recent = recipe.id in recently_cooked
    duplicate = duplicate_counts.get(recipe.id, 0) > 1
    not_useful = feedback.get(recipe.id) in {
        RecommendationOutcome.Type.DISMISSED,
        RecommendationOutcome.Type.HIDDEN,
    }
    score = max(
        0.0,
        min(
            1.0,
            (coverage * 0.8)
            + (0.1 if favorite else 0.0)
            - (0.1 if recent else 0.0)
            - (0.1 if duplicate else 0.0)
            - (0.15 if not_useful else 0.0),
        ),
    )
    reasons = []
    if total:
        reasons.append(f"{len(matched)} von {total} Zutaten sind vorrätig")
    else:
        reasons.append("Zutaten müssen noch dem Vorrat zugeordnet werden")
    if unknown_count:
        reasons.append(f"{unknown_count} Zutaten sollten im Vorrat geprüft werden")
    if favorite:
        reasons.append("Als Favorit markiert")
    if recent:
        reasons.append(f"In den letzten {RECENT_COOK_DAYS} Tagen gekocht")
    else:
        reasons.append(f"Nicht in den letzten {RECENT_COOK_DAYS} Tagen gekocht")
    if duplicate:
        reasons.append("Mehrfach im aktuellen Plan")
    if not_useful:
        reasons.append("Früher als nicht hilfreich markiert")
    return Recommendation(
        recipe=recipe,
        score=round(score, 4),
        matched_ingredients=sorted(matched, key=str.casefold),
        missing_ingredients=sorted(missing, key=str.casefold),
        reasons=reasons,
        score_components={
            "inventoryCoverage": round(coverage, 4),
            "favorite": 0.1 if favorite else 0.0,
            "recentCooked": -0.1 if recent else 0.0,
            "plannedDuplicate": -0.1 if duplicate else 0.0,
            "notUseful": -0.15 if not_useful else 0.0,
        },
    )


def recommend_for_user(*, user):
    household = household_for(user)
    recipes = list(
        Recipe.objects.filter(household=household, status=Recipe.Status.APPROVED)
        .prefetch_related("ingredients__canonical_ingredient")
        .order_by("title", "id")[: _candidate_limit()]
    )
    inventory_rows = list(
        InventoryItem.objects.filter(household=household).values("ingredient_id", "status")
    )
    inventory = {item["ingredient_id"]: item["status"] for item in inventory_rows}
    now = timezone.now()
    recent_cutoff = now - timedelta(days=RECENT_COOK_DAYS)
    recently_cooked = set(
        CookEvent.objects.filter(household=household, cooked_at__gte=recent_cutoff).values_list(
            "recipe_id", flat=True
        )
    )
    duplicate_counts = Counter(
        MealSlot.objects.filter(
            plan__household=household,
            entry_type=MealSlot.EntryType.RECIPE,
            cooked_at__isnull=True,
            date__gte=timezone.localdate(),
            recipe__isnull=False,
        ).values_list("recipe_id", flat=True)
    )
    favorite_ids = set(
        Recipe.objects.filter(
            id__in=[recipe.id for recipe in recipes], favorites__user=user
        ).values_list("id", flat=True)
    )
    feedback = _latest_feedback(household=household, recipe_ids=[recipe.id for recipe in recipes])
    inventory_updated_at = (
        InventoryItem.objects.filter(household=household).aggregate(updated_at=Max("updated_at"))[
            "updated_at"
        ]
        or now
    )
    run = RecommendationRun.objects.create(
        household=household,
        requested_by=user,
        scoring_version=SCORING_VERSION,
        inventory_snapshot_at=inventory_updated_at,
        input_snapshot=_snapshot(
            recipes=recipes,
            inventory=inventory_rows,
            duplicate_counts=duplicate_counts,
            recently_cooked=recently_cooked,
            feedback=feedback,
        ),
    )
    suggestions = [
        _score_recipe(
            recipe=recipe,
            inventory=inventory,
            duplicate_counts=duplicate_counts,
            recently_cooked=recently_cooked,
            feedback=feedback,
            favorite_ids=favorite_ids,
        )
        for recipe in recipes
    ]
    suggestions.sort(
        key=lambda item: (-item.score, item.recipe.title.casefold(), str(item.recipe.id))
    )
    return RecommendationResult(run=run, suggestions=suggestions)


def record_outcome(*, user, recipe_id, outcome, reason="", run_id=None):
    household = household_for(user)
    try:
        recipe_id = UUID(str(recipe_id))
        run_id = UUID(str(run_id)) if run_id else None
    except (TypeError, ValueError):
        raise ValueError("recipe_not_found")
    recipe = Recipe.objects.filter(id=recipe_id, household=household).first()
    if not recipe:
        raise ValueError("recipe_not_found")
    if outcome not in RecommendationOutcome.Type.values:
        raise ValueError("invalid_outcome")
    if reason and reason not in RecommendationOutcome.Reason.values:
        raise ValueError("invalid_reason")
    run = None
    if run_id:
        run = RecommendationRun.objects.filter(id=run_id, household=household).first()
        if not run:
            raise ValueError("recommendation_run_not_found")
    return RecommendationOutcome.objects.create(
        household=household, recipe=recipe, actor=user, run=run, outcome=outcome, reason=reason
    )
