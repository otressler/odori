import json

from django.test import TestCase

from core.models import Household, HouseholdMembership, User

from .models import CanonicalIngredient, InventoryEvent, InventoryItem


class PantryApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mara", password="pass")
        self.household = Household.objects.create(name="Mara")
        HouseholdMembership.objects.create(household=self.household, user=self.user, role="owner")
        self.ingredient = CanonicalIngredient.objects.create(
            household=self.household, name="Tomate"
        )
        self.client.force_login(self.user)

    def patch_inventory(self, body):
        return self.client.patch(
            "/api/v1/inventory", json.dumps(body), content_type="application/json"
        )

    def test_transition_creates_exactly_one_event(self):
        response = self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "in_stock", "version": 1}
                ]
            }
        )
        self.assertEqual(response.status_code, 200)
        item = InventoryItem.objects.get(ingredient=self.ingredient)
        self.assertEqual(item.status, "in_stock")
        self.assertEqual(InventoryEvent.objects.filter(item=item).count(), 1)

    def test_stale_inventory_version_conflicts(self):
        self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "in_stock", "version": 1}
                ]
            }
        )
        response = self.patch_inventory(
            {
                "items": [
                    {"ingredientId": str(self.ingredient.id), "status": "unknown", "version": 1}
                ]
            }
        )
        self.assertEqual(response.status_code, 409)

    def test_other_household_ingredient_is_hidden(self):
        other_household = Household.objects.create(name="Other")
        other = CanonicalIngredient.objects.create(household=other_household, name="Basilikum")
        response = self.patch_inventory(
            {"items": [{"ingredientId": str(other.id), "status": "in_stock", "version": 1}]}
        )
        self.assertEqual(response.status_code, 404)

    def test_inventory_page_creates_an_ingredient_and_initial_status(self):
        response = self.client.post(
            "/pantry/add/", {"name": "Basilikum", "status": InventoryItem.Status.IN_STOCK}
        )

        self.assertRedirects(response, "/pantry/")
        item = InventoryItem.objects.get(ingredient__name="Basilikum")
        self.assertEqual(item.status, InventoryItem.Status.IN_STOCK)

    def test_inventory_page_updates_status_with_version(self):
        item = InventoryItem.objects.create(
            household=self.household,
            ingredient=self.ingredient,
            status=InventoryItem.Status.UNKNOWN,
        )
        response = self.client.post(
            f"/pantry/{self.ingredient.id}/status/",
            {"status": InventoryItem.Status.NEEDS_REPLENISHMENT, "version": item.version},
        )

        self.assertRedirects(response, "/pantry/")
        item.refresh_from_db()
        self.assertEqual(item.status, InventoryItem.Status.NEEDS_REPLENISHMENT)
