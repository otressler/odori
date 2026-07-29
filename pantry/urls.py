from django.contrib.auth.decorators import login_required
from django.urls import path

from . import views

urlpatterns = [
    path("", login_required(views.inventory_page), name="inventory-page"),
    path(
        "icons/<uuid:ingredient_id>/",
        login_required(views.ingredient_icon),
        name="ingredient-icon",
    ),
    path("add/", login_required(views.inventory_create_page), name="inventory-create"),
    path(
        "categories/suggest/",
        login_required(views.inventory_category_suggestions_page),
        name="inventory-category-suggestions",
    ),
    path(
        "categories/review/",
        login_required(views.category_review_page),
        name="category-review",
    ),
    path(
        "categories/review/<uuid:ingredient_id>/assign/",
        login_required(views.category_review_assign_page),
        name="category-review-assign",
    ),
    path("condense/", login_required(views.inventory_condense_page), name="inventory-condense"),
    path(
        "condense/confirm/",
        login_required(views.inventory_confirm_merge_page),
        name="inventory-confirm-merge",
    ),
    path(
        "<uuid:ingredient_id>/status/",
        login_required(views.inventory_status_page),
        name="inventory-status",
    ),
]
