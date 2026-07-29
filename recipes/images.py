import base64
import binascii
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.observability import bind_context, current_context, log_event, record_provider_diagnostic

from .models import RecipeImageJob

logger = logging.getLogger(__name__)


class RecipeImageGenerationError(Exception):
    def __init__(self, message, *, error_code, http_status=None):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


def recipe_image_prompt(recipe):
    ingredients = ", ".join(line.source_text for line in recipe.ingredients.all())
    description = recipe.description or "A home-cooked dish."
    return (
        f"Editorial food photograph for the Odori recipe '{recipe.title}'. "
        f"Ingredients to feature: {ingredients or 'seasonal ingredients'}. "
        f"Description: {description}. "
        "Visual style: warm Tuscan home kitchen, sun-bleached plaster, handmade ceramic, "
        "terracotta, olive and saffron accents, natural window light, tactile and appetising. "
        "Overhead three-quarter composition, no people. "
        "No text, logos, watermarks, borders, UI, or mockup chrome."
    )


def queue_recipe_image(recipe):
    prompt = recipe_image_prompt(recipe)
    RecipeImageJob.objects.filter(
        recipe=recipe, state=RecipeImageJob.State.QUEUED
    ).update(state=RecipeImageJob.State.SUPERSEDED, finished_at=timezone.now())
    recipe.image.delete(save=False)
    recipe.image_status = "pending"
    recipe.image_prompt = prompt
    recipe.save(update_fields=["image", "image_status", "image_prompt"])
    return RecipeImageJob.objects.create(
        recipe=recipe,
        prompt=prompt,
        correlation_id=current_context().get("request_id"),
    )


