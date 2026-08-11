from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import Household, HouseholdMembership, ProviderDiagnostic, User

from .images import ingredient_icon_prompt, queue_ingredient_icon, run_next_ingredient_icon_job
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

    @override_settings(AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY=True)
    def test_native_transparency_prompt_requests_white_strokes(self):
        prompt = ingredient_icon_prompt(self.ingredient)

        self.assertIn("white hand-drawn strokes", prompt)
        self.assertIn("transparent background", prompt)
        self.assertNotIn("black hand-drawn strokes", prompt)

    @override_settings(AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY=False)
    def test_postprocessing_prompt_requests_black_strokes_on_white(self):
        prompt = ingredient_icon_prompt(self.ingredient)

        self.assertIn("black hand-drawn strokes", prompt)
        self.assertIn("white background", prompt)
        self.assertNotIn("transparent background", prompt)

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

    @override_settings(AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY=False)
    @patch("pantry.images.generate_image_bytes")
    def test_non_native_transparency_removes_white_background(self, generate_image_bytes_mock):
        source = Image.new("RGB", (2, 1), "white")
        source.putpixel((0, 0), (0, 0, 0))
        image_bytes = BytesIO()
        source.save(image_bytes, format="PNG")
        generate_image_bytes_mock.return_value = image_bytes.getvalue()

        queue_ingredient_icon(self.ingredient)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.assertTrue(run_next_ingredient_icon_job())
            self.ingredient.refresh_from_db()
            with Image.open(self.ingredient.icon.path) as result:
                self.assertEqual(result.mode, "RGBA")
                self.assertEqual(result.getpixel((0, 0))[3], 255)
                self.assertEqual(result.getpixel((1, 0))[3], 0)
        self.assertIsNone(generate_image_bytes_mock.call_args.kwargs["background"])

    def test_explicit_icon_backfill_respects_its_paid_work_limit(self):
        CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        CanonicalIngredient.objects.create(household=self.household, name="Zitrone")

        call_command("backfill_ingredient_icons", limit=2)

        self.assertEqual(IngredientIconJob.objects.filter(state="queued").count(), 2)

    def test_remove_ingredient_icons_clears_selected_icons_and_pending_jobs(self):
        second = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        self.ingredient.icon.save("tomate.png", ContentFile(b"icon"), save=True)
        self.ingredient.icon_status = "ready"
        self.ingredient.icon_prompt = "old prompt"
        self.ingredient.save(update_fields=["icon_status", "icon_prompt"])
        job = queue_ingredient_icon(self.ingredient)

        call_command("remove_ingredient_icons", str(self.ingredient.id))

        self.ingredient.refresh_from_db()
        second.refresh_from_db()
        job.refresh_from_db()
        self.assertFalse(self.ingredient.icon)
        self.assertEqual(self.ingredient.icon_status, "pending")
        self.assertEqual(self.ingredient.icon_prompt, "")
        self.assertEqual(job.state, IngredientIconJob.State.SUPERSEDED)
        self.assertFalse(second.icon)

    def test_remove_ingredient_icons_supports_all(self):
        second = CanonicalIngredient.objects.create(household=self.household, name="Basilikum")
        self.ingredient.icon.save("tomate.png", ContentFile(b"icon"), save=True)
        second.icon.save("basilikum.png", ContentFile(b"icon"), save=True)

        call_command("remove_ingredient_icons", "--all")

        self.ingredient.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.ingredient.icon)
        self.assertFalse(second.icon)

    def test_remove_ingredient_icons_requires_selection(self):
        with self.assertRaisesMessage(CommandError, "at least one ingredient ID"):
            call_command("remove_ingredient_icons")

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