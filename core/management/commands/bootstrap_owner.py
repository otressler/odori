from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Household, HouseholdMembership, User


class Command(BaseCommand):
    help = "Create the one-time owner account and its household."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--household", required=True)
        parser.add_argument("--display-name", default="")
        parser.add_argument("--password", help="Use only for non-interactive release automation.")

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.exists() or Household.objects.exists():
            raise CommandError("Owner bootstrap is only allowed for an empty installation.")
        password = options["password"] or getpass("Password: ")
        if not password:
            raise CommandError("A password is required.")
        owner = User.objects.create_user(
            username=options["username"], password=password, display_name=options["display_name"]
        )
        household = Household.objects.create(name=options["household"])
        HouseholdMembership.objects.create(
            household=household, user=owner, role=HouseholdMembership.Role.OWNER
        )
        self.stdout.write(self.style.SUCCESS(f"Created owner for {household.name}."))
