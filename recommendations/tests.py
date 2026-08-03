import json
import uuid
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from io import StringIO

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryItem
from planning.models import CookEvent, MealPlan, MealSlot
from planning.services import current_week_start
from recipes.models import (
    Recipe,
    RecipeIngredient,
    RecipeSource,
    RecipeTag,
    RecipeTagAssignment,
)

from .contracts import CandidateFeatures, RecommendationOptions
from .models import MAX_SNAPSHOT_BYTES, RecommendationFeedback, RecommendationRun
from .scoring import score_catalog_v1
from .services import (
    MAX_CANDIDATES,
    prune_recommendation_runs,
    recommend,
    record_feedback,
    replay_snapshot,
)


class RecommendationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="recommender", password="test-pass")
        self.household = Household.objects.create(name="Casa")
        HouseholdMembership.objects.create(
            household=self.household, user=self.user, role="owner"
        )
        self.source = RecipeSource.objects.create(household=self.household)
        self.week_start = current_week_start()
        self.client.force_login(self.user)

    def recipe(self, title="Pasta", recipe_id=None):
        values = {
            "household": self.household,
            "created_by": self.user,
            "source": self.source,
            "title": title,
            "status": Recipe.Status.APPROVED,
        }
        if recipe_id:
            values["id"] = recipe_id
        return Recipe.objects.create(**values)

    def options(self, **overrides):
        values = {"week_start": self.week_start}
        values.update(overrides)
        return RecommendationOptions(**values)


class CatalogV1ScoringTests(TestCase):
    def candidate(self, **overrides):
        values = {
            "recipe_id": "00000000-0000-0000-0000-000000000001",
            "recipe_version": 1,
            "title": "Test",
            "matched": 1,
            "missing": 1,
            "unknown": 1,
            "days_since_cook": 7,
            "preferred_tag_matches": 1,
            "planned_occurrences": 1,
            "feedback_state": "not_again",
        }
        values.update(overrides)
        return CandidateFeatures(**values)

    def test_catalog_v1_uses_exact_integer_components_and_clamp(self):
        scored = score_catalog_v1(self.candidate(), selected_tag_count=2)

        self.assertEqual(scored.components.inventory_coverage_bp, 2166)
        self.assertEqual(scored.components.missing_penalty_bp, 666)
        self.assertEqual(scored.components.unknown_penalty_bp, 333)
        self.assertEqual(scored.components.cook_recency_bp, 500)
        self.assertEqual(scored.components.preferred_tags_bp, 500)
        self.assertEqual(scored.components.already_planned_penalty_bp, 1500)
        self.assertEqual(scored.components.negative_feedback_penalty_bp, 2500)
        self.assertEqual(scored.score_bp, 0)

    def test_inventory_terms_are_zero_without_required_ingredients(self):
        scored = score_catalog_v1(
            self.candidate(matched=0, missing=0, unknown=0, feedback_state=None),
            selected_tag_count=2,
        )
        self.assertEqual(scored.components.inventory_coverage_bp, 0)
        self.assertEqual(scored.components.missing_penalty_bp, 0)
        self.assertEqual(scored.components.unknown_penalty_bp, 0)

    def test_fixture_replays_exact_order_scores_components_and_reasons(self):
        path = Path(__file__).parent / "test_fixtures" / "catalog_v1.json"
        fixture = json.loads(path.read_text(encoding="utf-8"))
        expected = fixture.pop("expected")

        replay = replay_snapshot(fixture)

        actual = [
            {
                "recipeId": item.candidate.recipe_id,
                "scoreBp": item.score_bp,
                "reasons": [reason.code for reason in item.reasons],
            }
            for item in replay.suggestions
        ]
        self.assertEqual(actual, expected)
        self.assertEqual(
            replay.suggestions[0].components.inventory_coverage_bp, 6500
        )


