"""Check a deployed instance without logging sensitive response content."""

import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

base_url = os.environ.get("ODORI_SMOKE_URL", "").rstrip("/")
if not base_url:
    sys.exit("Set ODORI_SMOKE_URL to the HTTPS application URL.")

checks = {"/health/live": 200, "/health/ready": 200, "/": 302}
for path, expected in checks.items():
    try:
        with urlopen(f"{base_url}{path}", timeout=10) as response:
            status = response.status
    except HTTPError as error:
        status = error.code
    except URLError as error:
        sys.exit(f"{path}: unavailable ({error.reason})")
    if status != expected:
        sys.exit(f"{path}: expected {expected}, received {status}")
print("Smoke checks passed.")
