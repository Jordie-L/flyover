"""Current conditions from Open-Meteo (https://open-meteo.com, no key needed).

Temperature is always requested in Celsius and converted for display, so
changing the unit setting never needs a new request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .http import get_json

URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat:.4f}&longitude={lon:.4f}"
    "&current=temperature_2m,weather_code,is_day&timezone=auto"
)

#: A reading older than this is hidden rather than shown as if it were current.
MAX_AGE_S = 3600


@dataclass(frozen=True)
class Weather:
    temperature_c: float
    code: int  # WMO weather interpretation code
    is_day: bool
    timezone: str  # IANA name for the coordinates, e.g. "America/Chicago"
    fetched_at: float  # unix time

    def age(self, now: float) -> float:
        return now - self.fetched_at

    def is_stale(self, now: float) -> bool:
        return self.age(now) > MAX_AGE_S

    def temperature(self, unit: str) -> int:
        """Whole degrees in ``unit`` ("F" or "C")."""
        value = self.temperature_c * 9 / 5 + 32 if unit == "F" else self.temperature_c
        return round(value)

    @property
    def icon(self) -> str:
        return icon_for(self.code, self.is_day)


def icon_for(code: int, is_day: bool) -> str:
    """Map a WMO weather code to the name of an icon in ``display.icons``."""
    if code in (0, 1):
        return "clear_day" if is_day else "clear_night"
    if code == 2:
        return "partly_day" if is_day else "partly_night"
    if code in (45, 48):
        return "fog"
    if 51 <= code <= 67 or 80 <= code <= 82:
        return "rain"
    if 71 <= code <= 77 or code in (85, 86):
        return "snow"
    if 95 <= code <= 99:
        return "storm"
    return "cloud"  # 3 (overcast) and anything unrecognised


def parse(payload: Any, now: float) -> Weather:
    """Turn an Open-Meteo reply into a ``Weather``. Raises ``ValueError`` if unusable."""
    try:
        current = payload["current"]
        return Weather(
            temperature_c=float(current["temperature_2m"]),
            code=int(current["weather_code"]),
            is_day=bool(current.get("is_day", 1)),
            timezone=str(payload.get("timezone", "")),
            fetched_at=now,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"unexpected weather reply: {exc!r}") from None


def fetch(
    lat: float,
    lon: float,
    *,
    get: Callable[[str], Any] = get_json,
    now: Callable[[], float] = time.time,
) -> Weather:
    return parse(get(URL.format(lat=lat, lon=lon)), now())
