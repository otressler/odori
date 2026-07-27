from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.models import Household, HouseholdMembership, User

from .models import IngredientCategory
from .semantic import EmbeddingResult
from .views import category_admin_page


class CategoryDiagnosticsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="mara", password="test-password")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(
            household=self.household,
            user=self.user,
            role=HouseholdMembership.Role.OWNER,
        )

    def test_category_test_exposes_text_vector_and_final_scores(self):
        category = IngredientCategory.objects.create(
            household=self.household,
            name="Trockenwaren",
            description="Pasta und Reis",
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )
        request = self.factory.post(
            "/admin/categories",
            {"action": "test", "ingredient": "Pasta"},
        )
        request.user = self.user
        request.request_id = "category-test-request"

        with (
            patch(
                "pantry.views.embed_with_diagnostics",
                return_value=EmbeddingResult(vector=[1.0, 0.0], state="succeeded"),
            ),
            patch("pantry.views.render", return_value=HttpResponse()) as render_mock,
        ):
            category_admin_page(request)

        context = render_mock.call_args.args[2]
        result = context["similarity_results"][0]
        self.assertEqual(result["category"], category)
        self.assertEqual(result["similarity"], 1.0)
        self.assertEqual(result["final_score"], 1.0)
        self.assertTrue(result["embedding_available"])
        self.assertEqual(result["embedding_dimensions"], 2)
        self.assertEqual(context["request_id"], "category-test-request")
