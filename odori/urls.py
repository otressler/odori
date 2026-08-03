from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.urls import include, path

from core import views as core_views
from core.services import household_owner_required
from pantry import views as pantry_views

urlpatterns = [
    path("orbit/", include("orbit.urls")),
    path(
        "admin/categories",
        login_required(household_owner_required(pantry_views.category_admin_page)),
        name="admin-categories",
    ),
    path(
        "admin/operations",
        login_required(core_views.operations_page),
        name="operations",
    ),
    path(
        "admin/operations/jobs/categories/<uuid:job_id>/retry",
        login_required(core_views.retry_category_job),
        name="retry-category-job",
    ),
    path(
        "admin/operations/jobs/images/<uuid:job_id>/retry",
        login_required(core_views.retry_image_job),
        name="retry-image-job",
    ),
    path("admin/", admin.site.urls),
    path("", core_views.home, name="home"),
    path("health/live", core_views.liveness, name="liveness"),
    path("health/ready", core_views.readiness, name="readiness"),
    path("health/worker", core_views.worker_readiness, name="worker-readiness"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/", include("allauth.urls")),
    path("households/new/", core_views.household_onboarding, name="household-onboarding"),
    path("households/", core_views.household_settings, name="household-settings"),
    path("households/switch/", core_views.switch_household, name="household-switch"),
    path(
        "households/join/<uuid:token>/",
        core_views.join_household,
        name="household-join",
    ),
    path("recipes/", include("recipes.urls")),
    path("pantry/", include("pantry.urls")),
    path("plan/", include("planning.urls")),
    path("shopping/", include("shopping.urls")),
    path("recommendations/", include("recommendations.urls")),
    path("api/v1/", include("core.api_urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
