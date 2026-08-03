import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from pantry.models import (
    CanonicalIngredient,
    IngredientCategory,
    InventoryItem,
    PantryCategorizationJob,
)
from planning.models import MealPlan, MealSlot
from planning.services import current_week_start
from recipes.models import Recipe, RecipeImageJob
from recommendations.generation import (
    GenerationAdmissionError,
    generation_configuration,
    retry_generated_recipe,
)
from recommendations.models import GeneratedRecipeJob, GenerationDailyUsage
from shopping.models import ShoppingItem, ShoppingList

from .models import (
    Household,
    HouseholdInvitation,
    HouseholdMembership,
    ProviderDiagnostic,
    WorkerHeartbeat,
)
from .observability import log_event
from .services import household_for, owner_household_for

logger = logging.getLogger(__name__)


def liveness(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def worker_readiness(request):
    try:
        heartbeat = WorkerHeartbeat.objects.filter(name="default").first()
    except DatabaseError:
        log_event(logger, "health.worker_database_unavailable", level=logging.ERROR)
        return JsonResponse(
            {"status": "unavailable", "reason": "database_unavailable"}, status=503
        )
    if not heartbeat:
        return JsonResponse({"status": "unavailable", "reason": "worker_not_seen"}, status=503)
    max_age = timezone.timedelta(seconds=settings.WORKER_HEARTBEAT_MAX_AGE_SECONDS)
    if timezone.now() - heartbeat.last_heartbeat_at > max_age:
        return JsonResponse({"status": "unavailable", "reason": "worker_stale"}, status=503)
    return JsonResponse({"status": "ok", "workerState": heartbeat.state})


def job_state_counts(model, *, household):
    counts = {state: 0 for state, _ in model.State.choices}
    queryset = model.for_household(household)
    for row in queryset.values("state").annotate(count=Count("id")):
        counts[row["state"]] = row["count"]
    return counts


@login_required
def operations_page(request):
    household = owner_household_for(request.user)
    heartbeat = WorkerHeartbeat.objects.filter(name="default").first()
    worker_max_age = timezone.timedelta(seconds=settings.WORKER_HEARTBEAT_MAX_AGE_SECONDS)
    worker_is_fresh = bool(
        heartbeat and timezone.now() - heartbeat.last_heartbeat_at <= worker_max_age
    )
    categories = list(IngredientCategory.objects.filter(household=household))
    ingredients = list(CanonicalIngredient.objects.filter(household=household, active=True))
    today = timezone.localdate()
    generation_usage = GenerationDailyUsage.objects.filter(
        household=household, date__year=today.year, date__month=today.month
    ).aggregate(
        reserved_calls=Sum("reserved_calls"),
        used_calls=Sum("used_calls"),
        reserved_input_tokens=Sum("reserved_input_tokens"),
        used_input_tokens=Sum("used_input_tokens"),
        reserved_output_tokens=Sum("reserved_output_tokens"),
        used_output_tokens=Sum("used_output_tokens"),
    )
    return render(
        request,
        "core/operations.html",
        {
            "household": household,
            "heartbeat": heartbeat,
            "worker_is_fresh": worker_is_fresh,
            "worker_max_age_seconds": settings.WORKER_HEARTBEAT_MAX_AGE_SECONDS,
            "category_jobs": PantryCategorizationJob.objects.filter(household=household)[:20],
            "image_jobs": RecipeImageJob.objects.filter(recipe__household=household)
            .select_related("recipe")[:20],
            "provider_diagnostics": ProviderDiagnostic.objects.filter(household=household)[:20],
            "category_queue_counts": job_state_counts(PantryCategorizationJob, household=household),
            "image_queue_counts": job_state_counts(RecipeImageJob, household=household),
            "generation_queue_counts": job_state_counts(
                GeneratedRecipeJob, household=household
            ),
            "generation_jobs": GeneratedRecipeJob.objects.filter(household=household)
            .select_related("recommendation_run", "recipe")[:20],
            "generation_config": generation_configuration(),
            "generation_usage": {
                key: value or 0 for key, value in generation_usage.items()
            },
            "category_embedding_count": sum(bool(category.embedding) for category in categories),
            "category_count": len(categories),
            "ingredient_embedding_count": sum(
                bool(ingredient.embedding) for ingredient in ingredients
            ),
            "ingredient_count": len(ingredients),
            "assigned_ingredient_count": sum(
                ingredient.category_id is not None for ingredient in ingredients
            ),
        },
    )


@login_required
@require_POST
def retry_category_job(request, job_id):
    household = owner_household_for(request.user)
    job = PantryCategorizationJob.objects.filter(id=job_id, household=household).first()
    if not job:
        raise Http404
    if job.state == PantryCategorizationJob.State.FAILED:
        job.state = PantryCategorizationJob.State.QUEUED
        job.error_message = ""
        job.error_code = ""
        job.started_at = None
        job.finished_at = None
        job.correlation_id = getattr(request, "request_id", None)
        job.save(
            update_fields=[
                "state",
                "error_message",
                "error_code",
                "started_at",
                "finished_at",
                "correlation_id",
            ]
        )
        log_event(
            logger,
            "admin.job_requeued",
            job_type="pantry_category",
            job_id=job.id,
            household_id=household.id,
        )
    return redirect("operations")


@login_required
@require_POST
def retry_image_job(request, job_id):
    household = owner_household_for(request.user)
    job = (
        RecipeImageJob.objects.select_related("recipe")
        .filter(id=job_id, recipe__household=household)
        .first()
    )
    if not job:
        raise Http404
    if job.state == RecipeImageJob.State.FAILED:
        job.state = RecipeImageJob.State.QUEUED
        job.error_message = ""
        job.error_code = ""
        job.started_at = None
        job.finished_at = None
        job.correlation_id = getattr(request, "request_id", None)
        job.save(
            update_fields=[
                "state",
                "error_message",
                "error_code",
                "started_at",
                "finished_at",
                "correlation_id",
            ]
        )
        job.recipe.image_status = "pending"
        job.recipe.save(update_fields=["image_status"])
        log_event(
            logger,
            "admin.job_requeued",
            job_type="recipe_image",
            job_id=job.id,
            household_id=household.id,
        )
    return redirect("operations")


@login_required
@require_POST
def retry_generation_job(request, job_id):
    owner_household_for(request.user)
    try:
        retry_generated_recipe(user=request.user, job_id=job_id, owner=True)
    except GenerationAdmissionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Generierungsauftrag wurde erneut eingereiht.")
    return redirect("operations")


def home(request):
    if not request.user.is_authenticated:
        return render(request, "landing.html")
    if not HouseholdMembership.objects.filter(user=request.user).exists():
        return redirect("household-onboarding")
    household = household_for(request.user)
    today = timezone.localdate()
    plan = MealPlan.objects.filter(
        household=household, week_start_date=current_week_start()
    ).first()
    today_slots = (
        MealSlot.objects.select_related("recipe")
        .filter(plan=plan, date=today)
        .order_by("created_at")
        if plan
        else MealSlot.objects.none()
    )
    active_list = ShoppingList.objects.filter(
        household=household, state=ShoppingList.State.ACTIVE
    ).first()
    open_items = (
        ShoppingItem.objects.filter(
            shopping_list=active_list, state=ShoppingItem.State.OPEN
        ).count()
        if active_list
        else 0
    )
    shopping_reminders = []
    if active_list:
        tomorrow = today + timezone.timedelta(days=1)
        due_items = ShoppingItem.objects.filter(
            shopping_list=active_list, state=ShoppingItem.State.OPEN
        ).order_by("label")
        for item in due_items:
            requirement = item.earliest_recipe_requirement
            if requirement and requirement["date"] in (today, tomorrow):
                shopping_reminders.append(
                    {
                        "item": item,
                        "requirement": requirement,
                        "when": "Heute" if requirement["date"] == today else "Morgen",
                    }
                )
    return render(
        request,
        "home.html",
        {
            "household": household,
            "today": today,
            "today_slots": today_slots,
            "plan": plan,
            "active_list": active_list,
            "open_items": open_items,
            "shopping_reminders": shopping_reminders,
            "recipe_count": Recipe.objects.filter(
                household=household, status=Recipe.Status.APPROVED
            ).count(),
            "in_stock_count": InventoryItem.objects.filter(
                household=household, status=InventoryItem.Status.IN_STOCK
            ).count(),
            "replenish_count": InventoryItem.objects.filter(
                household=household, status=InventoryItem.Status.NEEDS_REPLENISHMENT
            ).count(),
            "uncategorized_count": CanonicalIngredient.objects.filter(
                household=household, active=True, category__isnull=True
            ).count(),
        },
    )


@login_required
def household_onboarding(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        code = request.POST.get("registration_code", "").strip().upper()
        if name:
            with transaction.atomic():
                household = Household.objects.create(name=name)
                HouseholdMembership.objects.create(
                    household=household,
                    user=request.user,
                    role=HouseholdMembership.Role.OWNER,
                )
            request.session["active_household_id"] = str(household.id)
            messages.success(request, f"{household.name} ist bereit.")
            return redirect("home")
        if code:
            invitation = HouseholdInvitation.objects.filter(
                registration_code=code, active=True
            ).first()
            if invitation:
                return join_household(request, invitation=invitation)
            messages.error(request, "Dieser Haushaltscode ist nicht gültig.")
        else:
            messages.error(request, "Gib einen Namen oder einen Haushaltscode ein.")
    return render(request, "core/household_onboarding.html")


@login_required
@require_http_methods(["GET", "POST"])
def join_household(request, token=None, invitation=None):
    invitation = invitation or HouseholdInvitation.objects.filter(
        token=token, active=True
    ).first()
    if not invitation:
        messages.error(request, "Diese Einladung ist nicht mehr gültig.")
        return redirect("household-onboarding")
    if request.method == "GET":
        return render(request, "core/household_join.html", {"invitation": invitation})
    HouseholdMembership.objects.get_or_create(
        household=invitation.household,
        user=request.user,
        defaults={"role": HouseholdMembership.Role.MEMBER},
    )
    request.session["active_household_id"] = str(invitation.household_id)
    messages.success(request, f"Du bist jetzt Teil von {invitation.household.name}.")
    return redirect("home")


@login_required
@require_POST
def switch_household(request):
    membership = HouseholdMembership.objects.filter(
        user=request.user, household_id=request.POST.get("household_id")
    ).first()
    if not membership:
        raise Http404
    request.session["active_household_id"] = str(membership.household_id)
    return redirect(request.POST.get("next") or "home")


@login_required
def household_settings(request):
    household = owner_household_for(request.user)
    if request.method == "POST":
        HouseholdInvitation.objects.create(household=household, created_by=request.user)
        messages.success(request, "Einladung erstellt.")
        return redirect("household-settings")
    return render(
        request,
        "core/household_settings.html",
        {
            "household": household,
            "invitations": HouseholdInvitation.objects.filter(
                household=household, active=True
            ).order_by("-created_at"),
        },
    )
