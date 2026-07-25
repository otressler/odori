"""Packet 2D: the Milestone 2 vertical slice, driven through the real HTML views.

Plan a week, generate a shopping list, purchase into inventory, cook the meal, and
confirm the history and planned-stock protection that follow.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryEvent, InventoryItem
from planning.models import CookEvent, MealSlot
from planning.services import week_start_for
from recipes.models import Recipe, RecipeIngredient, RecipeSource, RecipeStep
from shopping.models import ShoppingItem, ShoppingList


class Milestone2WalkthroughTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="nonna", password="pass")
        self.household = Household.objects.create(name="Casa Odori")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")

        self.beans = CanonicalIngredient.objects.create(household=self.household, name="Bohnen")
        self.kale = CanonicalIngredient.objects.create(household=self.household, name="Schwarzkohl")
        self.kale_stock = InventoryItem.objects.create(
            household=self.household, ingredient=self.kale, status="in_stock"
        )

        source = RecipeSource.objects.create(household=self.household)
        self.recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=source,
            title="Ribollita",
            servings=2,
            status=Recipe.Status.APPROVED,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            sort_order=0,
            source_text="Bohnen",
            amount=300,
            unit="g",
            canonical_ingredient=self.beans,
        )
        RecipeIngredient.objects.create(
            recipe=self.recipe,
            sort_order=1,
            source_text="Schwarzkohl",
            amount=1,
            unit="Bund",
            canonical_ingredient=self.kale,
        )
        RecipeStep.objects.create(recipe=self.recipe, sort_order=0, body="Bohnen weich kochen.")
        RecipeStep.objects.create(recipe=self.recipe, sort_order=1, body="Alles ziehen lassen.")

        # Plan for tomorrow, so the meal is genuinely still ahead of us.
        self.date = timezone.localdate() + timedelta(days=1)
        self.week_start = week_start_for(self.date)
        self.client.force_login(self.user)

    def test_plan_shop_cook(self):
        # 1. Plan the meal for four instead of the recipe's two.
        response = self.client.post(
            f"/plan/{self.week_start.isoformat()}/slots/",
            {
                "entry_type": "recipe",
                "recipe_id": str(self.recipe.id),
                "date": self.date.isoformat(),
                "slot": "dinner",
                "servings": "4",
            },
        )
        self.assertEqual(response.status_code, 302)
        slot = MealSlot.objects.get(plan__household=self.household)
        self.assertEqual(slot.servings, 4)
        self.assertContains(self.client.get(f"/plan/{self.week_start.isoformat()}/"), "Ribollita")

        # 2. Calculate the shopping list from that plan.
        response = self.client.post(
            "/shopping/generate/", {"week_start": self.week_start.isoformat()}
        )
        self.assertEqual(response.status_code, 302)
        shopping_list = ShoppingList.objects.get(household=self.household)
        labels = set(shopping_list.items.values_list("label", flat=True))
        self.assertIn("Bohnen", labels)
        self.assertNotIn("Schwarzkohl", labels, "in-stock ingredients stay off the list")

        beans_item = shopping_list.items.get(canonical_ingredient=self.beans)
        self.assertEqual(beans_item.quantity_components[0]["amount"], "600")

        # 3. Regenerating is stable rather than duplicative.
        self.client.post("/shopping/generate/", {"week_start": self.week_start.isoformat()})
        self.assertEqual(shopping_list.items.count(), 1)
        self.assertEqual(ShoppingList.objects.filter(household=self.household).count(), 1)

        # 4. Buying the item stocks the pantry in one transaction.
        beans_item.refresh_from_db()
        self.client.post(
            f"/shopping/items/{beans_item.id}/purchase/", {"version": beans_item.version}
        )
        beans_item.refresh_from_db()
        self.assertEqual(beans_item.state, ShoppingItem.State.PURCHASED)
        beans_stock = InventoryItem.objects.get(ingredient=self.beans)
        self.assertEqual(beans_stock.status, "in_stock")
        self.assertEqual(
            InventoryEvent.objects.filter(
                item=beans_stock, origin=InventoryEvent.Origin.PURCHASE
            ).count(),
            1,
        )

        # 5. The pantry protects stock the plan still needs.
        response = self.client.post(
            f"/pantry/{self.beans.id}/status/",
            {"status": "needs_replenishment", "version": beans_stock.version},
        )
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Ribollita", status_code=409)
        beans_stock.refresh_from_db()
        self.assertEqual(beans_stock.status, "in_stock")

        # 6. Cook it, depleting only what the cook actually selected.
        self.assertContains(self.client.get(f"/plan/slots/{slot.id}/kitchen/"), "Zubereitung")
        self.client.post(
            f"/plan/slots/{slot.id}/cook/",
            {"slot_version": slot.version, "deplete": [str(self.beans.id)]},
        )
        slot.refresh_from_db()
        self.assertIsNotNone(slot.cooked_at)
        self.assertEqual(CookEvent.objects.filter(meal_slot=slot).count(), 1)

        beans_stock.refresh_from_db()
        self.assertEqual(beans_stock.status, "needs_replenishment")
        cook_event = InventoryEvent.objects.get(
            item=beans_stock, origin=InventoryEvent.Origin.COOK_RECIPE
        )
        self.assertEqual(cook_event.meal_slot_id, slot.id)

        self.kale_stock.refresh_from_db()
        self.assertEqual(self.kale_stock.status, "in_stock", "unselected stock is untouched")

        # 7. The history tells the story afterwards.
        self.assertContains(self.client.get("/plan/history/"), "Ribollita")

        # 8. A cooked meal no longer holds stock hostage.
        response = self.client.post(
            f"/pantry/{self.kale.id}/status/",
            {"status": "needs_replenishment", "version": self.kale_stock.version},
        )
        self.assertEqual(response.status_code, 302)
        self.kale_stock.refresh_from_db()
        self.assertEqual(self.kale_stock.status, "needs_replenishment")

    def test_confirming_planned_use_lets_the_change_through(self):
        self.client.post(
            f"/plan/{self.week_start.isoformat()}/slots/",
            {
                "entry_type": "recipe",
                "recipe_id": str(self.recipe.id),
                "date": self.date.isoformat(),
                "slot": "dinner",
            },
        )
        response = self.client.post(
            f"/pantry/{self.kale.id}/status/",
            {
                "status": "needs_replenishment",
                "version": self.kale_stock.version,
                "confirm_planned_use": "true",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.kale_stock.refresh_from_db()
        self.assertEqual(self.kale_stock.status, "needs_replenishment")
