"""The demo seed must stay repeatable and give the week planner something to suggest."""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient
from planning.services import current_week_start, get_or_create_plan
from recipes.models import Recipe
from recipes.recommendations import recommend_for_user


class SeedDemoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dev", password="pass")
        self.household = Household.objects.create(name="Development")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")

    def test_seeding_twice_creates_nothing_twice(self):
        call_command("seed_demo", stdout=StringIO())
        counts = Recipe.objects.count(), CanonicalIngredient.objects.count()
        call_command("seed_demo", stdout=StringIO())

        self.assertEqual((Recipe.objects.count(), CanonicalIngredient.objects.count()), counts)
        self.assertEqual(Recipe.objects.filter(status=Recipe.Status.APPROVED).count(), 10)

    def test_seeded_week_gets_planner_suggestions(self):
        call_command("seed_demo", stdout=StringIO())
        self.client.force_login(self.user)
        week_start = current_week_start()

        response = self.client.get(f"/plan/{week_start.isoformat()}/")

        self.assertEqual(len(response.context["suggestions"]), 4)
        plan = get_or_create_plan(user=self.user, week_start=week_start)
        by_title = {
            item.recipe.title: item
            for item in recommend_for_user(user=self.user, plan=plan).suggestions
        }
        self.assertIn("Feta", by_title["Shakshuka"].shared_ingredients)
        self.assertTrue(by_title["Kritharaki-Auflauf mit Feta"].planned_this_week)
