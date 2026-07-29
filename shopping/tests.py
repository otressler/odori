from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.files.base import ContentFile
from django.test import TestCase
from django.test import override_settings

from core.models import Household, HouseholdMembership, User
from pantry.models import CanonicalIngredient, InventoryEvent, InventoryItem
from planning.models import MealSlot
from planning.services import add_slot, current_week_start, get_or_create_plan, mark_cooked
from recipes.models import Recipe, RecipeIngredient, RecipeSource

from .models import ShoppingItem, ShoppingList
from .services import add_manual_item, generate_from_plan, purchase_item, set_item_state
from .units import normalize_unit


class ShoppingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="luca", password="pass")
        self.household = Household.objects.create(name="Casa Luca")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.flour = CanonicalIngredient.objects.create(household=self.household, name="Mehl")
        self.oil = CanonicalIngredient.objects.create(household=self.household, name="Olivenöl")
        self.week_start = current_week_start()
        self.plan = get_or_create_plan(user=self.user, week_start=self.week_start)
        self.source = RecipeSource.objects.create(household=self.household)
        self.client.force_login(self.user)

    def recipe_with(self, title, lines, servings=2):
        recipe = Recipe.objects.create(
            household=self.household,
            created_by=self.user,
            source=self.source,
            title=title,
            servings=servings,
            status=Recipe.Status.APPROVED,
        )
        for position, (text, amount, unit, canonical) in enumerate(lines):
            RecipeIngredient.objects.create(
                recipe=recipe,
                sort_order=position,
                source_text=text,
                amount=amount,
                unit=unit,
                canonical_ingredient=canonical,
            )
        return recipe

    def plan_recipe(self, recipe, *, day_offset=0, slot="dinner", servings=None):
        return add_slot(
            user=self.user,
            week_start=self.week_start,
            date=self.week_start + timedelta(days=day_offset),
            slot=slot,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe_id=recipe.id,
            servings=servings,
        )

    def generate(self, **kwargs):
        return generate_from_plan(user=self.user, week_start=self.week_start, **kwargs)


class CalculationTests(ShoppingTestCase):
    def test_same_ingredient_across_recipes_is_summed_once(self):
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        self.plan_recipe(
            self.recipe_with("Focaccia", [("Mehl", 250, "g", self.flour)]), day_offset=1
        )
        shopping_list = self.generate()
        item = shopping_list.items.get(canonical_ingredient=self.flour)
        self.assertEqual(item.quantity_components[0]["amount"], "750")
        self.assertEqual(item.quantity_components[0]["unit"], "g")
        self.assertEqual(len(item.recipe_refs), 2)

    def test_quantities_scale_with_planned_servings(self):
        recipe = self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)], servings=2)
        self.plan_recipe(recipe, servings=4)
        item = self.generate().items.get(canonical_ingredient=self.flour)
        self.assertEqual(item.quantity_components[0]["amount"], "1000")

    def test_incompatible_units_are_listed_side_by_side(self):
        # No conversion in this release: 1 kg and 500 g must not silently merge.
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        self.plan_recipe(self.recipe_with("Pizza", [("Mehl", 1, "kg", self.flour)]), day_offset=1)
        item = self.generate().items.get(canonical_ingredient=self.flour)
        units = {component["unit"] for component in item.quantity_components}
        self.assertEqual(units, {"g", "kg"})

    def test_unit_spellings_are_normalised(self):
        self.assertEqual(normalize_unit("Gramm"), "g")
        self.assertEqual(normalize_unit("EL"), "el")
        self.assertEqual(normalize_unit("Stück"), "stk")
        self.plan_recipe(self.recipe_with("A", [("Mehl", 100, "Gramm", self.flour)]))
        self.plan_recipe(self.recipe_with("B", [("Mehl", 100, "g", self.flour)]), day_offset=1)
        item = self.generate().items.get(canonical_ingredient=self.flour)
        self.assertEqual(len(item.quantity_components), 1)

    def test_ingredients_without_amounts_stay_useful(self):
        self.plan_recipe(self.recipe_with("Salat", [("Salz", None, "", None)]))
        item = self.generate().items.get(label="Salz")
        self.assertEqual(item.quantity_components[0]["amount"], None)
        self.assertIn("nach Bedarf", item.quantity_text)

    def test_in_stock_ingredients_are_left_out(self):
        InventoryItem.objects.create(
            household=self.household, ingredient=self.flour, status="in_stock"
        )
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate()
        self.assertFalse(shopping_list.items.filter(canonical_ingredient=self.flour).exists())

    def test_in_stock_can_be_included_on_request(self):
        InventoryItem.objects.create(
            household=self.household, ingredient=self.flour, status="in_stock"
        )
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate(include_in_stock=True)
        self.assertTrue(shopping_list.items.filter(canonical_ingredient=self.flour).exists())

    def test_unknown_stock_is_included_and_flagged(self):
        InventoryItem.objects.create(
            household=self.household, ingredient=self.flour, status="unknown"
        )
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        item = self.generate().items.get(canonical_ingredient=self.flour)
        self.assertTrue(item.needs_confirmation)

    def test_cooked_meals_drop_out_of_the_calculation(self):
        slot = self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        mark_cooked(user=self.user, slot_id=slot.id, slot_version=slot.version)
        self.assertFalse(self.generate().items.exists())


