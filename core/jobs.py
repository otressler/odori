import logging

from django.db import transaction
from django.utils import timezone

from core.observability import bind_context, log_event


def run_next_job(
    model,
    job_type,
    *,
    select_related,
    household_id_for,
    process,
    succeed,
    fail,
    ready=None,
    claimed=None,
    log_fields=None,
    logger,
):
    queryset = model.objects.select_for_update(of=("self",)).select_related(*select_related)
    with transaction.atomic():
        if ready is not None:
            queryset = ready(queryset)
        job = queryset.filter(state=model.State.QUEUED).order_by("created_at").first()
        if job is None:
            return False
        job.state = model.State.RUNNING
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
        if claimed is not None:
            claimed(job)

    household_id = household_id_for(job)
    fields = log_fields(job) if log_fields else {}
    log_event(
        logger,
        "job.started",
        job_type=job_type,
        job_id=job.id,
        household_id=household_id,
        attempt_count=job.attempt_count,
        **fields,
    )
    try:
        with bind_context(
            request_id=job.correlation_id,
            job_id=job.id,
            household_id=household_id,
        ):
            result = process(job)
    except Exception as exc:
        error_code = getattr(exc, "error_code", type(exc).__name__)
        with transaction.atomic():
            job = (
                model.objects.select_for_update(of=("self",))
                .select_related(*select_related)
                .get(id=job.id)
            )
            fail(job, exc)
            job.state = model.State.FAILED
            job.error_message = str(exc)[:500]
            job.error_code = error_code
            job.finished_at = timezone.now()
            job.save(update_fields=["state", "error_message", "error_code", "finished_at"])
        log_event(
            logger,
            "job.failed",
            level=logging.ERROR,
            job_type=job_type,
            job_id=job.id,
            household_id=household_id_for(job),
            error_code=job.error_code,
            **(log_fields(job) if log_fields else {}),
        )
        logger.exception("%s job failed", job_type)
        return True

    with transaction.atomic():
        job = (
            model.objects.select_for_update(of=("self",))
            .select_related(*select_related)
            .get(id=job.id)
        )
        completed = succeed(job, result)
        job.state = model.State.SUCCEEDED if completed else model.State.SUPERSEDED
        job.finished_at = timezone.now()
        job.save(update_fields=["state", "finished_at"])
    event = "job.completed" if completed else "job.superseded"
    log_event(
        logger,
        event,
        job_type=job_type,
        job_id=job.id,
        household_id=household_id_for(job),
        attempt_count=job.attempt_count,
        **(log_fields(job) if log_fields else {}),
    )
    return True
