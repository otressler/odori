from datetime import date as date_type
from datetime import timedelta

from django.db import transaction
from django.http import Http404
from django.utils import timezone

from core.services import household_for
from pantry.models import InventoryItem
from pantry.services import record_cooking_change
from recipes.models import Recipe

from .models import SLOT_SEQUENCE, CookEvent, MealPlan, MealSlot

RECENT_REPEAT_DAYS = 21


class StaleSlotVersion(Exception):
    def __init__(self, slot):
        self.slot = slot
        super().__init__("stale_version")


class SlotNotCookable(Exception):
    pass


def week_start_for(value):
    return value - timedelta(days=value.weekday())


def current_week_start():
    return week_start_for(timezone.localdate())


def parse_week_start(value):
    if not value:
        return current_week_start()
    if isinstance(value, date_type):
        return week_start_for(value)
    try:
        parsed = date_type.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("week_start must be an ISO date (YYYY-MM-DD)") from exc
    return week_start_for(parsed)


def get_or_create_plan(*, user, week_start):
    household = household_for(user)
    plan, _ = MealPlan.objects.get_or_create(household=household, week_start_date=week_start)
    return plan


def plan_slots(plan):
    return (
        MealSlot.objects.select_related("recipe").filter(plan=plan).order_by("date", "created_at")
    )


def week_grid(plan):
    """Ordered day/slot matrix so the UI never depends on alphabetical slot ordering."""

    by_cell = {}
    for slot in plan_slots(plan):
        by_cell.setdefault((slot.date, slot.slot), []).append(slot)
    days = []
    for offset in range(7):
        day = plan.week_start_date + timedelta(days=offset)
        days.append(
            {
                "date": day,
                "is_today": day == timezone.localdate(),
                "slots": [
                    {
                        "key": key,
                        "label": MealSlot.Slot(key).label,
                        "entries": by_cell.get((day, key), []),
                    }
                    for key in SLOT_SEQUENCE
                ],
            }
        )
    return days


def duplicate_recipe_ids(plan):
    seen = set()
    duplicates = set()
    for recipe_id in MealSlot.objects.filter(
        plan=plan, entry_type=MealSlot.EntryType.RECIPE, recipe__isnull=False
    ).values_list("recipe_id", flat=True):
        if recipe_id in seen:
            duplicates.add(recipe_id)
        seen.add(recipe_id)
    return duplicates


def recently_cooked_recipe_ids(household, days=RECENT_REPEAT_DAYS):
    since = timezone.now() - timedelta(days=days)
    return set(
        CookEvent.objects.filter(household=household, cooked_at__gte=since).values_list(
            "recipe_id", flat=True
        )
    )


def upcoming_slots_using_ingredient(*, household, ingredient, today=None):
    today = today or timezone.localdate()
    return list(
        MealSlot.objects.select_related("recipe")
        .filter(
            plan__household=household,
            entry_type=MealSlot.EntryType.RECIPE,
            cooked_at__isnull=True,
            date__gte=today,
            recipe__ingredients__canonical_ingredient=ingredient,
        )
        .distinct()
        .order_by("date")
    )


def slot_for_user(user, slot_id, lock=False):
    household = household_for(user)
    queryset = MealSlot.objects.filter(id=slot_id, plan__household=household)
    if lock:
        queryset = queryset.select_for_update()
    else:
        queryset = queryset.select_related("recipe", "plan")
    slot = queryset.first()
    if not slot:
        raise Http404
    return slot


def validate_entry(*, household, entry_type, recipe_id, servings, notes):
    if entry_type not in MealSlot.EntryType.values:
        raise ValueError("Unbekannter Eintragstyp.")
    if entry_type != MealSlot.EntryType.RECIPE:
        if not (notes or "").strip():
            raise ValueError("Reste und Notizen brauchen einen kurzen Text.")
        return None, None
    recipe = Recipe.objects.filter(
        id=recipe_id, household=household, status=Recipe.Status.APPROVED
    ).first()
    if not recipe:
        raise ValueError("Nur veröffentlichte Rezepte können eingeplant werden.")
    if servings in (None, ""):
        servings = recipe.servings or 2
    try:
        servings = int(servings)
    except (TypeError, ValueError) as exc:
        raise ValueError("Portionen müssen eine positive ganze Zahl sein.") from exc
    if servings < 1:
        raise ValueError("Portionen müssen eine positive ganze Zahl sein.")
    return recipe, servings


@transaction.atomic
def add_slot(*, user, week_start, date, slot, entry_type, recipe_id=None, servings=None, notes=""):
    household = household_for(user)
    plan = get_or_create_plan(user=user, week_start=week_start)
    if slot not in MealSlot.Slot.values:
        raise ValueError("Unbekannte Mahlzeit.")
    if not (plan.week_start_date <= date <= plan.week_start_date + timedelta(days=6)):
        raise ValueError("Das Datum liegt außerhalb der gewählten Woche.")
    recipe, resolved_servings = validate_entry(
        household=household,
        entry_type=entry_type,
        recipe_id=recipe_id,
        servings=servings,
        notes=notes,
    )
    return MealSlot.objects.create(
        plan=plan,
        date=date,
        slot=slot,
        entry_type=entry_type,
        recipe=recipe,
        servings=resolved_servings,
        notes=(notes or "").strip()[:300],
    )


