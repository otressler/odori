import hashlib
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.observability import bind_context
from providers.foundry_recipe_import import (
    RecipeExtractionError,
    extract_recipe_from_url,
)

from .models import ImportSource, Recipe, RecipeImportAttempt, RecipeImportJob, RecipeSource
from .services import create_or_update_recipe

logger = logging.getLogger(__name__)


class RetryableImportError(Exception):
    error_code = "provider_temporary"


class PermanentImportError(Exception):
    error_code = "invalid_source"


def create_import(*, household, source_type, content=b"", url="", content_type=""):
    hash_input = content or (url.strip().encode() if source_type == ImportSource.Type.URL else b"")
    content_hash = hashlib.sha256(hash_input).hexdigest()
    source, _ = ImportSource.objects.get_or_create(
        household=household,
        content_hash=content_hash,
        defaults={
            "source_type": source_type,
            "url": url,
            "content_type": content_type,
            "content_length": len(content),
        },
    )
    job = source.jobs.order_by("-created_at").first()
    if job is not None:
        return job, False
    job = RecipeImportJob.objects.create(
        household=household, source=source, available_at=timezone.now()
    )
    return job, True


def claim_next_import(*, now=None, lease_seconds=None):
    now = now or timezone.now()
    lease_seconds = lease_seconds or settings.IMPORT_JOB_LEASE_SECONDS
    with transaction.atomic():
        job = (
            RecipeImportJob.objects.select_for_update(skip_locked=True)
            .filter(state=RecipeImportJob.State.QUEUED)
            .filter(Q(available_at__isnull=True) | Q(available_at__lte=now))
            .order_by("created_at")
            .first()
        )
        if job is None:
            return None
        lease_id = uuid.uuid4()
        last_attempt = job.attempts.order_by("-number").first()
        next_attempt_number = max(
            job.attempt_count, last_attempt.number if last_attempt else 0
        ) + 1
        job.state = RecipeImportJob.State.RUNNING
        job.lease_id = lease_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.started_at = now
        job.attempt_count = next_attempt_number
        job.error_code = ""
        job.error_message = ""
        job.save(
            update_fields=[
                "state",
                "lease_id",
                "lease_expires_at",
                "started_at",
                "attempt_count",
                "error_code",
                "error_message",
            ]
        )
        attempt = RecipeImportAttempt.objects.create(
            job=job, number=next_attempt_number, lease_id=lease_id
        )
    return job, attempt


def recover_expired_imports(*, now=None):
    now = now or timezone.now()
    return RecipeImportJob.objects.filter(
        state=RecipeImportJob.State.RUNNING, lease_expires_at__lt=now
    ).update(
        state=RecipeImportJob.State.QUEUED,
        lease_id=None,
        lease_expires_at=None,
        available_at=now,
        error_code="lease_expired",
        error_message="Worker lease expired; the job was returned to the queue.",
    )


def _finish(job_id, lease_id, *, state, error=None, recipe=None):
    with transaction.atomic():
        job = RecipeImportJob.objects.select_for_update().get(id=job_id)
        if job.state != RecipeImportJob.State.RUNNING or job.lease_id != lease_id:
            return False
        now = timezone.now()
        attempt = job.attempts.get(number=job.attempt_count)
        attempt.finished_at = now
        attempt.outcome = state
        if error:
            attempt.error_code = error.error_code
        attempt.save(update_fields=["finished_at", "outcome", "error_code"])
        job.state = state
        job.recipe = recipe
        job.error_code = getattr(error, "error_code", "") if error else ""
        job.error_message = str(error)[:500] if error else ""
        job.lease_id = None
        job.lease_expires_at = None
        job.finished_at = now
        job.save(
            update_fields=[
                "state",
                "recipe",
                "error_code",
                "error_message",
                "lease_id",
                "lease_expires_at",
                "finished_at",
            ]
        )
    return True


def run_next_import_job(process=None):
    claimed = claim_next_import()
    if claimed is None:
        return False
    job, attempt = claimed
    process = process or _extract_and_create_recipe
    with bind_context(request_id=job.correlation_id, job_id=job.id, household_id=job.household_id):
        try:
            result = process(job)
            recipe = (
                result
                if isinstance(result, Recipe)
                else result.get("recipe")
                if isinstance(result, dict)
                else None
            )
            if recipe is None:
                source, _ = RecipeSource.objects.get_or_create(
                    import_source=job.source,
                    defaults={
                        "household_id": job.household_id,
                        "type": RecipeSource.Type.IMPORTED,
                        "attribution": job.source.url or "Imported recipe",
                    },
                )
                recipe, _ = Recipe.objects.get_or_create(
                    source=source,
                    defaults={
                        "household_id": job.household_id,
                        "created_by_id": job.household.memberships.values_list(
                            "user_id", flat=True
                        ).first(),
                        "title": (
                            result.get("title", "Imported recipe")
                            if isinstance(result, dict)
                            else "Imported recipe"
                        ),
                    },
                )
            _finish(job.id, attempt.lease_id, state=RecipeImportJob.State.SUCCEEDED, recipe=recipe)
        except RetryableImportError as exc:
            if job.attempt_count >= job.max_attempts:
                _finish(job.id, attempt.lease_id, state=RecipeImportJob.State.FAILED, error=exc)
            else:
                with transaction.atomic():
                    current = RecipeImportJob.objects.select_for_update().get(id=job.id)
                    if current.lease_id == attempt.lease_id:
                        current.state = RecipeImportJob.State.QUEUED
                        current.available_at = timezone.now() + timedelta(
                            seconds=settings.IMPORT_JOB_RETRY_DELAY_SECONDS
                        )
                        current.lease_id = None
                        current.lease_expires_at = None
                        current.error_code = exc.error_code
                        current.error_message = str(exc)[:500]
                        current.save(
                            update_fields=[
                                "state",
                                "available_at",
                                "lease_id",
                                "lease_expires_at",
                                "error_code",
                                "error_message",
                            ]
                        )
                        attempt.finished_at = timezone.now()
                        attempt.outcome = "retry"
                        attempt.error_code = exc.error_code
                        attempt.save(update_fields=["finished_at", "outcome", "error_code"])
        except Exception as exc:
            _finish(job.id, attempt.lease_id, state=RecipeImportJob.State.FAILED, error=exc)
            logger.exception("Recipe import failed")
    return True


def _extract_and_create_recipe(job):
    if job.source.source_type != ImportSource.Type.URL:
        raise PermanentImportError("Only URL recipe imports are supported yet.")
    job.stage = RecipeImportJob.Stage.EXTRACT
    job.save(update_fields=["stage"])
    try:
        data = extract_recipe_from_url(job.source.url)
    except RecipeExtractionError as exc:
        error = RetryableImportError(str(exc)) if exc.retryable else PermanentImportError(str(exc))
        error.error_code = exc.error_code
        raise error from exc

    job.stage = RecipeImportJob.Stage.NORMALIZE
    job.save(update_fields=["stage"])
    source, _ = RecipeSource.objects.get_or_create(
        import_source=job.source,
        defaults={
            "household_id": job.household_id,
            "type": RecipeSource.Type.IMPORTED,
            "attribution": job.source.url,
        },
    )
    membership = job.household.memberships.select_related("user").first()
    if membership is None:
        raise PermanentImportError("The import household has no available user.")
    recipe = create_or_update_recipe(
        user=membership.user,
        household=job.household,
        data=data,
        source_type=RecipeSource.Type.IMPORTED,
        source=source,
    )
    job.stage = RecipeImportJob.Stage.REVIEW
    job.save(update_fields=["stage"])
    return recipe