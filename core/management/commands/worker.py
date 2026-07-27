import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone

from core.models import WorkerHeartbeat
from core.observability import log_event
from pantry.jobs import recover_interrupted_category_jobs, run_next_category_job
from recipes.images import recover_interrupted_recipe_image_jobs, run_next_recipe_image_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run durable background jobs."

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

            processed_job = run_next_category_job() or run_next_recipe_image_job()
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
