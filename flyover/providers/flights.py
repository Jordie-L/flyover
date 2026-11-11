"""Live aircraft positions from a community ADS-B aggregator.

airplanes.live and adsb.lol both serve the ADS-B Exchange v2 format, so one
parser covers both. Only a circle around the display is requested.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from ..config import Config, LocationNotSet
from ..geo import KM_PER_NM, haversine_km
from ..models import Aircraft
from .http import get_json
from .poller import Poller

log = logging.getLogger(__name__)

URLS = {
    "airplanes_live": "https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{radius_nm:.1f}",
    "adsb_lol": "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{radius_nm:.1f}",
}

#: Look this much beyond the "overhead" radius, so an aircraft is already known
#: (route fetched, speed measured) by the time it arrives.
QUERY_BUFFER_KM = 10.0
MAX_QUERY_RADIUS_NM = 250.0  # the services' own limit


def query_radius_nm(radius_km: float) -> float:
    return min(MAX_QUERY_RADIUS_NM, (radius_km + QUERY_BUFFER_KM) / KM_PER_NM)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_aircraft(entry: dict, fetched_at: float) -> Aircraft | None:
    """One entry of the reply's ``ac`` list, or None if it has no usable position."""
    lat, lon = _number(entry.get("lat")), _number(entry.get("lon"))
    hex_id = entry.get("hex")
    if lat is None or lon is None or not isinstance(hex_id, str):
        return None
    alt = entry.get("alt_baro", entry.get("alt_geom"))
    on_ground = alt == "ground"
    alt_ft = None if on_ground else _number(alt)
    rate = _number(entry.get("baro_rate", entry.get("geom_rate")))
    seen_pos = _number(entry.get("seen_pos")) or 0.0
    return Aircraft(
        hex=hex_id.lower(),  # a leading "~" marks a non-ICAO address; it stays part of the identity
        callsign=str(entry.get("flight") or "").strip(),
        registration=str(entry.get("r") or "").strip(),
        type_code=str(entry.get("t") or "").strip(),
        lat=lat,
        lon=lon,
        alt_ft=None if alt_ft is None else round(alt_ft),
        ground_speed_kt=_number(entry.get("gs")),
        track_deg=_number(entry.get("track")),
        vert_rate_fpm=None if rate is None else round(rate),
        on_ground=on_ground,
        observed_at=fetched_at - seen_pos,
    )


def parse_reply(payload: Any, fetched_at: float) -> list[Aircraft]:
    """All usable aircraft in a reply. Raises ``ValueError`` if it is not a position reply."""
    if not isinstance(payload, dict) or not isinstance(payload.get("ac"), list):
        raise ValueError("unexpected flight data reply")
    found = (parse_aircraft(e, fetched_at) for e in payload["ac"] if isinstance(e, dict))
    return [a for a in found if a is not None]


class FlightFeed:
    """Polls for aircraft near the display, faster while something is close.

    ``quiet`` pauses the requests (set during quiet hours): the poller keeps
    ticking but makes no request and reports an empty sky.
    """

    def __init__(
        self,
        get_config: Callable[[], Config],
        *,
        get: Callable[[str], Any] = get_json,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._get_config = get_config
        self._get = get
        self._now = now
        self.quiet = False
        self._near = False
        self.poller: Poller[list[Aircraft]] = Poller("flights", self._fetch, self._interval)

    @property
    def aircraft(self) -> list[Aircraft]:
        return self.poller.latest or []

    def _interval(self) -> float:
        flights = self._get_config().flights
        return flights.poll_active_s if self._near else flights.poll_idle_s

    def _fetch(self) -> list[Aircraft]:
        cfg = self._get_config()
        if self.quiet:
            self._near = False
            return []
        if not cfg.location_is_set:
            raise LocationNotSet("location is not set")
        loc, flights = cfg.location, cfg.flights
        url = URLS[flights.provider].format(
            lat=loc.latitude, lon=loc.longitude, radius_nm=query_radius_nm(flights.radius_km)
        )
        fetched_at = self._now()
        aircraft = parse_reply(self._get(url), fetched_at)
        reach = flights.radius_km + QUERY_BUFFER_KM
        self._near = any(
            not a.on_ground
            and haversine_km(loc.latitude, loc.longitude, a.lat, a.lon) <= reach
            and (a.alt_ft is not None and a.alt_ft <= flights.max_altitude_ft)
            for a in aircraft
        )
        return aircraft

    def start(self) -> None:
        self.poller.start()

    def poke(self) -> None:
        self.poller.poke()

    def stop(self) -> None:
        self.poller.stop()
