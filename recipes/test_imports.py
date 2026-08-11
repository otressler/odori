from datetime import timedelta

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
from .models import Recipe, RecipeImportJob


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
        self.assertEqual(RecipeImportJob.objects.filter(state=RecipeImportJob.State.QUEUED).count(), 0)
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

        self.assertTrue(run_next_import_job(lambda current: (_ for _ in ()).throw(RetryableImportError("busy"))))
        job.refresh_from_db()
        self.assertEqual(job.state, RecipeImportJob.State.FAILED)
        self.assertEqual(job.error_code, "provider_temporary")
        self.assertEqual(job.attempts.count(), 1)

    def test_same_content_reuses_completed_job_and_draft(self):
        first, _ = create_import(
            household=self.household, source_type="url", content=b"same", url="https://example.test/recipe"
        )
        self.assertTrue(run_next_import_job(lambda current: {"title": "Imported"}))
        second, created = create_import(
            household=self.household, source_type="url", content=b"same", url="https://example.test/recipe"
        )

        self.assertFalse(created)
        self.assertEqual(second.id, first.id)
        self.assertEqual(Recipe.objects.filter(source__import_source=first.source).count(), 1)
