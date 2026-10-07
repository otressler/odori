from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from uuid import UUID

from django.conf import settings
from django.db.models import Max
from django.utils import timezone
from django.utils.formats import date_format

from core.services import household_for
from pantry.models import InventoryItem
from planning.models import CookEvent, MealSlot

from .models import Recipe, RecipeIngredient, RecommendationOutcome, RecommendationRun

SCORING_VERSION = "2026-10-1"
RECENT_COOK_DAYS = 21
SHARED_INGREDIENT_BONUS = 0.05
SHARED_INGREDIENT_MAX_BONUS = 0.15


@dataclass(frozen=True)
class Recommendation:
    recipe: Recipe
    score: float
    matched_ingredients: list[str]
    missing_ingredients: list[str]
    reasons: list[str]
    score_components: dict[str, float]
    recently_cooked: bool = False
    planned_this_week: bool = False
    dismissed_this_week: bool = False
    # Missing ingredients another dish of the planned week needs too: bought once, used twice.
    shared_ingredients: list[str] = field(default_factory=list)
    shared_reason: str = ""


@dataclass(frozen=True)
class RecommendationResult:
    run: RecommendationRun
    suggestions: list[Recommendation]


@dataclass(frozen=True)
class WeekContext:
    start: date
    recipe_ids: set
    # Canonical ingredient id -> earliest day an uncooked dish of the week needs it.
    needs: dict
    # "Nicht diese Woche" in the planner: hidden for this week only.
    dismissed: set


def _candidate_limit():
    return max(1, min(settings.RECOMMENDATION_CANDIDATE_LIMIT, 250))


def _week_context(plan):
    start = plan.week_start_date
    slots = list(
        MealSlot.objects.filter(
            plan=plan, entry_type=MealSlot.EntryType.RECIPE, recipe__isnull=False
        ).values_list("recipe_id", "date", "cooked_at")
    )
    upcoming = {}
    for recipe_id, day, cooked_at in slots:
        if cooked_at is None:
            upcoming[recipe_id] = min(day, upcoming.get(recipe_id, day))
    needs = {}
    lines = RecipeIngredient.objects.filter(
        recipe_id__in=upcoming, canonical_ingredient__isnull=False
    ).values_list("recipe_id", "canonical_ingredient_id")
    for recipe_id, ingredient_id in lines:
        day = upcoming[recipe_id]
        needs[ingredient_id] = min(day, needs.get(ingredient_id, day))
    dismissed = set(
        RecommendationOutcome.objects.filter(
            household_id=plan.household_id,
            outcome=RecommendationOutcome.Type.DISMISSED,
            run__input_snapshot__weekStart=start.isoformat(),
        ).values_list("recipe_id", flat=True)
    )
    return WeekContext(
        start=start,
        recipe_ids={recipe_id for recipe_id, _, _ in slots},
        needs=needs,
        dismissed=dismissed,
    )


def _snapshot(*, recipes, inventory, duplicate_counts, recently_cooked, feedback, week):
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
        "weekStart": week.start.isoformat() if week else None,
        "weekRecipeIds": sorted(str(recipe_id) for recipe_id in week.recipe_ids) if week else [],
        "weekIngredientIds": sorted(str(ingredient_id) for ingredient_id in week.needs)
        if week
        else [],
        "weekDismissedRecipeIds": sorted(str(recipe_id) for recipe_id in week.dismissed)
        if week
        else [],
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


def _shared_reason(shared):
    names = ", ".join(name for name, _ in shared)
    days = {day for _, day in shared}
    if len(days) == 1:
        return f"Auch am {date_format(days.pop(), 'D')} gebraucht: {names}"
    return f"Auch diese Woche gebraucht: {names}"


