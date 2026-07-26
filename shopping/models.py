import uuid
from datetime import date

from django.db import models

from core.models import Household
from pantry.models import CanonicalIngredient
from planning.models import MealPlan


class ShoppingList(models.Model):
    class State(models.TextChoices):
        ACTIVE = "active", "Aktiv"
        COMPLETED = "completed", "Abgeschlossen"
        ARCHIVED = "archived", "Archiviert"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    household = models.ForeignKey(
        Household, on_delete=models.CASCADE, related_name="shopping_lists"
    )
    name = models.CharField(max_length=120)
    plan = models.ForeignKey(
        MealPlan, null=True, blank=True, on_delete=models.SET_NULL, related_name="shopping_lists"
    )
    state = models.CharField(max_length=12, choices=State.choices, default=State.ACTIVE)
    version = models.PositiveIntegerField(default=1)
    generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ShoppingItem(models.Model):
    class Source(models.TextChoices):
        CALCULATED = "calculated", "Berechnet"
        MANUAL = "manual", "Manuell"

    class State(models.TextChoices):
        OPEN = "open", "Offen"
        PURCHASED = "purchased", "Gekauft"
        SKIPPED = "skipped", "Übersprungen"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shopping_list = models.ForeignKey(ShoppingList, on_delete=models.CASCADE, related_name="items")
    canonical_ingredient = models.ForeignKey(
        CanonicalIngredient,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shopping_items",
    )
    label = models.CharField(max_length=200)
    grouping_key = models.CharField(max_length=200)
    quantity_components = models.JSONField(default=list, blank=True)
    recipe_refs = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.CALCULATED)
    state = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    needs_confirmation = models.BooleanField(default=False)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["state", "label"]
        constraints = [
            models.UniqueConstraint(
                fields=["shopping_list", "grouping_key"],
                condition=models.Q(source="calculated"),
                name="unique_calculated_item_per_list",
            )
        ]

    @property
    def quantity_text(self):
        return " + ".join(component_text(component) for component in self.quantity_components)

    @property
    def recipe_titles(self):
        """Distinct recipe names; the same dish planned twice should read once."""

        titles = []
        for reference in self.recipe_refs:
            title = reference.get("title")
            if title and title not in titles:
                titles.append(title)
        return titles

    @property
    def recipe_requirements(self):
        """Planned recipe references in chronological order, with parsed dates for templates."""

        requirements = []
        for reference in self.recipe_refs:
            try:
                required_on = date.fromisoformat(reference["date"])
            except (KeyError, TypeError, ValueError):
                continue
            requirements.append({"date": required_on, "title": reference.get("title")})
        return sorted(requirements, key=lambda requirement: requirement["date"])

    @property
    def earliest_recipe_requirement(self):
        requirements = self.recipe_requirements
        return requirements[0] if requirements else None


def component_text(component):
    amount = component.get("amount")
    unit = component.get("unit") or ""
    if amount is None:
        mentions = component.get("mentions", 1)
        suffix = f"{mentions}×" if mentions > 1 else "nach Bedarf"
        return f"{suffix} {unit}".strip()
    return f"{amount} {unit}".strip()
