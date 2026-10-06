"""Step annotations: labelled timers and the ingredients a step needs."""

import re
from decimal import Decimal, InvalidOperation

MAX_STEP_TIMERS = 10
MAX_TIMER_LABEL_LENGTH = 80
MAX_TIMER_SECONDS = 24 * 60 * 60
DURATION_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d{1,2})$")


def clean_timers(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > MAX_STEP_TIMERS:
        raise ValueError(f"A step can contain at most {MAX_STEP_TIMERS} timers.")
    timers = []
    for timer in value:
        label = str(timer.get("label", "")).strip() if isinstance(timer, dict) else ""
        seconds = timer.get("seconds") if isinstance(timer, dict) else None
        if (
            not label
            or len(label) > MAX_TIMER_LABEL_LENGTH
            or not isinstance(seconds, int)
            or isinstance(seconds, bool)
            or not 0 < seconds <= MAX_TIMER_SECONDS
        ):
            raise ValueError(
                "Each timer needs a label of up to "
                f"{MAX_TIMER_LABEL_LENGTH} characters and a duration of up to 24 hours."
            )
        timers.append({"label": label, "seconds": seconds})
    return timers


def clean_ingredient_indexes(value, ingredient_count):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError("Step ingredients must reference recipe ingredients.")
    indexes = []
    for index in value:
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < ingredient_count
        ):
            raise ValueError("Step ingredients must reference recipe ingredients.")
        if index not in indexes:
            indexes.append(index)
    return indexes


def parse_duration(value):
    """Read "10" or "7,5" as minutes, and "1:30" or "1:05:00" as m:ss or h:mm:ss."""
    value = str(value or "").strip()
    match = DURATION_RE.match(value)
    if match:
        hours, minutes, seconds = (int(part or 0) for part in match.groups())
        if seconds >= 60 or (hours and minutes >= 60):
            raise ValueError(f"“{value}” is not a valid timer duration.")
        total = hours * 3600 + minutes * 60 + seconds
    else:
        try:
            total = int(Decimal(value.replace(",", ".")) * 60)
        except InvalidOperation as exc:
            raise ValueError(f"“{value}” is not a valid timer duration.") from exc
    if not 0 < total <= MAX_TIMER_SECONDS:
        raise ValueError("Timer durations must be between one second and 24 hours.")
    return total


def format_duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def describe_duration(seconds):
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    parts = []
    if hours:
        parts.append(f"{hours} Std.")
    if minutes:
        parts.append(f"{minutes} Min.")
    if seconds:
        parts.append(f"{seconds} Sek.")
    return " ".join(parts)
