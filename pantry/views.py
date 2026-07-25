from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from core.services import household_for

from .models import CanonicalIngredient, InventoryItem
from .services import PlannedIngredientInUse, change_inventory_status


def inventory_page(request):
    household = household_for(request.user)
    query = request.GET.get("q", "").strip()
    selected_status = request.GET.get("status", "")
    items = (
        InventoryItem.objects.select_related("ingredient")
        .filter(household=household)
        .order_by("ingredient__name")
    )
    if query:
        items = items.filter(ingredient__name__icontains=query)
    if selected_status in InventoryItem.Status.values:
        items = items.filter(status=selected_status)
    status_filters = [
        {
            "value": value,
            "label": label,
            "count": InventoryItem.objects.filter(household=household, status=value).count(),
        }
        for value, label in InventoryItem.Status.choices
    ]
    return render(
        request,
        "pantry/inventory.html",
        {
            "items": items,
            "query": query,
            "selected_status": selected_status,
            "status_choices": InventoryItem.Status.choices,
            "status_filters": status_filters,
        },
    )


def inventory_status_page(request, ingredient_id):
    status = request.POST.get("status")
    if status not in InventoryItem.Status.values:
        messages.error(request, "Ungültiger Verfügbarkeitsstatus.")
        return redirect("inventory-page")
    try:
        version = int(request.POST.get("version", ""))
    except ValueError:
        messages.error(request, "Der Vorratseintrag ist nicht mehr aktuell.")
        return redirect("inventory-page")
    try:
        item = change_inventory_status(
            user=request.user,
            ingredient_id=ingredient_id,
            status=status,
            version=version,
            confirm_planned_use=request.POST.get("confirm_planned_use") == "true",
        )
    except Http404:
        raise
    except PlannedIngredientInUse as conflict:
        ingredient = CanonicalIngredient.objects.get(
            id=ingredient_id, household=household_for(request.user)
        )
        return render(
            request,
            "pantry/planned_use.html",
            {
                "ingredient": ingredient,
                "slots": conflict.slots,
                "status": status,
                "version": version,
            },
            status=409,
        )
    if item is None:
        messages.error(
            request, "Der Vorrat wurde in der Zwischenzeit geändert. Bitte erneut prüfen."
        )
    else:
        messages.success(request, "Verfügbarkeit aktualisiert.")
    return redirect("inventory-page")


def inventory_create_page(request):
    household = household_for(request.user)
    name = request.POST.get("name", "").strip()
    status = request.POST.get("status", InventoryItem.Status.UNKNOWN)
    if not name:
        messages.error(request, "Ein Zutatenname ist erforderlich.")
        return redirect("inventory-page")
    if status not in InventoryItem.Status.values:
        messages.error(request, "Ungültiger Verfügbarkeitsstatus.")
        return redirect("inventory-page")
    ingredient, created = CanonicalIngredient.objects.get_or_create(household=household, name=name)
    inventory_item_exists = InventoryItem.objects.filter(
        household=household, ingredient=ingredient
    ).exists()
    if not created and inventory_item_exists:
        messages.error(request, "Diese Zutat ist bereits im Vorrat.")
        return redirect("inventory-page")
    change_inventory_status(
        user=request.user, ingredient_id=ingredient.id, status=status, version=1
    )
    messages.success(request, "Zutat zum Vorrat hinzugefügt.")
    return redirect("inventory-page")
