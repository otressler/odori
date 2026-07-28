from django.core.management.base import BaseCommand, CommandError

from core.models import Household
from pantry.catalog import sync_starter_catalog
from pantry.services import refresh_category_example_embeddings


class Command(BaseCommand):
    help = "Synchronize the curated German supermarket category catalog."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--household", help="Household UUID.")
        group.add_argument("--all-households", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--refresh-embeddings",
            action="store_true",
            help="Regenerate every active category-example embedding.",
        )

    def handle(self, *args, **options):
        if options["dry_run"] and options["refresh_embeddings"]:
            raise CommandError("--dry-run cannot be combined with --refresh-embeddings.")
        if options["all_households"]:
            households = Household.objects.all()
        else:
            households = Household.objects.filter(id=options["household"])
            if not households.exists():
                raise CommandError("Household was not found.")

        totals = {
            "categories_created": 0,
            "examples_created": 0,
            "examples_updated": 0,
            "deactivated": 0,
            "embeddings_updated": 0,
        }
        for household in households:
            result = sync_starter_catalog(household=household, dry_run=options["dry_run"])
            if options["refresh_embeddings"]:
                result["embeddings_updated"] = refresh_category_example_embeddings(
                    household=household,
                    force=True,
                )
            for key, value in result.items():
                totals[key] += value
        mode = "Would synchronize" if options["dry_run"] else "Synchronized"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode} {totals['categories_created']} categories; "
                f"{totals['examples_created']} created, {totals['examples_updated']} updated, "
                f"{totals['deactivated']} deactivated examples, and "
                f"{totals['embeddings_updated']} refreshed embeddings."
            )
        )
