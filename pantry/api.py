import json

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from core.services import household_for

from .models import CanonicalIngredient, IngredientCategory, InventoryItem
from .services import change_inventory_status, merge_ingredients


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
        search = request.GET.get("q", "")
        values = CanonicalIngredient.objects.filter(
            household=household, active=True, name__icontains=search
        )[:50]
        return JsonResponse({"ingredients": [ingredient_json(item) for item in values]})
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
    return JsonResponse(ingredient_json(ingredient))


def inventory_json(item):
    return {
        "id": str(item.id),
        "ingredientId": str(item.ingredient_id),
        "ingredient": item.ingredient.name,
        "status": item.status,
        "version": item.version,
        "updatedAt": item.updated_at.isoformat(),
    }


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
        result = change_inventory_status(
            user=request.user,
            ingredient_id=change.get("ingredientId"),
            status=change["status"],
            version=change.get("version", 1),
        )
        if result is None:
            return error("stale_version", "This inventory item changed elsewhere.", 409)
        updated.append(inventory_json(result))
    return JsonResponse({"items": updated})
