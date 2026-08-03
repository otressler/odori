import base64
import binascii
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

from core.observability import log_event, record_provider_diagnostic

logger = logging.getLogger(__name__)


class ImageGenerationError(Exception):
    def __init__(self, message, *, error_code, http_status=None):
        super().__init__(message)
        self.error_code = error_code
        self.http_status = http_status


def generate_image_bytes(
    prompt,
    *,
    household_id,
    job_id,
    correlation_id,
    operation,
    background=None,
    output_format=None,
    deployment=None,
):
    started = time.monotonic()
    deployment = settings.AZURE_OPENAI_IMAGE_DEPLOYMENT if deployment is None else deployment
    if not all((settings.AZURE_OPENAI_ENDPOINT, settings.AZURE_OPENAI_API_KEY, deployment)):
        error = ImageGenerationError(
            "Microsoft Foundry image generation is not configured.",
            error_code="missing_configuration",
        )
        _record_image_diagnostic(
            household_id=household_id,
            job_id=job_id,
            correlation_id=correlation_id,
            deployment=deployment,
            started=started,
            operation=operation,
            error=error,
        )
        raise error
    payload = {"prompt": prompt, "size": "1024x1024", "n": 1}
    if background:
        payload["background"] = background
    if output_format:
        payload["output_format"] = output_format
    request = Request(
        f"{settings.AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{deployment}/images/generations?api-version={settings.AZURE_OPENAI_IMAGE_API_VERSION}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "api-key": settings.AZURE_OPENAI_API_KEY},
        method="POST",
    )
    cause = None
    try:
        with urlopen(request, timeout=settings.AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS) as response:
            image_data = json.loads(response.read())["data"][0]["b64_json"]
    except TimeoutError as exc:
        cause = exc
        error = ImageGenerationError(
            "Microsoft Foundry image generation timed out.", error_code="timeout"
        )
    except HTTPError as exc:
        cause = exc
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except OSError:
            detail = ""
        error = ImageGenerationError(
            f"Microsoft Foundry could not generate the image. {detail}".strip(),
            error_code="http_error",
            http_status=exc.code,
        )
    except URLError as exc:
        cause = exc
        error = ImageGenerationError(
            "Microsoft Foundry could not generate the image.",
            error_code="network_error",
        )
    except (KeyError, ValueError, TypeError) as exc:
        cause = exc
        error = ImageGenerationError(
            "Microsoft Foundry returned an invalid image response.",
            error_code="invalid_response",
        )
    else:
        try:
            image_bytes = base64.b64decode(image_data, validate=True)
        except (binascii.Error, TypeError) as exc:
            cause = exc
            error = ImageGenerationError(
                "Microsoft Foundry returned an invalid image response.",
                error_code="invalid_response",
            )
        else:
            _record_image_diagnostic(
                household_id=household_id,
                job_id=job_id,
                correlation_id=correlation_id,
                deployment=deployment,
                started=started,
                operation=operation,
            )
            return image_bytes
    _record_image_diagnostic(
        household_id=household_id,
        job_id=job_id,
        correlation_id=correlation_id,
        deployment=deployment,
        started=started,
        operation=operation,
        error=error,
    )
    if cause is not None:
        raise error from cause
    raise error


def _record_image_diagnostic(
    *,
    household_id,
    job_id,
    correlation_id,
    deployment,
    started,
    operation,
    error=None,
):
    duration_ms = round((time.monotonic() - started) * 1000)
    state = "failed" if error else "succeeded"
    record_provider_diagnostic(
        household_id=household_id,
        correlation_id=correlation_id,
        job_id=job_id,
        operation=operation,
        state=state,
        error_code=error.error_code if error else "",
        http_status=error.http_status if error else None,
        deployment=deployment,
        duration_ms=duration_ms,
    )
    log_event(
        logger,
        "provider.image_generation_completed",
        level=logging.WARNING if error else logging.INFO,
        job_id=job_id,
        household_id=household_id,
        provider_state=state,
        error_code=error.error_code if error else "",
        http_status=error.http_status if error else None,
        deployment=deployment,
        duration_ms=duration_ms,
    )
