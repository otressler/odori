from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CanonicalIngredient


@receiver(post_save, sender=CanonicalIngredient)
def queue_icon_for_new_ingredient(sender, instance, created, **kwargs):
    if not created:
        return
    transaction.on_commit(lambda: _queue_icon(instance.id))


def _queue_icon(ingredient_id):
    from .images import queue_ingredient_icon

    ingredient = CanonicalIngredient.objects.filter(id=ingredient_id).first()
    if ingredient:
        queue_ingredient_icon(ingredient)