def _generate_image_bytes(
    prompt,
    *,
    household_id,
    job_id,
    correlation_id,
    operation="recipe_image_generation",
    background=None,
    output_format=None,
    deployment=None,
):
    started = time.monotonic()
    deployment = settings.AZURE_OPENAI_IMAGE_DEPLOYMENT if deployment is None else deployment
    if not all((settings.AZURE_OPENAI_ENDPOINT, settings.AZURE_OPENAI_API_KEY, deployment)):
        error = RecipeImageGenerationError(
            "Microsoft Foundry image generation is not configured.",
            error_code="missing_configuration",
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error
    payload = {"prompt": prompt, "size": "1024x1024", "n": 1}
    if background:
        payload["background"] = background
    if output_format:
        payload["output_format"] = output_format
    request = Request(
        f"{settings.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{deployment}/images/generations?api-version={settings.AZURE_OPENAI_IMAGE_API_VERSION}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS) as response:
            image_data = json.loads(response.read())["data"][0]["b64_json"]
    except TimeoutError as exc:
        error = RecipeImageGenerationError(
            "Microsoft Foundry image generation timed out.", error_code="timeout"
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error from exc
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except OSError:
            detail = ""
        error = RecipeImageGenerationError(
            f"Microsoft Foundry could not generate the image. {detail}".strip(),
            error_code="http_error",
            http_status=exc.code,
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error from exc
    except URLError as exc:
        error = RecipeImageGenerationError(
            "Microsoft Foundry could not generate the recipe image.",
            error_code="network_error",
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error from exc
    except (KeyError, ValueError, TypeError) as exc:
        error = RecipeImageGenerationError(
            "Microsoft Foundry returned an invalid image response.",
            error_code="invalid_response",
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error from exc
    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except (binascii.Error, TypeError) as exc:
        error = RecipeImageGenerationError(
            "Microsoft Foundry returned an invalid image response.",
            error_code="invalid_response",
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error from exc
    _record_image_diagnostic(
        household_id=household_id,
        job_id=job_id,
        correlation_id=correlation_id,
        deployment=deployment,
        started=started,
        operation=operation,
    )
    return image_bytes


def _record_image_diagnostic(
    *,
    household_id,
    job_id,
    correlation_id,
    deployment,
    started,
    operation="recipe_image_generation",
    error=None,
):
    duration_ms = round((time.monotonic() - started) * 1000)
    state = "failed" if error else "succeeded"
    record_provider_diagnostic(
        household_id=household_id,
        correlation_id=correlation_id,
        job_id=job_id,
        operation=operation,
        state=state,
        error_code=error.error_code if error else "",
        http_status=error.http_status if error else None,
        deployment=deployment,
        duration_ms=duration_ms,
    )
    log_event(
        logger,
        "provider.image_generation_completed",
        level=logging.WARNING if error else logging.INFO,
        job_id=job_id,
        household_id=household_id,
        provider_state=state,
        error_code=error.error_code if error else "",
        http_status=error.http_status if error else None,
        deployment=deployment,
        duration_ms=duration_ms,
    )


def recover_interrupted_recipe_image_jobs():
    return RecipeImageJob.objects.filter(state=RecipeImageJob.State.RUNNING).update(
        state=RecipeImageJob.State.QUEUED,
        error_message="",
        error_code="",
        started_at=None,
    )


def run_next_recipe_image_job():
    with transaction.atomic():
        job = (
            RecipeImageJob.objects.select_for_update()
            .select_related("recipe")
            .filter(state=RecipeImageJob.State.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return False
        job.state = RecipeImageJob.State.RUNNING
        job.started_at = timezone.now()
        job.attempt_count += 1
        job.error_message = ""
        job.error_code = ""
        job.save(
            update_fields=[
                "state",
                "started_at",
                "attempt_count",
                "error_message",
                "error_code",
            ]
        )
        log_event(
            logger,
            "job.started",
            job_type="recipe_image",
            job_id=job.id,
            recipe_id=job.recipe_id,
            household_id=job.recipe.household_id,
            attempt_count=job.attempt_count,
        )

    try:
        with bind_context(
            request_id=job.correlation_id,
            job_id=job.id,
            household_id=job.recipe.household_id,
        ):
            image_bytes = _generate_image_bytes(
                job.prompt,
                household_id=job.recipe.household_id,
                job_id=job.id,
                correlation_id=job.correlation_id,
            )
    except RecipeImageGenerationError as exc:
        with transaction.atomic():
            job = RecipeImageJob.objects.select_for_update().select_related("recipe").get(id=job.id)
            job.state = RecipeImageJob.State.FAILED
            job.error_message = str(exc)
            job.error_code = exc.error_code
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "error_code", "finished_at"])
            if job.recipe.image_prompt == job.prompt:
                job.recipe.image_status = "failed"
                job.recipe.save(update_fields=["image_status"])
        log_event(
            logger,
            "job.failed",
            level=logging.ERROR,
            job_type="recipe_image",
            job_id=job.id,
            recipe_id=job.recipe_id,
            household_id=job.recipe.household_id,
            error_code=job.error_code,
        )
        logger.exception("Recipe image job failed")
        return True
    except Exception as exc:
        with transaction.atomic():
            job = RecipeImageJob.objects.select_for_update().select_related("recipe").get(id=job.id)
            job.state = RecipeImageJob.State.FAILED
            job.error_message = str(exc)[:500]
            job.error_code = type(exc).__name__
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "error_code", "finished_at"])
            if job.recipe.image_prompt == job.prompt:
                job.recipe.image_status = "failed"
                job.recipe.save(update_fields=["image_status"])
        log_event(
            logger,
            "job.failed",
            level=logging.ERROR,
            job_type="recipe_image",
            job_id=job.id,
            recipe_id=job.recipe_id,
            household_id=job.recipe.household_id,
            error_code=job.error_code,
        )
        logger.exception("Unexpected recipe image job failure")
        return True

    with transaction.atomic():
        job = RecipeImageJob.objects.select_for_update().select_related("recipe").get(id=job.id)
        if job.recipe.image_prompt != job.prompt:
            job.state = RecipeImageJob.State.SUPERSEDED
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "finished_at"])
            log_event(
                logger,
                "job.superseded",
                job_type="recipe_image",
                job_id=job.id,
                recipe_id=job.recipe_id,
            )
            return True
        job.recipe.image.save(f"{job.recipe.id}.png", ContentFile(image_bytes), save=False)
        job.recipe.image_status = "ready"
        job.recipe.save(update_fields=["image", "image_status"])
        job.state = RecipeImageJob.State.SUCCEEDED
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "finished_at"])
    log_event(
        logger,
        "job.completed",
        job_type="recipe_image",
        job_id=job.id,
        recipe_id=job.recipe_id,
        household_id=job.recipe.household_id,
        attempt_count=job.attempt_count,
    )
    return True