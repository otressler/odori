"""Every redesigned page must render, both empty and with real data in place."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Household, HouseholdInvitation, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryItem
from planning.models import MealPlan, MealSlot
from planning.services import add_slot, current_week_start
from recipes.models import Recipe, RecipeIngredient, RecipeSource, RecipeStep
from shopping.models import ShoppingItem, ShoppingList
from shopping.services import add_manual_item, generate_from_plan


class PageRenderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nonna", password="pass")
        self.household = Household.objects.create(name="Casa Odori")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        InventoryItem.objects.create(
            household=self.household, ingredient=self.tomato, status="unavailable"
        )
        source = RecipeSource.objects.create(household=self.household)
        self.recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Ribollita",
            servings=4,
            status=Recipe.Status.APPROVED,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            sort_order=0,
            source_text="Tomaten",
            amount=400,
            unit="g",
            canonical_ingredient=self.tomato,
        )
        RecipeStep.objects.create(recipe=self.recipe, sort_order=0, body="Langsam ziehen lassen.")
        self.draft = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Entwurf",
            status=Recipe.Status.DRAFT,
        )
        self.client.force_login(self.user)

    def paths(self):
        week = current_week_start().isoformat()
        return [
            "/",
            "/recipes/",
            "/recipes/new/",
            f"/recipes/{self.recipe.id}/",
            f"/recipes/{self.draft.id}/edit/",
            "/pantry/",
            "/plan/",
            f"/plan/{week}/",
            "/plan/history/",
            "/shopping/",
        ]

    def test_pages_render_when_the_household_is_empty(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_pages_render_with_a_plan_and_a_shopping_list(self):
        week_start = current_week_start()
        slot = add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=1),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
            servings=6,
        )
        add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=2),
            slot="lunch",
            entry_type=MealSlot.EntryType.NOTE,
            notes="Auswärts essen",
        )
        shopping_list = generate_from_plan(user=self.user, week_start=week_start)
        add_manual_item(user=self.user, list_id=shopping_list.id, label="Spülmittel")

        extra = [f"/plan/slots/{slot.id}/kitchen/", f"/shopping/{shopping_list.id}/"]
        for path in self.paths() + extra:
            with self.subTest(path=path):
                # /shopping/ deliberately redirects to the active list.
                response = self.client.get(path, follow=True)
                self.assertEqual(response.status_code, 200, path)

    def test_the_home_page_counts_open_shopping_items(self):
        week_start = current_week_start()
        add_slot(
            user=self.user,
            week_start=week_start,
            date=week_start + timedelta(days=1),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        generate_from_plan(user=self.user, week_start=week_start)
        response = self.client.get("/")
        self.assertContains(response, "Offen auf der Liste")
        self.assertEqual(response.context["open_items"], 1)

    def test_home_page_does_not_create_a_meal_plan(self):
        self.assertFalse(MealPlan.objects.filter(household=self.household).exists())

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(MealPlan.objects.filter(household=self.household).exists())

    def test_home_page_links_to_uncategorized_review(self):
        response = self.client.get("/")

        self.assertEqual(response.context["uncategorized_count"], 1)
        self.assertContains(response, "/pantry/categories/review/")
        self.assertContains(response, "Odori hat Vorschläge vorbereitet")

    def test_home_page_reminds_about_open_item_needed_today(self):
        week_start = current_week_start()
        add_slot(
            user=self.user,
            week_start=week_start,
            date=timezone.localdate(),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        generate_from_plan(user=self.user, week_start=week_start)

        response = self.client.get("/")

        self.assertContains(response, "Bald benötigt")
        self.assertContains(response, "Heute")
        self.assertContains(response, "Tomate")

    def test_home_page_reminds_about_open_item_needed_tomorrow(self):
        shopping_list = ShoppingList.objects.create(household=self.household, name="Erledigungen")
        ShoppingItem.objects.create(
            shopping_list=shopping_list,
            canonical_ingredient=self.tomato,
            label="Tomate",
            grouping_key="ingredient:tomate",
            recipe_refs=[
                {
                    "title": "Ribollita",
                    "date": (timezone.localdate() + timedelta(days=1)).isoformat(),
                }
            ],
        )

        response = self.client.get("/")

        self.assertContains(response, "Bald benötigt")
        self.assertContains(response, "Morgen")
        self.assertContains(response, "Ribollita")

    def test_todays_meal_shows_a_cook_button(self):
        add_slot(
            user=self.user,
            week_start=current_week_start(),
            date=timezone.localdate(),
            slot="dinner",
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=self.recipe.id,
        )
        self.assertContains(self.client.get("/"), "Kochen")

    def test_sign_in_page_renders_for_anonymous_visitors(self):
        self.client.logout()
        response = self.client.get("/accounts/login/")
        self.assertContains(response, "Willkommen zurück")

    def test_anonymous_home_is_the_landing_page(self):
        self.client.logout()

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deine private Küche")
        self.assertContains(response, "toskanischen Küche")

    def test_user_without_household_is_sent_to_onboarding(self):
        newcomer = User.objects.create_user(username="newcomer", password="pass")
        self.client.force_login(newcomer)

        response = self.client.get("/")

        self.assertRedirects(response, "/households/new/")

    def test_user_can_create_a_household(self):
        newcomer = User.objects.create_user(username="newcomer", password="pass")
        self.client.force_login(newcomer)

        response = self.client.post("/households/new/", {"name": "Casa Nuova"})

        membership = HouseholdMembership.objects.get(user=newcomer)
        self.assertEqual(membership.household.name, "Casa Nuova")
        self.assertEqual(membership.role, HouseholdMembership.Role.OWNER)
        self.assertRedirects(response, "/")

    def test_household_admin_can_open_new_household_flow(self):
        response = self.client.get("/households/")

        self.assertContains(response, 'href="/households/new/"')

        response = self.client.post("/households/new/", {"name": "Casa Nuova"})

        membership = HouseholdMembership.objects.get(user=self.user, household__name="Casa Nuova")
        self.assertEqual(membership.role, HouseholdMembership.Role.OWNER)
        self.assertRedirects(response, "/")

    def test_user_can_join_with_registration_code(self):
        invitation = HouseholdInvitation.objects.create(
            household=self.household,
            created_by=self.user,
        )
        newcomer = User.objects.create_user(username="newcomer", password="pass")
        self.client.force_login(newcomer)

        response = self.client.post(
            "/households/new/", {"registration_code": invitation.registration_code}
        )

        self.assertTrue(
            HouseholdMembership.objects.filter(user=newcomer, household=self.household).exists()
        )
        self.assertRedirects(response, "/")

    def test_invitation_link_requires_a_post_confirmation(self):
        invitation = HouseholdInvitation.objects.create(
            household=self.household,
            created_by=self.user,
        )
        newcomer = User.objects.create_user(username="newcomer", password="pass")
        self.client.force_login(newcomer)

        response = self.client.get(f"/households/join/{invitation.token}/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            HouseholdMembership.objects.filter(user=newcomer, household=self.household).exists()
        )

        response = self.client.post(f"/households/join/{invitation.token}/")

        self.assertTrue(
            HouseholdMembership.objects.filter(user=newcomer, household=self.household).exists()
        )
        self.assertRedirects(response, "/")

    def test_user_can_switch_current_household(self):
        second = Household.objects.create(name="Casa Due")
        HouseholdMembership.objects.create(user=self.user, household=second, role="member")

        response = self.client.post("/households/switch/", {"household_id": second.id})

        self.assertRedirects(response, "/")
        self.assertEqual(self.client.get("/").context["household"], second)

    def test_signing_in_lands_on_the_home_page(self):
        self.client.logout()
        response = self.client.post("/accounts/login/", {"username": "nonna", "password": "pass"})
        self.assertRedirects(response, "/")
