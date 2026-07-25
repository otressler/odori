from django.contrib import admin
from django.urls import include, path

from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", core_views.home, name="home"),
    path("health/live", core_views.liveness, name="liveness"),
    path("health/ready", core_views.readiness, name="readiness"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("recipes/", include("recipes.urls")),
    path("pantry/", include("pantry.urls")),
    path("plan/", include("planning.urls")),
    path("shopping/", include("shopping.urls")),
    path("api/v1/", include("core.api_urls")),
]