def _score_recipe(
    *, recipe, inventory, duplicate_counts, recently_cooked, feedback, favorite_ids, week
):
    ingredients = {}
    unmapped = []
    for line in recipe.ingredients.all():
        if line.canonical_ingredient_id:
            ingredients.setdefault(line.canonical_ingredient_id, line.canonical_ingredient.name)
        else:
            unmapped.append(line.source_text)

    matched = []
    missing = []
    shared = []
    unknown_count = 0
    for ingredient_id, name in ingredients.items():
        status = inventory.get(ingredient_id)
        if status == InventoryItem.Status.AVAILABLE:
            matched.append(name)
        else:
            missing.append(name)
            unknown_count += status == InventoryItem.Status.UNKNOWN
            if week and ingredient_id in week.needs:
                shared.append((name, week.needs[ingredient_id]))
    missing.extend(unmapped)
    shared.sort(key=lambda item: (item[1], item[0].casefold()))

    total = len(ingredients) + len(unmapped)
    coverage = (len(matched) + (unknown_count * 0.4)) / total if total else 0.0
    favorite = recipe.id in favorite_ids
    recent = recipe.id in recently_cooked
    duplicate = duplicate_counts.get(recipe.id, 0) > 1
    in_week = week is not None and recipe.id in week.recipe_ids
    if in_week:
        shared = []
    shared_bonus = min(SHARED_INGREDIENT_MAX_BONUS, SHARED_INGREDIENT_BONUS * len(shared))
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
            + shared_bonus
            - (0.1 if recent else 0.0)
            - (0.1 if duplicate else 0.0)
            - (0.2 if in_week else 0.0)
            - (0.15 if not_useful else 0.0),
        ),
    )
    shared_reason = _shared_reason(shared) if shared else ""
    reasons = []
    if total:
        reasons.append(f"{len(matched)} von {total} Zutaten sind vorrätig")
    else:
        reasons.append("Zutaten müssen noch dem Vorrat zugeordnet werden")
    if shared_reason:
        reasons.append(shared_reason)
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
    if in_week:
        reasons.append("Diese Woche schon eingeplant")
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
            "sharedIngredients": round(shared_bonus, 4),
            "recentCooked": -0.1 if recent else 0.0,
            "plannedDuplicate": -0.1 if duplicate else 0.0,
            "plannedThisWeek": -0.2 if in_week else 0.0,
            "notUseful": -0.15 if not_useful else 0.0,
        },
        recently_cooked=recent,
        planned_this_week=in_week,
        dismissed_this_week=week is not None and recipe.id in week.dismissed,
        shared_ingredients=[name for name, _ in shared],
        shared_reason=shared_reason,
    )


def _reusable_run(*, household, user, inventory_snapshot_at, snapshot, now):
    """A reload with identical inputs is the same recommendation, not a new run."""

    return (
        RecommendationRun.objects.filter(
            household=household,
            requested_by=user,
            scoring_version=SCORING_VERSION,
            inventory_snapshot_at=inventory_snapshot_at,
            input_snapshot=snapshot,
            created_at__gte=now - timedelta(hours=12),
        )
        .order_by("-created_at")
        .first()
    )


def recommend_for_user(*, user, plan=None):
    """Rank the household's approved recipes; with `plan`, also weigh that week's dishes."""

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
    week = _week_context(plan) if plan is not None else None
    snapshot = _snapshot(
        recipes=recipes,
        inventory=inventory_rows,
        duplicate_counts=duplicate_counts,
        recently_cooked=recently_cooked,
        feedback=feedback,
        week=week,
    )
    run = _reusable_run(
        household=household,
        user=user,
        inventory_snapshot_at=inventory_updated_at,
        snapshot=snapshot,
        now=now,
    ) or RecommendationRun.objects.create(
        household=household,
        requested_by=user,
        scoring_version=SCORING_VERSION,
        inventory_snapshot_at=inventory_updated_at,
        input_snapshot=snapshot,
    )
    suggestions = [
        _score_recipe(
            recipe=recipe,
            inventory=inventory,
            duplicate_counts=duplicate_counts,
            recently_cooked=recently_cooked,
            feedback=feedback,
            favorite_ids=favorite_ids,
            week=week,
        )
        for recipe in recipes
    ]
    suggestions.sort(
        key=lambda item: (-item.score, item.recipe.title.casefold(), str(item.recipe.id))
    )
    return RecommendationResult(run=run, suggestions=suggestions)


def week_suggestions(result, *, limit):
    """What the week planner offers: nothing planned, recent, dismissed, or without signal."""

    return [
        suggestion
        for suggestion in result.suggestions
        if suggestion.score > 0
        and not suggestion.planned_this_week
        and not suggestion.dismissed_this_week
        and not suggestion.recently_cooked
    ][:limit]


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
