from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.http import require_POST

from . import views

urlpatterns = [
    path("", login_required(views.plan_page), name="plan-current"),
    path("history/", login_required(views.cook_history_page), name="cook-history"),
    path("<str:week_start>/", login_required(views.plan_page), name="plan-week"),
    path(
        "<str:week_start>/slots/",
        login_required(require_POST(views.slot_create_page)),
        name="slot-create",
    ),
    path(
        "slots/<uuid:slot_id>/update/",
        login_required(require_POST(views.slot_update_page)),
        name="slot-update",
    ),
    path(
        "slots/<uuid:slot_id>/move/",
        login_required(require_POST(views.slot_move_page)),
        name="slot-move",
    ),
    path(
        "slots/<uuid:slot_id>/delete/",
        login_required(require_POST(views.slot_delete_page)),
        name="slot-delete",
    ),
    path("slots/<uuid:slot_id>/kitchen/", login_required(views.kitchen_page), name="kitchen-mode"),
    path(
        "slots/<uuid:slot_id>/cook/",
        login_required(require_POST(views.slot_cook_page)),
        name="slot-cook",
    ),
    path(
        "slots/<uuid:slot_id>/uncook/",
        login_required(require_POST(views.slot_uncook_page)),
        name="slot-uncook",
    ),
]
