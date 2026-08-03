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
]
