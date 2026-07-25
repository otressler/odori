from django.core.management.base import BaseCommand, CommandError

from core.models import HouseholdMembership
from pantry.models import CanonicalIngredient, IngredientCategory, InventoryItem
from recipes.services import create_or_update_recipe


class Command(BaseCommand):
    help = "Seed a small German-language household dataset for local review."

    def handle(self, *args, **options):
        membership = HouseholdMembership.objects.select_related("household", "user").first()
        if not membership:
            raise CommandError("Run bootstrap_owner first.")
        household = membership.household
        produce, _ = IngredientCategory.objects.get_or_create(
            household=household, name="Gemüse", sort_order=1
        )
        pantry, _ = IngredientCategory.objects.get_or_create(
            household=household, name="Vorrat", sort_order=2
        )
        tomato, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Tomate", category=produce
        )
        pasta, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Pasta", category=pantry
        )
        InventoryItem.objects.get_or_create(
            household=household, ingredient=tomato, defaults={"status": "in_stock"}
        )
        create_or_update_recipe(
            user=membership.user,
            data={
                "title": "Pasta al Pomodoro",
                "servings": 2,
                "ingredients": [
                    {
                        "sourceText": "Tomaten",
                        "amount": "400",
                        "unit": "g",
                        "canonicalIngredientId": str(tomato.id),
                    },
                    {
                        "sourceText": "Pasta",
                        "amount": "250",
                        "unit": "g",
                        "canonicalIngredientId": str(pasta.id),
                    },
                ],
                "steps": [
                    {"body": "Pasta in Salzwasser kochen."},
                    {"body": "Tomaten köcheln lassen und vermengen."},
                ],
                "tags": ["schnell", "italienisch"],
            },
        )
        self.stdout.write(self.style.SUCCESS("German demo data is ready."))
