from django.test import TestCase

from .models import Household, HouseholdMembership, User


class HouseholdManagementTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create(username="owner")
        self.member = User.objects.create(username="member")
        self.household = Household.objects.create(name="Casa")
        HouseholdMembership.objects.create(
            household=self.household, user=self.owner, role=HouseholdMembership.Role.OWNER
        )
        HouseholdMembership.objects.create(
            household=self.household, user=self.member, role=HouseholdMembership.Role.MEMBER
        )
        self.client.force_login(self.owner)

    def test_owner_can_appoint_and_kick_member(self):
        response = self.client.post(
            f"/households/members/{self.member.id}/appoint-admin/",
        )
        self.assertRedirects(response, "/households/")
        self.assertEqual(
            HouseholdMembership.objects.get(household=self.household, user=self.member).role,
            HouseholdMembership.Role.ADMIN,
        )

        HouseholdMembership.objects.filter(
            household=self.household, user=self.member
        ).update(role=HouseholdMembership.Role.MEMBER)
        response = self.client.post(f"/households/members/{self.member.id}/kick/")
        self.assertRedirects(response, "/households/")
        self.assertFalse(
            HouseholdMembership.objects.filter(household=self.household, user=self.member).exists()
        )

    def test_last_admin_cannot_leave(self):
        response = self.client.post("/households/leave/")
        self.assertRedirects(response, "/households/")
        self.assertTrue(
            HouseholdMembership.objects.filter(household=self.household, user=self.owner).exists()
        )

    def test_member_cannot_manage_household(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get("/households/").status_code, 403)
        response = self.client.post(f"/households/members/{self.owner.id}/kick/")
        self.assertEqual(response.status_code, 403)

    def test_owner_can_delete_household(self):
        response = self.client.post("/households/delete/")
        self.assertRedirects(response, "/households/new/")
        self.assertFalse(Household.objects.filter(id=self.household.id).exists())

    def test_owner_can_delete_household_with_data(self):
        from datetime import date

        from pantry.models import CanonicalIngredient, InventoryEvent, InventoryItem
        from planning.models import CookEvent, MealPlan, MealSlot
        from recipes.models import (
            ImportSource,
            Recipe,
            RecipeImportJob,
            RecipeIngredient,
            RecipeSource,
        )
        from shopping.models import ShoppingItem, ShoppingList

        tomato = CanonicalIngredient.objects.create(household=self.household, name="Tomate")
        CanonicalIngredient.objects.create(
            household=self.household, name="Tomaten", merged_into=tomato, active=False
        )
        import_source = ImportSource.objects.create(
            household=self.household,
            source_type=ImportSource.Type.URL,
            url="https://example.test/recipe",
            content_hash="delete-test",
        )
        RecipeImportJob.objects.create(household=self.household, source=import_source)
        source = RecipeSource.objects.create(
            household=self.household,
            type=RecipeSource.Type.IMPORTED,
            import_source=import_source,
        )
        recipe = Recipe.objects.create(
            household=self.household, created_by=self.owner, source=source, title="Sugo"
        )
        RecipeIngredient.objects.create(
            recipe=recipe, canonical_ingredient=tomato, source_text="Tomaten", sort_order=0
        )
        plan = MealPlan.objects.create(household=self.household, week_start_date=date(2026, 1, 5))
        slot = MealSlot.objects.create(
            plan=plan, date=date(2026, 1, 5), slot=MealSlot.Slot.DINNER, recipe=recipe
        )
        CookEvent.objects.create(
            household=self.household, recipe=recipe, meal_slot=slot, actor=self.owner
        )
        item = InventoryItem.objects.create(household=self.household, ingredient=tomato)
        InventoryEvent.objects.create(
            item=item,
            previous_status=InventoryItem.Status.UNKNOWN,
            new_status=InventoryItem.Status.AVAILABLE,
            actor=self.owner,
        )
        shopping_list = ShoppingList.objects.create(household=self.household, name="Einkauf")
        ShoppingItem.objects.create(
            shopping_list=shopping_list,
            canonical_ingredient=tomato,
            label="Tomaten",
            grouping_key="tomate",
        )

        response = self.client.post("/households/delete/")

        self.assertRedirects(response, "/households/new/")
        self.assertFalse(Household.objects.filter(id=self.household.id).exists())
        self.assertFalse(Recipe.objects.exists())
        self.assertFalse(CanonicalIngredient.objects.exists())
        self.assertFalse(ImportSource.objects.exists())
