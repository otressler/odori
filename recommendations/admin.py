from django.contrib import admin

from .models import (
    GeneratedRecipeJob,
    GenerationDailyUsage,
    RecommendationFeedback,
    RecommendationRun,
)

admin.site.register(RecommendationRun)
admin.site.register(RecommendationFeedback)
admin.site.register(GeneratedRecipeJob)
admin.site.register(GenerationDailyUsage)
