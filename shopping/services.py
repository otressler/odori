from decimal import Decimal

from django.db import transaction
from django.http import Http404
from django.utils import timezone

from core.services import household_for
from pantry.models import InventoryItem
from pantry.services import record_purchase
from planning.models import MealPlan, MealSlot

from .models import ShoppingItem, ShoppingList
from .units import display_unit, normalize_unit


class StaleItemVersion(Exception):
    def __init__(self, item):
        self.item = item
        super().__init__("stale_version")


def quantize(value):
    quantized = value.quantize(Decimal("0.01")).normalize()
    if quantized == quantized.to_integral_value():
        quantized = quantized.quantize(Decimal("1"))
    return format(quantized, "f")


class Aggregate:
    """One shopping line under construction: summable buckets plus unquantified mentions."""

    def __init__(self, key, label, ingredient):
        self.key = key
        self.label = label
        self.ingredient = ingredient
        self.summable = {}
        self.unquantified = {}
        self.recipe_refs = []

    def add_line(self, amount, unit, factor):
        normalized = normalize_unit(unit)
        if amount is None:
            self.unquantified[normalized] = self.unquantified.get(normalized, 0) + 1
            return
        scaled = (Decimal(amount) * factor).quantize(Decimal("0.01"))
        self.summable[normalized] = self.summable.get(normalized, Decimal(0)) + scaled

    def add_reference(self, slot):
        reference = {
            "recipeId": str(slot.recipe_id),
            "title": slot.recipe.title or "Unbenannter Entwurf",
            "slotId": str(slot.id),
            "date": slot.date.isoformat(),
        }
        if reference not in self.recipe_refs:
            self.recipe_refs.append(reference)

    def components(self):
        components = [
            {"amount": quantize(total), "unit": display_unit(unit)}
            for unit, total in sorted(self.summable.items())
        ]
        components.extend(
            {"amount": None, "unit": display_unit(unit), "mentions": mentions}
            for unit, mentions in sorted(self.unquantified.items())
        )
        return components


def scaling_factor(slot):
    recipe_servings = slot.recipe.servings
    if not recipe_servings or not slot.servings:
        return Decimal(1)
    return Decimal(slot.servings) / Decimal(recipe_servings)


def collect_aggregates(plan):
    aggregates = {}
    slots = (
        MealSlot.objects.select_related("recipe")
        .prefetch_related("recipe__ingredients__canonical_ingredient")
        .filter(
            plan=plan,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe__isnull=False,
            cooked_at__isnull=True,
        )
        .order_by("date", "created_at")
    )
    for slot in slots:
        factor = scaling_factor(slot)
        for line in slot.recipe.ingredients.all():
            if line.canonical_ingredient_id:
                key = f"ingredient:{line.canonical_ingredient_id}"
                label = line.canonical_ingredient.name
            else:
                key = f"text:{line.source_text.strip().lower()}"
                label = line.source_text.strip()
            if not label:
                continue
            aggregate = aggregates.setdefault(key, Aggregate(key, label, line.canonical_ingredient))
            aggregate.add_line(line.amount, line.unit, factor)
            aggregate.add_reference(slot)
    return aggregates


def collect_requirements_until(*, household, until_date):
    """Collect remaining planned recipe requirements through an inclusive date."""

    aggregates = {}
    slots = (
        MealSlot.objects.select_related("recipe")
        .prefetch_related("recipe__ingredients__canonical_ingredient")
        .filter(
            plan__household=household,
            date__lte=until_date,
            entry_type=MealSlot.EntryType.RECIPE,
            recipe__isnull=False,
            cooked_at__isnull=True,
        )
        .order_by("date", "created_at")
    )
    for slot in slots:
        factor = scaling_factor(slot)
        for line in slot.recipe.ingredients.all():
            if line.canonical_ingredient_id:
                key = f"ingredient:{line.canonical_ingredient_id}"
                label = line.canonical_ingredient.name
            else:
                key = f"text:{line.source_text.strip().lower()}"
                label = line.source_text.strip()
            if not label:
                continue
            aggregate = aggregates.setdefault(
                key, Aggregate(key, label, line.canonical_ingredient)
            )
            aggregate.add_line(line.amount, line.unit, factor)
    return aggregates


