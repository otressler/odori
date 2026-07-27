import logging
import time

from django.core.management.base import BaseCommand
from django.db import connection

from pantry.jobs import recover_interrupted_category_jobs, run_next_category_job
from recipes.images import recover_interrupted_recipe_image_jobs, run_next_recipe_image_job

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run durable background jobs."

    def handle(self, *args, **options):
        self.stdout.write("Worker is ready.")
        logger.info("Worker is ready")
        recovered = recover_interrupted_recipe_image_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted recipe image job(s).")
            logger.info("Requeued %s interrupted recipe image job(s)", recovered)
        recovered = recover_interrupted_category_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted pantry categorization job(s).")
            logger.info("Requeued %s interrupted pantry categorization job(s)", recovered)
        while True:
            connection.ensure_connection()
            if not run_next_category_job() and not run_next_recipe_image_job():
                time.sleep(5)
