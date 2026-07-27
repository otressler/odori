from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from core.services import household_for

from .models import CanonicalIngredient, InventoryItem, PantryCategorizationJob
from .services import (
    PlannedIngredientInUse,
    change_inventory_status,
    merge_ingredients,
    queue_category_suggestions,
    similar_ingredient_recommendations,
)


def attach_upcoming_requirements(items, household):
    ingredient_ids = [item.ingredient_id for item in items]
    if not ingredient_ids:
        return
    from planning.models import MealSlot

    references = {}
    slots = (
        MealSlot.objects.filter(
            plan__household=household,
            entry_type=MealSlot.EntryType.RECIPE,
            cooked_at__isnull=True,
            date__gte=timezone.localdate(),
            recipe__ingredients__canonical_ingredient_id__in=ingredient_ids,
        )
        .values("recipe__ingredients__canonical_ingredient_id", "recipe__title", "date")
        .order_by("date", "created_at")
    )
    for slot in slots:
        ingredient_id = slot["recipe__ingredients__canonical_ingredient_id"]
        reference = {"title": slot["recipe__title"] or "Unbenanntes Rezept", "date": slot["date"]}
        if reference not in references.setdefault(ingredient_id, []):
            references[ingredient_id].append(reference)
    for item in items:
        item.upcoming_requirements = references.get(item.ingredient_id, [])


def inventory_page(request):
    household = household_for(request.user)
    query = request.GET.get("q", "").strip()
    selected_status = request.GET.get("status", "")
    items = list(
        InventoryItem.objects.select_related("ingredient")
        .filter(household=household)
        .order_by(
            "ingredient__category__sort_order",
            "ingredient__category__name",
            "ingredient__name",
        )
    )
    if query:
        items = items.filter(ingredient__name__icontains=query)
    if selected_status in InventoryItem.Status.values:
        items = [item for item in items if item.status == selected_status]
    attach_upcoming_requirements(items, household)
    category_job = (
        PantryCategorizationJob.objects.filter(household=household)
        .order_by("-created_at")
        .first()
    )
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
            "category_job": category_job,
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
    if created:
        from .semantic import update_embedding

        update_embedding(ingredient)
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


def inventory_condense_page(request):
    return render(
        request,
        "pantry/condense.html",
        {"recommendations": similar_ingredient_recommendations(user=request.user)},
    )


def inventory_confirm_merge_page(request):
    source_id = request.POST.get("source_id")
    target_id = request.POST.get("target_id")
    try:
        target = merge_ingredients(user=request.user, source_id=source_id, target_id=target_id)
    except Http404:
        messages.error(request, "Dieser Vorschlag ist nicht mehr verfügbar.")
    else:
        messages.success(request, f"Zutaten unter „{target.name}“ zusammengeführt.")
    return redirect("inventory-condense")


def inventory_category_suggestions_page(request):
    _, created = queue_category_suggestions(user=request.user)
    if created:
        messages.success(request, "Die Warengruppen werden im Hintergrund vorgeschlagen.")
    else:
        messages.info(request, "Warengruppen werden bereits vorgeschlagen.")
    return redirect("inventory-page")
