import json
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from planning.models import MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan
from recipes.models import Recipe, RecipeIngredient, RecipeSource

from .models import CanonicalIngredient, IngredientCategory, InventoryEvent, InventoryItem


class PantryApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="pass")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.ingredient = CanonicalIngredient.objects.create(
            household=self.household, name="Tomate"
        )
        self.client.force_login(self.user)

    def patch_inventory(self, body):
        return self.client.patch(
            "/api/v1/inventory", json.dumps(body), content_type="application/json"
        )

    def test_transition_creates_exactly_one_event(self):
        response = self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "in_stock", "version": 1}
                ]
            }
        )
        self.assertEqual(response.status_code, 200)
        item = InventoryItem.objects.get(ingredient=self.ingredient)
        self.assertEqual(item.status, "in_stock")
        self.assertEqual(InventoryEvent.objects.filter(item=item).count(), 1)

    def test_stale_inventory_version_conflicts(self):
        self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "in_stock", "version": 1}
                ]
            }
        )
        response = self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "unknown", "version": 1}
                ]
            }
        )
        self.assertEqual(response.status_code, 409)

    def test_other_household_ingredient_is_hidden(self):
        other_household = Household.objects.create(name="Other")
        other = CanonicalIngredient.objects.create(household=other_household, name="Basilikum")
        response = self.patch_inventory(
            {"items": [{"ingredientId": str(other.id), "status": "in_stock", "version": 1}]}
        )
        self.assertEqual(response.status_code, 404)

    def test_ingredient_search_fuzzily_matches_rispentomaten_to_tomate(self):
        response = self.client.get("/api/v1/ingredients?q=Rispentomaten")

        self.assertEqual(response.status_code, 200)
        result = response.json()["ingredients"]
        self.assertEqual(result[0]["id"], str(self.ingredient.id))
        self.assertGreaterEqual(result[0]["matchScore"], 0.84)

    def test_inventory_page_creates_an_ingredient_and_initial_status(self):
        response = self.client.post(
            "/pantry/add/", {"name": "Basilikum", "status": InventoryItem.Status.IN_STOCK}
        )

        self.assertRedirects(response, "/pantry/")
        item = InventoryItem.objects.get(ingredient__name="Basilikum")
        self.assertEqual(item.status, InventoryItem.Status.IN_STOCK)

    def test_inventory_page_updates_status_with_version(self):
        item = InventoryItem.objects.create(
            household=self.household,
            ingredient=self.ingredient,
            status=InventoryItem.Status.UNKNOWN,
        )
        response = self.client.post(
            f"/pantry/{self.ingredient.id}/status/",
            {"status": InventoryItem.Status.NEEDS_REPLENISHMENT, "version": item.version},
        )

        self.assertRedirects(response, "/pantry/")
        item.refresh_from_db()
        self.assertEqual(item.status, InventoryItem.Status.NEEDS_REPLENISHMENT)

    def test_condense_shows_similar_ingredient_recommendation_without_merging(self):
        plural = CanonicalIngredient.objects.create(household=self.household, name="Tomaten")

        response = self.client.get("/pantry/condense/")

        self.assertContains(response, "Tomaten")
        self.assertContains(response, "Tomate")
        plural.refresh_from_db()
        self.assertTrue(plural.active)

    def test_condense_merges_only_after_user_confirmation(self):
        plural = CanonicalIngredient.objects.create(household=self.household, name="Tomaten")

        response = self.client.post(
            "/pantry/condense/confirm/",
            {"source_id": plural.id, "target_id": self.ingredient.id},
        )

        self.assertRedirects(response, "/pantry/condense/")
        plural.refresh_from_db()
        self.assertFalse(plural.active)
        self.assertEqual(plural.merged_into_id, self.ingredient.id)

    def test_category_suggestions_seed_store_groups_and_assign_keyword_match(self):
        bread = CanonicalIngredient.objects.create(household=self.household, name="Brot")

        response = self.client.post("/pantry/categories/suggest/")

        self.assertRedirects(response, "/pantry/")
        self.assertTrue(
            IngredientCategory.objects.filter(
                household=self.household, name="Obst & Gemüse"
            ).exists()
        )
        bread.refresh_from_db()
        self.assertEqual(bread.category.name, "Bäckerei")

    def test_inventory_shows_earliest_upcoming_recipe_requirement(self):
        source = RecipeSource.objects.create(household=self.household)
        recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Tomatensuppe",
            status=Recipe.Status.APPROVED,
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            canonical_ingredient=self.ingredient,
            source_text="Tomate",
            sort_order=0,
        )
        week_start = current_week_start()
        get_or_create_plan(user=self.user, week_start=week_start)
        add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=timezone.localdate().weekday()),
            slot=MealSlot.Slot.DINNER,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
        )
        InventoryItem.objects.create(household=self.household, ingredient=self.ingredient)

        response = self.client.get("/pantry/")

        self.assertContains(response, "Spätestens")
        self.assertContains(response, "Tomatensuppe")
