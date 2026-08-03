import json

from django.core import management
from django.core.management.base import CommandError
from django.test import Client, TestCase

from .models import Household, HouseholdMembership, User


class IdentityAndIsolationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="mara", password="safe-pass", display_name="Mara"
        )
        self.household = Household.objects.create(name="Mara zuhause")
        HouseholdMembership.objects.create(
            household=self.household, user=self.user, role=HouseholdMembership.Role.OWNER
        )
        self.other = Household.objects.create(name="Other")

    def test_unauthenticated_home_is_public(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Deine private Küche")

    def test_csrf_blocks_mutating_api_request(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            "/api/v1/ingredients",
            data=json.dumps({"name": "Tomate"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_readiness_is_available(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)
        self.assertEqual(self.client.get("/health/ready").status_code, 200)

    def test_set_user_password_updates_a_hashed_password(self):
        management.call_command(
            "set_user_password", "mara", password="A1!new-secret"
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("A1!new-secret"))
        self.assertFalse(self.user.password == "A1!new-secret")

    def test_set_user_password_rejects_a_weak_password(self):
        original_password = self.user.password

        with self.assertRaises(CommandError):
            management.call_command("set_user_password", "mara", password="a")

        self.user.refresh_from_db()
        self.assertEqual(self.user.password, original_password)


class BootstrapOwnerCommandTests(TestCase):
    def test_bootstrap_owner_rejects_a_weak_password(self):
        with self.assertRaises(CommandError):
            management.call_command(
                "bootstrap_owner",
                username="owner",
                household="Home",
                password="a",
            )

        self.assertFalse(User.objects.exists())
