from decimal import Decimal
from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from core.api import error, read_json
from core.services import household_for
from pantry.mapping import assign_recipe_ingredient
from pantry.models import CanonicalIngredient

from .generation import GeneratedDraftError, queue_recipe_generation
from .imports import create_import
from .models import GeneratedRecipeRequest, Recipe, RecipeImportJob
from .recommendations import recommend_for_user, record_outcome
from .semantic import rank_recipes
from .services import (
    StaleRecipeVersion,
    approve_recipe,
    archive_recipe,
    create_or_update_recipe,
    toggle_favorite,
)


def ingredient_json(line, factor):
    amount = (line.amount * factor).quantize(Decimal("0.01")) if line.amount is not None else None
    return {
        "id": str(line.id),
        "canonicalIngredientId": str(line.canonical_ingredient_id)
        if line.canonical_ingredient_id
        else None,
        "sourceText": line.source_text,
        "amount": str(amount) if amount is not None else None,
        "unit": line.unit,
        "optional": line.optional,
        "matchState": line.match_state,
    }


def recipe_json(recipe, user, servings=None):
    factor = Decimal(1)
    if servings and recipe.servings:
        factor = Decimal(str(servings)) / recipe.servings
    return {
        "id": str(recipe.id),
        "title": recipe.title,
        "description": recipe.description,
        "status": recipe.status,
        "servings": recipe.servings,
        "version": recipe.version,
        "ingredients": [ingredient_json(line, factor) for line in recipe.ingredients.all()],
        "steps": [
            {"id": str(step.id), "body": step.body, "timerSeconds": step.timer_seconds}
            for step in recipe.steps.all()
        ],
        "tags": [
            assignment.tag.name for assignment in recipe.tag_assignments.select_related("tag")
        ],
        "favorite": recipe.favorites.filter(user=user).exists(),
        "imageStatus": recipe.image_status,
        "source": {"type": recipe.source.type},
    }


def visible_recipe(user, recipe_id):
    recipe = (
        Recipe.objects.select_related("source")
        .prefetch_related("ingredients", "steps", "tag_assignments__tag", "favorites")
        .filter(id=recipe_id, household=household_for(user))
        .first()
    )
    if not recipe:
        raise Http404
    return recipe


@login_required
@require_http_methods(["GET", "POST"])
def recipe_collection(request):
    household = household_for(request.user)
    if request.method == "GET":
        query = request.GET.get("q", "")
        include_archived = request.GET.get("includeArchived") == "true"
        recipes = (
            Recipe.objects.select_related("source")
            .prefetch_related("ingredients", "steps", "tag_assignments__tag", "favorites")
            .filter(household=household)
        )
        if not include_archived:
            recipes = recipes.exclude(status=Recipe.Status.ARCHIVED)
        recipes = list(recipes.order_by("title"))
        if query:
            recipes = rank_recipes(recipes, query)
        return JsonResponse({"recipes": [recipe_json(recipe, request.user) for recipe in recipes]})
    data = read_json(request)
    if data is None:
        return error("malformed_input", "Expected JSON.", 400)
    try:
        recipe = create_or_update_recipe(user=request.user, data=data)
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(recipe_json(recipe, request.user), status=201)


@login_required
@require_http_methods(["GET", "PATCH", "DELETE"])
def recipe_detail(request, recipe_id):
    recipe = visible_recipe(request.user, recipe_id)
    if request.method == "GET":
        return JsonResponse(recipe_json(recipe, request.user, request.GET.get("servings")))
    if request.method == "DELETE":
        data = read_json(request)
        if not isinstance(data, dict):
            return error("malformed_input", "Expected JSON.", 400)
        try:
            archive_recipe(recipe, version=data.get("version"))
        except StaleRecipeVersion:
            return error("stale_version", "This recipe changed elsewhere.", 409)
        return JsonResponse({}, status=204)
    if recipe.status == Recipe.Status.APPROVED:
        return error(
            "approved_recipe_immutable",
            "Create a draft revision before editing an approved recipe.",
            409,
        )
    data = read_json(request)
    if data is None:
        return error("malformed_input", "Expected JSON.", 400)
    if data.get("version") != recipe.version:
        return error("stale_version", "This recipe changed elsewhere.", 409)
    try:
        recipe = create_or_update_recipe(user=request.user, data=data, recipe=recipe)
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(recipe_json(recipe, request.user))


@login_required
@require_http_methods(["POST"])
def map_ingredient(request, recipe_id, ingredient_id):
    recipe = visible_recipe(request.user, recipe_id)
    line = recipe.ingredients.filter(id=ingredient_id).first()
    if not line:
        raise Http404
    data = read_json(request)
    canonical_id = data.get("canonicalIngredientId") if isinstance(data, dict) else None
    ingredient = CanonicalIngredient.objects.filter(
        id=canonical_id, household=household_for(request.user), active=True
    ).first()
    if not ingredient:
        return error("ingredient_not_found", "Ingredient was not found.", 404)
    try:
        assign_recipe_ingredient(
            user=request.user,
            recipe=recipe,
            line=line,
            ingredient=ingredient,
        )
    except ValueError as exc:
        return error("validation_failed", str(exc), 422)
    return JsonResponse(ingredient_json(line, Decimal(1)))


