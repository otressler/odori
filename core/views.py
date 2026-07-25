from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from pantry.models import InventoryItem
from recipes.models import Recipe

from .services import household_for


def liveness(request):
    return JsonResponse({"status": "ok"})


def readiness(request):
    try:
        connection.ensure_connection()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


@login_required
def home(request):
    household = household_for(request.user)
    return render(
        request,
        "home.html",
        {
            "household": household,
            "recipe_count": Recipe.objects.filter(
                household=household, status=Recipe.Status.APPROVED
            ).count(),
            "in_stock_count": InventoryItem.objects.filter(
                household=household, status=InventoryItem.Status.IN_STOCK
            ).count(),
        },
    )
