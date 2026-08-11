import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

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
from .models import ImportSource, Recipe, RecipeImportJob, RecipeSource


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
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Tomatenpasta",
                                    "description": "Schnelles Abendessen",
                                    "servings": 2,
                                    "ingredients": [
                                        {"sourceText": "Tomaten", "amount": "400", "unit": "g"}
                                    ],
                                    "steps": [{"body": "Tomaten kochen."}],
                                    "tags": ["schnell"],
                                }
                            )
                        }
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

    def test_import_rejects_non_http_urls(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/api/v1/recipe-imports",
            json.dumps({"url": "file:///tmp/recipe"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_url")
