import logging

from core.jobs import run_next_job

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


def _categorize(job):
    return categorize_household(
        household=job.household,
        job_id=job.id,
        correlation_id=job.correlation_id,
    )


def _complete_category_job(job, assigned_count):
    job.assigned_count = assigned_count
    job.save(update_fields=["assigned_count"])
    return True


def _fail_category_job(job, exc):
    return None


def _category_log_fields(job):
    return {"assigned_count": job.assigned_count} if job.state == job.State.SUCCEEDED else {}


def run_next_category_job():
    return run_next_job(
        PantryCategorizationJob,
        "pantry_category",
        select_related=("household",),
        household_id_for=lambda job: job.household_id,
        process=_categorize,
        succeed=_complete_category_job,
        fail=_fail_category_job,
        log_fields=_category_log_fields,
        logger=logger,
    )