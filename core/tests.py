import json

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

    def test_unauthenticated_pages_redirect_to_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

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
