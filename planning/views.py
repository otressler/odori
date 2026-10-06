from collections import Counter
from datetime import date as date_type
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from core.services import household_for
from pantry.models import InventoryItem
from pantry.semantic import normalized_text
from recipes.models import Recipe, RecipeFavorite, RecommendationOutcome
from recipes.recommendations import recommend_for_user, record_outcome, week_suggestions
from recipes.semantic import STRONG_TEXT_SCORE, score_recipes

from .models import SLOT_SEQUENCE, CookEvent, MealSlot
from .services import (
    SlotNotCookable,
    StaleSlotVersion,
    add_slot,
    cook_now,
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

PICKER_TAG_LIMIT = 12
PICKER_SEARCH_LIMIT = 20
PICKER_SUGGESTION_LIMIT = 4


def week_url(week_start, *, day=None, open_slot=None):
    """Week page URL; `open_slot` reopens the planning dialog, `day` scrolls back to it."""

    url = reverse("plan-week", args=[week_start.isoformat()])
    if day and open_slot:
        url += "?" + urlencode({"date": day.isoformat(), "slot": open_slot})
    if day:
        url += f"#day-{day.isoformat()}"
    return url


def picker_recipes(request, household):
    """Approved recipes with what the planning dialog needs to filter them in the browser."""

    recipes = list(
        Recipe.objects.filter(household=household, status=Recipe.Status.APPROVED)
        .prefetch_related("ingredients", "tag_assignments__tag")
        .order_by("title")
    )
    favorites = set(
        RecipeFavorite.objects.filter(user=request.user, recipe__household=household).values_list(
            "recipe_id", flat=True
        )
    )
    recent = recently_cooked_recipe_ids(household)
    # Tags are free text, so "Schnell" and "schnell" share one chip under the commoner spelling.
    tag_counts = Counter()
    tag_spellings = {}
    for recipe in recipes:
        recipe.tag_names = [assignment.tag.name for assignment in recipe.tag_assignments.all()]
        recipe.is_favorite = recipe.id in favorites
        recipe.is_recent = recipe.id in recent
        recipe.has_thumbnail = recipe.image_status == "ready" and bool(recipe.thumbnail)
        recipe.search_text = normalized_text(
            " ".join(
                [recipe.title, *recipe.tag_names]
                + [line.source_text for line in recipe.ingredients.all()]
            )
        )
        keys = {normalized_text(name): name for name in recipe.tag_names}
        recipe.tag_keys = "|".join(keys)
        tag_counts.update(keys.keys())
        for key, name in keys.items():
            tag_spellings.setdefault(key, Counter())[name] += 1
    # Favourites lead the unfiltered list; the rest stays alphabetical.
    recipes.sort(key=lambda recipe: (not recipe.is_favorite, recipe.title.casefold()))
    top_keys = sorted(tag_counts, key=lambda key: (-tag_counts[key], key))[:PICKER_TAG_LIMIT]
    tags = [{"name": tag_spellings[key].most_common(1)[0][0], "key": key} for key in top_keys]
    return recipes, tags


def picker_suggestions(request, plan, recipes):
    """Top recommendations for the planned week, as picker cards with one reason each."""

    if len(recipes) <= PICKER_SUGGESTION_LIMIT:
        # The whole book fits on screen; repeating it as suggestions only adds noise.
        return [], None
    result = recommend_for_user(user=request.user, plan=plan)
    by_id = {recipe.id: recipe for recipe in recipes}
    suggestions = []
    for suggestion in week_suggestions(result, limit=PICKER_SUGGESTION_LIMIT):
        recipe = by_id.get(suggestion.recipe.id)
        if recipe is None:
            continue
        suggestions.append(
            {
                "recipe": recipe,
                "reason": suggestion.shared_reason,
                "in_stock": len(suggestion.matched_ingredients),
                "total": len(suggestion.matched_ingredients) + len(suggestion.missing_ingredients),
            }
        )
    return suggestions, result.run


def record_planned_suggestion(request, recipe_id):
    """Tell the recommender a dish offered in the dialog's suggestions was planned."""

    run_id = request.POST.get("recommendation_run")
    if not (run_id and recipe_id and recipe_id in request.POST.get("suggested", "").split()):
        return
    try:
        record_outcome(
            user=request.user,
            recipe_id=recipe_id,
            outcome=RecommendationOutcome.Type.PLANNED,
            run_id=run_id,
        )
    except ValueError:
        pass


def requested_cell(request, start):
    """The day and meal a no-JS "+ planen" link asks the dialog to open for."""

    try:
        day = date_type.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        return None, None
    slot = request.GET.get("slot", "")
    if not (start <= day <= start + timedelta(days=6)) or slot not in MealSlot.Slot.values:
        return None, None
    return day, slot


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
    dialog_cell = None
    dialog_day, dialog_slot = requested_cell(request, start)
    for day in days:
        for cell in day["slots"]:
            for entry in cell["entries"]:
                entry.is_duplicate = entry.recipe_id in duplicates
                entry.is_recent_repeat = entry.recipe_id in recent
            if day["date"] == dialog_day and cell["key"] == dialog_slot:
                dialog_cell = {"date": day["date"], **cell}
    recipes, tags = picker_recipes(request, household)
    suggestions, recommendation_run = picker_suggestions(request, plan, recipes)
    return render(
        request,
        "planning/week.html",
        {
            "plan": plan,
            "days": days,
            "slot_labels": [MealSlot.Slot(key).label for key in SLOT_SEQUENCE],
            "entry_types": MealSlot.EntryType.choices,
            "courses": MealSlot.Course.choices,
            "recipes": recipes,
            "recipe_tags": tags,
            "suggestions": suggestions,
            "recommendation_run": recommendation_run,
            "dialog_cell": dialog_cell,
            "previous_week": start - timedelta(days=7),
            "next_week": start + timedelta(days=7),
            "this_week": current_week_start(),
            "week_end": start + timedelta(days=6),
        },
    )


def recipe_search(request):
    """Ranked recipe ids for the planning dialog; the browser already holds the cards."""

    query = request.GET.get("q", "").strip()[:200]
    if not query:
        return JsonResponse({"results": []})
    recipes = (
        Recipe.objects.filter(household=household_for(request.user), status=Recipe.Status.APPROVED)
        .prefetch_related("ingredients", "tag_assignments__tag")
        .order_by("title")
    )
    # Weak fuzzy word overlaps are noise under an "Ähnliche Rezepte" heading; keep typos
    # (strong fuzzy hits) and real embedding neighbours.
    results = [
        (recipe, score, method)
        for recipe, score, method in score_recipes(recipes, query)
        if method == "semantic" or score >= STRONG_TEXT_SCORE
    ]
    return JsonResponse(
        {
            "results": [
                {"id": str(recipe.id), "score": round(score, 3), "method": method}
                for recipe, score, method in results[:PICKER_SEARCH_LIMIT]
            ]
        }
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
    slot = request.POST.get("slot", "")
    try:
        day = read_date(request.POST.get("date"))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(week_url(start))
    try:
        entry = add_slot(
            user=request.user,
            week_start=start,
            date=day,
            slot=slot,
            entry_type=request.POST.get("entry_type", MealSlot.EntryType.RECIPE),
            recipe_id=request.POST.get("recipe_id") or None,
            servings=request.POST.get("servings") or None,
            notes=request.POST.get("notes", ""),
            course=request.POST.get("course", ""),
            servings_for_whole_slot=request.POST.get("servings_for_whole_slot") == "on",
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        # Reopen the dialog for the same meal so the choice can be corrected in place.
        return redirect(week_url(start, day=day, open_slot=slot))
    if entry.recipe_id:
        record_planned_suggestion(request, str(entry.recipe_id))
    messages.success(request, "Mahlzeit eingeplant.")
    if request.POST.get("then") == "another":
        return redirect(week_url(start, day=day, open_slot=slot))
    return redirect(week_url(start, day=day))


def cook_now_page(request, recipe_id):
    try:
        entry = cook_now(user=request.user, recipe_id=recipe_id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("recipe-detail", recipe_id=recipe_id)
    return redirect("kitchen-mode", slot_id=entry.id)


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
            course=request.POST.get("course") if "course" in request.POST else None,
            servings_for_whole_slot=request.POST.get("servings_for_whole_slot") == "on",
        )
    except StaleSlotVersion:
        messages.error(request, "Der Plan wurde inzwischen geändert. Bitte erneut prüfen.")
    except (ValueError, SlotNotCookable) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Mahlzeit aktualisiert.")
    return redirect(week_url(week, day=entry.date))


def slot_move_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    day = entry.date
    try:
        moved = shift_slot(
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
        day = moved.date
        messages.success(request, "Mahlzeit verschoben.")
    return redirect(week_url(week, day=day))


def slot_delete_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    delete_slot(user=request.user, slot_id=slot_id)
    messages.success(request, "Mahlzeit entfernt.")
    return redirect(week_url(week, day=entry.date))


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
    rows_by_line = {row["line"].id: row for row in lines}
    steps = list(recipe.steps.prefetch_related("ingredients"))
    for step in steps:
        step.ingredient_rows = [
            rows_by_line[line.id] for line in step.ingredients.all() if line.id in rows_by_line
        ]
    return render(
        request,
        "planning/kitchen.html",
        {
            "slot": entry,
            "recipe": recipe,
            "lines": lines,
            "steps": steps,
            "status_choices": InventoryItem.Status.choices,
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
                "status": InventoryItem.Status.UNAVAILABLE,
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
        messages.success(request, "Guten Appetit! Die Mahlzeit ist im Kochbuch vermerkt.")
    return redirect(week_url(week, day=entry.date))


def slot_uncook_page(request, slot_id):
    entry = slot_for_user(request.user, slot_id)
    week = entry.plan.week_start_date
    try:
        undo_cooked(user=request.user, slot_id=slot_id)
    except SlotNotCookable as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Markierung zurückgenommen.")
    return redirect(week_url(week, day=entry.date))


def cook_history_page(request):
    household = household_for(request.user)
    events = (
        CookEvent.objects.select_related("recipe", "actor")
        .filter(household=household)
        .order_by("-cooked_at")[:100]
    )
    return render(request, "planning/history.html", {"events": events})
