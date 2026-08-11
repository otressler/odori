from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from core.services import household_for

from .catalog import sync_starter_catalog
from .models import (
    CanonicalIngredient,
    IngredientCategory,
    IngredientCategoryExample,
    InventoryItem,
    PantryCategorizationJob,
)
from .semantic import embed_with_diagnostics
from .services import (
    PlannedIngredientInUse,
    add_category_example,
    category_review_items,
    change_inventory_status,
    classify_category,
    confirm_ingredient_category,
    curate_ingredient_category,
    merge_ingredients,
    queue_category_suggestions,
    remove_inventory_item,
    similar_ingredient_recommendations,
    toggle_category_example,
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
    selected_filter = request.GET.get("filter", "")
    items = (
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
    items = list(items)
    if selected_status in InventoryItem.Status.values:
        items = [item for item in items if item.status == selected_status]
    attach_upcoming_requirements(items, household)
    today = timezone.localdate()
    next_week = today + timedelta(days=7)
    next_week_count = sum(
        any(today <= requirement["date"] < next_week for requirement in item.upcoming_requirements)
        for item in items
    )
    if selected_filter == "next_week":
        items = [
            item
            for item in items
            if any(
                today <= requirement["date"] < next_week
                for requirement in item.upcoming_requirements
            )
        ]
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
    category_filters = [
        {
            "value": "next_week",
            "label": "Nächste Woche",
            "count": next_week_count,
        }
    ]
    return render(
        request,
        "pantry/inventory.html",
        {
            "items": items,
            "query": query,
            "selected_status": selected_status,
            "selected_filter": selected_filter,
            "status_choices": InventoryItem.Status.choices,
            "status_filters": status_filters,
            "category_filters": category_filters,
            "category_job": category_job,
        },
    )


def ingredient_icon(request, ingredient_id):
    ingredient = CanonicalIngredient.objects.filter(
        id=ingredient_id, household=household_for(request.user)
    ).first()
    if not ingredient or not ingredient.icon:
        raise Http404
    return FileResponse(ingredient.icon.open("rb"))


def category_admin_page(request):
    household = household_for(request.user)
    sync_starter_catalog(household=household)
    categories = list(
        IngredientCategory.objects.filter(household=household)
        .prefetch_related("examples")
        .order_by("sort_order", "name")
    )
    test_text = ""
    similarity_results = []
    embedding_test = None
    classification_result = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reassign":
            ingredient = curate_ingredient_category(
                user=request.user,
                ingredient_id=request.POST.get("ingredient_id"),
                category_id=request.POST.get("category_id"),
            )
            messages.success(
                request,
                f"{ingredient.name} wurde neu zugeordnet und als bestätigtes Beispiel gespeichert.",
            )
            return redirect("admin-categories")
        if action == "add-example":
            example = add_category_example(
                user=request.user,
                category_id=request.POST.get("category_id"),
                text=request.POST.get("text", ""),
            )
            queue_category_suggestions(user=request.user)
            messages.success(request, f"„{example.text}“ wird jetzt als Beispiel verwendet.")
            return redirect("admin-categories")
        if action == "toggle-example":
            example = toggle_category_example(
                user=request.user,
                example_id=request.POST.get("example_id"),
            )
            queue_category_suggestions(user=request.user)
            state = "aktiv" if example.active else "pausiert"
            messages.success(request, f"Das Beispiel „{example.text}“ ist jetzt {state}.")
            return redirect("admin-categories")
        if action == "save":
            changed = 0
            with transaction.atomic():
                for category in categories:
                    description = request.POST.get(
                        f"description-{category.id}", ""
                    ).strip()
                    if description == category.description:
                        continue
                    category.description = description
                    category.save(update_fields=["description"])
                    changed += 1
                if changed:
                    _, queued = queue_category_suggestions(user=request.user)
            if changed:
                messages.success(
                    request,
                    f"{changed} Beschreibung(en) gespeichert. "
                    + (
                        "Die Kategoriezuordnung wird im Hintergrund aktualisiert."
                        if queued
                        else "Die Kategoriezuordnung wird bereits aktualisiert."
                    ),
                )
            return redirect("admin-categories")
        if action == "test":
            test_text = request.POST.get("ingredient", "").strip()
            if test_text:
                embedding_test = embed_with_diagnostics(
                    test_text,
                    household_id=household.id,
                    operation="category_test",
                )
                if embedding_test.vector is None:
                    reason_messages = {
                        "disabled": "Embeddings sind deaktiviert.",
                        "missing_configuration": "Die Embedding-Konfiguration ist unvollständig.",
                        "timeout": "Der Embedding-Aufruf hat das Zeitlimit überschritten.",
                        "network_error": "Der Embedding-Dienst ist nicht erreichbar.",
                        "http_error": "Der Embedding-Dienst hat einen HTTP-Fehler gemeldet.",
                        "invalid_response": (
                            "Der Embedding-Dienst hat eine ungültige Antwort gesendet."
                        ),
                    }
                    messages.error(
                        request,
                        "Der Test konnte nicht ausgeführt werden: "
                        + reason_messages.get(
                            embedding_test.error_code,
                            "kein Embedding vom Modell erhalten.",
                        ),
                    )
                else:
                    classification_result = classify_category(
                        name=test_text,
                        ingredient_embedding=embedding_test.vector,
                        ingredient_embedding_model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
                        categories=categories,
                    )
                    similarity_results = [
                        {
                            "category": candidate["category"],
                            "text_score": float(candidate["exact_match"]),
                            "similarity": candidate["embedding_score"],
                            "final_score": candidate["score"],
                            "best_example": candidate["best_example"],
                            "matched_examples": candidate["matched_examples"],
                        }
                        for candidate in classification_result["candidates"]
                    ]
                    similarity_results.sort(
                        key=lambda result: (
                            result["final_score"] is not None,
                            result["final_score"] or 0.0,
                            result["similarity"] is not None,
                        ),
                        reverse=True,
                    )

    assignments = list(
        CanonicalIngredient.objects.filter(
            household=household,
            active=True,
            category__isnull=False,
        )
        .select_related("category")
        .order_by("category__sort_order", "category__name", "name")
    )
    assignments_by_category = {}
    for ingredient in assignments:
        assignments_by_category.setdefault(ingredient.category_id, []).append(ingredient)
    for category in categories:
        category.assigned_items = assignments_by_category.get(category.id, [])
        category.active_example_count = sum(
            example.active for example in category.examples.all()
        )

    return render(
        request,
        "pantry/category_admin.html",
        {
            "categories": categories,
            "assignment_count": len(assignments),
            "active_example_count": IngredientCategoryExample.objects.filter(
                household=household, active=True
            ).count(),
            "test_text": test_text,
            "similarity_results": similarity_results,
            "classification_result": classification_result,
            "embedding_test": embedding_test,
            "embedding_deployment": settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
            "request_id": getattr(request, "request_id", ""),
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


def inventory_remove_page(request, ingredient_id):
    remove_inventory_item(user=request.user, ingredient_id=ingredient_id)
    messages.success(request, "Zutat aus dem Vorrat entfernt.")
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
    if created:
        queue_category_suggestions(user=request.user)
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


def category_review_page(request):
    return render(
        request,
        "pantry/category_review.html",
        {"review_items": category_review_items(user=request.user)},
    )


def category_review_assign_page(request, ingredient_id):
    confirm_ingredient_category(
        user=request.user,
        ingredient_id=ingredient_id,
        category_id=request.POST.get("category_id"),
    )
    messages.success(request, "Warengruppe gespeichert. Odori merkt sich diese Zuordnung.")
    return redirect("category-review")
