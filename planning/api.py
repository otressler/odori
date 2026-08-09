from datetime import date as date_type

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from core.api import error, read_json
from core.services import household_for

from .models import CookEvent, MealSlot
from .services import (
    SlotNotCookable,
    StaleSlotVersion,
    add_slot,
    delete_slot,
    duplicate_recipe_ids,
    get_or_create_plan,
    mark_cooked,
    parse_week_start,
    plan_slots,
    recently_cooked_recipe_ids,
    update_slot,
)


def slot_json(slot, *, duplicate_ids=(), recent_ids=()):
    return {
        "id": str(slot.id),
        "date": slot.date.isoformat(),
        "slot": slot.slot,
        "entryType": slot.entry_type,
        "recipeId": str(slot.recipe_id) if slot.recipe_id else None,
        "servings": slot.servings,
        "notes": slot.notes,
        "cookedAt": slot.cooked_at.isoformat() if slot.cooked_at else None,
        "version": slot.version,
        "isDuplicate": slot.recipe_id in duplicate_ids,
        "isRecentRepeat": slot.recipe_id in recent_ids,
    }


def plan_json(plan, user):
    duplicate_ids = duplicate_recipe_ids(plan)
    recent_ids = recently_cooked_recipe_ids(household_for(user))
    return {
        "id": str(plan.id),
        "weekStart": plan.week_start_date.isoformat(),
        "version": plan.version,
        "slots": [
            slot_json(slot, duplicate_ids=duplicate_ids, recent_ids=recent_ids)
            for slot in plan_slots(plan)
        ],
    }


def read_iso_date(value):
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("date must be an ISO date (YYYY-MM-DD).") from exc


@login_required
@require_http_methods(["GET", "PUT"])
def meal_plan(request, week_start):
    try:
        start = parse_week_start(week_start)
    except ValueError as exc:
        return error("validation_failed", str(exc), fields={"weekStart": "Invalid."})
    plan = get_or_create_plan(user=request.user, week_start=start)
    return JsonResponse(plan_json(plan, request.user))


@login_required
@require_http_methods(["POST"])
def meal_plan_slots(request, week_start):
    data = read_json(request)
    if data is None:
        return error("malformed_input", "Expected a JSON object.", 400)
    try:
        slot = add_slot(
            user=request.user,
            week_start=parse_week_start(week_start),
            date=read_iso_date(data.get("date")),
            slot=data.get("slot", ""),
            entry_type=data.get("entryType", MealSlot.EntryType.RECIPE),
            recipe_id=data.get("recipeId"),
            servings=data.get("servings"),
            notes=data.get("notes", ""),
        )
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(
        slot_json(
            slot,
            duplicate_ids=duplicate_recipe_ids(slot.plan),
            recent_ids=recently_cooked_recipe_ids(household_for(request.user)),
        ),
        status=201,
    )


@login_required
@require_http_methods(["PATCH", "DELETE"])
def meal_slot_detail(request, slot_id):
    if request.method == "DELETE":
        delete_slot(user=request.user, slot_id=slot_id)
        return JsonResponse({}, status=204)
    data = read_json(request)
    if data is None:
        return error("malformed_input", "Expected a JSON object.", 400)
    try:
        slot = update_slot(
            user=request.user,
            slot_id=slot_id,
            version=data.get("version"),
            servings=data.get("servings"),
            notes=data.get("notes"),
            date=read_iso_date(data["date"]) if data.get("date") else None,
            slot=data.get("slot"),
        )
    except StaleSlotVersion as conflict:
        return error(
            "stale_version",
            "This meal slot changed elsewhere.",
            409,
            fields={"version": conflict.slot.version},
        )
    except (ValueError, SlotNotCookable) as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(
        slot_json(
            slot,
            duplicate_ids=duplicate_recipe_ids(slot.plan),
            recent_ids=recently_cooked_recipe_ids(household_for(request.user)),
        )
    )


@login_required
@require_http_methods(["POST"])
def meal_slot_mark_cooked(request, slot_id):
    data = read_json(request) or {}
    changes = data.get("inventoryChanges", [])
    if not isinstance(changes, list):
        return error(
            "validation_failed",
            "inventoryChanges must be an array.",
            fields={"inventoryChanges": "Invalid."},
        )
    try:
        slot = mark_cooked(
            user=request.user,
            slot_id=slot_id,
            slot_version=data.get("slotVersion"),
            inventory_changes=changes,
        )
    except StaleSlotVersion as conflict:
        return error(
            "stale_version",
            "This meal slot changed elsewhere.",
            409,
            fields={"version": conflict.slot.version},
        )
    except SlotNotCookable as exc:
        return error("invalid_state", str(exc), 409)
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(
        slot_json(
            slot,
            duplicate_ids=duplicate_recipe_ids(slot.plan),
            recent_ids=recently_cooked_recipe_ids(household_for(request.user)),
        )
    )


@login_required
@require_http_methods(["GET"])
def cook_history(request):
    events = (
        CookEvent.objects.select_related("recipe")
        .filter(household=household_for(request.user))
        .order_by("-cooked_at")[:100]
    )
    return JsonResponse(
        {
            "events": [
                {
                    "id": str(event.id),
                    "recipeId": str(event.recipe_id),
                    "title": event.recipe.title,
                    "cookedAt": event.cooked_at.isoformat(),
                }
                for event in events
            ]
        }
    )
