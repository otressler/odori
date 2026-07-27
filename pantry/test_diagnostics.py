from unittest.mock import patch

from django.test import TestCase, override_settings

from core.models import Household, HouseholdMembership, ProviderDiagnostic, User

from .jobs import run_next_category_job
from .models import PantryCategorizationJob
from .semantic import embed_with_diagnostics
from .services import queue_category_suggestions


class EmbeddingDiagnosticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="test-password")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.user,
            role=HouseholdMembership.Role.OWNER,
        )

    @override_settings(INGREDIENT_EMBEDDINGS_ENABLED=False)
    def test_disabled_embedding_records_owner_diagnostic(self):
        result = embed_with_diagnostics(
            "Spaghetti",
            household_id=self.household.id,
            operation="category_test",
        )

        self.assertIsNone(result.vector)
        self.assertEqual(result.state, ProviderDiagnostic.State.SKIPPED)
        self.assertEqual(result.error_code, "disabled")
        diagnostic = ProviderDiagnostic.objects.get(household=self.household)
        self.assertEqual(diagnostic.operation, "category_test")
        self.assertEqual(diagnostic.error_code, "disabled")

    def test_failed_category_job_records_attempt_and_error_code(self):
        queue_category_suggestions(user=self.user)

        with patch("pantry.jobs.categorize_household", side_effect=RuntimeError("database boom")):
            self.assertTrue(run_next_category_job())

        job = PantryCategorizationJob.objects.get(household=self.household)
        self.assertEqual(job.state, PantryCategorizationJob.State.FAILED)
        self.assertEqual(job.attempt_count, 1)
        self.assertEqual(job.error_code, "RuntimeError")
