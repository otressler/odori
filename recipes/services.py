from django.db import transaction
from django.utils import timezone

from core.services import household_for
from pantry.models import CanonicalIngredient
from pantry.semantic import best_match

from .contracts import RecipeDraft, validate_recipe_draft
from .images import queue_recipe_image, queue_recipe_image_if_needed
from .models import (
    Recipe,
    RecipeFavorite,
    RecipeIngredient,
    RecipeSource,
    RecipeStep,
    RecipeTag,
    RecipeTagAssignment,
)
from .semantic import update_search_embedding


class StaleRecipeVersion(Exception):
    pass


def create_or_update_recipe(
    *,
    user,
    data,
    recipe=None,
    source_type=RecipeSource.Type.MANUAL,
    queue_image=True,
    unmatched_state=RecipeIngredient.MatchState.UNRESOLVED,
):
    household = household_for(user)
    is_new_recipe = recipe is None
    draft = (
        data
        if isinstance(data, RecipeDraft)
        else validate_recipe_draft(data, partial=True)
    )
    data = draft.as_data()
    matched_ingredients = {}
    if "ingredients" in data:
        active_ingredients = list(
            CanonicalIngredient.objects.filter(household=household, active=True)
        )
        for index, line in enumerate(data["ingredients"]):
            if not line.get("canonicalIngredientId") and line.get("sourceText"):
                matched_ingredients[index] = best_match(active_ingredients, line["sourceText"])
    with transaction.atomic():
        if recipe is None:
            source = RecipeSource.objects.create(household=household, type=source_type)
            recipe = Recipe.objects.create(household=household, source=source, created_by=user)
        elif recipe.source.type != source_type:
            source_type = recipe.source.type
        if recipe.status == Recipe.Status.ARCHIVED:
            raise ValueError("Archived recipes cannot be edited.")
        for key in ("title", "description", "servings"):
            if key in data:
                setattr(recipe, key, data[key])
        recipe.version += 1
        recipe.save()
        if "ingredients" in data:
            RecipeIngredient.objects.filter(recipe=recipe).delete()
            for index, line in enumerate(data["ingredients"]):
                ingredient = matched_ingredients.get(index)
                if line.get("canonicalIngredientId"):
                    ingredient = CanonicalIngredient.objects.filter(
                        id=line["canonicalIngredientId"], household=household
                    ).first()
                    if not ingredient:
                        raise ValueError("Ingredient does not belong to this household.")
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    canonical_ingredient=ingredient,
                    source_text=line.get("sourceText", "").strip(),
                    amount=line.get("amount"),
                    unit=line.get("unit", "").strip(),
                    optional=bool(line.get("optional", False)),
                    sort_order=index,
                    match_state=RecipeIngredient.MatchState.MATCHED
                    if ingredient
                    else unmatched_state,
                )
        if "steps" in data:
            RecipeStep.objects.filter(recipe=recipe).delete()
            for index, step in enumerate(data["steps"]):
                body = (
                    step.get("body", "").strip()
                )
                if body:
                    RecipeStep.objects.create(
                        recipe=recipe,
                        body=body,
                        sort_order=index,
                        timer_seconds=step.get("timerSeconds"),
                    )
        if "tags" in data:
            RecipeTagAssignment.objects.filter(recipe=recipe).delete()
            for name in data["tags"]:
                tag, _ = RecipeTag.objects.get_or_create(
                    household=household, name=name.strip().lower()
                )
                RecipeTagAssignment.objects.create(recipe=recipe, tag=tag)
        if queue_image:
            queue_recipe_image_if_needed(recipe)
    if is_new_recipe or {"title", "description", "ingredients", "tags"} & data.keys():
        update_search_embedding(recipe)
    return recipe


@transaction.atomic
def regenerate_recipe_image(recipe):
    return queue_recipe_image(recipe)


@transaction.atomic
def approve_recipe(recipe):
    if not recipe.title.strip() or not recipe.steps.exists():
        raise ValueError("A title and at least one instruction are required before approval.")
    if (
        recipe.source.type == RecipeSource.Type.GENERATED
        and recipe.ingredients.filter(optional=False)
        .exclude(match_state=RecipeIngredient.MatchState.MATCHED)
        .exists()
    ):
        raise ValueError(
            "Generated recipes require every required ingredient to be matched before approval."
        )
    recipe.status = Recipe.Status.APPROVED
    recipe.version += 1
    recipe.save(update_fields=["status", "version", "updated_at"])
    return recipe


@transaction.atomic
def create_recipe_revision(recipe, user):
    if recipe.status != Recipe.Status.APPROVED:
        raise ValueError("Only approved recipes can be revised.")
    revision = Recipe.objects.create(
        household=recipe.household,
        source=recipe.source,
        created_by=user,
        title=recipe.title,
        servings=recipe.servings,
    )
    RecipeIngredient.objects.bulk_create(
        [
            RecipeIngredient(
                recipe=revision,
                canonical_ingredient=line.canonical_ingredient,
                source_text=line.source_text,
                amount=line.amount,
                unit=line.unit,
                optional=line.optional,
                sort_order=line.sort_order,
                match_state=line.match_state,
            )
            for line in recipe.ingredients.all()
        ]
    )
    RecipeStep.objects.bulk_create(
        [
            RecipeStep(
                recipe=revision,
                body=step.body,
                sort_order=step.sort_order,
                timer_seconds=step.timer_seconds,
            )
            for step in recipe.steps.all()
        ]
    )
    RecipeTagAssignment.objects.bulk_create(
        [
            RecipeTagAssignment(recipe=revision, tag=assignment.tag)
            for assignment in recipe.tag_assignments.all()
        ]
    )
    return revision


@transaction.atomic
def archive_recipe(recipe, *, version):
    recipe = Recipe.objects.select_for_update().get(id=recipe.id)
    if recipe.version != version:
        raise StaleRecipeVersion
    recipe.status = Recipe.Status.ARCHIVED
    recipe.archived_at = timezone.now()
    recipe.version += 1
    recipe.save(update_fields=["status", "archived_at", "version", "updated_at"])
    return recipe


@transaction.atomic
def toggle_favorite(recipe, user):
    favorite, created = RecipeFavorite.objects.get_or_create(recipe=recipe, user=user)
    if not created:
        favorite.delete()
    return created