class FeatureAssemblyTests(RecommendationTestCase):
    def test_distinct_required_ingredients_optional_lines_and_unresolved_lines(self):
        recipe = self.recipe()
        stocked = CanonicalIngredient.objects.create(
            household=self.household, name="Tomate"
        )
        missing = CanonicalIngredient.objects.create(
            household=self.household, name="Pasta"
        )
        InventoryItem.objects.create(
            household=self.household,
            ingredient=stocked,
            status=InventoryItem.Status.IN_STOCK,
        )
        InventoryItem.objects.create(
            household=self.household,
            ingredient=missing,
            status=InventoryItem.Status.NEEDS_REPLENISHMENT,
        )
        for order in range(2):
            RecipeIngredient.objects.create(
                recipe=recipe,
                canonical_ingredient=stocked,
                source_text="Tomate",
                sort_order=order,
            )
        RecipeIngredient.objects.create(
            recipe=recipe,
            canonical_ingredient=missing,
            source_text="Pasta",
            sort_order=2,
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            source_text="Geheimzutat",
            sort_order=3,
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            source_text="Optional",
            optional=True,
            sort_order=4,
        )

        result = recommend(user=self.user, options=self.options())
        candidate = result.suggestions[0].candidate

        self.assertEqual((candidate.matched, candidate.missing, candidate.unknown), (1, 1, 1))
        self.assertEqual(candidate.unresolved_count, 1)
        self.assertEqual(candidate.total, 3)

    def test_features_include_recency_tags_plan_and_negative_feedback(self):
        recipe = self.recipe()
        tag = RecipeTag.objects.create(household=self.household, name="Schnell")
        RecipeTagAssignment.objects.create(recipe=recipe, tag=tag)
        event = CookEvent.objects.create(
            household=self.household, recipe=recipe, actor=self.user
        )
        cooked_at = timezone.now() - timedelta(days=4)
        CookEvent.objects.filter(id=event.id).update(cooked_at=cooked_at)
        plan = MealPlan.objects.create(
            household=self.household, week_start_date=self.week_start
        )
        MealSlot.objects.create(
            plan=plan,
            date=self.week_start,
            slot=MealSlot.Slot.DINNER,
            recipe=recipe,
        )
        first = recommend(
            user=self.user,
            options=self.options(preferred_tag_ids=(str(tag.id),)),
        )
        record_feedback(
            user=self.user,
            run_id=first.run_id,
            recipe_id=recipe.id,
            outcome=RecommendationFeedback.Outcome.DISMISSED,
            reason=RecommendationFeedback.Reason.NOT_AGAIN,
        )

        result = recommend(
            user=self.user,
            options=self.options(preferred_tag_ids=(str(tag.id),)),
        )
        candidate = result.suggestions[0].candidate

        self.assertEqual(candidate.days_since_cook, 4)
        self.assertEqual(candidate.preferred_tag_matches, 1)
        self.assertEqual(candidate.planned_occurrences, 1)
        self.assertEqual(candidate.feedback_state, "not_again")

    def test_snapshot_is_bounded_and_contains_no_recipe_or_ingredient_text(self):
        recipe = self.recipe(title="Private title")
        ingredient = CanonicalIngredient.objects.create(
            household=self.household, name="Private ingredient"
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            canonical_ingredient=ingredient,
            source_text="Private ingredient",
            sort_order=0,
        )

        result = recommend(user=self.user, options=self.options())
        run = RecommendationRun.objects.get(id=result.run_id)
        encoded = json.dumps(run.snapshot, ensure_ascii=False).encode()

        self.assertLessEqual(len(encoded), MAX_SNAPSHOT_BYTES)
        self.assertNotIn("Private title", encoded.decode())
        self.assertNotIn("Private ingredient", encoded.decode())
        replay = run.replay_snapshot()
        self.assertEqual(replay.suggestions[0].score_bp, result.suggestions[0].score_bp)

    def test_candidate_bound_truncation_and_stable_uuid_tie_break(self):
        recipes = [
            Recipe(
                id=uuid.UUID(int=index + 1),
                household=self.household,
                created_by=self.user,
                source=self.source,
                title=f"Recipe {index}",
                status=Recipe.Status.APPROVED,
            )
            for index in range(MAX_CANDIDATES + 1)
        ]
        Recipe.objects.bulk_create(recipes)

        result = recommend(user=self.user, options=self.options(limit=20))

        self.assertTrue(result.candidate_set_truncated)
        self.assertEqual(result.candidate_count, MAX_CANDIDATES)
        self.assertEqual(
            [item.candidate.recipe_id for item in result.suggestions],
            [str(uuid.UUID(int=index + 1)) for index in range(20)],
        )

    def test_query_count_is_bounded_at_small_and_representative_sizes(self):
        def measured(count):
            Recipe.objects.all().delete()
            Recipe.objects.bulk_create(
                [
                    Recipe(
                        household=self.household,
                        created_by=self.user,
                        source=self.source,
                        title=f"R {index}",
                        status=Recipe.Status.APPROVED,
                    )
                    for index in range(count)
                ]
            )
            with CaptureQueriesContext(connection) as captured:
                recommend(user=self.user, options=self.options())
            return len(captured)

        small = measured(2)
        representative = measured(80)
        self.assertLessEqual(representative, 16)
        self.assertLessEqual(abs(representative - small), 2)


