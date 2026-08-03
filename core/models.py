import secrets
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class Household(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    display_name = models.CharField(max_length=120, blank=True)
    locale = models.CharField(max_length=20, default="de")

    @property
    def name_for_display(self):
        return self.display_name or self.get_username()

    def __str__(self):
        return self.name_for_display


class HouseholdMembership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=12, choices=Role.choices)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "user"], name="unique_membership")
        ]

    def __str__(self):
        return f"{self.user} in {self.household}"


def registration_code():
    return secrets.token_hex(3).upper()


class HouseholdInvitation(models.Model):
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    registration_code = models.CharField(
        max_length=6, default=registration_code, unique=True, editable=False
    )
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="invitations")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="invitations")
    created_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"Invitation {self.registration_code} for {self.household}"


class WorkerHeartbeat(models.Model):
    class State(models.TextChoices):
        IDLE = "idle", "Idle"
        WORKING = "working", "Working"
        DEGRADED = "degraded", "Degraded"

    name = models.CharField(max_length=80, unique=True, default="default")
    state = models.CharField(max_length=12, choices=State.choices, default=State.IDLE)
    started_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField()
    last_job_completed_at = models.DateTimeField(null=True, blank=True)
    last_error_message = models.CharField(max_length=500, blank=True)

    def __str__(self):
        return f"{self.name} ({self.get_state_display()})"


class ProviderDiagnostic(models.Model):
    class State(models.TextChoices):
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    household = models.ForeignKey(Household, on_delete=models.SET_NULL, null=True, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    job_id = models.UUIDField(null=True, blank=True)
    operation = models.CharField(max_length=80)
    state = models.CharField(max_length=12, choices=State.choices)
    error_code = models.CharField(max_length=80, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    deployment = models.CharField(max_length=120, blank=True)
    vector_dimensions = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.operation} ({self.get_state_display()})"
