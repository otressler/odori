import logging

from django.db import transaction
from django.utils import timezone

from .models import PantryCategorizationJob
from .services import categorize_household

logger = logging.getLogger(__name__)


def recover_interrupted_category_jobs():
    return PantryCategorizationJob.objects.filter(
        state=PantryCategorizationJob.State.RUNNING
    ).update(
        state=PantryCategorizationJob.State.QUEUED,
        error_message="",
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
        job.save(update_fields=["state", "started_at"])
        logger.info(
            "Started pantry categorization job %s for household %s", job.id, job.household_id
        )

    try:
        assigned_count = categorize_household(household=job.household)
    except Exception as exc:
        with transaction.atomic():
            job = PantryCategorizationJob.objects.select_for_update().get(id=job.id)
            job.state = PantryCategorizationJob.State.FAILED
            job.error_message = str(exc)[:500]
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "finished_at"])
        logger.exception("Pantry categorization job %s failed", job.id)
        return True

    with transaction.atomic():
        job = PantryCategorizationJob.objects.select_for_update().get(id=job.id)
        job.state = PantryCategorizationJob.State.SUCCEEDED
        job.assigned_count = assigned_count
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "assigned_count", "finished_at"])
    logger.info(
        "Completed pantry categorization job %s for household %s: assigned_count=%s",
        job.id,
        job.household_id,
        assigned_count,
    )
    return True