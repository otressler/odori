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
