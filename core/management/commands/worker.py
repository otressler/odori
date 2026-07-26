import time

from django.core.management.base import BaseCommand
from django.db import connection

from recipes.images import recover_interrupted_recipe_image_jobs, run_next_recipe_image_job


class Command(BaseCommand):
    help = "Run durable background jobs."

    def handle(self, *args, **options):
        self.stdout.write("Worker is ready.")
        recovered = recover_interrupted_recipe_image_jobs()
        if recovered:
            self.stdout.write(f"Requeued {recovered} interrupted recipe image job(s).")
        while True:
            connection.ensure_connection()
            if not run_next_recipe_image_job():
                time.sleep(5)
