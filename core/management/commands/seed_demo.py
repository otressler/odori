from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from core.models import HouseholdMembership
from pantry.models import (
    CanonicalIngredient,
    IngredientCategory,
    IngredientCategoryExample,
    InventoryEvent,
    InventoryItem,
)
from planning.models import CookEvent, MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan
from recipes.models import Recipe, RecipeFavorite
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
            household=household, name="Gemüse", defaults={"sort_order": 1}
        )
        pantry, _ = IngredientCategory.objects.get_or_create(
            household=household, name="Vorrat", defaults={"sort_order": 2}
        )
        dairy, _ = IngredientCategory.objects.get_or_create(
            household=household, name="Kühlung", defaults={"sort_order": 3}
        )
        for category, examples in (
            (produce, ("Tomaten", "Salat", "Kohl")),
            (pantry, ("Nudeln", "Bohnen", "Öl")),
            (dairy, ("Milch", "Joghurt", "Käse")),
        ):
            for text in examples:
                IngredientCategoryExample.objects.get_or_create(
                    category=category,
                    normalized_text=text.casefold(),
                    defaults={
                        "household": household,
                        "text": text,
                        "source": IngredientCategoryExample.Source.STARTER,
                        "source_key": f"demo-{category.id}-{text.casefold()}",
                    },
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
        onion, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Zwiebel", category=produce
        )
        yogurt, _ = CanonicalIngredient.objects.get_or_create(
            household=household, name="Joghurt", category=dairy
        )

        # A mix of states so the pantry screen shows all three at once.
        for ingredient, status in (
            (tomato, "available"),
            (oil, "available"),
            (pasta, "unavailable"),
            (beans, "unknown"),
            (kale, "available"),
            (onion, "unknown"),
            (yogurt, "unavailable"),
        ):
            item, created = InventoryItem.objects.get_or_create(
                household=household,
                ingredient=ingredient,
                defaults={"status": status},
            )
            if not created and item.status != status:
                previous_status = item.status
                item.status = status
                item.save(update_fields=["status", "updated_at"])
                InventoryEvent.objects.get_or_create(
                    item=item,
                    previous_status=previous_status,
                    new_status=status,
                    actor=user,
                    origin=InventoryEvent.Origin.MANUAL,
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
        RecipeFavorite.objects.get_or_create(recipe=ribollita, user=user)

        draft = create_or_update_recipe(
            user=user,
            recipe=existing_recipe(household, "Idee für Restetag"),
            data={
                "title": "Idee für Restetag",
                "description": "Ein bewusst unvollständiger Entwurf zum Ausprobieren.",
                "servings": 2,
                "ingredients": [{"sourceText": "Gemüse nach Wahl", "optional": True}],
                "steps": [{"body": "Reste prüfen und eine Kombination auswählen."}],
                "tags": ["entwurf"],
            },
        )
        if draft.status == Recipe.Status.ARCHIVED:
            draft.status = Recipe.Status.DRAFT
            draft.save(update_fields=["status", "updated_at"])

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

        cooked_slot = plan.slots.filter(recipe=sugo).order_by("date").first()
        if cooked_slot and not cooked_slot.cooked_at:
            cooked_slot.cooked_at = cooked_slot.created_at
            cooked_slot.save(update_fields=["cooked_at"])
            CookEvent.objects.get_or_create(
                meal_slot=cooked_slot,
                defaults={"household": household, "recipe": sugo, "actor": user},
            )
        generate_from_plan(user=user, week_start=week_start)
        self.stdout.write(self.style.SUCCESS("German demo data is ready."))
