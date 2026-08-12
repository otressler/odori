import json
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import Http404
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient

from .images import recipe_image_prompt
from .models import Recipe, RecipeImageJob, RecipeIngredient, RecipeSource
from .services import create_or_update_recipe, create_recipe_revision


class RecipeLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="pass")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.ingredient = CanonicalIngredient.objects.create(household=self.household, name="Pasta")
        self.client.force_login(self.user)

    def create_recipe(self, **overrides):
        payload = {
            "title": "Pasta al Pomodoro",
            "servings": 2,
            "ingredients": [
                {
                    "sourceText": "Pasta",
                    "amount": "200",
                    "unit": "g",
                    "canonicalIngredientId": str(self.ingredient.id),
                }
            ],
            "steps": [{"body": "Kochen."}],
            "tags": ["schnell"],
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/recipes", json.dumps(payload), content_type="application/json"
        )

    def test_valid_draft_can_be_approved_and_scaled_without_source_mutation(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]
        response = self.client.post(f"/api/v1/recipes/{recipe_id}/approve")
        self.assertEqual(response.status_code, 200)
        recipe = Recipe.objects.get(id=recipe_id)
        self.assertEqual(recipe.status, Recipe.Status.APPROVED)
        scaled = self.client.get(f"/api/v1/recipes/{recipe_id}?servings=3").json()
        self.assertEqual(scaled["ingredients"][0]["amount"], "300.00")
        self.assertEqual(str(recipe.ingredients.first().amount), "200.00")

    def test_draft_without_instructions_cannot_be_approved(self):
        response = self.create_recipe(steps=[])
        response = self.client.post(f"/api/v1/recipes/{response.json()['id']}/approve")
        self.assertEqual(response.status_code, 422)

    def test_approved_recipe_cannot_be_updated_in_place(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]
        self.client.post(f"/api/v1/recipes/{recipe_id}/approve")
        response = self.client.patch(
            f"/api/v1/recipes/{recipe_id}",
            json.dumps({"version": 2, "title": "Changed"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "approved_recipe_immutable")
        self.assertEqual(Recipe.objects.get(id=recipe_id).title, "Pasta al Pomodoro")

    def test_approved_recipe_revision_preserves_recipe_content(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        self.client.post(f"/api/v1/recipes/{recipe.id}/approve")
        recipe.refresh_from_db()

        revision = create_recipe_revision(recipe, self.user)

        self.assertEqual(revision.status, Recipe.Status.DRAFT)
        self.assertEqual(revision.title, recipe.title)
        self.assertEqual(revision.ingredients.count(), recipe.ingredients.count())
        self.assertEqual(revision.steps.count(), recipe.steps.count())
        self.assertEqual(
            list(revision.tag_assignments.values_list("tag__name", flat=True)), ["schnell"]
        )

    def test_recipe_pages_expose_curation_actions(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]

        response = self.client.get("/recipes/?q=Pomodoro")
        self.assertContains(response, "Rezepte suchen")
        self.assertContains(response, "Pasta al Pomodoro")
        response = self.client.get(f"/recipes/{recipe_id}/")
        self.assertContains(response, "Entwurf bearbeiten")
        self.assertContains(response, "Veröffentlichen")
        self.assertContains(response, "Favorisieren")
        self.assertContains(response, "Archivieren")

    def test_recipe_detail_shows_a_ready_recipe_image(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        recipe.image = "recipes/pasta.png"
        recipe.image_status = "ready"
        recipe.save(update_fields=["image", "image_status"])

        response = self.client.get(f"/recipes/{recipe.id}/")

        self.assertContains(response, reverse("recipe-image", args=[recipe.id]))

    def test_recipe_catalogue_separates_statuses_and_searches_ingredients_and_tags(self):
        response = self.create_recipe(
            title="Sommertopf",
            ingredients=[{"sourceText": "Basilikum"}],
            tags=["feierabend"],
        )
        recipe_id = response.json()["id"]
        self.client.post(f"/api/v1/recipes/{recipe_id}/approve")
        self.create_recipe(title="Unfertig", tags=["notiz"])

        ingredient_search = self.client.get("/recipes/?q=Basilikum")
        tag_search = self.client.get("/api/v1/recipes?q=feierabend")
        catalogue = self.client.get("/recipes/")

        self.assertContains(ingredient_search, "Sommertopf")
        self.assertEqual(
            [recipe["title"] for recipe in tag_search.json()["recipes"]], ["Sommertopf"]
        )
        self.assertContains(catalogue, "Veröffentlicht")
        self.assertContains(catalogue, "Entwürfe")

    def test_recipe_creation_queues_a_foundry_image_with_the_app_visual_style(self):
        response = self.create_recipe(
            title="Pasta al Limone",
            ingredients=[{"sourceText": "Zitrone"}],
            tags=["sommer"],
        )
        recipe = Recipe.objects.get(id=response.json()["id"])
        job = RecipeImageJob.objects.get(recipe=recipe)

        self.assertEqual(recipe.image_status, "pending")
        self.assertEqual(job.state, RecipeImageJob.State.QUEUED)
        self.assertIn("Zitrone", job.prompt)
        self.assertIn("terracotta, olive and saffron", job.prompt)

    def test_recipe_image_can_be_regenerated_from_the_detail_page(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        original_job = RecipeImageJob.objects.get(recipe=recipe)

        response = self.client.post(f"/recipes/{recipe.id}/image/regenerate/")

        self.assertRedirects(response, f"/recipes/{recipe.id}/")
        original_job.refresh_from_db()
        self.assertEqual(original_job.state, RecipeImageJob.State.SUPERSEDED)
        self.assertEqual(
            RecipeImageJob.objects.filter(recipe=recipe, state=RecipeImageJob.State.QUEUED).count(),
            1,
        )

    def test_recipe_save_only_queues_image_when_its_computed_prompt_changes(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        original_job = RecipeImageJob.objects.get(recipe=recipe)
        recipe.image = "recipes/existing.png"
        recipe.image_status = "ready"
        recipe.image_prompt = recipe_image_prompt(recipe)
        recipe.save(update_fields=["image", "image_status", "image_prompt"])

        create_or_update_recipe(user=self.user, recipe=recipe, data={"servings": 4})

        self.assertEqual(RecipeImageJob.objects.filter(recipe=recipe).count(), 1)
        original_job.refresh_from_db()
        self.assertEqual(original_job.state, RecipeImageJob.State.QUEUED)

        create_or_update_recipe(user=self.user, recipe=recipe, data={"title": "Pasta neu"})

        original_job.refresh_from_db()
        self.assertEqual(original_job.state, RecipeImageJob.State.SUPERSEDED)
        self.assertEqual(
            RecipeImageJob.objects.filter(recipe=recipe, state=RecipeImageJob.State.QUEUED).count(),
            1,
        )

    def test_embedding_work_happens_outside_recipe_write_transaction(self):
        transaction_states = []
        outer_savepoint_ids = list(connection.savepoint_ids)

        def record_best_match(*args):
            transaction_states.append(list(connection.savepoint_ids))
            return None

        def record_search_embedding(*args):
            transaction_states.append(list(connection.savepoint_ids))
            return False

        with (
            patch("recipes.services.best_match", side_effect=record_best_match),
            patch("recipes.services.update_search_embedding", side_effect=record_search_embedding),
        ):
            create_or_update_recipe(
                user=self.user,
                data={"title": "Suppe", "ingredients": [{"sourceText": "Linsen"}]},
            )

        self.assertEqual(transaction_states, [outer_savepoint_ids, outer_savepoint_ids])

    def test_servings_only_update_does_not_refresh_the_search_embedding(self):
        recipe = Recipe.objects.get(id=self.create_recipe().json()["id"])

        with patch("recipes.services.update_search_embedding") as update_embedding:
            create_or_update_recipe(user=self.user, recipe=recipe, data={"servings": 4})

        update_embedding.assert_not_called()

    def test_recipe_mutation_pages_reject_get_requests(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        line = recipe.ingredients.get()
        paths = [
            f"/recipes/{recipe.id}/approve/",
            f"/recipes/{recipe.id}/archive/",
            f"/recipes/{recipe.id}/favorite/",
            f"/recipes/{recipe.id}/revise/",
            f"/recipes/{recipe.id}/image/regenerate/",
            f"/recipes/{recipe.id}/ingredients/{line.id}/add-to-pantry/",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 405)

    def test_recipe_image_is_served_to_a_household_member(self):
        response = self.create_recipe()
        recipe = Recipe.objects.get(id=response.json()["id"])
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            recipe.image.save("pasta.png", SimpleUploadedFile("pasta.png", b"image-data"))
            response = self.client.get(reverse("recipe-image", args=[recipe.id]))
            image_bytes = b"".join(response.streaming_content)
            response.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(image_bytes, b"image-data")

    def test_recipe_form_accepts_more_than_twelve_ingredients(self):
        form_data = {"title": "Viele Zutaten", "servings": "2", "step-0": "Kochen."}
        for index in range(13):
            form_data[f"ingredient-source-{index}"] = f"Zutat {index + 1}"

        response = self.client.post("/recipes/new/", form_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Recipe.objects.get(title="Viele Zutaten").ingredients.count(), 13)

    def test_recipe_form_uses_semantic_ingredient_search_for_pantry_assignment(self):
        response = self.client.get("/recipes/new/")

        self.assertContains(response, "data-ingredient-search")
        self.assertContains(response, 'name="ingredient-canonical-0"')
        self.assertContains(response, 'type="hidden"')
        self.assertNotContains(response, '<select id="ingredient-canonical-0"')
        self.assertContains(response, 'fetch(searchEndpoint + "?q="')
        self.assertContains(response, "schnell anlegen")
        self.assertContains(response, 'method: "POST"')

    def test_recipe_form_accepts_more_than_twelve_steps(self):
        form_data = {"title": "Viele Schritte", "servings": "2"}
        for index in range(13):
            form_data[f"step-{index}"] = f"Schritt {index + 1}"

        response = self.client.post("/recipes/new/", form_data)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Recipe.objects.get(title="Viele Schritte").steps.count(), 13)

    def test_recipe_form_rejects_more_than_one_hundred_ingredients(self):
        form_data = {"title": "Zu viele Zutaten", "servings": "2", "step-0": "Kochen."}
        for index in range(101):
            form_data[f"ingredient-source-{index}"] = f"Zutat {index + 1}"

        response = self.client.post("/recipes/new/", form_data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.filter(title="Zu viele Zutaten").exists())

    def test_approved_recipe_can_be_revised_from_detail_workflow(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]
        self.client.post(f"/api/v1/recipes/{recipe_id}/approve")

        response = self.client.post(f"/recipes/{recipe_id}/revise/")

        self.assertRedirects(response, "/recipes/" + response.url.split("/")[2] + "/edit/")
        self.assertEqual(Recipe.objects.filter(status=Recipe.Status.DRAFT).count(), 1)

    def test_archived_recipes_are_hidden_from_default_search(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]
        response = self.client.delete(
            f"/api/v1/recipes/{recipe_id}",
            json.dumps({"version": response.json()["version"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 204)
        recipes = self.client.get("/api/v1/recipes").json()["recipes"]
        self.assertEqual(recipes, [])

    def test_recipe_archive_rejects_a_stale_version(self):
        response = self.create_recipe()
        recipe_id = response.json()["id"]

        response = self.client.delete(
            f"/api/v1/recipes/{recipe_id}",
            json.dumps({"version": 1}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(Recipe.objects.get(id=recipe_id).status, Recipe.Status.DRAFT)

    def test_recipe_creation_without_a_household_raises_not_found(self):
        user = User.objects.create_user(username="no-household", password="pass")

        with self.assertRaises(Http404):
            create_or_update_recipe(user=user, data={"title": "Kein Zuhause"})

    def test_recipe_from_another_household_is_hidden(self):
        other_user = User.objects.create_user(username="other", password="pass")
        other_household = Household.objects.create(name="Other")
        HouseholdMembership.objects.create(household=other_household, user=other_user, role="owner")
        source = RecipeSource.objects.create(household=other_household)
        other_recipe = Recipe.objects.create(
            household=other_household,
            title="Private",
            source=source,
            created_by=other_user,
        )
        response = self.client.get(f"/api/v1/recipes/{other_recipe.id}")
        self.assertEqual(response.status_code, 404)

    def test_recipe_auto_matches_a_fuzzy_ingredient_name(self):
        tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")

        response = self.create_recipe(
            ingredients=[{"sourceText": "Rispentomaten", "amount": "4", "unit": "Stück"}]
        )

        line = Recipe.objects.get(id=response.json()["id"]).ingredients.get()
        self.assertEqual(line.canonical_ingredient_id, tomato.id)
        self.assertEqual(line.match_state, RecipeIngredient.MatchState.MATCHED)

    def test_recipe_detail_quick_inserts_unresolved_ingredient_into_pantry(self):
        response = self.create_recipe(ingredients=[{"sourceText": "Basilikum"}])
        recipe = Recipe.objects.get(id=response.json()["id"])
        line = recipe.ingredients.get()

        response = self.client.post(f"/recipes/{recipe.id}/ingredients/{line.id}/add-to-pantry/")

        self.assertRedirects(response, f"/recipes/{recipe.id}/")
        line.refresh_from_db()
        self.assertEqual(line.canonical_ingredient.name, "Basilikum")
