import json
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryItem

from .generation import run_next_recipe_generation_job
from .models import (
    GeneratedRecipeRequest,
    Recipe,
    RecipeIngredient,
    RecipeSource,
    RecipeStep,
    RecommendationOutcome,
)
from .recommendations import recommend_for_user


class RecommendationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="mara")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(
            household=self.household, user=self.user, role=HouseholdMembership.Role.OWNER
        )
        self.client.force_login(self.user)
        self.tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        self.basil = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status=InventoryItem.Status.IN_STOCK
        )
        InventoryItem.objects.create(
            household=self.household, ingredient=self.basil, status=InventoryItem.Status.UNKNOWN
        )

    def recipe(self, title, ingredients):
        source = RecipeSource.objects.create(household=self.household)
        recipe = Recipe.objects.create(
            household=self.household,
            source=source,
            created_by=self.user,
            title=title,
            status=Recipe.Status.APPROVED,
        )
        RecipeStep.objects.create(recipe=recipe, body="Kochen.", sort_order=0)
        for sort_order, ingredient in enumerate(ingredients):
            RecipeIngredient.objects.create(
                recipe=recipe,
                canonical_ingredient=ingredient,
                source_text=ingredient.name,
                sort_order=sort_order,
                match_state=RecipeIngredient.MatchState.MATCHED,
            )
        return recipe

    def test_recommendations_are_stable_and_explain_inventory_coverage(self):
        pasta = self.recipe("Pasta", [self.tomato])
        soup = self.recipe("Suppe", [self.tomato, self.basil])

        first = self.client.get("/api/v1/recommendations")
        second = self.client.get("/api/v1/recommendations")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            [item["recipeId"] for item in first.json()["suggestions"]],
            [item["recipeId"] for item in second.json()["suggestions"]],
        )
        suggestion = first.json()["suggestions"][0]
        self.assertEqual(suggestion["recipeId"], str(pasta.id))
        self.assertEqual(suggestion["matchedIngredients"], ["Tomate"])
        self.assertIn("Vorrat geprüft", " ".join(first.json()["suggestions"][1]["reasons"]))
        self.assertEqual(first.json()["scoringVersion"], "2026-08-1")
        self.assertEqual(
            first.json()["suggestions"][1]["recipeId"],
            str(soup.id),
        )

    def test_recommendations_are_household_scoped_and_query_bounded(self):
        first_recipe = self.recipe("A", [self.tomato])
        for number in range(15):
            self.recipe(f"Rezept {number}", [self.tomato])
        other = Household.objects.create(name="Other")
        other_user = User.objects.create(username="other")
        HouseholdMembership.objects.create(household=other, user=other_user, role="owner")
        other_source = RecipeSource.objects.create(household=other)
        Recipe.objects.create(
            household=other,
            source=other_source,
            created_by=other_user,
            title="Private",
            status=Recipe.Status.APPROVED,
        )

        with CaptureQueriesContext(connection) as queries:
            result = recommend_for_user(user=self.user)

        self.assertLessEqual(len(queries), 12)
        self.assertEqual(result.suggestions[0].recipe.id, first_recipe.id)
        self.assertNotIn("Private", [item.recipe.title for item in result.suggestions])

    def test_outcome_is_scoped_to_the_recommendation_household(self):
        recipe = self.recipe("Pasta", [self.tomato])
        run_id = self.client.get("/api/v1/recommendations").json()["runId"]

        response = self.client.post(
            "/api/v1/recommendation-outcomes",
            json.dumps(
                {
                    "recipeId": str(recipe.id),
                    "runId": run_id,
                    "outcome": RecommendationOutcome.Type.DISMISSED,
                    "reason": RecommendationOutcome.Reason.NOT_RELEVANT,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(RecommendationOutcome.objects.get().recipe, recipe)

    def test_generation_is_disabled_by_default(self):
        response = self.client.post(
            "/api/v1/generated-recipe-drafts",
            json.dumps({"idea": "Pasta mit Tomaten"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "generation_disabled")

    @override_settings(
        RECIPE_GENERATION_ENABLED=True,
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT="test-deployment",
        RECIPE_GENERATION_DAILY_LIMIT=1,
    )
    @patch("recipes.generation.urlopen")
    def test_explicit_generation_is_queued_then_creates_a_reviewable_draft(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Tomatenpasta",
                                    "servings": 2,
                                    "ingredients": [{"sourceText": "Tomate"}],
                                    "steps": ["Kochen."],
                                    "tags": ["schnell"],
                                }
                            )
                        }
                    }
                ]
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response

        result = self.client.post(
            "/api/v1/generated-recipe-drafts",
            json.dumps({"idea": "Tomatenpasta"}),
            content_type="application/json",
        )

        self.assertEqual(result.status_code, 202)
        self.assertEqual(result.json()["state"], GeneratedRecipeRequest.State.QUEUED)
        mocked_urlopen.assert_not_called()

        self.assertTrue(run_next_recipe_generation_job())

        request = GeneratedRecipeRequest.objects.get()
        self.assertEqual(request.state, GeneratedRecipeRequest.State.SUCCEEDED)
        status = self.client.get(result.json()["statusUrl"])
        self.assertEqual(status.status_code, 200)
        recipe = Recipe.objects.get(id=status.json()["recipe"]["id"])
        self.assertEqual(recipe.status, Recipe.Status.DRAFT)
        self.assertEqual(recipe.source.type, RecipeSource.Type.GENERATED)
        self.assertEqual(self.client.post(f"/api/v1/recipes/{recipe.id}/approve").status_code, 200)

    @override_settings(
        RECIPE_GENERATION_ENABLED=True,
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT="test-deployment",
    )
    @patch("recipes.generation.urlopen")
    def test_invalid_generated_output_is_a_safe_async_failure(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = b'{"choices": []}'
        mocked_urlopen.return_value.__enter__.return_value = response

        result = self.client.post(
            "/api/v1/generated-recipe-drafts",
            json.dumps({"idea": "Pasta"}),
            content_type="application/json",
        )

        self.assertEqual(result.status_code, 202)
        self.assertTrue(run_next_recipe_generation_job())

        request = GeneratedRecipeRequest.objects.get()
        self.assertEqual(request.state, GeneratedRecipeRequest.State.FAILED)
        self.assertEqual(request.error_code, "invalid_output")
        status = self.client.get(result.json()["statusUrl"])
        self.assertEqual(status.json()["errorCode"], "invalid_output")
