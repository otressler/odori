import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import Household
from recipes.models import Recipe

MAX_SNAPSHOT_BYTES = 128 * 1024


class RecommendationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="recommendation_runs"
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="recommendation_runs",
    )
    snapshot = models.JSONField()
    snapshot_schema_version = models.PositiveSmallIntegerField(default=1)
    scoring_version = models.CharField(max_length=40)
    model_version = models.CharField(max_length=120, null=True, blank=True)
    inventory_snapshot_at = models.DateTimeField()
    candidate_count = models.PositiveSmallIntegerField()
    query_duration_ms = models.PositiveIntegerField(default=0)
    scoring_duration_ms = models.PositiveIntegerField(default=0)
    generation_eligible = models.BooleanField(default=False)
    generation_ineligibility_reason = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["household", "-created_at"], name="rec_run_recent_idx"
            )
        ]

    def clean(self):
        super().clean()
        encoded = json.dumps(
            self.snapshot, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode()
        if len(encoded) > MAX_SNAPSHOT_BYTES:
            raise ValidationError({"snapshot": "Recommendation snapshot exceeds 128 KiB."})

    def __str__(self):
        return f"{self.household} recommendations at {self.created_at}"

    def replay_snapshot(self):
        from .services import replay_snapshot

        return replay_snapshot(self.snapshot)


class RecommendationFeedback(models.Model):
    class Outcome(models.TextChoices):
        OPENED = "opened", "Opened"
        PLANNED = "planned", "Planned"
        COOKED = "cooked", "Cooked"
        DISMISSED = "dismissed", "Dismissed"
        HIDDEN = "hidden", "Hidden"

    class Reason(models.TextChoices):
        NOT_INTERESTED = "not_interested", "Not interested"
        TOO_MANY_MISSING = "too_many_missing", "Too many missing ingredients"
        RECENT_REPEAT = "recent_repeat", "Recent repeat"
        NOT_AGAIN = "not_again", "Not again"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="recommendation_feedback"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recommendation_feedback",
    )
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE, related_name="recommendation_feedback"
    )
    recommendation_run = models.ForeignKey(
        RecommendationRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="feedback",
    )
    outcome = models.CharField(max_length=12, choices=Outcome.choices)
    reason = models.CharField(max_length=24, choices=Reason.choices, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["recommendation_run", "user", "recipe", "outcome"],
                name="unique_rec_run_outcome",
            ),
            models.UniqueConstraint(
                fields=["household", "user", "recipe"],
                condition=Q(active=True)
                & (Q(outcome="hidden") | Q(outcome="dismissed", reason="not_again")),
                name="unique_active_rec_preference",
            ),
        ]
        indexes = [
            models.Index(
                fields=["household", "recommendation_run", "-created_at"],
                name="rec_feedback_run_idx",
            ),
            models.Index(
                fields=["household", "user", "recipe", "active"],
                name="rec_feedback_active_idx",
            ),
        ]

    def __str__(self):
        return f"{self.user}: {self.recipe} {self.outcome}"
