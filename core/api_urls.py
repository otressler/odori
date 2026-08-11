from django.urls import path

from pantry import api as pantry_api
from planning import api as planning_api
from recipes import api as recipes_api
from shopping import api as shopping_api

urlpatterns = [
    path("ingredients", pantry_api.ingredients),
    path("ingredients/category-scores", pantry_api.ingredient_category_scores),
    path("ingredients/<uuid:ingredient_id>", pantry_api.ingredient_detail),
    path("inventory", pantry_api.inventory),
    path("inventory/<uuid:ingredient_id>/change-status", pantry_api.change_status),
    path("recipes", recipes_api.recipe_collection),
    path("recipes/<uuid:recipe_id>", recipes_api.recipe_detail),
    path("recipes/<uuid:recipe_id>/approve", recipes_api.approve),
    path("recipes/<uuid:recipe_id>/favorite", recipes_api.favorite),
    path("recommendations", recipes_api.recommendations),
    path("recommendation-outcomes", recipes_api.recommendation_outcomes),
    path("generated-recipe-drafts", recipes_api.generated_recipe_draft),
    path("meal-plans/<str:week_start>", planning_api.meal_plan),
    path("meal-plans/<str:week_start>/slots", planning_api.meal_plan_slots),
    path("meal-plans/<str:week_start>/shopping-lists", shopping_api.generate),
    path("meal-slots/<uuid:slot_id>", planning_api.meal_slot_detail),
    path("meal-slots/<uuid:slot_id>/mark-cooked", planning_api.meal_slot_mark_cooked),
    path("cook-events", planning_api.cook_history),
    path("shopping-lists", shopping_api.shopping_lists),
    path("shopping-lists/<uuid:list_id>", shopping_api.shopping_list_detail),
    path("shopping-lists/<uuid:list_id>/items", shopping_api.shopping_items),
    path("shopping-lists/<uuid:list_id>/items/<uuid:item_id>", shopping_api.shopping_item_detail),
    path("shopping-lists/<uuid:list_id>/items/<uuid:item_id>/purchase", shopping_api.purchase),
]
