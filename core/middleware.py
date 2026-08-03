import logging
import time
import uuid
from datetime import timedelta

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone

from .observability import bind_context, log_event

logger = logging.getLogger("odori.request")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", "")
        try:
            request.request_id = str(uuid.UUID(request_id))
        except ValueError:
            request.request_id = str(uuid.uuid4())
        started = time.monotonic()
        user = getattr(request, "user", None)
        actor_id = str(user.id) if user and user.is_authenticated else None
        status = 500
        with bind_context(request_id=request.request_id, actor_id=actor_id):
            try:
                response = self.get_response(request)
                status = response.status_code
                response["X-Request-ID"] = request.request_id
                if status >= 500:
                    log_event(
                        logger,
                        "request.server_error",
                        level=logging.ERROR,
                        method=request.method,
                        path=request.path,
                        status=status,
                    )
                return response
            except Exception:
                log_event(
                    logger,
                    "request.unhandled_exception",
                    level=logging.ERROR,
                    method=request.method,
                    path=request.path,
                )
                logger.exception("Unhandled request exception")
                raise
            finally:
                log_event(
                    logger,
                    "request.completed",
                    method=request.method,
                    path=request.path,
                    status=status,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )


class AbsoluteSessionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = timezone.now()
            expires = request.session.get("absolute_session_expires_at")
            if expires and now.timestamp() >= expires:
                logout(request)
                return redirect("login")
            if not expires:
                request.session["absolute_session_expires_at"] = (
                    now + timedelta(seconds=60 * 60 * 24 * 7)
                ).timestamp()
        return self.get_response(request)


class ActiveHouseholdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.user._active_household_id = request.session.get("active_household_id")
        return self.get_response(request)
