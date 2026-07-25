import time

from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = "Run the durable-job worker placeholder. Import jobs arrive in milestone 3."

    def handle(self, *args, **options):
        self.stdout.write("Worker is ready; no job types are enabled before milestone 3.")
        while True:
            connection.ensure_connection()
            time.sleep(30)
