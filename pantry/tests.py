import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from planning.models import MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan
from recipes.models import Recipe, RecipeIngredient, RecipeSource

from .jobs import run_next_category_job
from .models import (
    CanonicalIngredient,
    IngredientCategory,
    InventoryEvent,
    InventoryItem,
    PantryCategorizationJob,
)


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

    def test_category_scores_expose_text_and_embedding_usage(self):
        IngredientCategory.objects.create(
            household=self.household, name="Trockenwaren", sort_order=50
        )

        response = self.client.get("/api/v1/ingredients/category-scores?name=Pasta")

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertFalse(result["embeddingUsed"])
        self.assertEqual(result["ingredient"]["name"], "Pasta")
        self.assertEqual(result["categories"][0]["name"], "Trockenwaren")
        self.assertEqual(result["categories"][0]["embeddingScore"], None)
        self.assertTrue(result["categories"][0]["qualifies"])

    def test_category_scores_use_persisted_embedding_models(self):
        category = IngredientCategory.objects.create(
            household=self.household,
            name="Trockenwaren",
            sort_order=50,
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )
        ingredient = CanonicalIngredient.objects.create(
            household=self.household,
            name="Unbekannt",
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )

        response = self.client.get(
            f"/api/v1/ingredients/category-scores?ingredientId={ingredient.id}"
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertTrue(result["embeddingUsed"])
        score = next(item for item in result["categories"] if item["id"] == str(category.id))
        self.assertEqual(score["embeddingScore"], 1.0)
        self.assertEqual(score["embeddingModel"], "test-embedding")
        self.assertTrue(score["embeddingUsed"])

    def test_category_admin_requires_login(self):
        response = Client().get("/admin/categories")

        self.assertRedirects(response, "/accounts/login/?next=/admin/categories")

    def test_category_admin_saves_descriptions_and_queues_embedding_refresh(self):
        category = IngredientCategory.objects.create(
            household=self.household, name="Trockenwaren", embedding=[1.0, 0.0]
        )

        with patch("pantry.views.embed_with_diagnostics") as embed_mock:
            response = self.client.post(
                "/admin/categories",
                {
                    "action": "save",
                    f"description-{category.id}": "Nudeln, Reis und haltbare Grundnahrungsmittel",
                },
            )

        self.assertRedirects(
            response, "/admin/categories", fetch_redirect_response=False
        )
        embed_mock.assert_not_called()
        category.refresh_from_db()
        self.assertEqual(
            category.description, "Nudeln, Reis und haltbare Grundnahrungsmittel"
        )
        self.assertEqual(category.embedding, [])
        self.assertEqual(category.embedding_model, "")
        job = PantryCategorizationJob.objects.get(household=self.household)
        self.assertEqual(job.state, PantryCategorizationJob.State.QUEUED)

    def test_category_admin_ranks_similarity_results_and_is_household_scoped(self):
        first = IngredientCategory.objects.create(
            household=self.household, name="Trockenwaren", embedding=[1.0, 0.0]
        )
        second = IngredientCategory.objects.create(
            household=self.household, name="Obst & Gemüse", embedding=[0.0, 1.0]
        )
        other_household = Household.objects.create(name="Other")
        IngredientCategory.objects.create(
            household=other_household, name="Geheim", embedding=[1.0, 0.0]
        )

        from .semantic import EmbeddingResult

        with patch(
            "pantry.views.embed_with_diagnostics",
            return_value=EmbeddingResult(vector=[1.0, 0.0], state="succeeded"),
        ):
            response = self.client.post(
                "/admin/categories", {"action": "test", "ingredient": "Spaghetti"}
            )

        self.assertEqual(response.status_code, 200)
        results = response.context["similarity_results"]
        self.assertEqual([result["category"] for result in results], [first, second])
        self.assertEqual(results[0]["similarity"], 1.0)
        self.assertEqual(results[1]["similarity"], 0.0)

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
        job = PantryCategorizationJob.objects.get(household=self.household)
        self.assertEqual(job.state, PantryCategorizationJob.State.QUEUED)

        self.assertTrue(run_next_category_job())
        self.assertTrue(
            IngredientCategory.objects.filter(
                household=self.household, name="Obst & Gemüse"
            ).exists()
        )
        bread.refresh_from_db()
        self.assertEqual(bread.category.name, "Bäckerei")

    def test_category_suggestions_does_not_queue_duplicate_active_jobs(self):
        first = self.client.post("/pantry/categories/suggest/")
        second = self.client.post("/pantry/categories/suggest/")

        self.assertRedirects(first, "/pantry/")
        self.assertRedirects(second, "/pantry/")
        self.assertEqual(
            PantryCategorizationJob.objects.filter(
                household=self.household,
                state=PantryCategorizationJob.State.QUEUED,
            ).count(),
            1,
        )
        self.assertContains(self.client.get("/pantry/"), "Warengruppen warten")

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

    def test_inventory_next_week_filter_excludes_requirements_from_day_eight(self):
        source = RecipeSource.objects.create(household=self.household)
        recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Tomatensuppe",
            status=Recipe.Status.APPROVED,
        )
        late_recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Basilikum-Pasta",
            status=Recipe.Status.APPROVED,
        )
        basil = CanonicalIngredient.objects.create(
            household=self.household, name="Basilikum"
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            canonical_ingredient=self.ingredient,
            source_text="Tomate",
            sort_order=0,
        )
        RecipeIngredient.objects.create(
            recipe=late_recipe,
            canonical_ingredient=basil,
            source_text="Basilikum",
            sort_order=1,
        )
        week_start = current_week_start()
        get_or_create_plan(user=self.user, week_start=week_start)
        add_slot(
            user=self.user,
            week_start=week_start,
            date=timezone.localdate() + timedelta(days=6),
            slot=MealSlot.Slot.DINNER,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
        )
        add_slot(
            user=self.user,
            week_start=week_start + timedelta(days=7),
            date=timezone.localdate() + timedelta(days=7),
            slot=MealSlot.Slot.DINNER,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=late_recipe.id,
        )
        InventoryItem.objects.create(household=self.household, ingredient=self.ingredient)
        InventoryItem.objects.create(household=self.household, ingredient=basil)

        response = self.client.get("/pantry/?filter=next_week")

        self.assertEqual(
            [item.ingredient.name for item in response.context["items"]],
            ["Tomate"],
        )
