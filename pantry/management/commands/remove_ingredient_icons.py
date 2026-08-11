import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from pantry.models import CanonicalIngredient, IngredientIconJob


class Command(BaseCommand):
    help = "Remove generated icons from selected ingredients or from all ingredients."

    def add_arguments(self, parser):
        parser.add_argument(
            "ingredient_ids",
            nargs="*",
            type=uuid.UUID,
            help="IDs of ingredients whose icons should be removed.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="remove_all",
            help="Remove icons from every ingredient.",
        )

    def handle(self, *args, **options):
        ingredient_ids = options["ingredient_ids"]
        if options["remove_all"] and ingredient_ids:
            raise CommandError("Use either ingredient IDs or --all, not both.")
        if not options["remove_all"] and not ingredient_ids:
            raise CommandError("Provide at least one ingredient ID or use --all.")

        ingredients = CanonicalIngredient.objects.all()
        if not options["remove_all"]:
            ingredients = ingredients.filter(id__in=ingredient_ids)
            found_ids = set(ingredients.values_list("id", flat=True))
            missing_ids = [
                ingredient_id for ingredient_id in ingredient_ids if ingredient_id not in found_ids
            ]
            if missing_ids:
                missing = ", ".join(str(ingredient_id) for ingredient_id in missing_ids)
                raise CommandError(f"Ingredient(s) not found: {missing}")

        removed = 0
        now = timezone.now()
        for ingredient in ingredients:
            ingredient.icon.delete(save=False)
            IngredientIconJob.objects.filter(
                ingredient=ingredient,
                state__in=[IngredientIconJob.State.QUEUED, IngredientIconJob.State.RUNNING],
            ).update(
                state=IngredientIconJob.State.SUPERSEDED,
                finished_at=now,
                available_at=None,
            )
            ingredient.icon_status = "pending"
            ingredient.icon_prompt = ""
            ingredient.save(update_fields=["icon", "icon_status", "icon_prompt"])
            removed += 1

        self.stdout.write(self.style.SUCCESS(f"Removed icons from {removed} ingredient(s)."))
