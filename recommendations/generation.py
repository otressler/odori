import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from core.observability import current_context, log_event
from core.services import household_for
from pantry.models import CanonicalIngredient, InventoryItem
from providers.azure_recipe_generation import AzureOpenAIRecipeGenerationProvider
from providers.recipe_generation import RecipeGenerationError, RecipeGenerationRequest
from recipes.models import Recipe, RecipeIngredient, RecipeSource
from recipes.services import create_or_update_recipe

from .models import GeneratedRecipeJob, GenerationDailyUsage, RecommendationRun

logger = logging.getLogger(__name__)

CONTEXT_MAX_CHARS = 6000
RUN_MAX_AGE = timedelta(hours=24)
GENERATION_COVERAGE_THRESHOLD_BP = 7500
PROMPT_VERSION = "recipe-generation-v1"
SCHEMA_VERSION = 1


class GenerationAdmissionError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def generation_configuration():
    configured = bool(
        settings.AZURE_OPENAI_ENDPOINT
        and settings.AZURE_OPENAI_API_KEY
        and settings.AZURE_OPENAI_RECIPE_DEPLOYMENT
    )
    return {
        "enabled": settings.GENERATED_RECIPES_ENABLED,
        "configured": configured,
        "available": settings.GENERATED_RECIPES_ENABLED and configured,
    }


def catalog_generation_eligibility(candidates):
    coverage = [
        (10000 * int(item["matched"]))
        // max(
            1,
            sum(int(item[key]) for key in ("matched", "missing", "unknown")),
        )
        for item in candidates
    ]
    if not coverage or max(coverage) < GENERATION_COVERAGE_THRESHOLD_BP:
        return True, ""
    return False, "catalog_coverage_sufficient"


