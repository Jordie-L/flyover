"""Decide which aircraft, if any, counts as "overhead".

Rules (all positions are dead-reckoned to the same instant first):

* ground traffic and aircraft above the altitude ceiling are ignored;
* an aircraft inside the radius is a candidate, and the closest wins;
* the aircraft being shown is only replaced by one that is clearly closer
  (``SWITCH_RATIO``), so two planes at similar distances do not flip-flop;
* when the shown aircraft leaves the radius it stays on screen for
  ``hold_seconds`` unless another aircraft has arrived.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from . import geo
from .config import Flights
from .models import Aircraft

SWITCH_RATIO = 0.7


@dataclass(frozen=True)
class Pick:
    aircraft: Aircraft
    lat: float
    lon: float
    distance_km: float
    inside: bool  # False while the aircraft is only being held after leaving the radius
    others: int  # other aircraft inside the radius right now


@dataclass(frozen=True)
class _Candidate:
    aircraft: Aircraft
    lat: float
    lon: float
    distance_km: float


class Selector:
    def __init__(self) -> None:
        self._current: Aircraft | None = None
        self._last_inside = 0.0

    def update(
        self,
        aircraft: Sequence[Aircraft],
        now: float,
        home: tuple[float, float],
        flights: Flights,
    ) -> Pick | None:
        candidates = self._candidates(aircraft, now, home, flights)
        inside = sorted((c for c in candidates if c.distance_km <= flights.radius_km),
                        key=lambda c: c.distance_km)
        current_hex = self._current.hex if self._current else None

        if inside:
            best = inside[0]
            current = next((c for c in inside if c.aircraft.hex == current_hex), None)
            chosen = current if current and best.distance_km >= SWITCH_RATIO * current.distance_km else best
            self._current, self._last_inside = chosen.aircraft, now
            return Pick(chosen.aircraft, chosen.lat, chosen.lon, chosen.distance_km, True,
                        len(inside) - 1)

        if self._current is not None and now - self._last_inside <= flights.hold_seconds:
            latest = next((c for c in candidates if c.aircraft.hex == current_hex), None)
            if latest is None:  # left the feed: carry on from the last report
                latest = self._candidate(self._current, now, home)
            self._current = latest.aircraft
            return Pick(latest.aircraft, latest.lat, latest.lon, latest.distance_km, False, 0)

        self._current = None
        return None

    @staticmethod
    def _candidate(a: Aircraft, now: float, home: tuple[float, float]) -> _Candidate:
        lat, lon = a.position_at(now)
        return _Candidate(a, lat, lon, geo.haversine_km(home[0], home[1], lat, lon))

    def _candidates(self, aircraft, now, home, flights: Flights) -> list[_Candidate]:
        return [
            self._candidate(a, now, home)
            for a in aircraft
            if not a.on_ground and a.alt_ft is not None and a.alt_ft <= flights.max_altitude_ft
        ]
