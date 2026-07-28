from django.test import TestCase, override_settings

from core.models import Household

from .catalog import sync_starter_catalog
from .models import IngredientCategory, IngredientCategoryExample
from .services import classify_category


class CategoryClassifierTests(TestCase):
    def setUp(self):
        self.household = Household.objects.create(name="Classifier")
        self.bakery = IngredientCategory.objects.create(
            household=self.household,
            name="Bäckerei",
            sort_order=10,
            minimum_similarity=0.5,
            minimum_margin=0.1,
        )
        self.dry_goods = IngredientCategory.objects.create(
            household=self.household,
            name="Trockenwaren",
            sort_order=20,
            minimum_similarity=0.5,
            minimum_margin=0.1,
        )

    def example(self, *, category, text, vector, model="test-embedding"):
        return IngredientCategoryExample.objects.create(
            household=self.household,
            category=category,
            text=text,
            normalized_text=text.casefold(),
            source=IngredientCategoryExample.Source.STARTER,
            source_key=f"{category.name}:{text}",
            embedding=vector,
            embedding_model=model,
        )

    def test_exact_example_match_wins_without_an_embedding(self):
        example = self.example(category=self.dry_goods, text="Spaghetti", vector=[])

        result = classify_category(
            name="Spaghetti",
            categories=[self.bakery, self.dry_goods],
        )

        self.assertEqual(result["state"], "assigned")
        self.assertEqual(result["category"], self.dry_goods)
        self.assertTrue(result["candidate"]["exact_match"])
        self.assertEqual(result["candidate"]["best_example"], example)

    def test_top_three_example_mean_ranks_category(self):
        self.example(category=self.bakery, text="Brot", vector=[0.6, 0.8])
        self.example(category=self.dry_goods, text="Penne", vector=[1.0, 0.0])
        self.example(category=self.dry_goods, text="Fusilli", vector=[1.0, 0.0])
        self.example(category=self.dry_goods, text="Reis", vector=[1.0, 0.0])

        result = classify_category(
            name="Nudeln",
            ingredient_embedding=[1.0, 0.0],
            ingredient_embedding_model="test-embedding",
            categories=[self.bakery, self.dry_goods],
        )

        self.assertEqual(result["state"], "assigned")
        self.assertEqual(result["category"], self.dry_goods)
        self.assertEqual(result["score"], 1.0)
        self.assertEqual(result["margin"], 0.4)
        self.assertEqual(
            [example.text for example in result["candidate"]["matched_examples"]],
            ["Fusilli", "Penne", "Reis"],
        )

    def test_small_margin_requires_review(self):
        self.example(category=self.bakery, text="Brot", vector=[0.99, 0.1])
        self.example(category=self.dry_goods, text="Penne", vector=[1.0, 0.0])

        result = classify_category(
            name="Unbekannt",
            ingredient_embedding=[1.0, 0.0],
            ingredient_embedding_model="test-embedding",
            categories=[self.bakery, self.dry_goods],
        )

        self.assertEqual(result["state"], "review_required")
        self.assertEqual(result["category"], self.dry_goods)
        self.assertLess(result["margin"], result["minimum_margin"])

    def test_incompatible_embedding_model_is_not_scored(self):
        self.example(category=self.dry_goods, text="Penne", vector=[1.0, 0.0])

        result = classify_category(
            name="Nudeln",
            ingredient_embedding=[1.0, 0.0],
            ingredient_embedding_model="another-embedding",
            categories=[self.dry_goods],
        )

        self.assertEqual(result["state"], "no_vectors")


class StarterCatalogTests(TestCase):
    def setUp(self):
        self.household = Household.objects.create(name="Catalog")

    @override_settings(INGREDIENT_EMBEDDINGS_ENABLED=False)
    def test_sync_is_idempotent_and_preserves_owner_examples(self):
        first = sync_starter_catalog(household=self.household)
        dry_goods = IngredientCategory.objects.get(
            household=self.household,
            name="Trockenwaren",
        )
        owner_example = IngredientCategoryExample.objects.create(
            household=self.household,
            category=dry_goods,
            text="Familiennudeln",
            normalized_text="familiennudeln",
            source=IngredientCategoryExample.Source.OWNER,
        )

        second = sync_starter_catalog(household=self.household)

        self.assertEqual(first["categories_created"], 8)
        self.assertGreater(first["examples_created"], 0)
        self.assertEqual(second["categories_created"], 0)
        self.assertEqual(second["examples_created"], 0)
        self.assertEqual(second["examples_updated"], 0)
        self.assertTrue(
            IngredientCategoryExample.objects.filter(id=owner_example.id, active=True).exists()
        )