def excluded_ingredient_ids(household):
    return set(
        InventoryItem.objects.filter(
            household=household, status=InventoryItem.Status.IN_STOCK
        ).values_list("ingredient_id", flat=True)
    )


def unknown_ingredient_ids(household):
    return set(
        InventoryItem.objects.filter(
            household=household, status=InventoryItem.Status.UNKNOWN
        ).values_list("ingredient_id", flat=True)
    )


@transaction.atomic
def generate_from_plan(*, user, week_start, include_in_stock=False):
    """Idempotent regeneration: only calculated, still-open entries are rewritten."""

    household = household_for(user)
    plan = MealPlan.objects.filter(household=household, week_start_date=week_start).first()
    if not plan:
        raise Http404
    shopping_list = (
        ShoppingList.objects.select_for_update()
        .filter(household=household, plan=plan, state=ShoppingList.State.ACTIVE)
        .first()
    )
    if not shopping_list:
        shopping_list = ShoppingList.objects.create(
            household=household,
            plan=plan,
            name=f"Woche ab {plan.week_start_date.strftime('%d.%m.%Y')}",
        )
    aggregates = collect_aggregates(plan)
    excluded = set() if include_in_stock else excluded_ingredient_ids(household)
    unknown = unknown_ingredient_ids(household)
    wanted = {
        key: aggregate
        for key, aggregate in aggregates.items()
        if not (aggregate.ingredient and aggregate.ingredient.id in excluded)
    }
    existing = {
        item.grouping_key: item
        for item in ShoppingItem.objects.select_for_update().filter(
            shopping_list=shopping_list, source=ShoppingItem.Source.CALCULATED
        )
    }
    for key, item in list(existing.items()):
        if item.state != ShoppingItem.State.OPEN:
            # Purchased and skipped decisions survive regeneration untouched.
            wanted.pop(key, None)
            existing.pop(key)
            continue
        if key not in wanted:
            item.delete()
            existing.pop(key)
    for key, aggregate in wanted.items():
        item = existing.get(key)
        needs_confirmation = bool(aggregate.ingredient and aggregate.ingredient.id in unknown)
        components = aggregate.components()
        if item:
            item.label = aggregate.label
            item.canonical_ingredient = aggregate.ingredient
            item.quantity_components = components
            item.recipe_refs = aggregate.recipe_refs
            item.needs_confirmation = needs_confirmation
            item.version += 1
            item.save()
        else:
            ShoppingItem.objects.create(
                shopping_list=shopping_list,
                canonical_ingredient=aggregate.ingredient,
                label=aggregate.label,
                grouping_key=key,
                quantity_components=components,
                recipe_refs=aggregate.recipe_refs,
                needs_confirmation=needs_confirmation,
            )
    shopping_list.generated_at = timezone.now()
    shopping_list.version += 1
    shopping_list.save(update_fields=["generated_at", "version"])
    return shopping_list


def list_for_user(user, list_id, lock=False):
    household = household_for(user)
    queryset = ShoppingList.objects.filter(id=list_id, household=household)
    if lock:
        queryset = queryset.select_for_update()
    shopping_list = queryset.first()
    if not shopping_list:
        raise Http404
    return shopping_list


def item_for_user(user, item_id, lock=False):
    household = household_for(user)
    queryset = ShoppingItem.objects.filter(id=item_id, shopping_list__household=household)
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related("shopping_list", "canonical_ingredient")
    item = queryset.first()
    if not item:
        raise Http404
    return item


