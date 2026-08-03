from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.services import household_for
from pantry.models import InventoryItem
from recipes.models import Recipe

from .models import SLOT_SEQUENCE, CookEvent, MealSlot
from .services import (
    SlotNotCookable,
    StaleSlotVersion,
    add_slot,
    current_week_start,
    delete_slot,
    duplicate_recipe_ids,
    get_or_create_plan,
    mark_cooked,
    parse_week_start,
    recently_cooked_recipe_ids,
    shift_slot,
    slot_for_user,
    undo_cooked,
    update_slot,
    week_grid,
)


def week_url(week_start):
    return reverse("plan-week", args=[week_start.isoformat()])


def plan_page(request, week_start=None):
    try:
        start = parse_week_start(week_start)
    except ValueError:
        messages.error(request, "Ungültige Woche. Es wird die aktuelle Woche angezeigt.")
        start = current_week_start()
    household = household_for(request.user)
    plan = get_or_create_plan(user=request.user, week_start=start)
    duplicates = duplicate_recipe_ids(plan)
    recent = recently_cooked_recipe_ids(household)
    days = week_grid(plan)
    for day in days:
        for cell in day["slots"]:
            for entry in cell["entries"]:
                entry.is_duplicate = entry.recipe_id in duplicates
                entry.is_recent_repeat = entry.recipe_id in recent
    recipes = list(
        Recipe.objects.filter(
            household=household, status=Recipe.Status.APPROVED
        ).order_by("title")
    )
    preselected_recipe_id = request.GET.get("recipe", "")
    for recipe in recipes:
        recipe.is_preselected = str(recipe.id) == preselected_recipe_id
    recommendation_run = (
        request.GET.get("recommendation_run", "")
        if any(recipe.is_preselected for recipe in recipes)
        else ""
    )
    return render(
        request,
        "planning/week.html",
        {
            "plan": plan,
            "days": days,
            "slot_choices": [(key, MealSlot.Slot(key).label) for key in SLOT_SEQUENCE],
            "entry_types": MealSlot.EntryType.choices,
            "recipes": recipes,
            "previous_week": start - timedelta(days=7),
            "next_week": start + timedelta(days=7),
            "this_week": current_week_start(),
            "week_end": start + timedelta(days=6),
            "default_date": request.GET.get("date", "") or start.isoformat(),
            "default_slot": request.GET.get("slot", "dinner"),
            "recommendation_run": recommendation_run,
        },
    )


def read_date(value):
    try:
        return date_type.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Ungültiges Datum.") from exc


def read_version(request, field="version"):
    try:
        return int(request.POST.get(field, ""))
    except ValueError as exc:
        raise ValueError("Der Eintrag ist nicht mehr aktuell.") from exc


def slot_create_page(request, week_start):
    start = parse_week_start(week_start)
    try:
        entry = add_slot(
            user=request.user,
            week_start=start,
            date=read_date(request.POST.get("date")),
            slot=request.POST.get("slot", ""),
            entry_type=request.POST.get("entry_type", MealSlot.EntryType.RECIPE),
            recipe_id=request.POST.get("recipe_id") or None,
            servings=request.POST.get("servings") or None,
            notes=request.POST.get("notes", ""),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        from recommendations.models import RecommendationFeedback
        from recommendations.services import record_feedback_safely

        record_feedback_safely(
            user=request.user,
            run_id=request.POST.get("recommendation_run"),
            recipe_id=entry.recipe_id,
            outcome=RecommendationFeedback.Outcome.PLANNED,
        )
        messages.success(request, "Mahlzeit eingeplant.")
    return redirect(week_url(start))


def slot_update_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    try:
        update_slot(
            user=request.user,
            slot_id=slot_id,
            version=read_version(request),
            servings=request.POST.get("servings") or None,
            notes=request.POST.get("notes") if "notes" in request.POST else None,
        )
    except StaleSlotVersion:
        messages.error(request, "Der Plan wurde inzwischen geändert. Bitte erneut prüfen.")
    except (ValueError, SlotNotCookable) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Mahlzeit aktualisiert.")
    return redirect(week_url(week))


def slot_move_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    try:
        shift_slot(
            user=request.user,
            slot_id=slot_id,
            version=read_version(request),
            day_delta=int(request.POST.get("day_delta", 0) or 0),
            slot_delta=int(request.POST.get("slot_delta", 0) or 0),
        )
    except StaleSlotVersion:
        messages.error(request, "Der Plan wurde inzwischen geändert. Bitte erneut prüfen.")
    except (ValueError, SlotNotCookable) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Mahlzeit verschoben.")
    return redirect(week_url(week))


def slot_delete_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    delete_slot(user=request.user, slot_id=slot_id)
    messages.success(request, "Mahlzeit entfernt.")
    return redirect(week_url(week))


def kitchen_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    if entry.entry_type != MealSlot.EntryType.RECIPE or not entry.recipe_id:
        raise Http404
    recipe = entry.recipe
    factor = Decimal(1)
    if recipe.servings and entry.servings:
        factor = Decimal(entry.servings) / Decimal(recipe.servings)
    statuses = {
        item.ingredient_id: item
        for item in InventoryItem.objects.filter(household=entry.plan.household)
    }
    lines = []
    for line in recipe.ingredients.select_related("canonical_ingredient").all():
        item = statuses.get(line.canonical_ingredient_id)
        lines.append(
            {
                "line": line,
                "scaled_amount": (line.amount * factor).quantize(Decimal("0.01"))
                if line.amount is not None
                else None,
                "inventory": item,
            }
        )
    return render(
        request,
        "planning/kitchen.html",
        {
            "slot": entry,
            "recipe": recipe,
            "lines": lines,
            "steps": recipe.steps.all(),
            "status_choices": InventoryItem.Status.choices,
            "recommendation_run": request.GET.get("recommendation_run", ""),
        },
    )


def slot_cook_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    changes = []
    for ingredient_id in request.POST.getlist("deplete"):
        item = InventoryItem.objects.filter(
            household=entry.plan.household, ingredient_id=ingredient_id
        ).first()
        changes.append(
            {
                "ingredientId": ingredient_id,
                "status": InventoryItem.Status.NEEDS_REPLENISHMENT,
                "version": item.version if item else None,
            }
        )
    try:
        mark_cooked(
            user=request.user,
            slot_id=slot_id,
            slot_version=read_version(request, "slot_version"),
            inventory_changes=changes,
        )
    except StaleSlotVersion:
        messages.error(request, "Der Plan wurde inzwischen geändert. Bitte erneut prüfen.")
    except (ValueError, SlotNotCookable) as exc:
        messages.error(request, str(exc))
    else:
        from recommendations.models import RecommendationFeedback
        from recommendations.services import record_feedback_safely

        record_feedback_safely(
            user=request.user,
            run_id=request.POST.get("recommendation_run"),
            recipe_id=entry.recipe_id,
            outcome=RecommendationFeedback.Outcome.COOKED,
        )
        messages.success(request, "Guten Appetit! Die Mahlzeit ist im Kochbuch vermerkt.")
    return redirect(week_url(week))


def slot_uncook_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    try:
        undo_cooked(user=request.user, slot_id=slot_id)
    except SlotNotCookable as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Markierung zurückgenommen.")
    return redirect(week_url(week))


def cook_history_page(request):
    household = household_for(request.user)
    events = (
        CookEvent.objects.select_related("recipe", "actor")
        .filter(household=household)
        .order_by("-cooked_at")[:100]
    )
    return render(request, "planning/history.html", {"events": events})
