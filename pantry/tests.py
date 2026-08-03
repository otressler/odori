import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from planning.models import MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan, week_start_for
from recipes.models import Recipe, RecipeIngredient, RecipeSource

from .jobs import run_next_category_job
from .models import (
    CanonicalIngredient,
    IngredientCategory,
    IngredientCategoryExample,
    InventoryEvent,
    InventoryItem,
    PantryCategorizationJob,
)
from .semantic import query_embedding
from .services import classify_category, similar_ingredient_recommendations


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

    def test_inventory_batch_rolls_back_changes_when_a_later_item_is_stale(self):
        other = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        other_item = InventoryItem.objects.create(
            household=self.household,
            ingredient=other,
            status=InventoryItem.Status.UNKNOWN,
            version=2,
        )

        response = self.patch_inventory(
            {
                "items": [
                    {
                        "ingredientId": str(self.ingredient.id),
                        "status": InventoryItem.Status.IN_STOCK,
                        "version": 1,
                    },
                    {
                        "ingredientId": str(other.id),
                        "status": InventoryItem.Status.IN_STOCK,
                        "version": 1,
                    },
                ]
            }
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(InventoryItem.objects.filter(ingredient=self.ingredient).exists())
        other_item.refresh_from_db()
        self.assertEqual(other_item.status, InventoryItem.Status.UNKNOWN)

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

    @patch("pantry.semantic.embed", return_value=[1.0, 0.0])
    def test_query_embeddings_are_cached(self, embed):
        query = "unique cached query"
        cache.clear()

        self.assertEqual(query_embedding(query), [1.0, 0.0])
        self.assertEqual(query_embedding(query.upper()), [1.0, 0.0])

        embed.assert_called_once_with(query)

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
        self.assertTrue(result["categories"][0]["exactMatch"])
        self.assertEqual(result["state"], "assigned")

    def test_ingredient_patch_rejects_invalid_field_types_and_oversized_alias_lists(self):
        response = self.client.patch(
            f"/api/v1/ingredients/{self.ingredient.id}",
            json.dumps({"name": 1, "aliases": ["ok"] * 51, "active": "true"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            set(response.json()["error"]["fields"]), {"name", "aliases", "active"}
        )
        self.ingredient.refresh_from_db()
        self.assertEqual(self.ingredient.name, "Tomate")

    def test_ingredient_creation_rejects_invalid_aliases(self):
        response = self.client.post(
            "/api/v1/ingredients",
            json.dumps({"name": "Basilikum", "aliases": [1]}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("aliases", response.json()["error"]["fields"])
        self.assertFalse(
            CanonicalIngredient.objects.filter(household=self.household, name="Basilikum").exists()
        )

    def test_ingredient_patch_normalizes_valid_text_fields(self):
        response = self.client.patch(
            f"/api/v1/ingredients/{self.ingredient.id}",
            json.dumps({"name": " Tomaten ", "aliases": [" Paradeiser "], "active": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.ingredient.refresh_from_db()
        self.assertEqual(self.ingredient.name, "Tomaten")
        self.assertEqual(self.ingredient.aliases, [" Paradeiser "])
        self.assertFalse(self.ingredient.active)

    def test_pantry_mutation_pages_reject_get_requests(self):
        paths = [
            "/pantry/add/",
            "/pantry/categories/suggest/",
            f"/pantry/categories/review/{self.ingredient.id}/assign/",
            "/pantry/condense/confirm/",
            f"/pantry/{self.ingredient.id}/status/",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_category_scores_use_persisted_embedding_models(self):
        category = IngredientCategory.objects.create(
            household=self.household,
            name="Trockenwaren",
            sort_order=50,
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=category,
            text="Testnudeln",
            normalized_text="testnudeln",
            source=IngredientCategoryExample.Source.OWNER,
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
        self.assertEqual(score["bestExample"], "Testnudeln")
        self.assertTrue(score["embeddingUsed"])

    def test_explicit_category_on_new_ingredient_becomes_a_confirmed_example(self):
        category = IngredientCategory.objects.create(
            household=self.household, name="Trockenwaren"
        )

        response = self.client.post(
            "/api/v1/ingredients",
            {"name": "Hauspasta", "categoryId": str(category.id)},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            IngredientCategoryExample.objects.filter(
                category=category,
                normalized_text="hauspasta",
                source=IngredientCategoryExample.Source.CONFIRMED,
                active=True,
            ).exists()
        )

    def test_category_admin_requires_login(self):
        response = Client().get("/admin/categories")

        self.assertRedirects(response, "/accounts/login/?next=/admin/categories")

    def test_category_admin_syncs_starter_catalog(self):
        response = self.client.get("/admin/categories")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            IngredientCategoryExample.objects.filter(
                household=self.household,
                category__name="Trockenwaren",
                normalized_text="spaghetti",
                source=IngredientCategoryExample.Source.STARTER,
                active=True,
            ).exists()
        )

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
        self.assertEqual(category.embedding, [1.0, 0.0])
        job = PantryCategorizationJob.objects.get(household=self.household)
        self.assertEqual(job.state, PantryCategorizationJob.State.QUEUED)

    def test_category_admin_ranks_similarity_results_and_is_household_scoped(self):
        first = IngredientCategory.objects.create(household=self.household, name="Trockenwaren")
        second = IngredientCategory.objects.create(household=self.household, name="Obst & Gemüse")
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=first,
            text="Testnudeln",
            normalized_text="testnudeln",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[1.0, 0.0],
            embedding_model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=second,
            text="Testobst",
            normalized_text="testobst",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[0.0, 1.0],
            embedding_model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        )
        other_household = Household.objects.create(name="Other")
        hidden_category = IngredientCategory.objects.create(
            household=other_household, name="Geheim"
        )
        IngredientCategoryExample.objects.create(
            household=other_household,
            category=hidden_category,
            text="Geheimzutat",
            normalized_text="geheimzutat",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[1.0, 0.0],
            embedding_model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        )

        from .semantic import EmbeddingResult

        with patch(
            "pantry.views.embed_with_diagnostics",
            return_value=EmbeddingResult(vector=[1.0, 0.0], state="succeeded"),
        ):
            response = self.client.post(
                "/admin/categories", {"action": "test", "ingredient": "Unbekanntes Testprodukt"}
            )

        self.assertEqual(response.status_code, 200)
        results = response.context["similarity_results"]
        self.assertEqual([result["category"] for result in results[:2]], [first, second])
        self.assertEqual(results[0]["similarity"], 1.0)
        self.assertEqual(results[1]["similarity"], 0.0)
        self.assertTrue(
            all(result["category"].household_id == self.household.id for result in results)
        )
        self.assertNotIn(hidden_category, [result["category"] for result in results])

    def test_category_admin_lists_assignments_and_can_correct_them(self):
        first = IngredientCategory.objects.create(household=self.household, name="Bäckerei")
        second = IngredientCategory.objects.create(
            household=self.household, name="Obst & Gemüse"
        )
        self.ingredient.category = first
        self.ingredient.save(update_fields=["category"])
        learned = IngredientCategoryExample.objects.create(
            household=self.household,
            category=first,
            text=self.ingredient.name,
            normalized_text="tomate",
            source="assigned",
            source_key=f"ingredient:{self.ingredient.id}",
            active=True,
        )

        page = self.client.get("/admin/categories")

        self.assertContains(page, "Zugeordnete Zutaten")
        self.assertContains(page, "Tomate")
        self.assertEqual(page.context["assignment_count"], 1)

        response = self.client.post(
            "/admin/categories",
            {
                "action": "reassign",
                "ingredient_id": self.ingredient.id,
                "category_id": second.id,
            },
        )

        self.assertRedirects(response, "/admin/categories")
        self.ingredient.refresh_from_db()
        learned.refresh_from_db()
        self.assertEqual(self.ingredient.category, second)
        self.assertFalse(learned.active)
        self.assertTrue(
            IngredientCategoryExample.objects.filter(
                category=second,
                normalized_text="tomate",
                source=IngredientCategoryExample.Source.CONFIRMED,
                active=True,
            ).exists()
        )

    def test_category_admin_can_add_and_disable_training_examples(self):
        category = IngredientCategory.objects.create(
            household=self.household, name="Trockenwaren"
        )

        response = self.client.post(
            "/admin/categories",
            {
                "action": "add-example",
                "category_id": category.id,
                "text": "Familiennudeln",
            },
        )

        self.assertRedirects(response, "/admin/categories")
        example = IngredientCategoryExample.objects.get(
            category=category, normalized_text="familiennudeln"
        )
        self.assertEqual(example.source, IngredientCategoryExample.Source.OWNER)
        self.assertTrue(example.active)

        response = self.client.post(
            "/admin/categories",
            {"action": "toggle-example", "example_id": example.id},
        )

        self.assertRedirects(response, "/admin/categories")
        example.refresh_from_db()
        self.assertFalse(example.active)

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

    def test_inventory_page_filters_by_query(self):
        matching = InventoryItem.objects.create(
            household=self.household,
            ingredient=self.ingredient,
        )
        other = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        InventoryItem.objects.create(household=self.household, ingredient=other)

        response = self.client.get("/pantry/", {"q": "tom"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["items"]), [matching])

    @override_settings(DEBUG=False)
    def test_ingredient_icon_is_served_only_to_its_household(self):
        self.ingredient.icon.name = "pantry-icons/tomate.png"
        self.ingredient.save(update_fields=["icon"])
        with patch(
            "django.db.models.fields.files.FieldFile.open",
            return_value=BytesIO(b"icon"),
        ):
            response = self.client.get(f"/pantry/icons/{self.ingredient.id}/")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), b"icon")

            other_user = User.objects.create_user(username="other", password="pass")
            other_household = Household.objects.create(name="Other")
            HouseholdMembership.objects.create(
                household=other_household, user=other_user, role="owner"
            )
            self.client.force_login(other_user)
            response = self.client.get(f"/pantry/icons/{self.ingredient.id}/")

        self.assertEqual(response.status_code, 404)

    def test_condense_shows_similar_ingredient_recommendation_without_merging(self):
        plural = CanonicalIngredient.objects.create(household=self.household, name="Tomaten")

        response = self.client.get("/pantry/condense/")

        self.assertContains(response, "Tomaten")
        self.assertContains(response, "Tomate")
        plural.refresh_from_db()
        self.assertTrue(plural.active)

    @patch("pantry.semantic.embed")
    def test_condense_uses_persisted_embeddings_without_provider_calls(self, embed):
        self.ingredient.embedding = [1.0, 0.0]
        self.ingredient.save(update_fields=["embedding"])
        plural = CanonicalIngredient.objects.create(
            household=self.household,
            name="Tomaten",
            embedding=[0.99, 0.01],
        )

        recommendations = similar_ingredient_recommendations(user=self.user)

        self.assertEqual(recommendations[0]["source"], plural)
        self.assertEqual(recommendations[0]["target"], self.ingredient)
        self.assertTrue(recommendations[0]["semantic"])
        embed.assert_not_called()

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

        self.assertRedirects(response, "/pantry/", fetch_redirect_response=False)
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

    @override_settings(AZURE_OPENAI_EMBEDDING_DEPLOYMENT="test-embedding")
    def test_automatic_category_assignment_becomes_a_curatable_example(self):
        category = IngredientCategory.objects.create(
            household=self.household,
            name="Fermentiertes",
            minimum_similarity=0.8,
            minimum_margin=0.1,
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=category,
            text="Kimchi",
            normalized_text="kimchi",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )
        self.ingredient.name = "Sauerkraut"
        self.ingredient.embedding = [1.0, 0.0]
        self.ingredient.embedding_model = "test-embedding"
        self.ingredient.save(update_fields=["name", "embedding", "embedding_model"])
        categories = [
            IngredientCategory.objects.prefetch_related("examples").get(id=category.id)
        ]

        with patch("pantry.services.ensure_suggested_categories", return_value=categories):
            from .services import categorize_household

            self.assertEqual(categorize_household(household=self.household), 1)

        learned = IngredientCategoryExample.objects.get(
            category=category,
            normalized_text="sauerkraut",
        )
        self.assertEqual(learned.source, "assigned")
        self.assertTrue(learned.active)

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

    def test_category_review_shows_ranked_suggestions_for_uncategorized_items(self):
        bakery = IngredientCategory.objects.create(
            household=self.household, name="Bäckerei", sort_order=10
        )
        produce = IngredientCategory.objects.create(
            household=self.household, name="Obst & Gemüse", sort_order=20
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=bakery,
            text="Brot",
            normalized_text="brot",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=produce,
            text="Apfel",
            normalized_text="apfel",
            source=IngredientCategoryExample.Source.OWNER,
            embedding=[0.0, 1.0],
            embedding_model="test-embedding",
        )
        self.ingredient.name = "Fermentino"
        self.ingredient.embedding = [0.9, 0.1]
        self.ingredient.embedding_model = "test-embedding"
        self.ingredient.save(update_fields=["name", "embedding", "embedding_model"])

        response = self.client.get("/pantry/categories/review/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fermentino")
        suggestions = response.context["review_items"][0]["suggestions"]
        self.assertEqual(
            [suggestion["category"] for suggestion in suggestions[:2]],
            [bakery, produce],
        )

    def test_confirming_category_assigns_item_and_teaches_classifier(self):
        category = IngredientCategory.objects.create(
            household=self.household, name="Obst & Gemüse"
        )
        self.ingredient.embedding = [0.8, 0.2]
        self.ingredient.embedding_model = "test-embedding"
        self.ingredient.save(update_fields=["embedding", "embedding_model"])

        response = self.client.post(
            f"/pantry/categories/review/{self.ingredient.id}/assign/",
            {"category_id": category.id},
        )

        self.assertRedirects(response, "/pantry/categories/review/")
        self.ingredient.refresh_from_db()
        self.assertEqual(self.ingredient.category, category)
        example = IngredientCategoryExample.objects.get(
            category=category,
            normalized_text="tomate",
            source=IngredientCategoryExample.Source.CONFIRMED,
        )
        self.assertEqual(example.embedding, [0.8, 0.2])
        self.assertEqual(example.embedding_model, "test-embedding")

        learned = classify_category(
            name="Tomate",
            categories=[
                IngredientCategory.objects.prefetch_related("examples").get(id=category.id)
            ],
        )
        self.assertEqual(learned["state"], "assigned")
        self.assertEqual(learned["category"], category)

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
        today = timezone.localdate()
        within_window_date = today + timedelta(days=6)
        outside_window_date = today + timedelta(days=7)
        within_window_week_start = week_start_for(within_window_date)
        outside_window_week_start = week_start_for(outside_window_date)
        get_or_create_plan(user=self.user, week_start=within_window_week_start)
        get_or_create_plan(user=self.user, week_start=outside_window_week_start)
        add_slot(
            user=self.user,
            week_start=within_window_week_start,
            date=within_window_date,
            slot=MealSlot.Slot.DINNER,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
        )
        add_slot(
            user=self.user,
            week_start=outside_window_week_start,
            date=outside_window_date,
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