def build_generation_context(run, *, requested_servings):
    inventory = list(
        CanonicalIngredient.objects.filter(household=run.household, active=True)
        .order_by("name")
        .values_list("id", "name")
    )
    statuses = dict(
        InventoryItem.objects.filter(household=run.household).values_list(
            "ingredient_id", "status"
        )
    )
    candidates = run.snapshot.get("candidates", [])
    recipe_ids = [item.get("recipeId") for item in candidates[:250]]
    titles = dict(
        Recipe.objects.filter(
            household=run.household, id__in=recipe_ids, status=Recipe.Status.APPROVED
        ).values_list("id", "title")
    )
    excluded = []
    for item in candidates:
        if int(item.get("plannedOccurrences", 0)) or int(item.get("daysSinceCook", 21)) <= 7:
            try:
                title = titles.get(uuid.UUID(str(item.get("recipeId"))))
            except (TypeError, ValueError, AttributeError):
                title = None
            if title:
                excluded.append(title)
        if len(excluded) == 20:
            break
    totals = {
        key: sum(int(item.get(key, 0)) for item in candidates)
        for key in ("matched", "missing", "unknown")
    }
    base = {
        "locale": run.requester.locale if run.requester else "de",
        "requestedServings": requested_servings,
        "catalogCoverage": {
            "candidateCount": min(len(candidates), 250),
            "matchedRequired": totals["matched"],
            "missingRequired": totals["missing"],
            "unknownRequired": totals["unknown"],
        },
        "recentOrPlannedRecipes": excluded,
        "ingredients": [],
    }
    for ingredient_id, name in inventory:
        base["ingredients"].append(
            {"name": name, "status": statuses.get(ingredient_id, InventoryItem.Status.UNKNOWN)}
        )
        encoded = json.dumps(base, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(encoded) > CONTEXT_MAX_CHARS:
            base["ingredients"].pop()
            base["ingredientsTruncated"] = True
            break
    encoded = json.dumps(base, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(encoded) > CONTEXT_MAX_CHARS:
        raise GenerationAdmissionError("context_too_large", "Generation context is too large.")
    return encoded


def _input_hash(context_json):
    return hashlib.sha256(context_json.encode()).hexdigest()


def _usage_limits(monthly):
    prefix = "MONTHLY" if monthly else "DAILY"
    return (
        getattr(settings, f"GENERATED_RECIPES_{prefix}_JOB_LIMIT"),
        getattr(settings, f"GENERATED_RECIPES_{prefix}_INPUT_TOKEN_LIMIT"),
        getattr(settings, f"GENERATED_RECIPES_{prefix}_OUTPUT_TOKEN_LIMIT"),
    )


def _usage_values(row):
    return (
        row.used_calls + row.reserved_calls,
        row.used_input_tokens + row.reserved_input_tokens,
        row.used_output_tokens + row.reserved_output_tokens,
    )


def _assert_within(values, additions, limits):
    if any(value + addition > limit for value, addition, limit in zip(values, additions, limits)):
        raise GenerationAdmissionError("quota_exceeded", "Recipe generation quota is exhausted.")


def _reserve(household):
    today = timezone.localdate()
    try:
        GenerationDailyUsage.objects.get_or_create(household=household, date=today)
    except IntegrityError:
        pass
    row = GenerationDailyUsage.objects.select_for_update().get(
        household=household, date=today
    )
    additions = (
        1,
        settings.GENERATED_RECIPES_MAX_INPUT_TOKENS,
        settings.GENERATED_RECIPES_MAX_OUTPUT_TOKENS,
    )
    _assert_within(_usage_values(row), additions, _usage_limits(False))
    month_rows = GenerationDailyUsage.objects.filter(
        household=household,
        date__year=today.year,
        date__month=today.month,
    ).aggregate(
        calls=Sum("used_calls") + Sum("reserved_calls"),
        input=Sum("used_input_tokens") + Sum("reserved_input_tokens"),
        output=Sum("used_output_tokens") + Sum("reserved_output_tokens"),
    )
    monthly_values = tuple(int(month_rows[key] or 0) for key in ("calls", "input", "output"))
    _assert_within(monthly_values, additions, _usage_limits(True))
    row.reserved_calls += additions[0]
    row.reserved_input_tokens += additions[1]
    row.reserved_output_tokens += additions[2]
    row.save()
    return row


def _reconcile(job, *, input_tokens, output_tokens, consume_call=True, conservative=False):
    if not job.reservation_date:
        return
    usage = GenerationDailyUsage.objects.select_for_update().get(
        household=job.household, date=job.reservation_date
    )
    usage.reserved_calls = max(0, usage.reserved_calls - 1)
    usage.reserved_input_tokens = max(
        0, usage.reserved_input_tokens - job.reserved_input_tokens
    )
    usage.reserved_output_tokens = max(
        0, usage.reserved_output_tokens - job.reserved_output_tokens
    )
    if consume_call:
        usage.used_calls += 1
        usage.used_input_tokens += (
            job.reserved_input_tokens if conservative else max(0, input_tokens)
        )
        usage.used_output_tokens += (
            job.reserved_output_tokens if conservative else max(0, output_tokens)
        )
    usage.save()
    job.reservation_date = None
    job.reserved_input_tokens = 0
    job.reserved_output_tokens = 0


def _validate_admission(run):
    config = generation_configuration()
    if not config["enabled"]:
        raise GenerationAdmissionError("generation_disabled", "Recipe generation is disabled.")
    if not config["configured"]:
        raise GenerationAdmissionError(
            "missing_configuration", "Recipe generation is not configured."
        )
    if timezone.now() - run.created_at > RUN_MAX_AGE:
        raise GenerationAdmissionError("run_stale", "Recommendation run is too old.")
    if not run.generation_eligible:
        raise GenerationAdmissionError(
            run.generation_ineligibility_reason or "run_ineligible",
            "This recommendation run is not eligible for generation.",
        )


@transaction.atomic
def queue_generated_recipe(*, user, run_id, requested_servings=4):
    if isinstance(requested_servings, bool) or not isinstance(requested_servings, int):
        raise GenerationAdmissionError("invalid_servings", "servings must be an integer.")
    if not 1 <= requested_servings <= 100:
        raise GenerationAdmissionError("invalid_servings", "servings must be between 1 and 100.")
    household = household_for(user)
    run = (
        RecommendationRun.objects.select_for_update()
        .select_related("household", "requester")
        .filter(id=run_id, household=household)
        .first()
    )
    if not run:
        raise GenerationAdmissionError("run_not_found", "Recommendation run was not found.")
    active = GeneratedRecipeJob.objects.filter(
        recommendation_run=run,
        state__in=(GeneratedRecipeJob.State.QUEUED, GeneratedRecipeJob.State.RUNNING),
    ).first()
    if active:
        return active, False
    _validate_admission(run)
    context_json = build_generation_context(run, requested_servings=requested_servings)
    last = GeneratedRecipeJob.objects.filter(household=household).order_by("-created_at").first()
    available_at = timezone.now()
    if last:
        available_at = max(
            available_at,
            last.created_at
            + timedelta(seconds=settings.GENERATED_RECIPES_MIN_INTERVAL_SECONDS),
        )
    usage = _reserve(household)
    job = GeneratedRecipeJob.objects.create(
        household=household,
        recommendation_run=run,
        requester=user,
        requested_servings=requested_servings,
        deployment_version=settings.AZURE_OPENAI_RECIPE_DEPLOYMENT,
        input_hash=_input_hash(context_json),
        available_at=available_at,
        reserved_input_tokens=settings.GENERATED_RECIPES_MAX_INPUT_TOKENS,
        reserved_output_tokens=settings.GENERATED_RECIPES_MAX_OUTPUT_TOKENS,
        reservation_date=usage.date,
        correlation_id=current_context().get("request_id"),
    )
    return job, True


@transaction.atomic
def retry_generated_recipe(*, user, job_id, owner=False):
    household = household_for(user)
    job = (
        GeneratedRecipeJob.objects.select_for_update()
        .select_related("recommendation_run__requester")
        .filter(id=job_id, household=household)
        .first()
    )
    if not job:
        raise GenerationAdmissionError("job_not_found", "Generation job was not found.")
    if job.state != GeneratedRecipeJob.State.FAILED or not job.retryable:
        raise GenerationAdmissionError("not_retryable", "Generation job cannot be retried.")
    if job.attempt_count >= settings.GENERATED_RECIPES_MAX_ATTEMPTS:
        raise GenerationAdmissionError("attempt_limit", "Generation attempt limit was reached.")
    _validate_admission(job.recommendation_run)
    context_json = build_generation_context(
        job.recommendation_run, requested_servings=job.requested_servings
    )
    usage = _reserve(household)
    job.state = GeneratedRecipeJob.State.QUEUED
    job.input_hash = _input_hash(context_json)
    job.available_at = timezone.now() + timedelta(
        seconds=settings.GENERATED_RECIPES_MIN_INTERVAL_SECONDS
    )
    job.retryable = False
    job.error_code = ""
    job.error_message = ""
    job.validation_issue_codes = []
    job.started_at = None
    job.finished_at = None
    job.correlation_id = current_context().get("request_id")
    job.reserved_input_tokens = settings.GENERATED_RECIPES_MAX_INPUT_TOKENS
    job.reserved_output_tokens = settings.GENERATED_RECIPES_MAX_OUTPUT_TOKENS
    job.reservation_date = usage.date
    job.save()
    return job


def _safe_error_message(code):
    return {
        "timeout": "The generation provider timed out.",
        "rate_limited": "The generation provider is busy.",
        "provider_unavailable": "The generation provider is unavailable.",
        "network_error": "The generation provider could not be reached.",
        "content_filtered": "The generated result was rejected by safety filters.",
        "invalid_provider_schema": "The provider returned an invalid recipe.",
        "interrupted_unknown": "The worker stopped during a provider call.",
    }.get(code, "Recipe generation failed.")


def run_next_generated_recipe_job(provider=None):
    with transaction.atomic():
        job = (
            GeneratedRecipeJob.objects.select_for_update()
            .select_related("recommendation_run__requester")
            .filter(
                state=GeneratedRecipeJob.State.QUEUED,
                available_at__lte=timezone.now(),
            )
            .order_by("created_at")
            .first()
        )
        if not job:
            return False
        job.state = GeneratedRecipeJob.State.RUNNING
        job.started_at = timezone.now()
        job.attempt_count += 1
        job.save(update_fields=["state", "started_at", "attempt_count"])
    try:
        context_json = build_generation_context(
            job.recommendation_run, requested_servings=job.requested_servings
        )
        if _input_hash(context_json) != job.input_hash:
            with transaction.atomic():
                job = GeneratedRecipeJob.objects.select_for_update().get(id=job.id)
                _reconcile(job, input_tokens=0, output_tokens=0, consume_call=False)
                job.state = GeneratedRecipeJob.State.FAILED
                job.error_code = "context_changed"
                job.error_message = "Recommendation context changed before generation."
                job.retryable = False
                job.finished_at = timezone.now()
                job.save()
            return True
        provider = provider or AzureOpenAIRecipeGenerationProvider()
        result = provider.generate(
            RecipeGenerationRequest(
                context_json=context_json,
                max_output_tokens=settings.GENERATED_RECIPES_MAX_OUTPUT_TOKENS,
            ),
            household_id=job.household_id,
            job_id=job.id,
            correlation_id=job.correlation_id,
        )
        with transaction.atomic():
            job = (
                GeneratedRecipeJob.objects.select_for_update()
                .select_related("requester")
                .get(id=job.id)
            )
            recipe = create_or_update_recipe(
                user=job.requester,
                data=result.draft,
                source_type=RecipeSource.Type.GENERATED,
                queue_image=False,
                unmatched_state=RecipeIngredient.MatchState.REVIEW_NEEDED,
            )
            recipe.source.attribution = f"Generated with {job.deployment_version}"
            recipe.source.save(update_fields=["attribution"])
            _reconcile(
                job,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            job.recipe = recipe
            job.state = GeneratedRecipeJob.State.SUCCEEDED
            job.input_tokens = result.input_tokens
            job.output_tokens = result.output_tokens
            job.retryable = False
            job.finished_at = timezone.now()
            job.save()
    except RecipeGenerationError as exc:
        with transaction.atomic():
            job = GeneratedRecipeJob.objects.select_for_update().get(id=job.id)
            _reconcile(
                job,
                input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens,
            )
            job.state = GeneratedRecipeJob.State.FAILED
            job.error_code = exc.error_code
            job.error_message = _safe_error_message(exc.error_code)
            job.retryable = bool(
                exc.retryable
                and job.attempt_count < settings.GENERATED_RECIPES_MAX_ATTEMPTS
            )
            job.input_tokens = exc.input_tokens
            job.output_tokens = exc.output_tokens
            job.finished_at = timezone.now()
            job.save()
        log_event(
            logger,
            "job.failed",
            level=logging.ERROR,
            job_type="generated_recipe",
            job_id=job.id,
            household_id=job.household_id,
            error_code=job.error_code,
        )
    return True


@transaction.atomic
def recover_interrupted_generation_jobs():
    jobs = list(
        GeneratedRecipeJob.objects.select_for_update().filter(
            state=GeneratedRecipeJob.State.RUNNING
        )
    )
    for job in jobs:
        _reconcile(
            job,
            input_tokens=0,
            output_tokens=0,
            conservative=True,
        )
        job.state = GeneratedRecipeJob.State.FAILED
        job.error_code = "interrupted_unknown"
        job.error_message = _safe_error_message(job.error_code)
        job.retryable = job.attempt_count < settings.GENERATED_RECIPES_MAX_ATTEMPTS
        job.finished_at = timezone.now()
        job.save()
    return len(jobs)


def generation_job_json(job):
    return {
        "id": str(job.id),
        "runId": str(job.recommendation_run_id),
        "state": job.state,
        "retryable": job.retryable,
        "attempts": job.attempt_count,
        "errorCode": job.error_code or None,
        "validationIssueCodes": job.validation_issue_codes,
        "inputTokens": job.input_tokens,
        "outputTokens": job.output_tokens,
        "recipeId": str(job.recipe_id) if job.recipe_id else None,
        "createdAt": job.created_at.isoformat(),
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "finishedAt": job.finished_at.isoformat() if job.finished_at else None,
    }
