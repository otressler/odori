from django.db import transaction
from django.http import Http404

from core.services import household_for

from .models import CanonicalIngredient, InventoryEvent, InventoryItem


@transaction.atomic
def change_inventory_status(*, user, ingredient_id, status, version):
    household = household_for(user)
    ingredient = CanonicalIngredient.objects.filter(id=ingredient_id, household=household).first()
    if not ingredient:
        raise Http404
    item, created = InventoryItem.objects.select_for_update().get_or_create(
        household=household,
        ingredient=ingredient,
        defaults={"status": InventoryItem.Status.UNKNOWN},
    )
    if not created and item.version != version:
        return None
    previous_status = item.status
    item.status = status
    item.version += 1
    item.save()
    InventoryEvent.objects.create(
        item=item,
        previous_status=previous_status,
        new_status=status,
        actor=user,
        origin=InventoryEvent.Origin.MANUAL,
    )
    return item


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
