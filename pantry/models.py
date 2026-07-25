import uuid

from django.conf import settings
from django.db import models

from core.models import Household


class IngredientCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="ingredient_categories"
    )
    name = models.CharField(max_length=80)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["household", "name"], name="unique_category_name")
        ]
        ordering = ["sort_order", "name"]


class CanonicalIngredient(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="ingredients")
    name = models.CharField(max_length=120)
    category = models.ForeignKey(
        IngredientCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    aliases = models.JSONField(default=list, blank=True)
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
        IN_STOCK = "in_stock", "In stock"
        NEEDS_REPLENISHMENT = "needs_replenishment", "Needs replenishment"
        UNKNOWN = "unknown", "Unknown"

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
    created_at = models.DateTimeField(auto_now_add=True)
