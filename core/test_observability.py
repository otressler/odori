import io
import json
import logging

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from .middleware import RequestContextMiddleware
from .observability import JsonFormatter, log_event


def broken_response(_):
    raise RuntimeError("broken")


class RequestContextMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(JsonFormatter())
        self.logger = logging.getLogger("odori.request")
        self.logger.addHandler(self.handler)
        self.logger.setLevel(logging.INFO)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def records(self):
        return [json.loads(line) for line in self.stream.getvalue().splitlines()]

    def test_logs_completed_request_and_returns_correlation_id(self):
        request = self.factory.get("/health/live")

        response = RequestContextMiddleware(lambda _: HttpResponse("ok"))(request)

        self.assertEqual(response["X-Request-ID"], request.request_id)
        completion = next(
            record for record in self.records() if record["event"] == "request.completed"
        )
        self.assertEqual(completion["status"], 200)
        self.assertEqual(completion["request_id"], request.request_id)

    def test_logs_exception_and_completion_for_failed_request(self):
        request = self.factory.get("/broken")

        with self.assertRaisesRegex(RuntimeError, "broken"):
            RequestContextMiddleware(broken_response)(request)

        events = {record["event"] for record in self.records()}
        self.assertIn("request.unhandled_exception", events)
        self.assertIn("request.completed", events)


class JsonFormatterTests(SimpleTestCase):
    def test_redacts_sensitive_fields(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.getLogger("odori.test")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            log_event(logger, "provider.call", api_key="not-a-secret", status=200)
        finally:
            logger.removeHandler(handler)

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["api_key"], "[redacted]")
        self.assertEqual(payload["status"], 200)
