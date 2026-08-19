from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction


def purgeable_models():
    user_model = get_user_model()
    models = []
    seen_tables = set()
    for model in apps.get_models(include_auto_created=True):
        if model is user_model or model._meta.proxy:
            continue
        if model._meta.app_config and model._meta.app_config.label in {
            "admin",
            "auth",
            "contenttypes",
            "sites",
        }:
            continue
        table = model._meta.db_table
        if table in seen_tables:
            continue
        seen_tables.add(table)
        models.append(model)
    return models


def deletion_order(models):
    model_set = set(models)
    dependencies = {model: set() for model in models}
    for model in models:
        for field in model._meta.get_fields():
            if not field.is_relation or field.auto_created:
                continue
            remote_model = getattr(field, "related_model", None)
            if remote_model in model_set and remote_model is not model:
                dependencies[model].add(remote_model)

    ordered = []
    remaining = set(models)
    while remaining:
        ready = [
            model
            for model in remaining
            if not any(model in dependencies[other] for other in remaining if other is not model)
        ]
        if not ready:
            # Cyclic relations are uncommon here; deleting one model at a time
            # still lets the database report any genuinely unsafe constraint.
            ready = [next(iter(remaining))]
        ordered.extend(ready)
        remaining.difference_update(ready)
    return ordered


class Command(BaseCommand):
    help = "Delete all application data while preserving user accounts."

    @transaction.atomic
    def handle(self, *args, **options):
        models = purgeable_models()
        deleted = 0
        for model in deletion_order(models):
            count, _ = model._base_manager.all().delete()
            deleted += count
        self.stdout.write(
            self.style.SUCCESS(f"Purged {deleted} records; user accounts were preserved.")
        )
