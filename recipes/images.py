import logging
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from core.jobs import run_next_job
from core.observability import current_context
from providers.foundry_images import generate_image_bytes

from .models import RecipeImageJob

logger = logging.getLogger(__name__)


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


def queue_recipe_image(recipe, *, prompt=None):
    prompt = prompt or recipe_image_prompt(recipe)
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


def queue_recipe_image_if_needed(recipe):
    prompt = recipe_image_prompt(recipe)
    if recipe.image and recipe.image_prompt == prompt:
        return None
    return queue_recipe_image(recipe, prompt=prompt)


def recover_interrupted_recipe_image_jobs():
    return RecipeImageJob.objects.filter(state=RecipeImageJob.State.RUNNING).update(
        state=RecipeImageJob.State.QUEUED,
        error_message="",
        error_code="",
        started_at=None,
    )


def _generate_recipe_image(job):
    return generate_image_bytes(
        job.prompt,
        household_id=job.recipe.household_id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        operation="recipe_image_generation",
    )


def _recipe_thumbnail(image_bytes):
    from PIL import Image, ImageOps

    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.fit(
            source.convert("RGB"),
            (settings.RECIPE_THUMBNAIL_SIZE, settings.RECIPE_THUMBNAIL_SIZE),
            method=Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        image.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


def _complete_recipe_image_job(job, image_bytes):
    if job.recipe.image_prompt != job.prompt:
        return False
    job.recipe.image.save(f"{job.recipe.id}.png", ContentFile(image_bytes), save=False)
    job.recipe.thumbnail.save(
        f"{job.recipe.id}.jpg", ContentFile(_recipe_thumbnail(image_bytes)), save=False
    )
    job.recipe.image_status = "ready"
    job.recipe.save(update_fields=["image", "thumbnail", "image_status"])
    return True


def _fail_recipe_image_job(job, exc):
    if job.recipe.image_prompt == job.prompt:
        job.recipe.image_status = "failed"
        job.recipe.save(update_fields=["image_status"])


def run_next_recipe_image_job():
    return run_next_job(
        RecipeImageJob,
        "recipe_image",
        select_related=("recipe",),
        household_id_for=lambda job: job.recipe.household_id,
        process=_generate_recipe_image,
        succeed=_complete_recipe_image_job,
        fail=_fail_recipe_image_job,
        log_fields=lambda job: {"recipe_id": job.recipe_id},
        logger=logger,
    )