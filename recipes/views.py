import re
from decimal import Decimal

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from core.services import household_for
from pantry.models import CanonicalIngredient

from .models import Recipe
from .services import (
    approve_recipe,
    archive_recipe,
    create_or_update_recipe,
    create_recipe_revision,
    toggle_favorite,
)


def recipe_list(request):
    household = household_for(request.user)
    query = request.GET.get("q", "").strip()
    recipes = (
        Recipe.objects.filter(household=household)
        .exclude(status=Recipe.Status.ARCHIVED)
        .order_by("title")
    )
    if query:
        recipes = recipes.filter(title__icontains=query)
    return render(request, "recipes/list.html", {"recipes": recipes, "query": query})


def recipe_form_context(user, recipe=None):
    household = household_for(user)
    ingredient_rows = []
    step_rows = []
    tags = ""
    if recipe:
        ingredient_rows = [
            {
                "source_text": line.source_text,
                "amount": line.amount,
                "unit": line.unit,
                "canonical_ingredient_id": line.canonical_ingredient_id,
            }
            for line in recipe.ingredients.all()
        ]
        step_rows = [{"body": step.body} for step in recipe.steps.all()]
        tags = ", ".join(recipe.tag_assignments.values_list("tag__name", flat=True))
    ingredient_rows.append({})
    step_rows.append({})
    return {
        "recipe": recipe,
        "ingredient_rows": ingredient_rows,
        "step_rows": step_rows,
        "canonical_ingredients": CanonicalIngredient.objects.filter(
            household=household, active=True
        ),
        "tags": tags,
    }


def recipe_form_data(request):
    ingredients = []
    ingredient_indexes = sorted(
        int(match.group(1))
        for field_name in request.POST
        if (match := re.fullmatch(r"ingredient-source-(\d+)", field_name))
    )
    for index in ingredient_indexes:
        source_text = request.POST.get(f"ingredient-source-{index}", "").strip()
        if not source_text:
            continue
        ingredient = {"sourceText": source_text}
        for key, field in (("amount", "ingredient-amount"), ("unit", "ingredient-unit")):
            value = request.POST.get(f"{field}-{index}", "").strip()
            if value:
                ingredient[key] = value
        canonical_id = request.POST.get(f"ingredient-canonical-{index}", "")
        if canonical_id:
            ingredient["canonicalIngredientId"] = canonical_id
        ingredients.append(ingredient)
    step_indexes = sorted(
        int(match.group(1))
        for field_name in request.POST
        if (match := re.fullmatch(r"step-(\d+)", field_name))
    )
    steps = [
        {"body": request.POST.get(f"step-{index}", "").strip()}
        for index in step_indexes
        if request.POST.get(f"step-{index}", "").strip()
    ]
    return {
        "title": request.POST.get("title", "").strip(),
        "servings": request.POST.get("servings", "").strip() or None,
        "ingredients": ingredients,
        "steps": steps,
        "tags": [tag.strip() for tag in request.POST.get("tags", "").split(",") if tag.strip()],
    }


def save_recipe_form(request, recipe=None):
    try:
        return create_or_update_recipe(
            user=request.user, data=recipe_form_data(request), recipe=recipe
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return None


def recipe_create_page(request):
    if request.method == "POST":
        recipe = save_recipe_form(request)
        if recipe:
            messages.success(
                request, "Entwurf gespeichert. Zutaten können anschließend zugeordnet werden."
            )
            return redirect("recipe-detail", recipe_id=recipe.id)
    return render(request, "recipes/form.html", recipe_form_context(request.user))


def recipe_edit_page(request, recipe_id):
    household = household_for(request.user)
    recipe = (
        Recipe.objects.prefetch_related("ingredients", "steps", "tag_assignments__tag")
        .filter(id=recipe_id, household=household)
        .first()
    )
    if not recipe:
        raise Http404
    if recipe.status != Recipe.Status.DRAFT:
        messages.error(request, "Veröffentlichte Rezepte werden als neuer Entwurf überarbeitet.")
        return redirect("recipe-detail", recipe_id=recipe.id)
    if request.method == "POST":
        saved_recipe = save_recipe_form(request, recipe)
        if saved_recipe:
            messages.success(request, "Entwurf aktualisiert.")
            return redirect("recipe-detail", recipe_id=saved_recipe.id)
    return render(request, "recipes/form.html", recipe_form_context(request.user, recipe))


def recipe_detail_page(request, recipe_id):
    household = household_for(request.user)
    recipe = (
        Recipe.objects.prefetch_related("ingredients", "steps", "tag_assignments__tag")
        .filter(id=recipe_id, household=household)
        .first()
    )
    if not recipe:
        raise Http404
    requested_servings = recipe.servings
    if request.GET.get("servings"):
        try:
            requested_servings = max(1, int(request.GET["servings"]))
        except ValueError:
            messages.error(request, "Portionen müssen eine positive ganze Zahl sein.")
    factor = Decimal(1)
    if recipe.servings and requested_servings:
        factor = Decimal(requested_servings) / recipe.servings
    for ingredient in recipe.ingredients.all():
        ingredient.scaled_amount = (
            (ingredient.amount * factor).quantize(Decimal("0.01"))
            if ingredient.amount is not None
            else None
        )
    return render(
        request,
        "recipes/detail.html",
        {
            "recipe": recipe,
            "requested_servings": requested_servings,
            "is_favorite": recipe.favorites.filter(user=request.user).exists(),
        },
    )


def recipe_approve_page(request, recipe_id):
    recipe = recipe_for_user(request.user, recipe_id)
    try:
        approve_recipe(recipe)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Rezept veröffentlicht.")
    return redirect("recipe-detail", recipe_id=recipe.id)


def recipe_archive_page(request, recipe_id):
    recipe = recipe_for_user(request.user, recipe_id)
    archive_recipe(recipe)
    messages.success(request, "Rezept archiviert.")
    return redirect("recipe-list")


def recipe_favorite_page(request, recipe_id):
    recipe = recipe_for_user(request.user, recipe_id)
    is_favorite = toggle_favorite(recipe, request.user)
    messages.success(request, "Als Favorit gespeichert." if is_favorite else "Favorit entfernt.")
    return redirect("recipe-detail", recipe_id=recipe.id)


def recipe_revision_page(request, recipe_id):
    recipe = recipe_for_user(request.user, recipe_id)
    try:
        revision = create_recipe_revision(recipe, request.user)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("recipe-detail", recipe_id=recipe.id)
    messages.success(request, "Ein neuer Entwurf wurde erstellt.")
    return redirect("recipe-edit", recipe_id=revision.id)


def recipe_for_user(user, recipe_id):
    recipe = (
        Recipe.objects.prefetch_related("ingredients", "steps", "tag_assignments__tag")
        .filter(id=recipe_id, household=household_for(user))
        .first()
    )
    if not recipe:
        raise Http404
    return recipe
