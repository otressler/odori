from django.contrib.auth.decorators import login_required
from django.urls import path
from django.views.decorators.http import require_POST

from . import views

urlpatterns = [
    path("", login_required(views.recommendation_page), name="recommendations"),
    path(
        "feedback/",
        login_required(require_POST(views.feedback_page)),
        name="recommendation-feedback",
    ),
    path(
        "<uuid:run_id>/generate/",
        login_required(require_POST(views.queue_generation_page)),
        name="queue-generation",
    ),
    path(
        "generated/<uuid:job_id>/",
        login_required(views.generation_status_page),
        name="generation-status",
    ),
    path(
        "generated/<uuid:job_id>/retry/",
        login_required(require_POST(views.retry_generation_page)),
        name="retry-generation",
    ),
]