class RegenerationTests(ShoppingTestCase):
    def setUp(self):
        super().setUp()
        self.plan_recipe(
            self.recipe_with(
                "Brot", [("Mehl", 500, "g", self.flour), ("Olivenöl", 2, "EL", self.oil)]
            )
        )

    def test_regeneration_is_stable(self):
        first = self.generate()
        labels = sorted(first.items.values_list("label", flat=True))
        second = self.generate()
        self.assertEqual(first.id, second.id)
        self.assertEqual(sorted(second.items.values_list("label", flat=True)), labels)
        self.assertEqual(ShoppingList.objects.count(), 1)

    def test_manual_items_survive_regeneration(self):
        shopping_list = self.generate()
        add_manual_item(user=self.user, list_id=shopping_list.id, label="Spülmittel")
        self.generate()
        self.assertTrue(shopping_list.items.filter(label="Spülmittel").exists())

    def test_purchased_items_are_not_resurrected(self):
        shopping_list = self.generate()
        item = shopping_list.items.get(canonical_ingredient=self.flour)
        purchase_item(user=self.user, item_id=item.id, version=item.version)
        self.generate()
        remaining = shopping_list.items.filter(canonical_ingredient=self.flour)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().state, ShoppingItem.State.PURCHASED)

    def test_skipped_items_stay_skipped(self):
        shopping_list = self.generate()
        item = shopping_list.items.get(canonical_ingredient=self.flour)
        set_item_state(
            user=self.user,
            item_id=item.id,
            version=item.version,
            state=ShoppingItem.State.SKIPPED,
        )
        self.generate()
        item.refresh_from_db()
        self.assertEqual(item.state, ShoppingItem.State.SKIPPED)

    def test_removing_a_meal_removes_its_open_lines(self):
        shopping_list = self.generate()
        self.assertEqual(shopping_list.items.count(), 2)
        MealSlot.objects.all().delete()
        self.generate()
        self.assertEqual(shopping_list.items.count(), 0)

    def test_regeneration_bumps_the_list_version(self):
        shopping_list = self.generate()
        before = shopping_list.version
        self.generate()
        shopping_list.refresh_from_db()
        self.assertGreater(shopping_list.version, before)


class PurchaseTests(ShoppingTestCase):
    def setUp(self):
        super().setUp()
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        self.list = self.generate()
        self.item = self.list.items.get(canonical_ingredient=self.flour)

    def test_purchase_stocks_the_pantry(self):
        purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)
        self.item.refresh_from_db()
        self.assertEqual(self.item.state, ShoppingItem.State.PURCHASED)
        inventory = InventoryItem.objects.get(ingredient=self.flour)
        self.assertEqual(inventory.status, "in_stock")
        self.assertEqual(
            InventoryEvent.objects.get(item=inventory).origin, InventoryEvent.Origin.PURCHASE
        )

    def test_purchase_clears_the_confirmation_flag(self):
        self.item.needs_confirmation = True
        self.item.save(update_fields=["needs_confirmation"])
        purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)
        self.item.refresh_from_db()
        self.assertFalse(self.item.needs_confirmation)

    def test_a_failing_pantry_write_rolls_the_purchase_back(self):
        with mock.patch("shopping.services.record_purchase", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)
        self.item.refresh_from_db()
        self.assertEqual(self.item.state, ShoppingItem.State.OPEN)
        self.assertFalse(InventoryItem.objects.filter(ingredient=self.flour).exists())

    def test_purchase_never_asks_about_planned_use(self):
        InventoryItem.objects.create(
            household=self.household, ingredient=self.flour, status="needs_replenishment"
        )
        purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)
        self.assertEqual(InventoryItem.objects.get(ingredient=self.flour).status, "in_stock")

    def test_unlinked_items_do_not_touch_the_pantry(self):
        manual = add_manual_item(user=self.user, list_id=self.list.id, label="Servietten")
        purchase_item(user=self.user, item_id=manual.id, version=manual.version)
        self.assertEqual(InventoryItem.objects.count(), 0)

    def test_stale_version_is_refused(self):
        purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)
        with self.assertRaises(Exception):
            purchase_item(user=self.user, item_id=self.item.id, version=self.item.version)


