from django.db import transaction
from django.http import Http404

from core.services import household_for

from .models import CanonicalIngredient, InventoryEvent, InventoryItem


class PlannedIngredientInUse(Exception):
    """A manual transition away from `in_stock` would affect upcoming planned meals."""

    def __init__(self, slots):
        self.slots = list(slots)
        super().__init__("planned_ingredient_in_use")


def locked_item(household, ingredient):
    return InventoryItem.objects.select_for_update().get_or_create(
        household=household,
        ingredient=ingredient,
        defaults={"status": InventoryItem.Status.UNKNOWN},
    )


def write_status(*, item, status, actor, origin, meal_slot=None):
    previous_status = item.status
    item.status = status
    item.version += 1
    item.save(update_fields=["status", "version", "updated_at"])
    InventoryEvent.objects.create(
        item=item,
        previous_status=previous_status,
        new_status=status,
        actor=actor,
        origin=origin,
        meal_slot=meal_slot,
    )
    return item


def removes_planned_stock(previous_status, new_status):
    return (
        previous_status == InventoryItem.Status.IN_STOCK
        and new_status != InventoryItem.Status.IN_STOCK
    )


@transaction.atomic
def change_inventory_status(*, user, ingredient_id, status, version, confirm_planned_use=False):
    """Manual availability change. Returns `None` when the supplied version is stale."""

    household = household_for(user)
    ingredient = CanonicalIngredient.objects.filter(id=ingredient_id, household=household).first()
    if not ingredient:
        raise Http404
    item, created = locked_item(household, ingredient)
    if not created and item.version != version:
        return None
    if not confirm_planned_use and removes_planned_stock(item.status, status):
        from planning.services import upcoming_slots_using_ingredient

        slots = upcoming_slots_using_ingredient(household=household, ingredient=ingredient)
        if slots:
            raise PlannedIngredientInUse(slots)
    return write_status(item=item, status=status, actor=user, origin=InventoryEvent.Origin.MANUAL)


def record_purchase(*, user, household, ingredient):
    """Runs inside the shopping purchase transaction; never asks for confirmation."""

    item, _ = locked_item(household, ingredient)
    return write_status(
        item=item,
        status=InventoryItem.Status.IN_STOCK,
        actor=user,
        origin=InventoryEvent.Origin.PURCHASE,
    )


def record_cooking_change(*, user, household, ingredient, status, version, meal_slot):
    """Runs inside the mark-cooked transaction. Returns `None` on a stale version."""

    item, created = locked_item(household, ingredient)
    if version is not None and not created and item.version != version:
        return None
    return write_status(
        item=item,
        status=status,
        actor=user,
        origin=InventoryEvent.Origin.COOK_RECIPE,
        meal_slot=meal_slot,
    )


@transaction.atomic
def merge_ingredients(*, user, source_id, target_id):
    household = household_for(user)
    source = (
        CanonicalIngredient.objects.select_for_update()
        .filter(id=source_id, household=household)
        .first()
    )
    target = (
        CanonicalIngredient.objects.select_for_update()
        .filter(id=target_id, household=household)
        .first()
    )
    if not source or not target or source == target:
        raise Http404
    from recipes.models import RecipeIngredient

    RecipeIngredient.objects.filter(canonical_ingredient=source).update(canonical_ingredient=target)
    source_item = InventoryItem.objects.filter(household=household, ingredient=source).first()
    target_item = InventoryItem.objects.filter(household=household, ingredient=target).first()
    if source_item and not target_item:
        source_item.ingredient = target
        source_item.version += 1
        source_item.save()
    elif source_item:
        source_item.delete()
    source.active = False
    source.merged_into = target
    source.save(update_fields=["active", "merged_into"])
    return target
