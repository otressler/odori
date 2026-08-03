from django.core.management.base import BaseCommand, CommandError

from pantry.images import queue_missing_ingredient_icons
from pantry.models import CanonicalIngredient


class Command(BaseCommand):
    help = "Queue a capped batch of missing ingredient icons."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=25,
            help="Maximum number of paid icon generation jobs to queue (default: 25).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        if limit < 1:
            raise CommandError("--limit must be at least 1.")
        ingredients = CanonicalIngredient.objects.filter(
            active=True,
            icon="",
        ).exclude(icon_status="failed")
        queued = queue_missing_ingredient_icons(ingredients, limit=limit)
        self.stdout.write(self.style.SUCCESS(f"Queued {queued} ingredient icon(s)."))
