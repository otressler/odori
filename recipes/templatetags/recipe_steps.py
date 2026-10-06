from django import template

from recipes.steps import describe_duration

register = template.Library()


@register.filter
def duration(seconds):
    return describe_duration(seconds) if seconds else ""
