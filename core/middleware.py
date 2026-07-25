import json
import logging
import time
import uuid
from datetime import timedelta

from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone

logger = logging.getLogger("odori.request")


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        started = time.monotonic()
        response = self.get_response(request)
        response["X-Request-ID"] = request.request_id
        logger.info(
            json.dumps(
                {
                    "request_id": request.request_id,
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )
        )
        return response


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
