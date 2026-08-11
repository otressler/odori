from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.http import require_POST

from . import views

urlpatterns = [
    path("", login_required(views.recipe_list), name="recipe-list"),
    path("new/", login_required(views.recipe_create_page), name="recipe-create"),
    path("<uuid:recipe_id>/edit/", login_required(views.recipe_edit_page), name="recipe-edit"),
    path(
        "<uuid:recipe_id>/approve/",
        login_required(require_POST(views.recipe_approve_page)),
        name="recipe-approve",
    ),
    path(
        "<uuid:recipe_id>/archive/",
        login_required(require_POST(views.recipe_archive_page)),
        name="recipe-archive",
    ),
    path(
        "<uuid:recipe_id>/favorite/",
        login_required(require_POST(views.recipe_favorite_page)),
        name="recipe-favorite",
    ),
    path(
        "<uuid:recipe_id>/revise/",
        login_required(require_POST(views.recipe_revision_page)),
        name="recipe-revision",
    ),
    path(
        "<uuid:recipe_id>/image/regenerate/",
        login_required(require_POST(views.recipe_image_regenerate_page)),
        name="recipe-image-regenerate",
    ),
    path(
        "<uuid:recipe_id>/image/",
        login_required(views.recipe_image),
        name="recipe-image",
    ),
    path(
        "<uuid:recipe_id>/thumbnail/",
        login_required(views.recipe_thumbnail),
        name="recipe-thumbnail",
    ),
    path(
        "<uuid:recipe_id>/ingredients/<uuid:ingredient_id>/add-to-pantry/",
        login_required(require_POST(views.recipe_ingredient_to_pantry_page)),
        name="recipe-ingredient-to-pantry",
    ),
    path("<uuid:recipe_id>/", login_required(views.recipe_detail_page), name="recipe-detail"),
]
