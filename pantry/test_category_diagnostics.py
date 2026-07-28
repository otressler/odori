from unittest.mock import patch

from django.http import HttpResponse
from django.test import RequestFactory, TestCase

from core.models import Household, HouseholdMembership, User

from .models import IngredientCategory, IngredientCategoryExample
from .semantic import EmbeddingResult
from .services import category_score_details
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
        self.assertEqual(result["similarity"], None)
        self.assertEqual(result["final_score"], 1.0)
        self.assertEqual(result["best_example"].text, "Pasta")
        self.assertEqual(context["classification_result"]["state"], "assigned")
        self.assertEqual(context["request_id"], "category-test-request")

    def test_spaghetti_matches_dry_goods_without_character_overlap_boost(self):
        bakery = IngredientCategory.objects.create(
            household=self.household,
            name="Bäckerei",
        )
        dry_goods = IngredientCategory.objects.create(
            household=self.household,
            name="Trockenwaren",
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=bakery,
            text="Brot",
            normalized_text="brot",
            source=IngredientCategoryExample.Source.STARTER,
            source_key="bakery:brot",
            embedding=[1.0, 0.0],
            embedding_model="test-embedding",
        )
        IngredientCategoryExample.objects.create(
            household=self.household,
            category=dry_goods,
            text="Spaghetti",
            normalized_text="spaghetti",
            source=IngredientCategoryExample.Source.STARTER,
            source_key="dry-goods:spaghetti",
            embedding=[0.0, 1.0],
            embedding_model="test-embedding",
        )

        bakery_scores = category_score_details(
            name="Spaghetti",
            ingredient_embedding=[0.0, 1.0],
            ingredient_embedding_model="test-embedding",
            category=bakery,
        )
        dry_goods_scores = category_score_details(
            name="Spaghetti",
            ingredient_embedding=[0.0, 1.0],
            ingredient_embedding_model="test-embedding",
            category=dry_goods,
        )

        self.assertEqual(bakery_scores["text_score"], 0.0)
        self.assertEqual(bakery_scores["score"], 0.0)
        self.assertEqual(dry_goods_scores["text_score"], 1.0)
        self.assertEqual(dry_goods_scores["embedding_score"], None)
        self.assertEqual(dry_goods_scores["score"], 1.0)
