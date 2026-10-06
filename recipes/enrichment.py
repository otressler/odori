"""AI enrichment that fills empty step timers and step ingredients after a recipe is written."""

import logging

from django.conf import settings

from core.jobs import run_next_job
from core.observability import current_context
from providers.foundry_recipe_import import annotate_recipe_steps

from .models import Recipe, RecipeIngredient, RecipeStep, RecipeStepEnrichmentJob

logger = logging.getLogger(__name__)
ACTIVE_STATES = (RecipeStepEnrichmentJob.State.QUEUED, RecipeStepEnrichmentJob.State.RUNNING)


def is_configured():
    return bool(
        settings.AZURE_OPENAI_ENDPOINT.startswith("https://")
        and settings.AZURE_OPENAI_API_KEY
        and settings.AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT
    )


def _ingredient_text(line):
    amount = f"{line.amount.normalize():f}" if line.amount is not None else ""
    return " ".join(part for part in (amount, line.unit, line.source_text) if part)


def queue_step_enrichment(recipe):
    if not is_configured():
        raise ValueError("Step enrichment is not configured.")
    if recipe.status == Recipe.Status.ARCHIVED:
        raise ValueError("Archived recipes cannot be enriched.")
    if not RecipeStep.objects.filter(recipe=recipe).exists():
        raise ValueError("The recipe has no steps to enrich.")
    active = RecipeStepEnrichmentJob.objects.filter(recipe=recipe, state__in=ACTIVE_STATES).first()
    if active:
        return active
    return RecipeStepEnrichmentJob.objects.create(
        recipe=recipe, correlation_id=current_context().get("request_id")
    )


def queue_step_enrichment_if_needed(recipe):
    """Queue enrichment when a step is still missing its timers or its ingredients."""
    if not is_configured():
        return None
    steps = RecipeStep.objects.filter(recipe=recipe).prefetch_related("ingredients")
    if not any(not step.timers or not step.ingredients.all() for step in steps):
        return None
    return queue_step_enrichment(recipe)


def recover_interrupted_step_enrichment_jobs():
    return RecipeStepEnrichmentJob.objects.filter(
        state=RecipeStepEnrichmentJob.State.RUNNING
    ).update(
        state=RecipeStepEnrichmentJob.State.QUEUED,
        error_message="",
        error_code="",
        started_at=None,
    )


def _annotate_steps(job):
    recipe = job.recipe
    if recipe.status == Recipe.Status.ARCHIVED:
        return None
    lines = list(RecipeIngredient.objects.filter(recipe=recipe))
    steps = list(RecipeStep.objects.filter(recipe=recipe))
    if not steps:
        return None
    annotations = annotate_recipe_steps(
        ingredients=[_ingredient_text(line) for line in lines],
        steps=[step.body for step in steps],
    )
    return {
        "line_ids": [line.id for line in lines],
        "steps": [(step.id, annotation) for step, annotation in zip(steps, annotations)],
    }


def _apply_annotations(job, result):
    """Fill only what is still empty, and only on steps and lines that were not rewritten."""
    if result is None:
        return False
    recipe = Recipe.objects.select_for_update().get(id=job.recipe_id)
    if recipe.status == Recipe.Status.ARCHIVED:
        return False
    current_lines = {line.id: line for line in RecipeIngredient.objects.filter(recipe=recipe)}
    current_steps = {
        step.id: step
        for step in RecipeStep.objects.filter(recipe=recipe).prefetch_related("ingredients")
    }
    changed = False
    for step_id, annotation in result["steps"]:
        step = current_steps.get(step_id)
        if step is None:
            continue
        if not step.timers and annotation["timers"]:
            step.timers = annotation["timers"]
            step.save(update_fields=["timers"])
            changed = True
        lines = [
            current_lines[line_id]
            for index in annotation["ingredientIndexes"]
            if (line_id := result["line_ids"][index]) in current_lines
        ]
        if not step.ingredients.all() and lines:
            step.ingredients.set(lines)
            changed = True
    if changed:
        recipe.version += 1
        recipe.save(update_fields=["version", "updated_at"])
    return True


def _fail(job, exc):
    return None


def run_next_step_enrichment_job():
    return run_next_job(
        RecipeStepEnrichmentJob,
        "recipe_step_enrichment",
        select_related=("recipe",),
        household_id_for=lambda job: job.recipe.household_id,
        process=_annotate_steps,
        succeed=_apply_annotations,
        fail=_fail,
        logger=logger,
    )