@transaction.atomic
def update_slot(*, user, slot_id, version, servings=None, notes=None, date=None, slot=None):
    entry = slot_for_user(user, slot_id, lock=True)
    if entry.version != version:
        raise StaleSlotVersion(entry)
    if entry.cooked_at and (date is not None or slot is not None):
        raise SlotNotCookable("Gekochte Mahlzeiten können nicht verschoben werden.")
    if date is not None:
        week_start = entry.plan.week_start_date
        if not (week_start <= date <= week_start + timedelta(days=6)):
            raise ValueError("Das Datum liegt außerhalb der gewählten Woche.")
        entry.date = date
    if slot is not None:
        if slot not in MealSlot.Slot.values:
            raise ValueError("Unbekannte Mahlzeit.")
        entry.slot = slot
    if servings is not None and entry.entry_type == MealSlot.EntryType.RECIPE:
        try:
            servings = int(servings)
        except (TypeError, ValueError) as exc:
            raise ValueError("Portionen müssen eine positive ganze Zahl sein.") from exc
        if servings < 1:
            raise ValueError("Portionen müssen eine positive ganze Zahl sein.")
        entry.servings = servings
    if notes is not None:
        entry.notes = notes.strip()[:300]
    entry.version += 1
    entry.save()
    return entry


def shift_slot(*, user, slot_id, version, day_delta=0, slot_delta=0):
    """Explicit keyboard/touch movement; drag-and-drop is an optional enhancement."""

    entry = slot_for_user(user, slot_id)
    week_start = entry.plan.week_start_date
    target_date = entry.date + timedelta(days=day_delta)
    target_date = min(max(target_date, week_start), week_start + timedelta(days=6))
    index = SLOT_SEQUENCE.index(entry.slot)
    target_slot = SLOT_SEQUENCE[min(max(index + slot_delta, 0), len(SLOT_SEQUENCE) - 1)]
    return update_slot(
        user=user, slot_id=slot_id, version=version, date=target_date, slot=target_slot
    )


@transaction.atomic
def delete_slot(*, user, slot_id):
    entry = slot_for_user(user, slot_id, lock=True)
    CookEvent.objects.filter(meal_slot=entry).update(meal_slot=None)
    entry.delete()


@transaction.atomic
def mark_cooked(*, user, slot_id, slot_version, inventory_changes=()):
    """Records one cook event and only the inventory changes the user explicitly chose."""

    household = household_for(user)
    entry = slot_for_user(user, slot_id, lock=True)
    if entry.entry_type != MealSlot.EntryType.RECIPE or not entry.recipe_id:
        raise SlotNotCookable("Nur Rezept-Einträge können als gekocht markiert werden.")
    if entry.version != slot_version:
        raise StaleSlotVersion(entry)
    if entry.cooked_at:
        raise SlotNotCookable("Diese Mahlzeit ist bereits als gekocht markiert.")
    allowed = {
        str(ingredient_id)
        for ingredient_id in entry.recipe.ingredients.exclude(
            canonical_ingredient__isnull=True
        ).values_list("canonical_ingredient_id", flat=True)
    }
    entry.cooked_at = timezone.now()
    entry.version += 1
    entry.save(update_fields=["cooked_at", "version"])
    CookEvent.objects.create(household=household, recipe=entry.recipe, meal_slot=entry, actor=user)
    for change in inventory_changes:
        ingredient_id = str(change.get("ingredientId") or change.get("ingredient_id") or "")
        if ingredient_id not in allowed:
            raise ValueError("Diese Zutat gehört nicht zum gekochten Rezept.")
        status = change.get("status")
        if status not in InventoryItem.Status.values:
            raise ValueError("Ungültiger Verfügbarkeitsstatus.")
        ingredient = next(
            line.canonical_ingredient
            for line in entry.recipe.ingredients.all()
            if str(line.canonical_ingredient_id) == ingredient_id
        )
        result = record_cooking_change(
            user=user,
            household=household,
            ingredient=ingredient,
            status=status,
            version=change.get("version"),
            meal_slot=entry,
        )
        if result is None:
            raise StaleSlotVersion(entry)
    return entry


@transaction.atomic
def undo_cooked(*, user, slot_id):
    entry = slot_for_user(user, slot_id, lock=True)
    if not entry.cooked_at:
        raise SlotNotCookable("Diese Mahlzeit ist nicht als gekocht markiert.")
    CookEvent.objects.filter(meal_slot=entry).delete()
    entry.cooked_at = None
    entry.version += 1
    entry.save(update_fields=["cooked_at", "version"])
    return entry
