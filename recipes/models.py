import uuid

from django.conf import settings
from django.db import models

from core.models import Household
from pantry.models import CanonicalIngredient


class RecipeSource(models.Model):
    class Type(models.TextChoices):
        MANUAL = "manual", "Manual"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.MANUAL)
    attribution = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)


class Recipe(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Entwurf"
        APPROVED = "approved", "Veröffentlicht"
        ARCHIVED = "archived", "Archiviert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="recipes")
    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    servings = models.PositiveIntegerField(null=True, blank=True)
    search_embedding = models.JSONField(default=list, blank=True)
    search_embedding_model = models.CharField(max_length=120, blank=True)
    image = models.FileField(upload_to="recipes/", blank=True)
    image_status = models.CharField(
        max_length=12,
        choices=(("pending", "Ausstehend"), ("ready", "Fertig"), ("failed", "Fehlgeschlagen")),
        default="pending",
    )
    image_prompt = models.TextField(blank=True)
    source = models.ForeignKey(RecipeSource, on_delete=models.PROTECT, related_name="recipes")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    archived_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RecipeImageJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SUPERSEDED = "superseded", "Superseded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="image_jobs")
    prompt = models.TextField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class RecipeIngredient(models.Model):
    class MatchState(models.TextChoices):
        MATCHED = "matched", "Matched"
        REVIEW_NEEDED = "review_needed", "Review needed"
        UNRESOLVED = "unresolved", "Unresolved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="ingredients")
    canonical_ingredient = models.ForeignKey(
        CanonicalIngredient,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="recipe_lines",
    )
    source_text = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=40, blank=True)
    optional = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField()
    match_state = models.CharField(
        max_length=20, choices=MatchState.choices, default=MatchState.UNRESOLVED
    )

    class Meta:
        ordering = ["sort_order"]


class RecipeStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    body = models.TextField()
    sort_order = models.PositiveIntegerField()
    timer_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order"]


class RecipeTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE)
    name = models.CharField(max_length=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "name"], name="unique_recipe_tag")
        ]


class RecipeTagAssignment(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="tag_assignments")
    tag = models.ForeignKey(RecipeTag, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "tag"], name="unique_recipe_tag_assignment")
        ]


class RecipeFavorite(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="favorites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "user"], name="unique_recipe_favorite")
        ]
