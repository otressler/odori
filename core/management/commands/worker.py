import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from core.models import WorkerHeartbeat
from core.observability import log_event
from pantry.images import recover_interrupted_ingredient_icon_jobs, run_next_ingredient_icon_job
from pantry.jobs import recover_interrupted_category_jobs, run_next_category_job
from recipes.images import recover_interrupted_recipe_image_jobs, run_next_recipe_image_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run durable background jobs."

    def run_next_job(self):
        runners = (
            ("pantry_category", run_next_category_job),
            ("recipe_image", run_next_recipe_image_job),
            ("ingredient_icon", run_next_ingredient_icon_job),
        )
        for job_type, runner in runners:
            started = time.monotonic()
            try:
                processed = runner()
            except Exception:
                log_event(
                    logger,
                    "worker.job_execution_failed",
                    level=logging.ERROR,
                    job_type=job_type,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                raise
            if processed:
                log_event(
                    logger,
                    "worker.job_execution_completed",
                    job_type=job_type,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return True
        return False

    def heartbeat(self, *, state, last_error_message="", completed_job=False):
        now = timezone.now()
        heartbeat, _ = WorkerHeartbeat.objects.get_or_create(
            name="default",
            defaults={
                "state": state,
                "started_at": now,
                "last_heartbeat_at": now,
            },
        )
        heartbeat.state = state
        heartbeat.last_heartbeat_at = now
        heartbeat.last_error_message = last_error_message[:500]
        if completed_job:
            heartbeat.last_job_completed_at = now
        heartbeat.save(
            update_fields=[
                "state",
                "last_heartbeat_at",
                "last_job_completed_at",
                "last_error_message",
            ]
        )

    def handle(self, *args, **options):
        self.stdout.write("Worker is ready.")
        log_event(logger, "worker.started")
        self.heartbeat(state=WorkerHeartbeat.State.IDLE)
        recovered = recover_interrupted_recipe_image_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted recipe image job(s).")
            log_event(logger, "worker.jobs_requeued", job_type="recipe_image", count=recovered)
        recovered = recover_interrupted_ingredient_icon_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted ingredient icon job(s).")
            log_event(logger, "worker.jobs_requeued", job_type="ingredient_icon", count=recovered)
        recovered = recover_interrupted_category_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted pantry categorization job(s).")
            log_event(logger, "worker.jobs_requeued", job_type="pantry_category", count=recovered)

        while True:
            try:
                connection.ensure_connection()
            except Exception as exc:
                self.stderr.write(f"Database connection error: {exc}. Retrying in 5 seconds...")
                self.heartbeat(
                    state=WorkerHeartbeat.State.DEGRADED, last_error_message=str(exc)
                )
                log_event(logger, "worker.database_connection_failed", level=logging.ERROR)
                logger.exception("Worker database connection failed")
                time.sleep(5)
                continue

            processed_job = self.run_next_job()
            self.heartbeat(
                state=(
                    WorkerHeartbeat.State.WORKING
                    if processed_job
                    else WorkerHeartbeat.State.IDLE
                ),
                completed_job=processed_job,
            )
            if not processed_job:
                time.sleep(5)
