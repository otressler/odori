import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Household, HouseholdMembership, User

from .imports import (
    RetryableImportError,
    claim_next_import,
    create_import,
    recover_expired_imports,
    run_next_import_job,
)
from .models import ImportSource, Recipe, RecipeImportJob, RecipeIngredient, RecipeSource


@override_settings(IMPORT_JOB_LEASE_SECONDS=60, IMPORT_JOB_RETRY_DELAY_SECONDS=0)
class RecipeImportJobTests(TestCase):
    def setUp(self):
        self.household = Household.objects.create(name="Test household")
        self.user = User.objects.create_user(username="owner", password="password")
        HouseholdMembership.objects.create(
            household=self.household, user=self.user, role=HouseholdMembership.Role.OWNER
        )

    def test_claim_creates_one_lease_and_attempt(self):
        job, created = create_import(
            household=self.household, source_type="url", url="https://example.test/recipe"
        )

        self.assertTrue(created)
        claimed = claim_next_import()
        self.assertIsNotNone(claimed)
        claimed_job, attempt = claimed
        self.assertEqual(claimed_job.id, job.id)
        self.assertEqual(attempt.number, 1)
        self.assertEqual(
            RecipeImportJob.objects.filter(state=RecipeImportJob.State.QUEUED).count(), 0
        )
        self.assertIsNone(claim_next_import())

    def test_expired_lease_is_returned_to_queue(self):
        job, _ = create_import(household=self.household, source_type="pdf", content=b"pdf")
        claim_next_import()
        RecipeImportJob.objects.filter(id=job.id).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        self.assertEqual(recover_expired_imports(), 1)
        job.refresh_from_db()
        self.assertEqual(job.state, RecipeImportJob.State.QUEUED)
        self.assertEqual(job.error_code, "lease_expired")

    def test_retry_exhaustion_is_terminal(self):
        job, _ = create_import(household=self.household, source_type="image", content=b"image")
        job.max_attempts = 1
        job.save(update_fields=["max_attempts"])

        self.assertTrue(
            run_next_import_job(
                lambda current: (_ for _ in ()).throw(RetryableImportError("busy"))
            )
        )
        job.refresh_from_db()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "provider_temporary")
        self.assertEqual(job.attempts.count(), 1)

    def test_same_content_reuses_completed_job_and_draft(self):
        first, _ = create_import(
            household=self.household,
            source_type="url",
            content=b"same",
            url="https://example.test/recipe",
        )
        self.assertTrue(run_next_import_job(lambda current: {"title": "Imported"}))
        second, created = create_import(
            household=self.household,
            source_type="url",
            content=b"same",
            url="https://example.test/recipe",
        )

        self.assertFalse(created)
        self.assertEqual(second.id, first.id)
        self.assertEqual(Recipe.objects.filter(source__import_source=first.source).count(), 1)

    @override_settings(
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT="recipe-import",
    )
    @patch("providers.foundry_recipe_import.urlopen")
    def test_url_import_is_queued_then_creates_imported_draft(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": json.dumps({
                                "title": "Tomatenpasta",
                                "description": "Schnelles Abendessen",
                                "servings": 2,
                                "ingredients": [
                                    {
                                        "sourceText": "400 g Tomaten",
                                        "amount": "400",
                                        "unit": "g",
                                    }
                                ],
                                "steps": [{"body": "Tomaten kochen."}],
                                "tags": ["schnell"],
                            }),
                        }],
                    }
                ]
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        self.client.force_login(self.user)

        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "https://example.test/recipe"}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        mocked_urlopen.assert_not_called()
        self.assertTrue(run_next_import_job())

        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.SUCCEEDED)
        recipe = Recipe.objects.get(id=job.recipe_id)
        self.assertEqual(recipe.source.type, RecipeSource.Type.IMPORTED)
        self.assertEqual(recipe.source.import_source.source_type, ImportSource.Type.URL)
        ingredient = RecipeIngredient.objects.get(recipe=recipe)
        self.assertEqual(ingredient.source_text, "Tomaten")
        self.assertEqual(str(ingredient.amount), "400.00")
        self.assertEqual(ingredient.unit, "g")
        request_body = json.loads(mocked_urlopen.call_args.args[0].data)
        self.assertTrue(mocked_urlopen.call_args.args[0].full_url.endswith("/openai/v1/responses"))
        self.assertEqual(request_body["model"], "recipe-import")
        self.assertEqual(request_body["max_output_tokens"], 4000)
        self.assertEqual(request_body["tools"], [{"type": "web_search"}])
        prompt = request_body["input"][0]["content"][0]["text"]
        self.assertIn("Translate the complete recipe into German", prompt)
        self.assertIn("German metric kitchen units", prompt)
        self.assertIn("sourceText", prompt)
        status = self.client.get(queued.json()["statusUrl"])
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["recipe"]["title"], "Tomatenpasta")

    @override_settings(
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT="recipe-import",
    )
    @patch("providers.foundry_recipe_import.urlopen")
    def test_url_without_recipe_is_a_terminal_import_failure(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {"choices": [{"message": {"content": json.dumps(
                {"error": {"code": "recipe_not_found", "message": "No recipe."}}
            )}}]}
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        self.client.force_login(self.user)
        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "https://example.test/article"}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        self.assertTrue(run_next_import_job())
        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "recipe_not_found")
        self.assertEqual(
            self.client.get(queued.json()["statusUrl"]).json()["errorCode"],
            "recipe_not_found",
        )

    @override_settings(
        RECIPE_GENERATION_ENABLED=True,
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT="recipe-generation",
    )
    @patch("providers.foundry_recipe_import.urlopen")
    def test_plaintext_import_uses_recipe_generation_model_and_normalizes(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{
                            "type": "output_text",
                            "text": json.dumps({
                                "title": "Tomatenpasta",
                                "description": "Schnelles Abendessen",
                                "servings": 2,
                                "ingredients": [
                                    {"sourceText": "Tomaten", "amount": "400", "unit": "g"}
                                ],
                                "steps": [{"body": "Tomaten kochen."}],
                                "tags": ["schnell"],
                            }),
                        }],
                    }
                ]
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        self.client.force_login(self.user)

        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"text": "400 g tomatoes; cook until soft."}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        mocked_urlopen.assert_not_called()
        self.assertTrue(run_next_import_job())

        source = ImportSource.objects.get()
        self.assertEqual(source.source_type, ImportSource.Type.TEXT)
        self.assertEqual(source.content, "400 g tomatoes; cook until soft.")
        request_body = json.loads(mocked_urlopen.call_args.args[0].data)
        self.assertTrue(mocked_urlopen.call_args.args[0].full_url.endswith("/openai/v1/responses"))
        self.assertEqual(request_body["model"], "recipe-generation")
        self.assertEqual(request_body["tools"], [{"type": "web_search"}])
        prompt = request_body["input"][0]["content"][0]["text"]
        self.assertIn("complete recipe", prompt)
        self.assertIn("well-known dish", prompt)
        self.assertIn("natural-language cooking goal", prompt)
        self.assertIn("natural German", prompt)
        self.assertIn("German metric kitchen units", prompt)
        self.assertIn("sourceText", prompt)

    @override_settings(
        RECIPE_GENERATION_ENABLED=True,
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT="recipe-generation",
    )
    @patch("providers.foundry_recipe_import.urlopen")
    def test_plaintext_provider_request_error_is_not_retryable(self, mocked_urlopen):
        mocked_urlopen.side_effect = HTTPError(
            "https://example.test/openai/v1/responses",
            400,
            "Bad Request",
            {},
            MagicMock(read=MagicMock(return_value=b'{"error":{"code":"invalid_value"}}')),
        )
        self.client.force_login(self.user)
        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"text": "Cacio e pepe"}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        self.assertTrue(run_next_import_job())
        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "provider_request_rejected")
        self.assertEqual(job.attempt_count, 1)

    @override_settings(
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT="recipe-import",
    )
    @patch("providers.foundry_recipe_import.log_event")
    @patch("providers.foundry_recipe_import.urlopen")
    def test_invalid_recipe_content_logs_response_diagnostics(
        self, mocked_urlopen, mocked_log_event
    ):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "This is not JSON."},
                    }
                ]
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        self.client.force_login(self.user)

        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "https://example.test/recipe"}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        self.assertTrue(run_next_import_job())
        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "invalid_output")
        mocked_log_event.assert_called_once()
        self.assertEqual(mocked_log_event.call_args.args[1], "provider.recipe_response_diagnostics")
        fields = mocked_log_event.call_args.kwargs
        self.assertEqual(fields["response_shape"], "chat_completions")
        self.assertEqual(fields["finish_reason"], "stop")
        self.assertEqual(fields["content_length"], len("This is not JSON."))
        self.assertEqual(fields["content_preview"], repr("This is not JSON."))

    @override_settings(
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT="recipe-import",
    )
    @patch("providers.foundry_recipe_import.urlopen")
    def test_truncated_recipe_content_has_distinct_error(self, mocked_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": ""},
                    }
                ]
            }
        ).encode()
        mocked_urlopen.return_value.__enter__.return_value = response
        self.client.force_login(self.user)

        queued = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "https://example.test/recipe"}),
            content_type="application/json",
        )

        self.assertEqual(queued.status_code, 202)
        self.assertTrue(run_next_import_job())
        job = RecipeImportJob.objects.get()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "provider_response_truncated")

    def test_plaintext_import_rejects_oversized_text(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"text": "x" * 20_001}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_text")

    def test_import_rejects_non_http_urls(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "file:///tmp/recipe"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_url")
