"""Validate Azure OpenAI embedding connectivity from inside a container."""

import argparse
import json
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _read_env(name):
    return os.environ.get(name, "").strip()


def _masked(value):
    if not value:
        return "(empty)"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _print_result(result):
    prefix = "PASS" if result.ok else "FAIL"
    print(f"[{prefix}] {result.name}: {result.detail}")


def _parse_endpoint(endpoint):
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        return None, CheckResult(
            name="Endpoint format",
            ok=False,
            detail="AZURE_OPENAI_ENDPOINT must be an absolute https URL.",
        )
    return parsed, CheckResult(
        name="Endpoint format", ok=True, detail=f"Host {parsed.netloc} is a valid https endpoint."
    )


def _check_dns(host):
    started = time.monotonic()
    try:
        addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443)})
    except OSError as exc:
        return CheckResult("DNS", False, f"Could not resolve {host}: {exc}")
    duration_ms = round((time.monotonic() - started) * 1000)
    return CheckResult(
        "DNS",
        True,
        f"Resolved {host} to {', '.join(addresses)} in {duration_ms} ms.",
    )


def _check_tls(host, timeout_seconds):
    started = time.monotonic()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout_seconds) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=host) as tls_socket:
                protocol = tls_socket.version()
                cipher = tls_socket.cipher()
    except OSError as exc:
        return CheckResult("TLS handshake", False, f"TLS connection to {host}:443 failed: {exc}")
    duration_ms = round((time.monotonic() - started) * 1000)
    return CheckResult(
        "TLS handshake",
        True,
        f"Connected with {protocol} / {cipher[0]} in {duration_ms} ms.",
    )


def _check_endpoint_reachability(endpoint, timeout_seconds):
    started = time.monotonic()
    request = Request(endpoint.rstrip("/") + "/", method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
    except HTTPError as exc:
        status = exc.code
    except URLError as exc:
        return CheckResult("Endpoint reachability", False, f"Request failed: {exc.reason}")
    duration_ms = round((time.monotonic() - started) * 1000)
    return CheckResult(
        "Endpoint reachability",
        True,
        f"Received HTTP {status} in {duration_ms} ms (connectivity confirmed).",
    )


def _check_embeddings_api(endpoint, deployment, api_key, api_version, timeout_seconds, text):
    started = time.monotonic()
    request = Request(
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/embeddings?api-version={api_version}",
        data=json.dumps({"input": text}).encode(),
        headers={"Content-Type": "application/json", "api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read())
            vector = payload["data"][0]["embedding"]
    except TimeoutError:
        return CheckResult(
            "Embeddings API",
            False,
            f"Timed out after {timeout_seconds} seconds.",
        )
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode(errors="replace")
        except OSError:
            detail = ""
        body_note = f" Body: {detail}" if detail else ""
        return CheckResult(
            "Embeddings API",
            False,
            f"HTTP {exc.code}.{body_note}",
        )
    except URLError as exc:
        return CheckResult("Embeddings API", False, f"Network error: {exc.reason}")
    except (KeyError, TypeError, ValueError) as exc:
        return CheckResult("Embeddings API", False, f"Invalid JSON response: {exc}")
    duration_ms = round((time.monotonic() - started) * 1000)
    if not isinstance(vector, list) or not vector:
        return CheckResult(
            "Embeddings API",
            False,
            "Response did not contain a valid embedding vector.",
        )
    return CheckResult(
        "Embeddings API",
        True,
        f"Embedding call succeeded in {duration_ms} ms with {len(vector)} dimensions.",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Validate embedding endpoint configuration and network connectivity."
    )
    parser.add_argument("--text", default="odori connectivity probe")
    parser.add_argument("--api-version", default="2024-10-21")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(_read_env("AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS") or "5"),
    )
    args = parser.parse_args()

    endpoint = _read_env("AZURE_OPENAI_ENDPOINT")
    deployment = _read_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT")
    api_key = _read_env("AZURE_OPENAI_API_KEY")

    print("Embedding connectivity diagnostics")
    print(f"- Endpoint: {endpoint or '(empty)'}")
    print(f"- Deployment: {deployment or '(empty)'}")
    print(f"- API key: {_masked(api_key)}")
    print(f"- Timeout: {args.timeout_seconds} seconds")
    print("")

    checks = []

    parsed, endpoint_result = _parse_endpoint(endpoint) if endpoint else (
        None,
        CheckResult("Endpoint format", False, "AZURE_OPENAI_ENDPOINT is empty."),
    )
    checks.append(endpoint_result)

    if not deployment:
        checks.append(
            CheckResult(
                "Deployment setting",
                False,
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT is empty.",
            )
        )
    else:
        checks.append(CheckResult("Deployment setting", True, "Deployment value is present."))

    if not api_key:
        checks.append(CheckResult("API key setting", False, "AZURE_OPENAI_API_KEY is empty."))
    else:
        checks.append(CheckResult("API key setting", True, "API key value is present."))

    if parsed:
        host = parsed.hostname or parsed.netloc
        checks.append(_check_dns(host))
        checks.append(_check_tls(host, args.timeout_seconds))
        checks.append(_check_endpoint_reachability(endpoint, args.timeout_seconds))
        if deployment and api_key:
            checks.append(
                _check_embeddings_api(
                    endpoint=endpoint,
                    deployment=deployment,
                    api_key=api_key,
                    api_version=args.api_version,
                    timeout_seconds=args.timeout_seconds,
                    text=args.text,
                )
            )

    failed = [check for check in checks if not check.ok]
    for check in checks:
        _print_result(check)

    print("")
    if failed:
        print(f"Result: {len(failed)} check(s) failed.")
        return 1
    print("Result: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
