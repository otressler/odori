from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import urlencode

from core.services import household_for
from planning.services import current_week_start, parse_week_start
from recipes.models import RecipeTag

from .contracts import RecommendationOptions
from .generation import (
    GenerationAdmissionError,
    generation_configuration,
    queue_generated_recipe,
    retry_generated_recipe,
)
from .models import GeneratedRecipeJob, RecommendationFeedback
from .services import recommend, record_feedback

REASON_LABELS = {
    "inventory_coverage": "Viele benötigte Zutaten sind vorrätig.",
    "missing_ingredients": "Einige Zutaten müssen nachgekauft werden.",
    "unknown_ingredients": "Bei einigen Zutaten ist der Vorrat unklar.",
    "cook_recency": "Dieses Rezept gab es schon länger nicht.",
    "preferred_tags": "Passt zu deinen ausgewählten Vorlieben.",
    "already_planned": "Ist in dieser Woche bereits eingeplant.",
    "negative_feedback": "Wurde zuvor als nicht passend markiert.",
}


def _card(suggestion, run_id, week_start):
    candidate = suggestion.candidate
    query = urlencode({"recommendation_run": run_id})
    planner_query = urlencode(
        {"recipe": candidate.recipe_id, "recommendation_run": run_id}
    )
    return {
        "suggestion": suggestion,
        "candidate": candidate,
        "reason_labels": [REASON_LABELS[reason.code] for reason in suggestion.reasons],
        "detail_url": f"{reverse('recipe-detail', args=[candidate.recipe_id])}?{query}",
        "planner_url": (
            f"{reverse('plan-week', args=[week_start.isoformat()])}?{planner_query}"
        ),
    }


def recommendation_page(request):
    household = household_for(request.user)
    selected_tag_ids = tuple(request.GET.getlist("tag"))
    try:
        week_start = parse_week_start(request.GET.get("weekStart"))
    except ValueError:
        week_start = current_week_start()
        messages.error(request, "Die gewählte Woche ist ungültig.")
    result = None
    cards = []
    page_error = ""
    try:
        result = recommend(
            user=request.user,
            options=RecommendationOptions(
                week_start=week_start,
                preferred_tag_ids=selected_tag_ids,
            ),
        )
        cards = [
            _card(item, result.run_id, week_start) for item in result.suggestions
        ]
    except ValueError as exc:
        page_error = str(exc)
    return render(
        request,
        "recommendations/index.html",
        {
            "result": result,
            "cards": cards,
            "page_error": page_error,
            "week_start": week_start,
            "tags": RecipeTag.objects.filter(household=household).order_by("name"),
            "selected_tag_ids": set(selected_tag_ids),
            "feedback_reasons": RecommendationFeedback.Reason.choices,
            "generation": generation_configuration(),
        },
    )


def feedback_page(request):
    next_url = request.POST.get("next") or reverse("recommendations")
    try:
        record_feedback(
            user=request.user,
            run_id=request.POST.get("run_id"),
            recipe_id=request.POST.get("recipe_id"),
            outcome=request.POST.get(
                "outcome", RecommendationFeedback.Outcome.DISMISSED
            ),
            reason=request.POST.get("reason", ""),
        )
    except ValueError:
        messages.error(request, "Die Rückmeldung konnte nicht gespeichert werden.")
    else:
        messages.success(request, "Danke für deine Rückmeldung.")
    return redirect(next_url)


def queue_generation_page(request, run_id):
    try:
        servings = int(request.POST.get("servings", "4"))
        job, _ = queue_generated_recipe(
            user=request.user, run_id=run_id, requested_servings=servings
        )
    except (GenerationAdmissionError, ValueError):
        messages.error(request, "Der Rezeptentwurf konnte nicht gestartet werden.")
        return redirect("recommendations")
    return redirect("generation-status", job_id=job.id)


def generation_status_page(request, job_id):
    job = (
        GeneratedRecipeJob.objects.select_related("recipe")
        .filter(id=job_id, household=household_for(request.user))
        .first()
    )
    if not job:
        from django.http import Http404

        raise Http404
    return render(request, "recommendations/generation_status.html", {"job": job})


def retry_generation_page(request, job_id):
    try:
        job = retry_generated_recipe(user=request.user, job_id=job_id)
    except GenerationAdmissionError:
        messages.error(request, "Dieser Auftrag kann nicht erneut ausgeführt werden.")
        return redirect("generation-status", job_id=job_id)
    messages.success(request, "Der Auftrag wurde erneut eingereiht.")
    return redirect("generation-status", job_id=job.id)
