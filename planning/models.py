import uuid

from django.conf import settings
from django.db import models

from core.models import Household
from recipes.models import Recipe

SLOT_SEQUENCE = ["breakfast", "lunch", "dinner", "snack"]


class MealPlan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="meal_plans")
    week_start_date = models.DateField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["household", "week_start_date"], name="unique_household_week"
            )
        ]
        ordering = ["-week_start_date"]


class MealSlot(models.Model):
    class Slot(models.TextChoices):
        BREAKFAST = "breakfast", "Frühstück"
        LUNCH = "lunch", "Mittag"
        DINNER = "dinner", "Abend"
        SNACK = "snack", "Snack"

    class EntryType(models.TextChoices):
        RECIPE = "recipe", "Rezept"
        LEFTOVERS = "leftovers", "Reste"
        NOTE = "note", "Notiz"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(MealPlan, on_delete=models.CASCADE, related_name="slots")
    date = models.DateField()
    slot = models.CharField(max_length=16, choices=Slot.choices)
    entry_type = models.CharField(
        max_length=16, choices=EntryType.choices, default=EntryType.RECIPE
    )
    recipe = models.ForeignKey(
        Recipe, null=True, blank=True, on_delete=models.PROTECT, related_name="meal_slots"
    )
    servings = models.PositiveIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    cooked_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "created_at"]

    @property
    def is_cooked(self):
        return self.cooked_at is not None

    @property
    def display_label(self):
        if self.entry_type == self.EntryType.RECIPE and self.recipe:
            return self.recipe.title or "Unbenannter Entwurf"
        return self.notes


class CookEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(Household, on_delete=models.CASCADE, related_name="cook_events")
    recipe = models.ForeignKey(Recipe, on_delete=models.PROTECT, related_name="cook_events")
    meal_slot = models.OneToOneField(
        MealSlot, null=True, blank=True, on_delete=models.SET_NULL, related_name="cook_event"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cooked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cooked_at"]
