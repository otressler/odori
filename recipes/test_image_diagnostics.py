from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from PIL import Image

from core.models import Household, HouseholdMembership, ProviderDiagnostic, User
from providers.foundry_images import generate_image_bytes

from .images import run_next_recipe_image_job
from .models import Recipe, RecipeImageJob, RecipeSource


class RecipeImageDiagnosticsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="test-password")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.user,
            role=HouseholdMembership.Role.OWNER,
        )
        source = RecipeSource.objects.create(household=self.household)
        self.recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Pasta",
        )
        self.job = RecipeImageJob.objects.create(recipe=self.recipe, prompt="A pasta dish")

    @staticmethod
    def _image_bytes(size=(1024, 768)):
        output = BytesIO()
        Image.new("RGB", size, "red").save(output, format="PNG")
        return output.getvalue()

    @override_settings(AZURE_OPENAI_ENDPOINT="", AZURE_OPENAI_API_KEY="")
    def test_missing_image_configuration_is_persisted_as_diagnostic(self):
        self.assertTrue(run_next_recipe_image_job())

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, RecipeImageJob.State.FAILED)
        self.assertEqual(self.job.attempt_count, 1)
        self.assertEqual(self.job.error_code, "missing_configuration")
        diagnostic = ProviderDiagnostic.objects.get(job_id=self.job.id)
        self.assertEqual(diagnostic.operation, "recipe_image_generation")
        self.assertEqual(diagnostic.error_code, "missing_configuration")

    @patch("recipes.images.generate_image_bytes")
    def test_generated_image_is_saved_with_default_storage(self, generate_image_bytes_mock):
        generate_image_bytes_mock.return_value = self._image_bytes()
        self.recipe.image_prompt = self.job.prompt
        self.recipe.save(update_fields=["image_prompt"])

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.assertTrue(run_next_recipe_image_job())
            self.job.refresh_from_db()
            self.recipe.refresh_from_db()
            self.assertEqual(self.job.state, RecipeImageJob.State.SUCCEEDED)
            self.assertEqual(self.recipe.image_status, "ready")
            self.assertTrue(self.recipe.image.name.endswith(".png"))
            self.assertTrue(self.recipe.thumbnail.name.endswith(".jpg"))
            with self.recipe.thumbnail.open("rb") as thumbnail_file:
                with Image.open(thumbnail_file) as thumbnail:
                    self.assertEqual(thumbnail.size, (512, 512))
        generate_image_bytes_mock.assert_called_once()

    def test_missing_thumbnail_is_generated_from_existing_image(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.recipe.image.save(
                "existing.png",
                ContentFile(self._image_bytes((800, 600))),
                save=True,
            )
            call_command("generate_recipe_thumbnails")
            self.recipe.refresh_from_db()
            self.assertTrue(self.recipe.thumbnail.name.endswith(".jpg"))
        self.assertEqual(RecipeImageJob.objects.filter(recipe=self.recipe).count(), 1)

    @patch("recipes.images.generate_image_bytes", return_value=b"generated-image")
    def test_stale_image_job_is_marked_superseded(self, generate_image_bytes_mock):
        self.recipe.image_prompt = "A newer prompt"
        self.recipe.save(update_fields=["image_prompt"])

        self.assertTrue(run_next_recipe_image_job())

        self.job.refresh_from_db()
        self.assertEqual(self.job.state, RecipeImageJob.State.SUPERSEDED)
        generate_image_bytes_mock.assert_called_once()

    @override_settings(
        AZURE_OPENAI_ENDPOINT="https://example.test",
        AZURE_OPENAI_API_KEY="test-key",
        AZURE_OPENAI_IMAGE_DEPLOYMENT="test-deployment",
    )
    @patch("providers.foundry_images.urlopen")
    def test_invalid_icon_image_data_records_the_icon_operation(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = b'{"data": [{"b64_json": "not-valid-base64"}]}'

        with self.assertRaises(Exception):
            generate_image_bytes(
                "A pantry icon",
                household_id=self.household.id,
                job_id=self.job.id,
                correlation_id=None,
                operation="ingredient_icon_generation",
            )

        diagnostic = ProviderDiagnostic.objects.get(job_id=self.job.id)
        self.assertEqual(diagnostic.operation, "ingredient_icon_generation")
