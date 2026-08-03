import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import UUID

_context = ContextVar("odori_observability_context", default={})
_SENSITIVE_FIELD_NAMES = (
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "session",
    "token",
)


def _is_sensitive(name):
    return any(part in name.casefold() for part in _SENSITIVE_FIELD_NAMES)


def _safe_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _is_sensitive(str(key)) else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)


@contextmanager
def bind_context(**values):
    current = _context.get()
    token = _context.set({**current, **_safe_value(values)})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(logger, event, *, level=logging.INFO, **fields):
    logger.log(
        level,
        event,
        extra={"event": event, "observability_fields": _safe_value(fields)},
    )


def current_context():
    return _context.get().copy()


def record_provider_diagnostic(
    *,
    household_id,
    correlation_id,
    job_id,
    operation,
    state,
    error_code="",
    http_status=None,
    deployment="",
    vector_dimensions=None,
    input_tokens=None,
    output_tokens=None,
    duration_ms=0,
):
    from .models import ProviderDiagnostic

    diagnostic = ProviderDiagnostic.objects.create(
        household_id=household_id,
        correlation_id=correlation_id,
        job_id=job_id,
        operation=operation,
        state=state,
        error_code=error_code,
        http_status=http_status,
        deployment=deployment,
        vector_dimensions=vector_dimensions,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
    )
    if household_id:
        stale_ids = list(
            ProviderDiagnostic.objects.filter(household_id=household_id)
            .order_by("-created_at")
            .values_list("id", flat=True)[200:]
        )
        if stale_ids:
            ProviderDiagnostic.objects.filter(id__in=stale_ids).delete()
    return diagnostic


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            **_context.get(),
            **getattr(record, "observability_fields", {}),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=True)