class ShoppingAuthorizationTests(ShoppingTestCase):
    def setUp(self):
        super().setUp()
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        self.list = self.generate()
        self.item = self.list.items.first()
        self.outsider = User.objects.create_user(username="fremd", password="pass")
        other = Household.objects.create(name="Andere")
        HouseholdMembership.objects.create(household=other, user=self.outsider, role="owner")

    def test_other_household_cannot_open_the_list(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/shopping/{self.list.id}/")
        self.assertEqual(response.status_code, 404)

    def test_other_household_cannot_purchase_an_item(self):
        self.client.force_login(self.outsider)
        response = self.client.post(
            f"/api/v1/shopping-lists/{self.list.id}/items/{self.item.id}/purchase"
        )
        self.assertEqual(response.status_code, 404)
        self.item.refresh_from_db()
        self.assertEqual(self.item.state, ShoppingItem.State.OPEN)

    def test_anonymous_users_are_sent_to_the_sign_in_page(self):
        self.client.logout()
        response = self.client.get("/shopping/")
        self.assertEqual(response.status_code, 302)


class ShoppingPageTests(ShoppingTestCase):
    def test_empty_state_points_at_the_planner(self):
        response = self.client.get("/shopping/")
        self.assertContains(response, "Zum Wochenplan")

    def test_generated_list_renders_quantities(self):
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate()
        response = self.client.get(f"/shopping/{shopping_list.id}/")
        self.assertContains(response, "Mehl")
        self.assertContains(response, "500")

    @override_settings(DEBUG=False)
    def test_generated_list_renders_icons_through_the_authenticated_endpoint(self):
        self.flour.icon.save("mehl.png", ContentFile(b"icon"), save=True)
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate()

        response = self.client.get(f"/shopping/{shopping_list.id}/")

        self.assertContains(response, f'/pantry/icons/{self.flour.id}/')

    def test_list_groups_every_state_by_category_and_offers_compact_view(self):
        from pantry.models import IngredientCategory

        category = IngredientCategory.objects.create(household=self.household, name="Backwaren")
        self.flour.category = category
        self.flour.save(update_fields=["category"])
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate()
        item = shopping_list.items.get(canonical_ingredient=self.flour)
        set_item_state(
            user=self.user,
            item_id=item.id,
            version=item.version,
            state=ShoppingItem.State.PURCHASED,
        )
        add_manual_item(
            user=self.user,
            list_id=shopping_list.id,
            label="Hefe",
            ingredient_id=self.flour.id,
        )

        response = self.client.get(f"/shopping/{shopping_list.id}/")

        self.assertContains(response, 'class="shopping-category-list"', count=2)
        self.assertContains(response, "Backwaren", count=2)
        self.assertContains(response, 'data-shopping-view="compact"')

    def test_generated_list_renders_the_earliest_required_date(self):
        self.plan_recipe(self.recipe_with("Brot", [("Mehl", 500, "g", self.flour)]))
        shopping_list = self.generate()

        response = self.client.get(f"/shopping/{shopping_list.id}/")

        self.assertContains(response, "Benötigt am")
        self.assertContains(response, "Brot")

    def test_decimal_amounts_are_not_mangled(self):
        self.plan_recipe(self.recipe_with("Sugo", [("Olivenöl", Decimal("2.5"), "EL", self.oil)]))
        item = self.generate().items.get(canonical_ingredient=self.oil)
        self.assertEqual(item.quantity_components[0]["amount"], "2.5")
