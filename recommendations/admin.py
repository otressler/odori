from django.contrib import admin

from .models import RecommendationFeedback, RecommendationRun

admin.site.register(RecommendationRun)
admin.site.register(RecommendationFeedback)