@login_required
@require_http_methods(["POST"])
def approve(request, recipe_id):
    recipe = visible_recipe(request.user, recipe_id)
    try:
        approve_recipe(recipe)
    except ValueError as exc:
        return error("validation_failed", str(exc))
    return JsonResponse(recipe_json(recipe, request.user))


@login_required
@require_http_methods(["POST"])
def favorite(request, recipe_id):
    recipe = visible_recipe(request.user, recipe_id)
    return JsonResponse({"favorite": toggle_favorite(recipe, request.user)})


@login_required
@require_http_methods(["GET"])
def recommendations(request):
    result = recommend_for_user(user=request.user)
    return JsonResponse(
        {
            "runId": str(result.run.id),
            "scoringVersion": result.run.scoring_version,
            "inventorySnapshotAt": result.run.inventory_snapshot_at.isoformat(),
            "suggestions": [
                {
                    "recipeId": str(suggestion.recipe.id),
                    "title": suggestion.recipe.title,
                    "matchedIngredients": suggestion.matched_ingredients,
                    "missingIngredients": suggestion.missing_ingredients,
                    "reasons": suggestion.reasons,
                    "score": suggestion.score,
                    "scoreComponents": suggestion.score_components,
                }
                for suggestion in result.suggestions
            ],
        }
    )


@login_required
@require_http_methods(["POST"])
def recommendation_outcomes(request):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected JSON.", 400)
    try:
        outcome = record_outcome(
            user=request.user,
            recipe_id=data.get("recipeId"),
            outcome=data.get("outcome"),
            reason=data.get("reason", ""),
            run_id=data.get("runId"),
        )
    except ValueError as exc:
        code = str(exc)
        return error(
            code,
            "The recommendation outcome could not be recorded.",
            404 if code.endswith("not_found") else 422,
        )
    return JsonResponse({"id": str(outcome.id), "outcome": outcome.outcome}, status=201)


@login_required
@require_http_methods(["POST"])
def generated_recipe_draft(request):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected JSON.", 400)
    try:
        generated_request = queue_recipe_generation(user=request.user, idea=data.get("idea"))
    except GeneratedDraftError as exc:
        statuses = {
            "generation_disabled": 503,
            "generation_quota_exceeded": 429,
            "invalid_request": 422,
        }
        return error(exc.code, "The generated recipe could not be queued.", statuses[exc.code])
    return JsonResponse(
        {
            "requestId": str(generated_request.id),
            "state": generated_request.state,
            "statusUrl": f"/api/v1/generated-recipe-drafts/{generated_request.id}",
        },
        status=202,
    )


@login_required
@require_http_methods(["GET"])
def generated_recipe_draft_status(request, request_id):
    generated_request = GeneratedRecipeRequest.objects.filter(
        id=request_id, household=household_for(request.user)
    ).select_related("recipe__source").first()
    if not generated_request:
        raise Http404
    body = {
        "requestId": str(generated_request.id),
        "state": generated_request.state,
        "errorCode": generated_request.error_code or None,
    }
    if generated_request.recipe:
        body["recipe"] = recipe_json(generated_request.recipe, request.user)
    return JsonResponse(body)


@login_required
@require_http_methods(["POST"])
def recipe_import(request):
    data = read_json(request)
    if not isinstance(data, dict):
        return error("malformed_input", "Expected JSON.", 400)
    text = str(data.get("text", "")).strip()
    if text:
        if len(text) > 20_000:
            return error("invalid_text", "Recipe text must be 20,000 characters or fewer.")
        job, _ = create_import(
            household=household_for(request.user),
            source_type="text",
            content=text.encode("utf-8"),
        )
        return JsonResponse(
            {
                "importId": str(job.id),
                "state": job.state,
                "statusUrl": f"/api/v1/recipe-imports/{job.id}",
            },
            status=202,
        )
    url = str(data.get("url", "")).strip()
    parsed_url = urlparse(url)
    if len(url) > 2048 or parsed_url.scheme not in ("http", "https") or not parsed_url.netloc:
        return error("invalid_url", "A valid HTTP(S) recipe URL is required.")
    job, _ = create_import(
        household=household_for(request.user),
        source_type="url",
        url=url,
    )
    return JsonResponse(
        {
            "importId": str(job.id),
            "state": job.state,
            "statusUrl": f"/api/v1/recipe-imports/{job.id}",
        },
        status=202,
    )


@login_required
@require_http_methods(["GET"])
def recipe_import_status(request, import_id):
    job = (
        RecipeImportJob.objects.select_related("source", "recipe__source")
        .filter(id=import_id, household=household_for(request.user))
        .first()
    )
    if not job:
        raise Http404
    body = {
        "importId": str(job.id),
        "state": job.state,
        "stage": job.stage,
        "errorCode": job.error_code or None,
    }
    if job.recipe:
        body["recipe"] = recipe_json(job.recipe, request.user)
    return JsonResponse(body)
