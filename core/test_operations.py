import json
import uuid
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.utils import timezone

from pantry.models import CanonicalIngredient, IngredientIconJob, PantryCategorizationJob
from recipes.models import (
    Recipe,
    RecipeImageJob,
    RecipeImportAttempt,
    RecipeImportJob,
    RecipeSource,
)

from .models import Household, HouseholdMembership, ProviderDiagnostic, User, WorkerHeartbeat
from .views import (
    job_state_counts,
    operations_page,
    retry_category_job,
    retry_import_job,
    worker_readiness,
)


class OperationsPageTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.owner = User.objects.create_user(username="owner", password="test-password")
        self.member = User.objects.create_user(username="member", password="test-password")
        self.household = Household.objects.create(name="Casa Odori")
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.owner,
            role=HouseholdMembership.Role.OWNER,
        )
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.member,
            role=HouseholdMembership.Role.MEMBER,
        )

    def test_operations_page_is_owner_scoped_and_includes_diagnostics(self):
        WorkerHeartbeat.objects.create(
            name="default",
            state=WorkerHeartbeat.State.IDLE,
            last_heartbeat_at=timezone.now(),
        )
        ProviderDiagnostic.objects.create(
            household=self.household,
            operation="category_test",
            state=ProviderDiagnostic.State.FAILED,
            error_code="timeout",
        )
        PantryCategorizationJob.objects.create(
            household=self.household,
            state=PantryCategorizationJob.State.FAILED,
            attempt_count=2,
            error_code="timeout",
        )
        request = self.factory.get("/admin/operations")
        request.user = self.owner

        with patch("core.views.render", return_value=HttpResponse()) as render_mock:
            response = operations_page(request)

        self.assertEqual(response.status_code, 200)
        context = render_mock.call_args.args[2]
        self.assertTrue(context["worker_is_fresh"])
        self.assertEqual(context["category_queue_counts"]["failed"], 1)
        self.assertEqual(context["provider_diagnostics"][0].error_code, "timeout")

    def test_operations_page_rejects_household_member(self):
        request = self.factory.get("/admin/operations")
        request.user = self.member

        with self.assertRaises(PermissionDenied):
            operations_page(request)

    def test_job_state_counts_uses_each_model_household_query(self):
        ingredient = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        IngredientIconJob.objects.create(
            ingredient=ingredient, prompt="A tomato", state=IngredientIconJob.State.FAILED
        )
        source = RecipeSource.objects.create(household=self.household)
        recipe = Recipe.objects.create(
            household=self.household, created_by=self.owner, source=source, title="Soup"
        )
        RecipeImageJob.objects.create(
            recipe=recipe, prompt="A soup", state=RecipeImageJob.State.SUCCEEDED
        )

        self.assertEqual(
            job_state_counts(IngredientIconJob, household=self.household)["failed"], 1
        )
        self.assertEqual(
            job_state_counts(RecipeImageJob, household=self.household)["succeeded"], 1
        )

    def test_failed_category_job_can_be_requeued_by_owner(self):
        job = PantryCategorizationJob.objects.create(
            household=self.household,
            state=PantryCategorizationJob.State.FAILED,
            error_message="timed out",
            error_code="timeout",
        )
        request = self.factory.post(f"/admin/operations/jobs/categories/{job.id}/retry")
        request.user = self.owner
        request.request_id = str(uuid.uuid4())

        response = retry_category_job(request, job.id)

        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.state, PantryCategorizationJob.State.QUEUED)
        self.assertEqual(job.error_code, "")
        self.assertIsNotNone(job.correlation_id)

    def test_worker_health_reports_missing_heartbeat(self):
        response = worker_readiness(self.factory.get("/health/worker"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["reason"], "worker_not_seen")

    def test_failed_import_retry_preserves_attempt_numbering(self):
        from recipes.models import ImportSource

        import_source = ImportSource.objects.create(
            household=self.household,
            source_type=ImportSource.Type.URL,
            url="https://example.test/recipe",
            content_hash="retry-test",
        )
        job = RecipeImportJob.objects.create(
            household=self.household,
            source=import_source,
            state=RecipeImportJob.State.FAILED,
            attempt_count=1,
        )
        RecipeImportAttempt.objects.create(job=job, number=1, lease_id=uuid.uuid4())
        request = self.factory.post(f"/admin/operations/jobs/imports/{job.id}/retry")
        request.user = self.owner
        request.request_id = str(uuid.uuid4())

        response = retry_import_job(request, job.id)

        self.assertEqual(response.status_code, 302)
        job.refresh_from_db()
        self.assertEqual(job.state, RecipeImportJob.State.QUEUED)
        self.assertEqual(job.attempt_count, 1)
