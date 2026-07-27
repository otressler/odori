import logging

from django.db import transaction
from django.utils import timezone

from core.observability import bind_context, log_event

from .models import PantryCategorizationJob
from .services import categorize_household

logger = logging.getLogger(__name__)


def recover_interrupted_category_jobs():
    return PantryCategorizationJob.objects.filter(
        state=PantryCategorizationJob.State.RUNNING
    ).update(
        state=PantryCategorizationJob.State.QUEUED,
        error_message="",
        error_code="",
        started_at=None,
    )


def run_next_category_job():
    with transaction.atomic():
        job = (
            PantryCategorizationJob.objects.select_for_update()
            .select_related("household")
            .filter(state=PantryCategorizationJob.State.QUEUED)
            .order_by("created_at")
            .first()
        )
        if job is None:
            return False
        job.state = PantryCategorizationJob.State.RUNNING
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
            job_type="pantry_category",
            job_id=job.id,
            household_id=job.household_id,
            attempt_count=job.attempt_count,
        )

    try:
        with bind_context(
            request_id=job.correlation_id,
            job_id=job.id,
            household_id=job.household_id,
        ):
            assigned_count = categorize_household(
                household=job.household,
                job_id=job.id,
                correlation_id=job.correlation_id,
            )
    except Exception as exc:
        with transaction.atomic():
            job = PantryCategorizationJob.objects.select_for_update().get(id=job.id)
            job.state = PantryCategorizationJob.State.FAILED
            job.error_message = str(exc)[:500]
            job.error_code = type(exc).__name__
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "error_code", "finished_at"])
        log_event(
            logger,
            "job.failed",
            level=logging.ERROR,
            job_type="pantry_category",
            job_id=job.id,
            household_id=job.household_id,
            error_code=job.error_code,
        )
        logger.exception("Pantry categorization job failed")
        return True

    with transaction.atomic():
        job = PantryCategorizationJob.objects.select_for_update().get(id=job.id)
        job.state = PantryCategorizationJob.State.SUCCEEDED
        job.assigned_count = assigned_count
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "assigned_count", "finished_at"])
    log_event(
        logger,
        "job.completed",
        job_type="pantry_category",
        job_id=job.id,
        household_id=job.household_id,
        assigned_count=assigned_count,
        attempt_count=job.attempt_count,
    )
    return True