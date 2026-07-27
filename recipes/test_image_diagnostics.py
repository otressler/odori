from django.test import TestCase, override_settings

from core.models import Household, HouseholdMembership, ProviderDiagnostic, User

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