class PersistenceAndFeedbackTests(RecommendationTestCase):
    def test_run_retention_keeps_newest_100_and_nulls_feedback_reference(self):
        recipe = self.recipe()
        old_run = RecommendationRun.objects.create(
            household=self.household,
            requester=self.user,
            snapshot={"candidates": []},
            scoring_version="catalog-v1",
            inventory_snapshot_at=timezone.now(),
            candidate_count=0,
        )
        feedback = RecommendationFeedback.objects.create(
            household=self.household,
            user=self.user,
            recipe=recipe,
            recommendation_run=old_run,
            outcome=RecommendationFeedback.Outcome.OPENED,
        )
        for _ in range(100):
            RecommendationRun.objects.create(
                household=self.household,
                requester=self.user,
                snapshot={"candidates": []},
                scoring_version="catalog-v1",
                inventory_snapshot_at=timezone.now(),
                candidate_count=0,
            )

        self.assertEqual(prune_recommendation_runs(self.household), 1)
        feedback.refresh_from_db()
        self.assertIsNone(feedback.recommendation_run_id)
        self.assertEqual(
            RecommendationRun.objects.filter(household=self.household).count(), 100
        )

    def test_feedback_is_idempotent_and_household_scoped(self):
        recipe = self.recipe()
        result = recommend(user=self.user, options=self.options())

        first, created = record_feedback(
            user=self.user,
            run_id=result.run_id,
            recipe_id=recipe.id,
            outcome=RecommendationFeedback.Outcome.OPENED,
        )
        second, created_again = record_feedback(
            user=self.user,
            run_id=result.run_id,
            recipe_id=recipe.id,
            outcome=RecommendationFeedback.Outcome.OPENED,
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.id, second.id)
        other = Household.objects.create(name="Other")
        other_user = User.objects.create_user(username="other", password="test-pass")
        HouseholdMembership.objects.create(household=other, user=other_user, role="owner")
        with self.assertRaises(ValueError):
            record_feedback(
                user=other_user,
                run_id=result.run_id,
                recipe_id=recipe.id,
                outcome=RecommendationFeedback.Outcome.OPENED,
            )

    def test_model_rejects_oversized_snapshot(self):
        run = RecommendationRun(
            household=self.household,
            requester=self.user,
            snapshot={"payload": "x" * MAX_SNAPSHOT_BYTES},
            scoring_version="catalog-v1",
            inventory_snapshot_at=timezone.now(),
            candidate_count=0,
        )
        with self.assertRaises(ValidationError):
            run.full_clean()

    def test_benchmark_command_reports_required_measurements(self):
        self.recipe()
        output = StringIO()

        call_command(
            "benchmark_recommendations",
            household=str(self.household.id),
            iterations=2,
            stdout=output,
        )

        report = output.getvalue()
        self.assertIn("sql_count median=", report)
        self.assertIn("query median=", report)
        self.assertIn("scoring median=", report)
        self.assertIn("total median=", report)


