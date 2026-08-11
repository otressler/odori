import uuid

from django.conf import settings
from django.db import models

from core.models import Household
from pantry.models import CanonicalIngredient


class RecipeSource(models.Model):
    class Type(models.TextChoices):
        MANUAL = "manual", "Manual"
        GENERATED = "generated", "Generated"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.MANUAL)
    attribution = models.CharField(max_length=255, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.attribution or self.get_type_display()


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
    thumbnail = models.FileField(upload_to="recipe-thumbnails/", blank=True)
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

    class Meta:
        indexes = [
            models.Index(fields=["household", "status"], name="recipe_household_status_idx"),
        ]

    def __str__(self):
        return self.title or "Untitled recipe"


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
    attempt_count = models.PositiveIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def for_household(cls, household):
        return cls.objects.filter(recipe__household=household)

    def __str__(self):
        return f"Image for {self.recipe} ({self.get_state_display()})"


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

    def __str__(self):
        return self.source_text


class RecipeStep(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="steps")
    body = models.TextField()
    sort_order = models.PositiveIntegerField()
    timer_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order"]

    def __str__(self):
        return self.body[:80]


class RecipeTag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE)
    name = models.CharField(max_length=60)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "name"], name="unique_recipe_tag")
        ]

    def __str__(self):
        return self.name


class RecipeTagAssignment(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="tag_assignments")
    tag = models.ForeignKey(RecipeTag, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "tag"], name="unique_recipe_tag_assignment")
        ]

    def __str__(self):
        return f"{self.recipe}: {self.tag}"


class RecipeFavorite(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name="favorites")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["recipe", "user"], name="unique_recipe_favorite")
        ]

    def __str__(self):
        return f"{self.user} likes {self.recipe}"


class RecommendationRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="recommendation_runs"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    scoring_version = models.CharField(max_length=40)
    inventory_snapshot_at = models.DateTimeField()
    input_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["household", "created_at"], name="recommendation_run_house_idx")
        ]


class RecommendationOutcome(models.Model):
    class Type(models.TextChoices):
        OPENED = "opened", "Opened"
        PLANNED = "planned", "Planned"
        COOKED = "cooked", "Cooked"
        DISMISSED = "dismissed", "Dismissed"
        HIDDEN = "hidden", "Hidden"

    class Reason(models.TextChoices):
        NOT_RELEVANT = "not_relevant", "Not relevant"
        TOO_MANY_INGREDIENTS = "too_many_ingredients", "Too many ingredients"
        RECENTLY_COOKED = "recently_cooked", "Recently cooked"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="recommendation_outcomes"
    )
    recipe = models.ForeignKey(
        Recipe, on_delete=models.CASCADE, related_name="recommendation_outcomes"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    run = models.ForeignKey(
        RecommendationRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="outcomes"
    )
    outcome = models.CharField(max_length=12, choices=Type.choices)
    reason = models.CharField(max_length=24, choices=Reason.choices, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=["household", "recipe", "created_at"],
                name="recommendation_outcome_rec_idx",
            )
        ]


class GeneratedRecipeRequest(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="generated_recipe_requests"
    )
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    recipe = models.ForeignKey(
        Recipe, null=True, blank=True, on_delete=models.SET_NULL, related_name="generation_requests"
    )
    idea = models.TextField()
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    attempt_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    provider_deployment = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=40)
    correlation_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["household", "created_at"], name="generated_recipe_req_idx")
        ]
