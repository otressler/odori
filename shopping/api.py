from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from core.services import household_for
from pantry.api import error, payload
from planning.services import parse_week_start

from .models import ShoppingList
from .services import (
    StaleItemVersion,
    add_manual_item,
    delete_item,
    generate_from_plan,
    list_for_user,
    purchase_item,
    set_item_state,
)


def item_json(item):
    return {
        "id": str(item.id),
        "listId": str(item.shopping_list_id),
        "label": item.label,
        "ingredientId": str(item.canonical_ingredient_id) if item.canonical_ingredient_id else None,
        "quantityComponents": item.quantity_components,
        "recipeRefs": item.recipe_refs,
        "source": item.source,
        "state": item.state,
        "needsConfirmation": item.needs_confirmation,
        "version": item.version,
    }


def list_json(shopping_list, with_items=True):
    body = {
        "id": str(shopping_list.id),
        "name": shopping_list.name,
        "state": shopping_list.state,
        "planId": str(shopping_list.plan_id) if shopping_list.plan_id else None,
        "version": shopping_list.version,
        "generatedAt": shopping_list.generated_at.isoformat()
        if shopping_list.generated_at
        else None,
    }
    if with_items:
        body["items"] = [item_json(item) for item in shopping_list.items.all().order_by("label")]
    return body


@login_required
@require_http_methods(["GET"])
def shopping_lists(request):
    household = household_for(request.user)
    lists = ShoppingList.objects.filter(household=household)
    return JsonResponse({"lists": [list_json(entry, with_items=False) for entry in lists]})


@login_required
@require_http_methods(["GET"])
def shopping_list_detail(request, list_id):
    return JsonResponse(list_json(list_for_user(request.user, list_id)))


@login_required
@require_http_methods(["POST"])
def generate(request, week_start):
    try:
        start = parse_week_start(week_start)
    except ValueError as exc:
        return error("validation_failed", str(exc), fields={"weekStart": "Invalid."})
    try:
        shopping_list = generate_from_plan(user=request.user, week_start=start)
    except Http404:
        return error("not_found", "No plan exists for this week.", 404)
    return JsonResponse(list_json(shopping_list), status=201)


@login_required
@require_http_methods(["POST"])
def shopping_items(request, list_id):
    data = payload(request)
    if data is None:
        return error("malformed_input", "Expected a JSON object.", 400)
    try:
        item = add_manual_item(
            user=request.user,
            list_id=list_id,
            label=data.get("label", ""),
            ingredient_id=data.get("ingredientId"),
        )
    except ValueError as exc:
        return error("validation_failed", str(exc), fields={"label": "Required."})
    return JsonResponse(item_json(item), status=201)


@login_required
@require_http_methods(["PATCH", "DELETE"])
def shopping_item_detail(request, list_id, item_id):
    if request.method == "DELETE":
        data = payload(request)
        if not isinstance(data, dict):
            return error("malformed_input", "Expected a JSON object.", 400)
        try:
            delete_item(user=request.user, item_id=item_id, version=data.get("version"))
        except StaleItemVersion as conflict:
            return error(
                "stale_version",
                "This shopping item changed elsewhere.",
                409,
                fields={"version": conflict.item.version},
            )
        return JsonResponse({}, status=204)
    data = payload(request)
    if data is None:
        return error("malformed_input", "Expected a JSON object.", 400)
    try:
        item = set_item_state(
            user=request.user,
            item_id=item_id,
            version=data.get("version"),
            state=data.get("state", ""),
        )
    except StaleItemVersion as conflict:
        return error(
            "stale_version",
            "This shopping item changed elsewhere.",
            409,
            fields={"version": conflict.item.version},
        )
    except ValueError as exc:
        return error("validation_failed", str(exc), fields={"state": "Invalid."})
    return JsonResponse(item_json(item))


@login_required
@require_http_methods(["POST"])
def purchase(request, list_id, item_id):
    data = payload(request) or {}
    try:
        item = purchase_item(user=request.user, item_id=item_id, version=data.get("version"))
    except StaleItemVersion as conflict:
        return error(
            "stale_version",
            "This shopping item changed elsewhere.",
            409,
            fields={"version": conflict.item.version},
        )
    return JsonResponse(item_json(item))
