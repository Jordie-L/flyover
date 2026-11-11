"""A minimal JSON-over-HTTPS GET, on the standard library.

One request every few seconds does not justify another dependency on a Pi
Zero. Every failure (no route, timeout, HTTP error, bad JSON) is raised as
``HttpError`` so callers handle one exception type.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Deliberately generic: it names the kind of client and nothing about who runs it.
USER_AGENT = "pi-display-client/0.1"


class HttpError(Exception):
    """A request failed, or the reply was not usable JSON."""


def get_json(url: str, *, timeout: float = 10.0, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise HttpError(f"HTTP {exc.code} from {_host(url)}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise HttpError(f"cannot reach {_host(url)}: {reason}") from None
    try:
        return json.loads(body)
    except ValueError:
        raise HttpError(f"{_host(url)} did not return JSON") from None


def _host(url: str) -> str:
    """Host only: error messages and logs must not carry the query string (our coordinates)."""
    return urllib.parse.urlparse(url).netloc


__all__ = ["HttpError", "USER_AGENT", "get_json"]
