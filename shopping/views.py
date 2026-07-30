from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from core.services import household_for
from pantry.images import queue_missing_ingredient_icons
from pantry.models import CanonicalIngredient
from planning.services import current_week_start, parse_week_start

from .models import ShoppingItem, ShoppingList
from .services import (
    StaleItemVersion,
    add_manual_item,
    delete_item,
    generate_from_plan,
    item_for_user,
    list_for_user,
    purchase_item,
    set_item_state,
)


def list_url(shopping_list):
    return reverse("shopping-detail", args=[shopping_list.id])


def shopping_index(request):
    household = household_for(request.user)
    lists = (
        ShoppingList.objects.filter(household=household)
        .exclude(state=ShoppingList.State.ARCHIVED)
        .order_by("-created_at")
    )
    active = lists.filter(state=ShoppingList.State.ACTIVE).first()
    if active:
        return redirect(list_url(active))
    return render(
        request,
        "shopping/index.html",
        {"lists": lists, "week_start": current_week_start().isoformat()},
    )


def shopping_generate_page(request):
    try:
        week_start = parse_week_start(request.POST.get("week_start"))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("shopping-index")
    try:
        shopping_list = generate_from_plan(user=request.user, week_start=week_start)
    except Http404:
        messages.error(request, "Für diese Woche gibt es noch keinen Plan.")
        return redirect("plan-current")
    messages.success(request, "Einkaufsliste aktualisiert. Eigene Einträge bleiben erhalten.")
    return redirect(list_url(shopping_list))


def shopping_detail(request, list_id):
    household = household_for(request.user)
    shopping_list = list_for_user(request.user, list_id)
    items = (
        ShoppingItem.objects.select_related("canonical_ingredient")
        .filter(shopping_list=shopping_list)
        .order_by(
            "canonical_ingredient__category__sort_order",
            "canonical_ingredient__category__name",
            "label",
        )
    )
    buckets = {"open": [], "purchased": [], "skipped": []}
    for item in items:
        buckets[item.state].append(item)
    queue_missing_ingredient_icons(
        {item.canonical_ingredient for item in items if item.canonical_ingredient_id}
    )
    return render(
        request,
        "shopping/detail.html",
        {
            "list": shopping_list,
            "open_items": buckets["open"],
            "purchased_items": buckets["purchased"],
            "skipped_items": buckets["skipped"],
            "ingredients": CanonicalIngredient.objects.filter(
                household=household, active=True
            ).order_by("name"),
            "week_start": shopping_list.plan.week_start_date.isoformat()
            if shopping_list.plan_id
            else current_week_start().isoformat(),
        },
    )


def read_version(request):
    try:
        return int(request.POST.get("version", ""))
    except ValueError as exc:
        raise ValueError("Der Eintrag ist nicht mehr aktuell.") from exc


def shopping_item_create(request, list_id):
    try:
        add_manual_item(
            user=request.user,
            list_id=list_id,
            label=request.POST.get("label", ""),
            ingredient_id=request.POST.get("ingredient_id") or None,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Eintrag hinzugefügt.")
    return redirect("shopping-detail", list_id=list_id)


def shopping_item_state(request, item_id):
    item = item_for_user(request.user, item_id)
    try:
        set_item_state(
            user=request.user,
            item_id=item_id,
            version=read_version(request),
            state=request.POST.get("state", ""),
        )
    except StaleItemVersion:
        messages.error(request, "Der Eintrag wurde inzwischen geändert. Bitte erneut prüfen.")
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("shopping-detail", list_id=item.shopping_list_id)


def shopping_item_purchase(request, item_id):
    item = item_for_user(request.user, item_id)
    try:
        purchase_item(user=request.user, item_id=item_id, version=read_version(request))
    except StaleItemVersion:
        messages.error(request, "Der Eintrag wurde inzwischen geändert. Bitte erneut prüfen.")
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Gekauft und im Vorrat vermerkt.")
    return redirect("shopping-detail", list_id=item.shopping_list_id)


def shopping_item_delete(request, item_id):
    item = item_for_user(request.user, item_id)
    list_id = item.shopping_list_id
    try:
        delete_item(user=request.user, item_id=item_id, version=read_version(request))
    except StaleItemVersion:
        messages.error(request, "Der Eintrag wurde inzwischen geändert. Bitte erneut prüfen.")
    else:
        messages.success(request, "Eintrag entfernt.")
    return redirect("shopping-detail", list_id=list_id)


def shopping_complete(request, list_id):
    shopping_list = list_for_user(request.user, list_id)
    shopping_list.state = ShoppingList.State.COMPLETED
    shopping_list.version += 1
    shopping_list.save(update_fields=["state", "version"])
    messages.success(request, "Einkauf abgeschlossen.")
    return redirect("shopping-index")