@transaction.atomic
def add_manual_item(*, user, list_id, label, ingredient_id=None):
    from pantry.models import CanonicalIngredient

    household = household_for(user)
    shopping_list = list_for_user(user, list_id)
    label = (label or "").strip()
    if not label:
        raise ValueError("Ein Name ist erforderlich.")
    ingredient = None
    if ingredient_id:
        ingredient = CanonicalIngredient.objects.filter(
            id=ingredient_id, household=household
        ).first()
        if not ingredient:
            raise Http404
    return ShoppingItem.objects.create(
        shopping_list=shopping_list,
        canonical_ingredient=ingredient,
        label=label[:200],
        grouping_key=f"manual:{label.lower()}",
        source=ShoppingItem.Source.MANUAL,
    )


@transaction.atomic
def add_pantry_item(*, user, list_id, ingredient_id):
    household = household_for(user)
    shopping_list = list_for_user(user, list_id)
    pantry_item = (
        InventoryItem.objects.select_related("ingredient")
        .filter(
            household=household,
            ingredient_id=ingredient_id,
            status__in=[
                InventoryItem.Status.NEEDS_REPLENISHMENT,
                InventoryItem.Status.UNKNOWN,
            ],
        )
        .first()
    )
    if not pantry_item:
        raise Http404
    if ShoppingItem.objects.filter(
        shopping_list=shopping_list, canonical_ingredient_id=ingredient_id
    ).exists():
        return None
    return ShoppingItem.objects.create(
        shopping_list=shopping_list,
        canonical_ingredient=pantry_item.ingredient,
        label=pantry_item.ingredient.name,
        grouping_key=f"pantry:{pantry_item.ingredient_id}",
        source=ShoppingItem.Source.MANUAL,
        needs_confirmation=pantry_item.status == InventoryItem.Status.UNKNOWN,
    )


@transaction.atomic
def add_pantry_items(*, user, list_id, status=InventoryItem.Status.NEEDS_REPLENISHMENT):
    """Add pantry ingredients with the requested status."""

    household = household_for(user)
    shopping_list = list_for_user(user, list_id)
    pantry_items = InventoryItem.objects.filter(
        household=household,
        status=status,
    ).select_related("ingredient")
    existing_ingredient_ids = set(
        ShoppingItem.objects.filter(
            shopping_list=shopping_list,
            canonical_ingredient__isnull=False,
        ).values_list("canonical_ingredient_id", flat=True)
    )
    added = 0
    for pantry_item in pantry_items:
        if pantry_item.ingredient_id in existing_ingredient_ids:
            continue
        ShoppingItem.objects.create(
            shopping_list=shopping_list,
            canonical_ingredient=pantry_item.ingredient,
            label=pantry_item.ingredient.name,
            grouping_key=f"pantry:{pantry_item.ingredient_id}",
            source=ShoppingItem.Source.MANUAL,
        )
        existing_ingredient_ids.add(pantry_item.ingredient_id)
        added += 1
    return added


@transaction.atomic
def set_item_state(*, user, item_id, version, state):
    if state not in ShoppingItem.State.values:
        raise ValueError("Unbekannter Status.")
    item = item_for_user(user, item_id, lock=True)
    if item.version != version:
        raise StaleItemVersion(item)
    item.state = state
    item.version += 1
    item.save(update_fields=["state", "version", "updated_at"])
    return item


@transaction.atomic
def purchase_item(*, user, item_id, version):
    """Shopping state and pantry availability move together or not at all."""

    household = household_for(user)
    item = item_for_user(user, item_id, lock=True)
    if item.version != version:
        raise StaleItemVersion(item)
    item.state = ShoppingItem.State.PURCHASED
    item.needs_confirmation = False
    item.version += 1
    item.save(update_fields=["state", "needs_confirmation", "version", "updated_at"])
    if item.canonical_ingredient_id:
        from pantry.models import CanonicalIngredient

        ingredient = CanonicalIngredient.objects.get(
            id=item.canonical_ingredient_id, household=household
        )
        record_purchase(user=user, household=household, ingredient=ingredient)
    return item


@transaction.atomic
def delete_item(*, user, item_id, version):
    item = item_for_user(user, item_id, lock=True)
    if item.version != version:
        raise StaleItemVersion(item)
    item.delete()
