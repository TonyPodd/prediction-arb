from __future__ import annotations

import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    pass


def get_json(url: str, params: dict | None = None, timeout: float = 20.0, retries: int = 2) -> dict | list:
    if params:
        query = urlencode({key: value for key, value in params.items() if value is not None})
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{query}"

    request = Request(url, headers={"User-Agent": "prediction-arb-parser/0.1"})
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = response.read().decode("utf-8")
            break
        except Exception as exc:  # noqa: BLE001 - keep HTTP failures actionable at the CLI boundary.
            last_exc = exc
            if attempt >= retries:
                raise ApiError(f"GET {url} failed: {exc}") from exc
            time.sleep(0.5 * (attempt + 1))
    else:
        raise ApiError(f"GET {url} failed: {last_exc}")

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(f"GET {url} returned invalid JSON") from exc
