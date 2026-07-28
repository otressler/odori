import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import Household


class IngredientCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="ingredient_categories"
    )
    name = models.CharField(max_length=80)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    embedding = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=120, blank=True)
    minimum_similarity = models.FloatField(null=True, blank=True)
    minimum_margin = models.FloatField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "name"], name="unique_category_name")
        ]
        ordering = ["sort_order", "name"]


class IngredientCategoryExample(models.Model):
    class Source(models.TextChoices):
        STARTER = "starter", "Starter catalog"
        OWNER = "owner", "Owner"
        ASSIGNED = "assigned", "Automatic assignment"
        CONFIRMED = "confirmed", "Confirmed assignment"
        IMPORTED = "imported", "Imported"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="ingredient_category_examples"
    )
    category = models.ForeignKey(
        IngredientCategory, on_delete=models.CASCADE, related_name="examples"
    )
    text = models.CharField(max_length=120)
    normalized_text = models.CharField(max_length=120)
    source = models.CharField(max_length=12, choices=Source.choices)
    source_key = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    embedding = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "normalized_text"],
                name="unique_category_example_text",
            ),
            models.UniqueConstraint(
                fields=["category", "source", "source_key"],
                condition=Q(source_key__gt=""),
                name="unique_category_example_source_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["household", "category", "active"],
                name="pantry_inge_househo_7ca0b4_idx",
            ),
        ]
        ordering = ["category__sort_order", "category__name", "text"]


class PantryCategorizationJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="pantry_categorization_jobs"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    assigned_count = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveIntegerField(default=0)
    error_message = models.CharField(max_length=500, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    correlation_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["household"],
                condition=Q(state__in=["queued", "running"]),
                name="one_active_pantry_categorization_job",
            )
        ]


class CanonicalIngredient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="ingredients")
    name = models.CharField(max_length=120)
    category = models.ForeignKey(
        IngredientCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    aliases = models.JSONField(default=list, blank=True)
    embedding = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=120, blank=True)
    active = models.BooleanField(default=True)
    merged_into = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "name"], name="unique_ingredient_name")
        ]
        ordering = ["name"]


class InventoryItem(models.Model):
    class Status(models.TextChoices):
        IN_STOCK = "in_stock", "Vorrätig"
        NEEDS_REPLENISHMENT = "needs_replenishment", "Nachkaufen"
        UNKNOWN = "unknown", "Unbekannt"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="inventory_items"
    )
    ingredient = models.ForeignKey(
        CanonicalIngredient, on_delete=models.PROTECT, related_name="inventory_items"
    )
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.UNKNOWN)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "ingredient"], name="one_inventory_item")
        ]


class InventoryEvent(models.Model):
    class Origin(models.TextChoices):
        MANUAL = "manual", "Manual"
        PURCHASE = "purchase", "Purchase"
        COOK_RECIPE = "cook_recipe", "Cook recipe"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(InventoryItem, on_delete=models.PROTECT, related_name="events")
    previous_status = models.CharField(max_length=24, choices=InventoryItem.Status.choices)
    new_status = models.CharField(max_length=24, choices=InventoryItem.Status.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.MANUAL)
    meal_slot = models.ForeignKey(
        "planning.MealSlot",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inventory_events",
    )
    created_at = models.DateTimeField(auto_now_add=True)
