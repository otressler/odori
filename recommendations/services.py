import json
import time
import uuid
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.utils import DatabaseError
from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from core.services import household_for
from pantry.models import InventoryItem
from planning.models import MealSlot
from recipes.models import Recipe, RecipeIngredient, RecipeTag, RecipeTagAssignment

from .contracts import (
    CandidateFeatures,
    IngredientDetail,
    RecommendationOptions,
    RecommendationResult,
)
from .models import MAX_SNAPSHOT_BYTES, RecommendationFeedback, RecommendationRun
from .scoring import CATALOG_V1, score_candidate

MAX_CANDIDATES = 250
MAX_SELECTED_TAGS = 20
MAX_SUGGESTIONS = 20
MAX_RUNS_PER_HOUSEHOLD = 100
SNAPSHOT_SCHEMA_VERSION = 1


def _elapsed_ms(start_ns):
    return max(0, (time.perf_counter_ns() - start_ns) // 1_000_000)


def _validated_tag_ids(household, raw_ids):
    if len(raw_ids) > MAX_SELECTED_TAGS:
        raise ValueError(f"At most {MAX_SELECTED_TAGS} preferred tags may be selected.")
    try:
        ids = tuple(dict.fromkeys(uuid.UUID(str(value)) for value in raw_ids))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("preferredTagIds must contain valid UUIDs.") from exc
    found = set(
        RecipeTag.objects.filter(household=household, id__in=ids).values_list("id", flat=True)
    )
    if found != set(ids):
        raise ValueError("Every preferred tag must belong to the active household.")
    return tuple(str(value) for value in ids)


def _local_days_since(value, as_of):
    if value is None:
        return 21
    cooked_date = timezone.localtime(value).date()
    return min(21, max(0, (timezone.localtime(as_of).date() - cooked_date).days))


def _ingredient_features(recipe, inventory_statuses):
    canonical = {}
    unresolved = []
    for line in recipe.recommendation_ingredients:
        if line.optional:
            continue
        if line.canonical_ingredient_id:
            canonical.setdefault(line.canonical_ingredient_id, line.canonical_ingredient)
        else:
            unresolved.append(
                IngredientDetail(canonical_id=None, name=line.source_text, unresolved=True)
            )

    matched = []
    missing = []
    unknown = list(unresolved)
    for ingredient_id, ingredient in canonical.items():
        detail = IngredientDetail(canonical_id=str(ingredient_id), name=ingredient.name)
        status = inventory_statuses.get(ingredient_id)
        if status == InventoryItem.Status.IN_STOCK:
            matched.append(detail)
        elif status == InventoryItem.Status.NEEDS_REPLENISHMENT:
            missing.append(detail)
        else:
            unknown.append(detail)
    key = lambda item: (item.name.casefold(), item.canonical_id or "")
    return (
        tuple(sorted(matched, key=key)),
        tuple(sorted(missing, key=key)),
        tuple(sorted(unknown, key=key)),
        len(unresolved),
    )


def _snapshot_candidate(candidate):
    return {
        "recipeId": candidate.recipe_id,
        "recipeVersion": candidate.recipe_version,
        "matched": candidate.matched,
        "missing": candidate.missing,
        "unknown": candidate.unknown,
        "daysSinceCook": candidate.days_since_cook,
        "preferredTagMatches": candidate.preferred_tag_matches,
        "plannedOccurrences": candidate.planned_occurrences,
        "feedbackState": candidate.feedback_state,
    }


def _build_snapshot(
    *,
    as_of,
    inventory_snapshot_at,
    options,
    selected_tag_ids,
    truncated,
    candidates,
):
    snapshot = {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "scoringVersion": options.scoring_version,
        "asOf": as_of.isoformat(),
        "inventorySnapshotAt": inventory_snapshot_at.isoformat(),
        "targetWeek": options.week_start.isoformat(),
        "selectedTagIds": list(selected_tag_ids),
        "candidateSetTruncated": truncated,
        "candidates": [_snapshot_candidate(candidate) for candidate in candidates],
    }
    encoded = json.dumps(
        snapshot, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode()
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Recommendation snapshot exceeds 128 KiB.")
    return snapshot


@transaction.atomic
def prune_recommendation_runs(household, *, keep=MAX_RUNS_PER_HOUSEHOLD):
    stale_ids = list(
        RecommendationRun.objects.filter(household=household)
        .order_by("-created_at", "-id")
        .values_list("id", flat=True)[keep:]
    )
    if stale_ids:
        RecommendationRun.objects.filter(id__in=stale_ids).delete()
    return len(stale_ids)


def recommend(*, user, options):
    if not isinstance(options, RecommendationOptions):
        raise TypeError("options must be RecommendationOptions")
    if options.scoring_version not in (CATALOG_V1,):
        raise ValueError(f"Unsupported scoring version: {options.scoring_version}")
    if not 1 <= options.limit <= MAX_SUGGESTIONS:
        raise ValueError(f"limit must be between 1 and {MAX_SUGGESTIONS}.")

    as_of = timezone.now()
    query_started = time.perf_counter_ns()
    household = household_for(user)
    selected_tag_ids = _validated_tag_ids(household, options.preferred_tag_ids)
    selected_tag_uuid_set = {uuid.UUID(value) for value in selected_tag_ids}

    inventory_items = list(
        InventoryItem.objects.filter(household=household).only(
            "ingredient_id", "status", "updated_at"
        )
    )
    inventory_statuses = {item.ingredient_id: item.status for item in inventory_items}
    inventory_snapshot_at = max(
        (item.updated_at for item in inventory_items), default=as_of
    )

    ingredient_prefetch = Prefetch(
        "ingredients",
        queryset=RecipeIngredient.objects.select_related("canonical_ingredient").order_by(
            "sort_order"
        ),
        to_attr="recommendation_ingredients",
    )
    tag_prefetch = Prefetch(
        "tag_assignments",
        queryset=RecipeTagAssignment.objects.only("recipe_id", "tag_id"),
        to_attr="recommendation_tag_assignments",
    )
    loaded = list(
        Recipe.objects.filter(household=household, status=Recipe.Status.APPROVED)
        .annotate(latest_cooked_at=Max("cook_events__cooked_at"))
        .prefetch_related(ingredient_prefetch, tag_prefetch)
        .order_by("id")[: MAX_CANDIDATES + 1]
    )
    truncated = len(loaded) > MAX_CANDIDATES
    recipes = loaded[:MAX_CANDIDATES]
    recipe_ids = [recipe.id for recipe in recipes]

    planned_counts = {
        row["recipe_id"]: row["count"]
        for row in (
            MealSlot.objects.filter(
                plan__household=household,
                plan__week_start_date=options.week_start,
                recipe_id__in=recipe_ids,
            )
            .values("recipe_id")
            .annotate(count=Count("id"))
        )
    }

    feedback_states = {}
    feedback_rows = RecommendationFeedback.objects.filter(
        household=household,
        user=user,
        recipe_id__in=recipe_ids,
        active=True,
    ).filter(Q(outcome=RecommendationFeedback.Outcome.HIDDEN) | Q(reason="not_again"))
    for recipe_id, outcome, reason in feedback_rows.values_list("recipe_id", "outcome", "reason"):
        feedback_states[recipe_id] = (
            "hidden" if outcome == RecommendationFeedback.Outcome.HIDDEN else reason
        )
    query_duration_ms = _elapsed_ms(query_started)

    scoring_started = time.perf_counter_ns()
    candidates = []
    for recipe in recipes:
        matched_items, missing_items, unknown_items, unresolved_count = _ingredient_features(
            recipe, inventory_statuses
        )
        preferred_matches = sum(
            assignment.tag_id in selected_tag_uuid_set
            for assignment in recipe.recommendation_tag_assignments
        )
        candidates.append(
            CandidateFeatures(
                recipe_id=str(recipe.id),
                recipe_version=recipe.version,
                title=recipe.title,
                matched=len(matched_items),
                missing=len(missing_items),
                unknown=len(unknown_items),
                days_since_cook=_local_days_since(recipe.latest_cooked_at, as_of),
                preferred_tag_matches=preferred_matches,
                planned_occurrences=planned_counts.get(recipe.id, 0),
                feedback_state=feedback_states.get(recipe.id),
                matched_ingredients=matched_items,
                missing_ingredients=missing_items,
                unknown_ingredients=unknown_items,
                unresolved_count=unresolved_count,
            )
        )
    suggestions = [
        score_candidate(
            candidate,
            scoring_version=options.scoring_version,
            selected_tag_count=len(selected_tag_ids),
        )
        for candidate in candidates
    ]
    suggestions.sort(key=lambda item: (-item.score_bp, item.candidate.recipe_id))
    scoring_duration_ms = _elapsed_ms(scoring_started)

    snapshot = _build_snapshot(
        as_of=as_of,
        inventory_snapshot_at=inventory_snapshot_at,
        options=options,
        selected_tag_ids=selected_tag_ids,
        truncated=truncated,
        candidates=candidates,
    )
    run = RecommendationRun(
        household=household,
        requester=user,
        snapshot=snapshot,
        snapshot_schema_version=SNAPSHOT_SCHEMA_VERSION,
        scoring_version=options.scoring_version,
        inventory_snapshot_at=inventory_snapshot_at,
        candidate_count=len(candidates),
        query_duration_ms=query_duration_ms,
        scoring_duration_ms=scoring_duration_ms,
        generation_eligible=False,
        generation_ineligibility_reason="generation_not_available",
    )
    run.full_clean()
    run.save()
    prune_recommendation_runs(household)
    return RecommendationResult(
        run_id=str(run.id),
        scoring_version=options.scoring_version,
        as_of=as_of,
        inventory_snapshot_at=inventory_snapshot_at,
        week_start=options.week_start,
        candidate_set_truncated=truncated,
        candidate_count=len(candidates),
        query_duration_ms=query_duration_ms,
        scoring_duration_ms=scoring_duration_ms,
        suggestions=tuple(suggestions[: options.limit]),
    )


def replay_snapshot(snapshot):
    if snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("Unsupported recommendation snapshot schema version.")
    scoring_version = snapshot.get("scoringVersion")
    selected_tag_count = len(snapshot.get("selectedTagIds", []))
    candidates = []
    for item in snapshot.get("candidates", []):
        candidates.append(
            CandidateFeatures(
                recipe_id=str(item["recipeId"]),
                recipe_version=int(item["recipeVersion"]),
                title="",
                matched=int(item["matched"]),
                missing=int(item["missing"]),
                unknown=int(item["unknown"]),
                days_since_cook=int(item["daysSinceCook"]),
                preferred_tag_matches=int(item["preferredTagMatches"]),
                planned_occurrences=int(item["plannedOccurrences"]),
                feedback_state=item.get("feedbackState"),
            )
        )
    suggestions = [
        score_candidate(
            candidate,
            scoring_version=scoring_version,
            selected_tag_count=selected_tag_count,
        )
        for candidate in candidates
    ]
    suggestions.sort(key=lambda item: (-item.score_bp, item.candidate.recipe_id))
    return RecommendationResult(
        run_id=None,
        scoring_version=scoring_version,
        as_of=datetime.fromisoformat(snapshot["asOf"]),
        inventory_snapshot_at=datetime.fromisoformat(snapshot["inventorySnapshotAt"]),
        week_start=datetime.fromisoformat(snapshot["targetWeek"]).date(),
        candidate_set_truncated=bool(snapshot.get("candidateSetTruncated")),
        candidate_count=len(candidates),
        query_duration_ms=0,
        scoring_duration_ms=0,
        suggestions=tuple(suggestions),
    )


def _run_contains_recipe(run, recipe_id):
    return any(
        str(candidate.get("recipeId")) == str(recipe_id)
        for candidate in run.snapshot.get("candidates", [])
    )


@transaction.atomic
def record_feedback(*, user, run_id, recipe_id, outcome, reason=""):
    if outcome not in RecommendationFeedback.Outcome.values:
        raise ValueError("Unknown recommendation outcome.")
    if reason and reason not in RecommendationFeedback.Reason.values:
        raise ValueError("Unknown recommendation feedback reason.")
    if reason and outcome not in (
        RecommendationFeedback.Outcome.DISMISSED,
        RecommendationFeedback.Outcome.HIDDEN,
    ):
        raise ValueError("A reason is only valid for dismissed or hidden feedback.")
    try:
        run_id = uuid.UUID(str(run_id))
        recipe_id = uuid.UUID(str(recipe_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Recommendation run and recipe IDs must be valid UUIDs.") from exc
    household = household_for(user)
    run = RecommendationRun.objects.filter(
        id=run_id, household=household, requester=user
    ).first()
    recipe = Recipe.objects.filter(id=recipe_id, household=household).first()
    if not run or not recipe or not _run_contains_recipe(run, recipe.id):
        raise ValueError("Recommendation run and recipe do not match the active household.")
    preference = outcome == RecommendationFeedback.Outcome.HIDDEN or (
        outcome == RecommendationFeedback.Outcome.DISMISSED
        and reason == RecommendationFeedback.Reason.NOT_AGAIN
    )
    if preference:
        feedback = (
            RecommendationFeedback.objects.filter(
                household=household,
                user=user,
                recipe=recipe,
                active=True,
            )
            .filter(
                Q(outcome=RecommendationFeedback.Outcome.HIDDEN)
                | Q(
                    outcome=RecommendationFeedback.Outcome.DISMISSED,
                    reason=RecommendationFeedback.Reason.NOT_AGAIN,
                )
            )
            .first()
        )
        created = feedback is None
        if feedback is None:
            feedback = RecommendationFeedback.objects.create(
                household=household,
                user=user,
                recipe=recipe,
                recommendation_run=run,
                outcome=outcome,
                reason=reason,
            )
        else:
            feedback.recommendation_run = run
            feedback.outcome = outcome
            feedback.reason = reason
            feedback.save(
                update_fields=["recommendation_run", "outcome", "reason"]
            )
    else:
        feedback, created = RecommendationFeedback.objects.update_or_create(
            recommendation_run=run,
            user=user,
            recipe=recipe,
            outcome=outcome,
            defaults={
                "household": household,
                "reason": reason,
                "active": True,
            },
        )
    return feedback, created


def record_feedback_safely(*, user, run_id, recipe_id, outcome, reason=""):
    if not run_id:
        return None
    try:
        return record_feedback(
            user=user,
            run_id=run_id,
            recipe_id=recipe_id,
            outcome=outcome,
            reason=reason,
        )[0]
    except (TypeError, ValueError, ValidationError, DatabaseError):
        return None
