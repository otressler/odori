from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from core.models import HouseholdMembership
from pantry.models import CanonicalIngredient, IngredientCategory, InventoryItem
from planning.models import MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan
from recipes.models import Recipe
from recipes.services import approve_recipe, create_or_update_recipe
from shopping.services import generate_from_plan


def existing_recipe(household, title):
    """Keep repeated seed runs idempotent instead of piling up duplicate drafts."""

    return Recipe.objects.filter(household=household, title=title).order_by("created_at").first()


class Command(BaseCommand):
    help = "Seed a small German-language household dataset for local review."

    def handle(self, *args, **options):
        membership = HouseholdMembership.objects.select_related("household", "user").first()
        if not membership:
            raise CommandError("Run bootstrap_owner first.")
        household = membership.household
        user = membership.user

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
        beans, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Cannellini-Bohnen", category=pantry
        )
        kale, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Schwarzkohl", category=produce
        )
        oil, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Olivenöl", category=pantry
        )

        # A mix of states so the pantry screen shows all three at once.
        for ingredient, status in (
            (tomato, "available"),
            (oil, "available"),
            (pasta, "unavailable"),
            (beans, "unknown"),
        ):
            InventoryItem.objects.get_or_create(
                household=household, ingredient=ingredient, defaults={"status": status}
            )

        sugo = create_or_update_recipe(
            user=user,
            recipe=existing_recipe(household, "Pasta al Pomodoro"),
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
                    {
                        "sourceText": "Olivenöl",
                        "amount": "2",
                        "unit": "EL",
                        "canonicalIngredientId": str(oil.id),
                    },
                ],
                "steps": [
                    {"body": "Pasta in Salzwasser kochen."},
                    {"body": "Tomaten köcheln lassen und vermengen."},
                ],
                "tags": ["schnell", "italienisch"],
            },
        )
        ribollita = create_or_update_recipe(
            user=user,
            recipe=existing_recipe(household, "Ribollita"),
            data={
                "title": "Ribollita",
                "servings": 4,
                "ingredients": [
                    {
                        "sourceText": "Cannellini-Bohnen",
                        "amount": "300",
                        "unit": "g",
                        "canonicalIngredientId": str(beans.id),
                    },
                    {
                        "sourceText": "Schwarzkohl",
                        "amount": "1",
                        "unit": "Bund",
                        "canonicalIngredientId": str(kale.id),
                    },
                    {
                        "sourceText": "Tomaten",
                        "amount": "400",
                        "unit": "g",
                        "canonicalIngredientId": str(tomato.id),
                    },
                    {"sourceText": "Salz"},
                ],
                "steps": [
                    {"body": "Bohnen weich kochen und beiseitestellen."},
                    {"body": "Gemüse in Olivenöl anschwitzen."},
                    {"body": "Alles zusammen langsam ziehen lassen."},
                ],
                "tags": ["toskanisch", "eintopf"],
            },
        )
        for recipe in (sugo, ribollita):
            if recipe.status != Recipe.Status.APPROVED:
                approve_recipe(recipe)

        week_start = current_week_start()
        plan = get_or_create_plan(user=user, week_start=week_start)
        for day_offset, slot, recipe, servings in (
            (0, "dinner", sugo, 2),
            (2, "dinner", ribollita, 4),
            (4, "lunch", sugo, 4),
        ):
            date = week_start + timedelta(days=day_offset)
            if plan.slots.filter(date=date, slot=slot).exists():
                continue
            add_slot(
                user=user,
                week_start=week_start,
                date=date,
                slot=slot,
                entry_type=MealSlot.EntryType.RECIPE,
                recipe_id=recipe.id,
                servings=servings,
            )
        if not plan.slots.filter(entry_type=MealSlot.EntryType.NOTE).exists():
            add_slot(
                user=user,
                week_start=week_start,
                date=week_start + timedelta(days=5),
                slot="dinner",
                entry_type=MealSlot.EntryType.NOTE,
                notes="Auswärts essen",
            )

        generate_from_plan(user=user, week_start=week_start)
        self.stdout.write(self.style.SUCCESS("German demo data is ready."))
