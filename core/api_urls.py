from django.urls import path

from pantry import api as pantry_api
from recipes import api as recipes_api

urlpatterns = [
    path("ingredients", pantry_api.ingredients),
    path("ingredients/<uuid:ingredient_id>", pantry_api.ingredient_detail),
    path("inventory", pantry_api.inventory),
    path("recipes", recipes_api.recipe_collection),
    path("recipes/<uuid:recipe_id>", recipes_api.recipe_detail),
    path("recipes/<uuid:recipe_id>/approve", recipes_api.approve),
    path("recipes/<uuid:recipe_id>/favorite", recipes_api.favorite),
]
