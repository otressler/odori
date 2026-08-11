import logging
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db.models import Q
from django.utils import timezone

from core.jobs import run_next_job
from core.observability import current_context
from providers.foundry_images import generate_image_bytes

from .models import IngredientIconJob

logger = logging.getLogger(__name__)


def ingredient_icon_prompt(ingredient):
    if settings.AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY:
        style = (
            "white hand-drawn strokes with a few clean carved details on a transparent "
            "background"
        )
    else:
        style = (
            "bold black hand-drawn strokes with a few clean carved details on a plain "
            "white background"
        )
    return (
        f"Low-resolution stylized pantry icon of {ingredient.name}. "
        f"A simple centered and fully visible drawing with {style}. "
        "No coloured background, no text, logos, watermarks, border, "
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
    latest_started_at = (
        IngredientIconJob.objects.filter(started_at__isnull=False)
        .order_by("-started_at")
        .values_list("started_at", flat=True)
        .first()
    )
    available_at = timezone.now()
    if latest_started_at:
        available_at = max(
            available_at,
            latest_started_at
            + timedelta(seconds=settings.AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS),
        )
    return IngredientIconJob.objects.create(
        ingredient=ingredient,
        prompt=prompt,
        correlation_id=current_context().get("request_id"),
        available_at=available_at,
    )


def queue_missing_ingredient_icons(ingredients, *, limit=None):
    queued = 0
    for ingredient in ingredients:
        if limit is not None and queued >= limit:
            break
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


def _defer_queued_icon_jobs(job):
    IngredientIconJob.objects.filter(state=IngredientIconJob.State.QUEUED).filter(
        Q(available_at__isnull=True) | Q(available_at__lt=job.started_at)
    ).update(
        available_at=job.started_at
        + timedelta(seconds=settings.AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS)
    )


def _generate_ingredient_icon(job):
    image_bytes = generate_image_bytes(
        job.prompt,
        household_id=job.ingredient.household_id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        operation="ingredient_icon_generation",
        background=(
            "transparent"
            if settings.AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY
            else None
        ),
        output_format="png",
        deployment=settings.AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT,
    )
    try:
        if not settings.AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY:
            image_bytes = _remove_white_background(image_bytes)
        return _resize_square(image_bytes, settings.INGREDIENT_ICON_SIZE)
    except (OSError, ValueError):
        return image_bytes


def _remove_white_background(image_bytes):
    from PIL import Image, ImageOps

    with Image.open(BytesIO(image_bytes)) as source:
        bmp = BytesIO()
        source.convert("RGB").save(bmp, format="BMP")
    bmp.seek(0)
    with Image.open(bmp) as source:
        image = ImageOps.invert(source.convert("RGB")).convert("RGBA")
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, _ = pixels[x, y]
            pixels[x, y] = (red, green, blue, 0 if (red + green + blue) / 3 <= 100 else 255)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _resize_square(image_bytes, side_length):
    from PIL import Image, ImageOps

    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.fit(
            source.convert("RGBA"),
            (side_length, side_length),
            method=Image.Resampling.LANCZOS,
        )
        alpha = image.getchannel("A").point(lambda value: 255 if value == 255 else 0)
        image.putalpha(alpha)
        output = BytesIO()
        image.save(output, format="PNG")
    return output.getvalue()


def _complete_ingredient_icon_job(job, image_bytes):
    if job.ingredient.icon_prompt != job.prompt:
        return False
    job.ingredient.icon.save(f"{job.ingredient.id}.png", ContentFile(image_bytes), save=False)
    job.ingredient.icon_status = "ready"
    job.ingredient.save(update_fields=["icon", "icon_status"])
    return True


def _fail_ingredient_icon_job(job, exc):
    if job.ingredient.icon_prompt == job.prompt:
        job.ingredient.icon_status = "failed"
        job.ingredient.save(update_fields=["icon_status"])


def _ready_icon_jobs(queryset):
    return queryset.filter(Q(available_at__isnull=True) | Q(available_at__lte=timezone.now()))


def run_next_ingredient_icon_job():
    return run_next_job(
        IngredientIconJob,
        "ingredient_icon",
        select_related=("ingredient",),
        household_id_for=lambda job: job.ingredient.household_id,
        process=_generate_ingredient_icon,
        succeed=_complete_ingredient_icon_job,
        fail=_fail_ingredient_icon_job,
        ready=_ready_icon_jobs,
        claimed=_defer_queued_icon_jobs,
        log_fields=lambda job: {"ingredient_id": job.ingredient_id},
        logger=logger,
    )