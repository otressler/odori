import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from core.models import Household, HouseholdMembership, User
from planning.models import MealSlot
from planning.services import add_slot, current_week_start
from providers.foundry_recipe_import import normalize_step_annotations

from .enrichment import queue_step_enrichment, run_next_step_enrichment_job
from .imports import create_import, run_next_import_job
from .models import Recipe, RecipeImportJob, RecipeStep, RecipeStepEnrichmentJob
from .steps import clean_timers, describe_duration, format_duration, parse_duration

FOUNDRY_SETTINGS = {
    "AZURE_OPENAI_ENDPOINT": "https://example.test",
    "AZURE_OPENAI_API_KEY": "test-key",
    "AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT": "recipe-import",
    "AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT": "recipe-generation",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "",
}


def chat_response(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}]}
    ).encode()
    return response


class StepDurationTests(SimpleTestCase):
    def test_durations_accept_minutes_and_clock_notation(self):
        self.assertEqual(parse_duration("10"), 600)
        self.assertEqual(parse_duration("7,5"), 450)
        self.assertEqual(parse_duration("1:30"), 90)
        self.assertEqual(parse_duration("1:05:00"), 3900)

    def test_invalid_durations_are_rejected(self):
        for value in ("", "abc", "0", "1:75", "25:00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_duration(value)

    def test_durations_are_formatted_for_inputs_and_reading(self):
        self.assertEqual(format_duration(90), "1:30")
        self.assertEqual(format_duration(3900), "1:05:00")
        self.assertEqual(describe_duration(5400), "1 Std. 30 Min.")
        self.assertEqual(describe_duration(45), "45 Sek.")

    def test_manual_timers_need_a_label_and_a_bounded_duration(self):
        self.assertEqual(
            clean_timers([{"label": " Nudeln ", "seconds": 600}]),
            [{"label": "Nudeln", "seconds": 600}],
        )
        for timer in ({"label": "", "seconds": 60}, {"label": "Ruhen", "seconds": 0}):
            with self.subTest(timer=timer), self.assertRaises(ValueError):
                clean_timers([timer])

    def test_model_annotations_drop_unusable_values_instead_of_failing(self):
        annotations = normalize_step_annotations(
            {
                "timers": [
                    {"label": "Köcheln", "seconds": "900"},
                    {"label": "", "seconds": 60},
                    {"label": "Zu lang", "seconds": 999_999},
                    "10 Minuten",
                ],
                "ingredientIndexes": [1, 1, 7, -1, True, "0"],
            },
            ingredient_count=2,
        )

        self.assertEqual(annotations["timers"], [{"label": "Köcheln", "seconds": 900}])
        self.assertEqual(annotations["ingredientIndexes"], [1])


class StepAnnotationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="lucia", password="pass")
        self.household = Household.objects.create(name="Lucia")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.client.force_login(self.user)

    def create_recipe(self, **overrides):
        payload = {
            "title": "Pasta al Pomodoro",
            "servings": 2,
            "ingredients": [
                {"sourceText": "Spaghetti", "amount": "200", "unit": "g"},
                {"sourceText": "Tomaten", "amount": "400", "unit": "g"},
                {"sourceText": "Basilikum"},
            ],
            "steps": [
                {
                    "body": "Spaghetti kochen, Tomaten einkochen.",
                    "timers": [
                        {"label": "Spaghetti", "seconds": 540},
                        {"label": "Sugo", "seconds": 900},
                    ],
                    "ingredientIndexes": [0, 1],
                },
                {"body": "Mit Basilikum servieren.", "ingredientIndexes": [2]},
            ],
        }
        payload.update(overrides)
        return self.client.post(
            "/api/v1/recipes", json.dumps(payload), content_type="application/json"
        )

    def test_api_round_trips_step_timers_and_ingredients(self):
        response = self.create_recipe()

        self.assertEqual(response.status_code, 201)
        first, second = response.json()["steps"]
        self.assertEqual(
            first["timers"],
            [{"label": "Spaghetti", "seconds": 540}, {"label": "Sugo", "seconds": 900}],
        )
        self.assertEqual(first["ingredientIndexes"], [0, 1])
        self.assertEqual(second["timers"], [])
        self.assertEqual(second["ingredientIndexes"], [2])
        step = RecipeStep.objects.get(sort_order=0)
        self.assertEqual(
            [line.source_text for line in step.ingredients.all()], ["Spaghetti", "Tomaten"]
        )

    def test_invalid_step_annotations_are_rejected(self):
        response = self.create_recipe(
            steps=[{"body": "Kochen.", "timers": [{"label": "", "seconds": 60}]}]
        )
        self.assertEqual(response.status_code, 422)

        response = self.create_recipe(steps=[{"body": "Kochen.", "ingredientIndexes": [3]}])
        self.assertEqual(response.status_code, 422)
        self.assertFalse(Recipe.objects.exists())

    def test_ingredient_only_update_keeps_step_links_by_position(self):
        recipe = self.create_recipe().json()
        response = self.client.patch(
            f"/api/v1/recipes/{recipe['id']}",
            json.dumps(
                {
                    "version": recipe["version"],
                    "ingredients": [
                        {"sourceText": "Linguine", "amount": "200", "unit": "g"},
                        {"sourceText": "Kirschtomaten", "amount": "400", "unit": "g"},
                        {"sourceText": "Basilikum"},
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        step = RecipeStep.objects.get(sort_order=0)
        self.assertEqual(
            [line.source_text for line in step.ingredients.all()], ["Linguine", "Kirschtomaten"]
        )

    def test_step_only_update_references_existing_ingredients(self):
        recipe = self.create_recipe().json()
        response = self.client.patch(
            f"/api/v1/recipes/{recipe['id']}",
            json.dumps(
                {
                    "version": recipe["version"],
                    "steps": [{"body": "Alles zusammen kochen.", "ingredientIndexes": [2, 0]}],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["steps"][0]["ingredientIndexes"], [0, 2])

    def test_form_saves_timers_and_maps_ingredient_rows_after_removals(self):
        response = self.client.post(
            "/recipes/new/",
            {
                "title": "Risotto",
                "ingredient-source-0": "Reis",
                "ingredient-source-1": "",
                "ingredient-source-3": "Brühe",
                "step-0": "Reis anrösten.",
                "step-ingredients-0": ["0"],
                "step-1": "Brühe angießen und rühren.",
                "step-ingredients-1": ["3", "1", "9"],
                "step-timer-label-1-0": "Risotto",
                "step-timer-duration-1-0": "18",
                "step-timer-label-1-2": "Ruhen",
                "step-timer-duration-1-2": "1:30",
                "step-timer-label-1-3": "",
                "step-timer-duration-1-3": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        first, second = Recipe.objects.get(title="Risotto").steps.all()
        self.assertEqual([line.source_text for line in first.ingredients.all()], ["Reis"])
        self.assertEqual([line.source_text for line in second.ingredients.all()], ["Brühe"])
        self.assertEqual(
            second.timers,
            [{"label": "Risotto", "seconds": 1080}, {"label": "Ruhen", "seconds": 90}],
        )

    def test_form_rejects_a_timer_without_a_duration(self):
        response = self.client.post(
            "/recipes/new/",
            {
                "title": "Risotto",
                "step-0": "Reis anrösten.",
                "step-timer-label-0-0": "Rösten",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Recipe.objects.exists())
        self.assertContains(response, "needs a duration")

    def test_edit_form_and_detail_page_show_step_annotations(self):
        recipe_id = self.create_recipe().json()["id"]

        edit = self.client.get(f"/recipes/{recipe_id}/edit/")
        self.assertContains(edit, 'name="step-timer-label-0-1"')
        self.assertContains(edit, 'value="Sugo"')
        self.assertContains(edit, 'value="15:00"')
        self.assertContains(edit, 'name="step-ingredients-0" value="1" checked')
        self.assertContains(edit, 'id="step-template"')

        detail = self.client.get(f"/recipes/{recipe_id}/")
        self.assertContains(detail, "Sugo · 15 Min.")
        self.assertContains(detail, 'class="step-meta__ingredient">400 g Tomaten')

    def test_kitchen_mode_offers_step_timers_and_scaled_step_ingredients(self):
        recipe = Recipe.objects.get(id=self.create_recipe().json()["id"])
        self.client.post(f"/api/v1/recipes/{recipe.id}/approve")
        slot = add_slot(
            user=self.user,
            week_start=current_week_start(),
            date=current_week_start() + timedelta(days=1),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
            servings=4,
        )

        response = self.client.get(f"/plan/slots/{slot.id}/kitchen/")

        self.assertContains(response, 'data-label="Spaghetti" data-seconds="540"')
        self.assertContains(response, 'class="kitchen-step__ingredients"')
        self.assertContains(response, "800 g")
        self.assertContains(response, 'id="running-timers"')

    @override_settings(**FOUNDRY_SETTINGS)
    @patch("providers.foundry_recipe_import.urlopen")
    def test_imported_recipe_receives_step_timers_and_ingredients(self, mocked_urlopen):
        mocked_urlopen.return_value.__enter__.return_value = chat_response(
            {
                "title": "Tomatenpasta",
                "ingredients": [
                    {"sourceText": "400 g Tomaten", "amount": "400", "unit": "g"},
                    {"sourceText": "Spaghetti", "amount": "200", "unit": "g"},
                ],
                "steps": [
                    {
                        "body": "Tomaten 20 Minuten köcheln.",
                        "timers": [{"label": "Sugo köcheln", "seconds": 1200}],
                        "ingredientIndexes": [0],
                    },
                    {"body": "Spaghetti kochen.", "timers": "9 Minuten", "ingredientIndexes": [5]},
                ],
            }
        )
        create_import(household=self.household, source_type="url", url="https://example.test/pasta")

        self.assertTrue(run_next_import_job())

        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.SUCCEEDED)
        first, second = job.recipe.steps.all()
        self.assertEqual(first.timers, [{"label": "Sugo köcheln", "seconds": 1200}])
        self.assertEqual([line.source_text for line in first.ingredients.all()], ["Tomaten"])
        self.assertEqual(second.timers, [])
        self.assertFalse(second.ingredients.exists())
        self.assertFalse(RecipeStepEnrichmentJob.objects.exists())
        prompt = json.loads(mocked_urlopen.call_args.args[0].data)["input"][0]["content"][0]
        self.assertIn("ingredientIndexes", prompt["text"])
        self.assertIn("timers", prompt["text"])

    def test_manual_recipe_is_not_enriched_without_a_configured_provider(self):
        self.create_recipe()
        self.assertFalse(RecipeStepEnrichmentJob.objects.exists())

    @override_settings(**FOUNDRY_SETTINGS)
    @patch("providers.foundry_recipe_import.urlopen")
    def test_manual_recipe_is_enriched_without_overwriting_user_annotations(self, mocked_urlopen):
        recipe_id = self.create_recipe(
            steps=[
                {
                    "body": "Spaghetti 9 Minuten kochen.",
                    "timers": [{"label": "Meine Pasta", "seconds": 480}],
                },
                {"body": "Tomaten 15 Minuten einkochen, Basilikum dazu.", "ingredientIndexes": [2]},
            ]
        ).json()["id"]
        job = RecipeStepEnrichmentJob.objects.get(recipe_id=recipe_id)
        mocked_urlopen.return_value.__enter__.return_value = chat_response(
            {
                "steps": [
                    {"timers": [{"label": "Spaghetti", "seconds": 540}], "ingredientIndexes": [0]},
                    {
                        "timers": [{"label": "Sugo", "seconds": 900}],
                        "ingredientIndexes": [1, 2],
                    },
                ]
            }
        )

        self.assertTrue(run_next_step_enrichment_job())

        job.refresh_from_db()
        self.assertEqual(job.state, RecipeStepEnrichmentJob.State.SUCCEEDED)
        first, second = Recipe.objects.get(id=recipe_id).steps.all()
        self.assertEqual(first.timers, [{"label": "Meine Pasta", "seconds": 480}])
        self.assertEqual([line.source_text for line in first.ingredients.all()], ["Spaghetti"])
        self.assertEqual(second.timers, [{"label": "Sugo", "seconds": 900}])
        self.assertEqual([line.source_text for line in second.ingredients.all()], ["Basilikum"])
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/deployments/recipe-generation/chat/completions", request.full_url)
        recipe_text = json.loads(request.data)["messages"][1]["content"]
        self.assertIn("200 g Spaghetti", recipe_text)
        self.assertIn("Spaghetti 9 Minuten kochen.", recipe_text)

    @override_settings(**FOUNDRY_SETTINGS)
    @patch("providers.foundry_recipe_import.urlopen")
    def test_enrichment_skips_steps_rewritten_while_it_ran(self, mocked_urlopen):
        recipe_id = self.create_recipe(steps=[{"body": "Kochen."}]).json()["id"]
        recipe = Recipe.objects.get(id=recipe_id)

        def rewrite_then_respond(*args, **kwargs):
            self.client.patch(
                f"/api/v1/recipes/{recipe_id}",
                json.dumps({"version": recipe.version, "steps": [{"body": "Neu."}]}),
                content_type="application/json",
            )
            return chat_response({"steps": [{"timers": [{"label": "Kochen", "seconds": 60}]}]})

        mocked_urlopen.return_value.__enter__.side_effect = rewrite_then_respond

        self.assertTrue(run_next_step_enrichment_job())

        self.assertEqual(Recipe.objects.get(id=recipe_id).steps.get().timers, [])

    @override_settings(**FOUNDRY_SETTINGS)
    @patch("providers.foundry_recipe_import.urlopen")
    def test_mismatched_enrichment_response_fails_the_job(self, mocked_urlopen):
        recipe_id = self.create_recipe().json()["id"]
        mocked_urlopen.return_value.__enter__.return_value = chat_response({"steps": []})

        self.assertTrue(run_next_step_enrichment_job())

        job = RecipeStepEnrichmentJob.objects.get(recipe_id=recipe_id)
        self.assertEqual(job.state, RecipeStepEnrichmentJob.State.FAILED)
        self.assertEqual(job.error_code, "invalid_output")

    @override_settings(**FOUNDRY_SETTINGS)
    def test_enrichment_can_be_requested_from_the_detail_page_once_at_a_time(self):
        recipe = Recipe.objects.get(id=self.create_recipe().json()["id"])
        RecipeStepEnrichmentJob.objects.all().delete()

        detail = self.client.get(f"/recipes/{recipe.id}/")
        self.assertContains(detail, f"/recipes/{recipe.id}/steps/enrich/")

        response = self.client.post(f"/recipes/{recipe.id}/steps/enrich/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(queue_step_enrichment(recipe), RecipeStepEnrichmentJob.objects.get())
        self.assertEqual(self.client.get(f"/recipes/{recipe.id}/steps/enrich/").status_code, 405)
