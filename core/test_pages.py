"""Every redesigned page must render, both empty and with real data in place."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryItem
from planning.models import MealSlot
from planning.services import add_slot, current_week_start
from recipes.models import Recipe, RecipeIngredient, RecipeSource, RecipeStep
from shopping.models import ShoppingItem, ShoppingList
from shopping.services import add_manual_item, generate_from_plan


class PageRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nonna", password="pass")
        self.household = Household.objects.create(name="Casa Odori")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status="needs_replenishment"
        )
        source = RecipeSource.objects.create(household=self.household)
        self.recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Ribollita",
            servings=4,
            status=Recipe.Status.APPROVED,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            sort_order=0,
            source_text="Tomaten",
            amount=400,
            unit="g",
            canonical_ingredient=self.tomato,
        )
        RecipeStep.objects.create(recipe=self.recipe, sort_order=0, body="Langsam ziehen lassen.")
        self.draft = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Entwurf",
            status=Recipe.Status.DRAFT,
        )
        self.client.force_login(self.user)

    def paths(self):
        week = current_week_start().isoformat()
        return [
            "/",
            "/recipes/",
            "/recipes/new/",
            f"/recipes/{self.recipe.id}/",
            f"/recipes/{self.draft.id}/edit/",
            "/pantry/",
            "/plan/",
            f"/plan/{week}/",
            "/plan/history/",
            "/shopping/",
        ]

    def test_pages_render_when_the_household_is_empty(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_pages_render_with_a_plan_and_a_shopping_list(self):
        week_start = current_week_start()
        slot = add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=1),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
            servings=6,
        )
        add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=2),
            slot="lunch",
            entry_type=MealSlot.EntryType.NOTE,
            notes="Auswärts essen",
        )
        shopping_list = generate_from_plan(user=self.user, week_start=week_start)
        add_manual_item(user=self.user, list_id=shopping_list.id, label="Spülmittel")

        extra = [f"/plan/slots/{slot.id}/kitchen/", f"/shopping/{shopping_list.id}/"]
        for path in self.paths() + extra:
            with self.subTest(path=path):
                # /shopping/ deliberately redirects to the active list.
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200, path)

    def test_the_home_page_counts_open_shopping_items(self):
        week_start = current_week_start()
        add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=1),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        generate_from_plan(user=self.user, week_start=week_start)
        response = self.client.get("/")
        self.assertContains(response, "Offen auf der Liste")
        self.assertEqual(response.context["open_items"], 1)

    def test_home_page_reminds_about_open_item_needed_today(self):
        week_start = current_week_start()
        add_slot(
            user=self.user,
            week_start=week_start,
            date=timezone.localdate(),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        generate_from_plan(user=self.user, week_start=week_start)

        response = self.client.get("/")

        self.assertContains(response, "Bald benötigt")
        self.assertContains(response, "Heute")
        self.assertContains(response, "Tomate")

    def test_home_page_reminds_about_open_item_needed_tomorrow(self):
        shopping_list = ShoppingList.objects.create(household=self.household, name="Erledigungen")
        ShoppingItem.objects.create(
            shopping_list=shopping_list,
            canonical_ingredient=self.tomato,
            label="Tomate",
            grouping_key="ingredient:tomate",
            recipe_refs=[
                {
                    "title": "Ribollita",
                    "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                }
            ],
        )

        response = self.client.get("/")

        self.assertContains(response, "Bald benötigt")
        self.assertContains(response, "Morgen")
        self.assertContains(response, "Ribollita")

    def test_todays_meal_shows_a_cook_button(self):
        add_slot(
            user=self.user,
            week_start=current_week_start(),
            date=timezone.localdate(),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        self.assertContains(self.client.get("/"), "Kochen")

    def test_sign_in_page_renders_for_anonymous_visitors(self):
        self.client.logout()
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Willkommen zurück")

    def test_signing_in_lands_on_the_home_page(self):
        self.client.logout()
        response = self.client.post("/accounts/login/", {"username": "nonna", "password": "pass"})
        self.assertRedirects(response, "/")
