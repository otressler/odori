from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.http import require_POST

from . import views

urlpatterns = [
    path("", login_required(views.shopping_index), name="shopping-index"),
    path(
        "generate/",
        login_required(require_POST(views.shopping_generate_page)),
        name="shopping-generate",
    ),
    path("<uuid:list_id>/", login_required(views.shopping_detail), name="shopping-detail"),
    path(
        "<uuid:list_id>/items/",
        login_required(require_POST(views.shopping_item_create)),
        name="shopping-item-create",
    ),
    path(
        "<uuid:list_id>/pantry-items/",
        login_required(require_POST(views.shopping_pantry_items)),
        name="shopping-pantry-items",
    ),
    path(
        "<uuid:list_id>/complete/",
        login_required(require_POST(views.shopping_complete)),
        name="shopping-complete",
    ),
    path(
        "items/<uuid:item_id>/state/",
        login_required(require_POST(views.shopping_item_state)),
        name="shopping-item-state",
    ),
    path(
        "items/<uuid:item_id>/purchase/",
        login_required(require_POST(views.shopping_item_purchase)),
        name="shopping-item-purchase",
    ),
    path(
        "items/<uuid:item_id>/delete/",
        login_required(require_POST(views.shopping_item_delete)),
        name="shopping-item-delete",
    ),
]
