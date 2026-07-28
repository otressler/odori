import json

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from core.services import household_for

from .catalog import sync_starter_catalog
from .models import CanonicalIngredient, IngredientCategory, InventoryItem
from .semantic import embed_with_diagnostics, rank_ingredients
from .services import (
    PlannedIngredientInUse,
    change_inventory_status,
    classify_category,
    merge_ingredients,
    queue_category_suggestions,
)


def payload(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


def error(code, message, status=422, fields=None):
    body = {"error": {"code": code, "message": message}}
    if fields:
        body["error"]["fields"] = fields
    return JsonResponse(body, status=status)


def ingredient_json(ingredient):
    return {
        "id": str(ingredient.id),
        "name": ingredient.name,
        "aliases": ingredient.aliases,
        "active": ingredient.active,
        "categoryId": str(ingredient.category_id) if ingredient.category_id else None,
    }


@login_required
@require_http_methods(["GET", "POST"])
def ingredients(request):
    household = household_for(request.user)
    if request.method == "GET":
        search = request.GET.get("q", "").strip()
        values = CanonicalIngredient.objects.filter(household=household, active=True)
        ranked = (
            rank_ingredients(values, search)[:50]
            if search
            else [(item, 0, False) for item in values[:50]]
        )
        return JsonResponse(
            {
                "ingredients": [
                    {**ingredient_json(item), "matchScore": round(score, 3), "semantic": semantic}
                    for item, score, semantic in ranked
                ]
            }
        )
    data = payload(request)
    if not data or not isinstance(data.get("name"), str) or not data["name"].strip():
        return error("validation_failed", "A name is required.", fields={"name": "Required."})
    category = None
    if data.get("categoryId"):
        category = IngredientCategory.objects.filter(
            id=data["categoryId"], household=household
        ).first()
        if not category:
            raise Http404
    try:
        ingredient = CanonicalIngredient.objects.create(
            household=household,
            name=data["name"].strip(),
            category=category,
            aliases=data.get("aliases", []),
        )
    except IntegrityError:
        return error(
            "validation_failed",
            "This ingredient already exists.",
            fields={"name": "Already exists."},
        )
    queue_category_suggestions(user=request.user)
    return JsonResponse(ingredient_json(ingredient), status=201)


@login_required
@require_http_methods(["PATCH"])
def ingredient_detail(request, ingredient_id):
    data = payload(request)
    if not data:
        return error("malformed_input", "Expected a JSON object.", 400)
    household = household_for(request.user)
    ingredient = CanonicalIngredient.objects.filter(id=ingredient_id, household=household).first()
    if not ingredient:
        raise Http404
    if "mergeIntoId" in data:
        merged = merge_ingredients(
            user=request.user, source_id=ingredient_id, target_id=data["mergeIntoId"]
        )
        return JsonResponse(ingredient_json(merged))
    for key in ("name", "aliases", "active"):
        if key in data:
            setattr(ingredient, key, data[key])
    ingredient.save()
    if "name" in data or "aliases" in data:
        queue_category_suggestions(user=request.user)
    return JsonResponse(ingredient_json(ingredient))


@login_required
@require_http_methods(["GET"])
def ingredient_category_scores(request):
    household = household_for(request.user)
    ingredient_id = request.GET.get("ingredientId")
    name = request.GET.get("name", "").strip()
    if ingredient_id:
        ingredient = CanonicalIngredient.objects.filter(
            id=ingredient_id, household=household
        ).first()
        if not ingredient:
            raise Http404
        name = ingredient.name
        embedding = ingredient.embedding
        aliases = ingredient.aliases
        embedding_model = ingredient.embedding_model
        ingredient_json_data = {
            "id": str(ingredient.id),
            "name": ingredient.name,
            "embeddingModel": ingredient.embedding_model or None,
        }
    elif name:
        embedding_result = embed_with_diagnostics(
            name,
            household_id=household.id,
            operation="category_score_test",
        )
        embedding = embedding_result.vector or []
        aliases = []
        embedding_model = (
            settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT if embedding_result.vector else ""
        )
        ingredient_json_data = {"id": None, "name": name, "embeddingModel": None}
    else:
        return error(
            "validation_failed",
            "Provide ingredientId or name.",
            fields={"ingredientId": "Required when name is omitted."},
        )

    sync_starter_catalog(household=household)
    categories = list(
        IngredientCategory.objects.filter(household=household)
        .prefetch_related("examples")
        .order_by("sort_order", "name")
    )
    classification = classify_category(
        name=name,
        aliases=aliases,
        ingredient_embedding=embedding,
        ingredient_embedding_model=embedding_model,
        categories=categories,
    )
    scores = [
        {
            "id": str(candidate["category"].id),
            "name": candidate["category"].name,
            "exactMatch": candidate["exact_match"],
            "embeddingScore": (
                round(candidate["embedding_score"], 3)
                if candidate["embedding_score"] is not None
                else None
            ),
            "score": round(candidate["score"], 3) if candidate["score"] is not None else None,
            "embeddingUsed": candidate["embedding_score"] is not None,
            "bestExample": candidate["best_example"].text if candidate["best_example"] else None,
            "matchedExamples": [example.text for example in candidate["matched_examples"]],
        }
        for candidate in classification["candidates"]
    ]
    scores.sort(key=lambda item: (item["score"] is None, -(item["score"] or 0.0), item["name"]))
    return JsonResponse(
        {
            "ingredient": ingredient_json_data,
            "state": classification["state"],
            "topCategory": classification["category"].name if classification["category"] else None,
            "runnerUpCategory": (
                classification["runner_up"].name if classification["runner_up"] else None
            ),
            "topScore": (
                round(classification["score"], 3) if classification["score"] is not None else None
            ),
            "runnerUpScore": (
                round(classification["runner_up_score"], 3)
                if classification["runner_up_score"] is not None
                else None
            ),
            "margin": (
                round(classification["margin"], 3)
                if classification["margin"] is not None
                else None
            ),
            "minimumSimilarity": classification.get("minimum_similarity"),
            "minimumMargin": classification.get("minimum_margin"),
            "embeddingUsed": any(item["embeddingUsed"] for item in scores),
            "categories": scores,
        }
    )


def inventory_json(item):
    return {
        "id": str(item.id),
        "ingredientId": str(item.ingredient_id),
        "ingredient": item.ingredient.name,
        "status": item.status,
        "version": item.version,
        "updatedAt": item.updated_at.isoformat(),
    }


def planned_slots_json(slots):
    return [
        {
            "slotId": str(slot.id),
            "date": slot.date.isoformat(),
            "slot": slot.slot,
            "recipeId": str(slot.recipe_id) if slot.recipe_id else None,
            "title": slot.recipe.title if slot.recipe_id else slot.notes,
        }
        for slot in slots
    ]


def planned_conflict(conflict):
    body = {
        "error": {
            "code": "planned_ingredient_in_use",
            "message": "Diese Zutat wird für geplante Mahlzeiten gebraucht.",
            "plannedSlots": planned_slots_json(conflict.slots),
        }
    }
    return JsonResponse(body, status=409)


@login_required
@require_http_methods(["GET", "PATCH"])
def inventory(request):
    household = household_for(request.user)
    if request.method == "GET":
        items = (
            InventoryItem.objects.select_related("ingredient")
            .filter(household=household)
            .order_by("ingredient__name")
        )
        return JsonResponse({"items": [inventory_json(item) for item in items]})
    data = payload(request)
    changes = data.get("items") if data else None
    if not isinstance(changes, list):
        return error("validation_failed", "items must be an array.", fields={"items": "Required."})
    updated = []
    for change in changes:
        if change.get("status") not in InventoryItem.Status.values:
            return error(
                "validation_failed", "Invalid availability status.", fields={"status": "Invalid."}
            )
        try:
            result = change_inventory_status(
                user=request.user,
                ingredient_id=change.get("ingredientId"),
                status=change["status"],
                version=change.get("version", 1),
                confirm_planned_use=bool(change.get("confirmPlannedUse")),
            )
        except PlannedIngredientInUse as conflict:
            return planned_conflict(conflict)
        if result is None:
            return error("stale_version", "This inventory item changed elsewhere.", 409)
        updated.append(inventory_json(result))
    return JsonResponse({"items": updated})


@login_required
@require_http_methods(["POST"])
def change_status(request, ingredient_id):
    """Two-step availability change: `confirmPlannedUse` is an explicit user decision."""

    data = payload(request)
    if data is None:
        return error("malformed_input", "Expected a JSON object.", 400)
    if data.get("status") not in InventoryItem.Status.values:
        return error(
            "validation_failed", "Invalid availability status.", fields={"status": "Invalid."}
        )
    try:
        result = change_inventory_status(
            user=request.user,
            ingredient_id=ingredient_id,
            status=data["status"],
            version=data.get("version", 1),
            confirm_planned_use=bool(data.get("confirmPlannedUse")),
        )
    except PlannedIngredientInUse as conflict:
        return planned_conflict(conflict)
    if result is None:
        return error("stale_version", "This inventory item changed elsewhere.", 409)
    return JsonResponse(inventory_json(result))
