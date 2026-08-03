from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Household, HouseholdMembership, ProviderDiagnostic, User

from .images import queue_ingredient_icon, run_next_ingredient_icon_job
from .models import CanonicalIngredient, IngredientIconJob


@override_settings(AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS=0)
class IngredientIconGenerationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="test-password")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.user,
            role=HouseholdMembership.Role.OWNER,
        )
        self.ingredient = CanonicalIngredient.objects.create(
            household=self.household,
            name="Tomate",
        )

    @override_settings(AZURE_OPENAI_ENDPOINT="", AZURE_OPENAI_API_KEY="")
    def test_missing_configuration_is_persisted_as_an_icon_diagnostic(self):
        job = queue_ingredient_icon(self.ingredient)

        self.assertTrue(run_next_ingredient_icon_job())

        job.refresh_from_db()
        self.assertEqual(job.state, IngredientIconJob.State.FAILED)
        self.assertEqual(job.error_code, "missing_configuration")
        diagnostic = ProviderDiagnostic.objects.get(job_id=job.id)
        self.assertEqual(diagnostic.operation, "ingredient_icon_generation")

    @patch("pantry.images.generate_image_bytes", return_value=b"generated-icon")
    def test_generated_icon_is_saved_with_default_storage(self, generate_image_bytes_mock):
        queue_ingredient_icon(self.ingredient)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.assertTrue(run_next_ingredient_icon_job())

        self.ingredient.refresh_from_db()
        self.assertEqual(self.ingredient.icon_status, "ready")
        self.assertTrue(self.ingredient.icon.name.endswith(".png"))
        generate_image_bytes_mock.assert_called_once()
        self.assertEqual(
            generate_image_bytes_mock.call_args.kwargs["operation"], "ingredient_icon_generation"
        )
        self.assertEqual(generate_image_bytes_mock.call_args.kwargs["background"], "transparent")
        self.assertEqual(generate_image_bytes_mock.call_args.kwargs["output_format"], "png")
        self.assertEqual(
            generate_image_bytes_mock.call_args.kwargs["deployment"],
            settings.AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT,
        )

    def test_explicit_icon_backfill_respects_its_paid_work_limit(self):
        CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        CanonicalIngredient.objects.create(household=self.household, name="Zitrone")

        call_command("backfill_ingredient_icons", limit=2)

        self.assertEqual(IngredientIconJob.objects.filter(state="queued").count(), 2)

    @override_settings(AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS=60)
    @patch("pantry.images.generate_image_bytes", return_value=b"generated-icon")
    def test_icon_runner_defers_next_job_instead_of_sleeping(self, generate_image_bytes_mock):
        second = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        first_job = queue_ingredient_icon(self.ingredient)
        second_job = queue_ingredient_icon(second)

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.assertTrue(run_next_ingredient_icon_job())

        first_job.refresh_from_db()
        second_job.refresh_from_db()
        self.assertEqual(first_job.state, IngredientIconJob.State.SUCCEEDED)
        self.assertGreater(second_job.available_at, timezone.now())
        self.assertFalse(run_next_ingredient_icon_job())
        generate_image_bytes_mock.assert_called_once()