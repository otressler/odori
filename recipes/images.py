import base64
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from .models import RecipeImageJob

logger = logging.getLogger(__name__)


class RecipeImageGenerationError(Exception):
    pass


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
    return RecipeImageJob.objects.create(recipe=recipe, prompt=prompt)


def _generate_image_bytes(prompt):
    deployment = settings.AZURE_OPENAI_IMAGE_DEPLOYMENT
    if not all((settings.AZURE_OPENAI_ENDPOINT, settings.AZURE_OPENAI_API_KEY, deployment)):
        raise RecipeImageGenerationError("Microsoft Foundry image generation is not configured.")
    request = Request(
        f"{settings.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{deployment}/images/generations?api-version={settings.AZURE_OPENAI_IMAGE_API_VERSION}",
        data=json.dumps({"prompt": prompt, "size": "1024x1024", "n": 1}).encode(),
        headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS) as response:
            image_data = json.loads(response.read())["data"][0]["b64_json"]
    except (HTTPError, URLError, KeyError, ValueError, TimeoutError) as exc:
        raise RecipeImageGenerationError(
            "Microsoft Foundry could not generate the recipe image."
        ) from exc
    return base64.b64decode(image_data)


def recover_interrupted_recipe_image_jobs():
    return RecipeImageJob.objects.filter(state=RecipeImageJob.State.RUNNING).update(
        state=RecipeImageJob.State.QUEUED,
        error_message="",
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
        job.save(update_fields=["state", "started_at"])
        logger.info("Started recipe image job %s for recipe %s", job.id, job.recipe_id)

    try:
        image_bytes = _generate_image_bytes(job.prompt)
    except RecipeImageGenerationError as exc:
        with transaction.atomic():
            job = RecipeImageJob.objects.select_for_update().select_related("recipe").get(id=job.id)
            job.state = RecipeImageJob.State.FAILED
            job.error_message = str(exc)
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "finished_at"])
            if job.recipe.image_prompt == job.prompt:
                job.recipe.image_status = "failed"
                job.recipe.save(update_fields=["image_status"])
        logger.exception("Recipe image job %s failed", job.id)
        return True

    with transaction.atomic():
        job = RecipeImageJob.objects.select_for_update().select_related("recipe").get(id=job.id)
        if job.recipe.image_prompt != job.prompt:
            job.state = RecipeImageJob.State.SUPERSEDED
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "finished_at"])
            logger.info("Superseded recipe image job %s for recipe %s", job.id, job.recipe_id)
            return True
        job.recipe.image.save(f"{job.recipe.id}.png", ContentFile(image_bytes), save=False)
        job.recipe.image_status = "ready"
        job.recipe.save(update_fields=["image", "image_status"])
        job.state = RecipeImageJob.State.SUCCEEDED
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "finished_at"])
    logger.info("Completed recipe image job %s for recipe %s", job.id, job.recipe_id)
    return True