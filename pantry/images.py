import logging
import time

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.observability import bind_context, current_context, log_event
from recipes.images import RecipeImageGenerationError, _generate_image_bytes

from .models import IngredientIconJob

logger = logging.getLogger(__name__)


def ingredient_icon_prompt(ingredient):
    return (
        f"Low-resolution stylized pantry icon of {ingredient.name}. "
        "A simple white silhouette with a few clean carved details, centered and fully visible. "
        "Transparent background, no coloured background, no text, logos, watermarks, border, "
        "UI, shadows, or mockup chrome. Square composition for a small shopping-list icon."
    )


def queue_ingredient_icon(ingredient):
    prompt = ingredient_icon_prompt(ingredient)
    IngredientIconJob.objects.filter(
        ingredient=ingredient, state=IngredientIconJob.State.QUEUED
    ).update(state=IngredientIconJob.State.SUPERSEDED, finished_at=timezone.now())
    ingredient.icon.delete(save=False)
    ingredient.icon_status = "pending"
    ingredient.icon_prompt = prompt
    ingredient.save(update_fields=["icon", "icon_status", "icon_prompt"])
    return IngredientIconJob.objects.create(
        ingredient=ingredient,
        prompt=prompt,
        correlation_id=current_context().get("request_id"),
    )


def queue_missing_ingredient_icons(ingredients):
    queued = 0
    for ingredient in ingredients:
        if ingredient.icon or ingredient.icon_status == "failed":
            continue
        active_job_exists = IngredientIconJob.objects.filter(
            ingredient=ingredient,
            state__in=[IngredientIconJob.State.QUEUED, IngredientIconJob.State.RUNNING],
        ).exists()
        if not active_job_exists:
            queue_ingredient_icon(ingredient)
            queued += 1
    return queued


def recover_interrupted_ingredient_icon_jobs():
    return IngredientIconJob.objects.filter(state=IngredientIconJob.State.RUNNING).update(
        state=IngredientIconJob.State.QUEUED,
        error_message="",
        error_code="",
        started_at=None,
    )


def run_next_ingredient_icon_job():
    with transaction.atomic():
        job = (
            IngredientIconJob.objects.select_for_update()
            .select_related("ingredient")
            .filter(state=IngredientIconJob.State.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return False
        job.state = IngredientIconJob.State.RUNNING
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

    try:
        with bind_context(
            request_id=job.correlation_id,
            job_id=job.id,
            household_id=job.ingredient.household_id,
        ):
            image_bytes = _generate_image_bytes(
                job.prompt,
                household_id=job.ingredient.household_id,
                job_id=job.id,
                correlation_id=job.correlation_id,
                operation="ingredient_icon_generation",
                background="transparent",
                output_format="png",
                deployment=settings.AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT,
            )
    except RecipeImageGenerationError as exc:
        error_message, error_code = str(exc), exc.error_code
    except Exception as exc:
        error_message, error_code = str(exc)[:500], type(exc).__name__
        logger.exception("Unexpected ingredient icon job failure")
    else:
        with transaction.atomic():
            job = (
                IngredientIconJob.objects.select_for_update()
                .select_related("ingredient")
                .get(id=job.id)
            )
            if job.ingredient.icon_prompt != job.prompt:
                job.state = IngredientIconJob.State.SUPERSEDED
                job.finished_at = timezone.now()
                job.save(update_fields=["state", "finished_at"])
                return True
            job.ingredient.icon.save(
                f"{job.ingredient.id}.png", ContentFile(image_bytes), save=False
            )
            job.ingredient.icon_status = "ready"
            job.ingredient.save(update_fields=["icon", "icon_status"])
            job.state = IngredientIconJob.State.SUCCEEDED
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "finished_at"])
        log_event(
            logger,
            "job.completed",
            job_type="ingredient_icon",
            job_id=job.id,
            ingredient_id=job.ingredient_id,
            household_id=job.ingredient.household_id,
            attempt_count=job.attempt_count,
        )
        time.sleep(settings.AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS)
        return True

    with transaction.atomic():
        job = (
            IngredientIconJob.objects.select_for_update()
            .select_related("ingredient")
            .get(id=job.id)
        )
        job.state = IngredientIconJob.State.FAILED
        job.error_message = error_message
        job.error_code = error_code
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "error_message", "error_code", "finished_at"])
        if job.ingredient.icon_prompt == job.prompt:
            job.ingredient.icon_status = "failed"
            job.ingredient.save(update_fields=["icon_status"])
    log_event(
        logger,
        "job.failed",
        level=logging.ERROR,
        job_type="ingredient_icon",
        job_id=job.id,
        ingredient_id=job.ingredient_id,
        household_id=job.ingredient.household_id,
        error_code=job.error_code,
    )
    time.sleep(settings.AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS)
    return True