class RecommendationApiAndUiTests(RecommendationTestCase):
    def test_api_returns_ranked_explainable_suggestions(self):
        recipe = self.recipe()
        ingredient = CanonicalIngredient.objects.create(
            household=self.household, name="Basilikum"
        )
        RecipeIngredient.objects.create(
            recipe=recipe,
            canonical_ingredient=ingredient,
            source_text="Basilikum",
            sort_order=0,
        )
        InventoryItem.objects.create(
            household=self.household,
            ingredient=ingredient,
            status=InventoryItem.Status.IN_STOCK,
        )

        response = self.client.post(
            "/api/v1/recommendations",
            data=json.dumps(
                {
                    "weekStart": self.week_start.isoformat(),
                    "preferredTagIds": [],
                    "limit": 10,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["run"]["scoringVersion"], "catalog-v1")
        self.assertEqual(body["suggestions"][0]["recipeId"], str(recipe.id))
        self.assertEqual(body["suggestions"][0]["matchedIngredients"][0]["name"], "Basilikum")
        self.assertIn("components", body["suggestions"][0])
        self.assertIn("reasons", body["suggestions"][0])

    def test_api_rejects_foreign_tags_and_feedback(self):
        recipe = self.recipe()
        result = recommend(user=self.user, options=self.options())
        other = Household.objects.create(name="Other")
        foreign_tag = RecipeTag.objects.create(household=other, name="Foreign")

        response = self.client.post(
            "/api/v1/recommendations",
            data=json.dumps(
                {
                    "weekStart": self.week_start.isoformat(),
                    "preferredTagIds": [str(foreign_tag.id)],
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)

        foreign_source = RecipeSource.objects.create(household=other)
        foreign_recipe = Recipe.objects.create(
            household=other,
            source=foreign_source,
            created_by=self.user,
            title="Foreign",
            status=Recipe.Status.APPROVED,
        )
        response = self.client.post(
            f"/api/v1/recommendations/{result.run_id}/feedback",
            data=json.dumps(
                {"recipeId": str(foreign_recipe.id), "outcome": "opened"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)
        self.assertFalse(
            RecommendationFeedback.objects.filter(recipe=foreign_recipe).exists()
        )
        self.assertTrue(Recipe.objects.filter(id=recipe.id).exists())

    def test_feedback_endpoint_is_idempotent(self):
        recipe = self.recipe()
        result = recommend(user=self.user, options=self.options())
        url = f"/api/v1/recommendations/{result.run_id}/feedback"
        payload = json.dumps({"recipeId": str(recipe.id), "outcome": "opened"})

        self.assertEqual(
            self.client.post(url, data=payload, content_type="application/json").status_code,
            201,
        )
        self.assertEqual(
            self.client.post(url, data=payload, content_type="application/json").status_code,
            200,
        )

    def test_html_page_navigation_and_planner_preselection(self):
        recipe = self.recipe()

        page = self.client.get("/recommendations/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, recipe.title)
        self.assertContains(page, "Warum dieses Rezept?")
        run = RecommendationRun.objects.latest("created_at")

        planner = self.client.get(
            f"/plan/{self.week_start.isoformat()}/",
            {"recipe": recipe.id, "recommendation_run": run.id},
        )
        self.assertContains(
            planner,
            f'<option value="{recipe.id}" selected>',
            html=False,
        )
        self.assertContains(planner, f'name="recommendation_run" value="{run.id}"')

    def test_opened_and_planned_outcomes_are_recorded_without_blocking_actions(self):
        recipe = self.recipe()
        result = recommend(user=self.user, options=self.options())

        detail = self.client.get(
            f"/recipes/{recipe.id}/",
            {"recommendation_run": result.run_id},
        )
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(
            RecommendationFeedback.objects.filter(
                recommendation_run_id=result.run_id,
                outcome=RecommendationFeedback.Outcome.OPENED,
            ).exists()
        )

        response = self.client.post(
            f"/plan/{self.week_start.isoformat()}/slots/",
            {
                "date": self.week_start.isoformat(),
                "slot": "dinner",
                "entry_type": "recipe",
                "recipe_id": recipe.id,
                "recommendation_run": result.run_id,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            RecommendationFeedback.objects.filter(
                recommendation_run_id=result.run_id,
                outcome=RecommendationFeedback.Outcome.PLANNED,
            ).exists()
        )

        response = self.client.post(
            f"/plan/{self.week_start.isoformat()}/slots/",
            {
                "date": self.week_start.isoformat(),
                "slot": "lunch",
                "entry_type": "recipe",
                "recipe_id": recipe.id,
                "recommendation_run": "invalid",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MealSlot.objects.filter(recipe=recipe).count(), 2)
