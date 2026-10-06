import json

from django import template

register = template.Library()

# Job, worker and provider states share one vocabulary on the operations page.
STATE_LABELS = {
    "queued": "Wartend",
    "running": "Läuft",
    "succeeded": "Erfolgreich",
    "failed": "Fehlgeschlagen",
    "cancelled": "Abgebrochen",
    "superseded": "Ersetzt",
    "skipped": "Übersprungen",
    "idle": "Bereit",
    "working": "Arbeitet",
    "degraded": "Beeinträchtigt",
}

STATE_TONES = {
    "queued": "waiting",
    "running": "active",
    "working": "active",
    "succeeded": "ok",
    "idle": "ok",
    "failed": "error",
    "degraded": "warning",
}


@register.filter
def state_label(state):
    return STATE_LABELS.get(state, state)


@register.filter
def state_tone(state):
    return STATE_TONES.get(state, "neutral")


@register.filter
def pretty_json(text):
    """Indent JSON for reading; leave anything else as it is."""
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return text
