from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from pantry.models import InventoryItem
from planning.models import MealSlot
from planning.services import current_week_start, get_or_create_plan
from recipes.models import Recipe
from shopping.models import ShoppingItem, ShoppingList

from .services import household_for


def liveness(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
def home(request):
    household = household_for(request.user)
    today = timezone.localdate()
    plan = get_or_create_plan(user=request.user, week_start=current_week_start())
    today_slots = (
        MealSlot.objects.select_related("recipe")
        .filter(plan=plan, date=today)
        .order_by("created_at")
    )
    active_list = ShoppingList.objects.filter(
        household=household, state=ShoppingList.State.ACTIVE
    ).first()
    open_items = (
        ShoppingItem.objects.filter(
            shopping_list=active_list, state=ShoppingItem.State.OPEN
        ).count()
        if active_list
        else 0
    )
    shopping_reminders = []
    if active_list:
        tomorrow = today + timezone.timedelta(days=1)
        due_items = ShoppingItem.objects.filter(
            shopping_list=active_list, state=ShoppingItem.State.OPEN
        ).order_by("label")
        for item in due_items:
            requirement = item.earliest_recipe_requirement
            if requirement and requirement["date"] in (today, tomorrow):
                shopping_reminders.append(
                    {
                        "item": item,
                        "requirement": requirement,
                        "when": "Heute" if requirement["date"] == today else "Morgen",
                    }
                )
    return render(
        request,
        "home.html",
        {
            "household": household,
            "today": today,
            "today_slots": today_slots,
            "plan": plan,
            "active_list": active_list,
            "open_items": open_items,
            "shopping_reminders": shopping_reminders,
            "recipe_count": Recipe.objects.filter(
                household=household, status=Recipe.Status.APPROVED
            ).count(),
            "in_stock_count": InventoryItem.objects.filter(
                household=household, status=InventoryItem.Status.IN_STOCK
            ).count(),
            "replenish_count": InventoryItem.objects.filter(
                household=household, status=InventoryItem.Status.NEEDS_REPLENISHMENT
            ).count(),
        },
    )
