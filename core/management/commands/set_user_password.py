from getpass import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from core.models import User


class Command(BaseCommand):
    help = "Set the password for an existing user."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument(
            "--password",
            help="Password for non-interactive use. Prefer the interactive prompts when possible.",
        )

    def handle(self, *args, **options):
        try:
            user = User.objects.get(username=options["username"])
        except User.DoesNotExist as exc:
            raise CommandError(f'User "{options["username"]}" does not exist.') from exc

        password = options["password"]
        if password is None:
            password = getpass("New password: ")
            confirmation = getpass("Confirm password: ")
            if password != confirmation:
                raise CommandError("The passwords do not match.")

        if not password:
            raise CommandError("A password is required.")
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        user.set_password(password)
        user.save(update_fields=["password"])
        self.stdout.write(self.style.SUCCESS(f'Password updated for user "{user.username}".'))