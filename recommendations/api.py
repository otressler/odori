from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core.api import error, read_json
from core.services import household_for
from planning.services import parse_week_start

from .contracts import RecommendationOptions
from .generation import (
    GenerationAdmissionError,
    generation_configuration,
    generation_job_json,
    queue_generated_recipe,
    retry_generated_recipe,
)
from .models import GeneratedRecipeJob
from .services import MAX_SUGGESTIONS, recommend, record_feedback


def _ingredient_json(item):
    return {
        "canonicalIngredientId": item.canonical_id,
        "name": item.name,
        "unresolved": item.unresolved,
    }


def _suggestion_json(suggestion):
    candidate = suggestion.candidate
    return {
        "recipeId": candidate.recipe_id,
        "recipeVersion": candidate.recipe_version,
        "title": candidate.title,
        "scoreBp": suggestion.score_bp,
        "components": suggestion.components.as_dict(),
        "matchedCount": candidate.matched,
        "missingCount": candidate.missing,
        "unknownCount": candidate.unknown,
        "unresolvedCount": candidate.unresolved_count,
        "matchedIngredients": [
            _ingredient_json(item) for item in candidate.matched_ingredients
        ],
        "missingIngredients": [
            _ingredient_json(item) for item in candidate.missing_ingredients
        ],
        "unknownIngredients": [
            _ingredient_json(item) for item in candidate.unknown_ingredients
        ],
        "optionalIngredients": [
            _ingredient_json(item) for item in candidate.optional_ingredients
        ],
        "daysSinceCook": candidate.days_since_cook,
        "preferredTagMatches": candidate.preferred_tag_matches,
        "plannedOccurrences": candidate.planned_occurrences,
        "feedbackState": candidate.feedback_state,
        "reasons": [{"code": reason.code} for reason in suggestion.reasons],
    }


@login_required
@require_http_methods(["POST"])
def recommendations(request):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected a JSON object.", 400)
    preferred_tag_ids = data.get("preferredTagIds", [])
    if not isinstance(preferred_tag_ids, list):
        return error(
            "validation_failed",
            "preferredTagIds must be an array.",
            fields={"preferredTagIds": "Invalid."},
        )
    limit = data.get("limit", MAX_SUGGESTIONS)
    if isinstance(limit, bool) or not isinstance(limit, int):
        return error(
            "validation_failed",
            f"limit must be an integer between 1 and {MAX_SUGGESTIONS}.",
            fields={"limit": "Invalid."},
        )
    try:
        options = RecommendationOptions(
            week_start=parse_week_start(data.get("weekStart")),
            preferred_tag_ids=tuple(preferred_tag_ids),
            limit=limit,
        )
        result = recommend(user=request.user, options=options)
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(
        {
            "run": {
                "id": result.run_id,
                "asOf": result.as_of.isoformat(),
                "targetWeek": result.week_start.isoformat(),
                "scoringVersion": result.scoring_version,
                "inventorySnapshotAt": result.inventory_snapshot_at.isoformat(),
                "candidateCount": result.candidate_count,
                "candidateSetTruncated": result.candidate_set_truncated,
                "queryDurationMs": result.query_duration_ms,
                "scoringDurationMs": result.scoring_duration_ms,
                "generationEligible": result.generation_eligible,
                "generationIneligibilityReason": (
                    result.generation_ineligibility_reason or None
                ),
                "generationAvailable": generation_configuration()["available"],
            },
            "suggestions": [_suggestion_json(item) for item in result.suggestions],
        },
        status=201,
    )


@login_required
@require_http_methods(["POST"])
def recommendation_feedback(request, run_id):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected a JSON object.", 400)
    try:
        feedback, created = record_feedback(
            user=request.user,
            run_id=run_id,
            recipe_id=data.get("recipeId"),
            outcome=data.get("outcome"),
            reason=data.get("reason", ""),
        )
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(
        {
            "id": str(feedback.id),
            "outcome": feedback.outcome,
            "reason": feedback.reason or None,
            "active": feedback.active,
        },
        status=201 if created else 200,
    )


@login_required
@require_http_methods(["POST"])
def queue_generation(request, run_id):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected a JSON object.", 400)
    if set(data) - {"servings"}:
        return error("validation_failed", "Only servings may be supplied.")
    try:
        job, created = queue_generated_recipe(
            user=request.user,
            run_id=run_id,
            requested_servings=data.get("servings", 4),
        )
    except GenerationAdmissionError as exc:
        return error(exc.code, str(exc))
    return JsonResponse(generation_job_json(job), status=202 if created else 200)


@login_required
@require_http_methods(["GET"])
def generation_status(request, job_id):
    job = (
        GeneratedRecipeJob.objects.filter(
            id=job_id, household=household_for(request.user)
        )
        .select_related("recommendation_run")
        .first()
    )
    if not job:
        return error("job_not_found", "Generation job was not found.", 404)
    return JsonResponse(generation_job_json(job))


@login_required
@require_http_methods(["POST"])
def retry_generation(request, job_id):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected a JSON object.", 400)
    if data:
        return error("validation_failed", "This endpoint does not accept fields.")
    try:
        job = retry_generated_recipe(user=request.user, job_id=job_id)
    except GenerationAdmissionError as exc:
        return error(exc.code, str(exc))
    return JsonResponse(generation_job_json(job), status=202)
