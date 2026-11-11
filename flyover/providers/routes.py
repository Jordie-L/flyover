"""Flight number, route and aircraft details from adsbdb (https://www.adsbdb.com).

Lookups never block the display. ``RouteResolver.lookup`` answers from memory
and queues anything it does not know; one background thread fetches it
(politely: one request a second at most) and stores the result in SQLite, so a
reboot does not mean asking again for aircraft seen last week.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import queue
import re
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from ..models import Airport, RouteInfo
from .http import HttpError, get_json

log = logging.getLogger(__name__)

BASE_URL = "https://api.adsbdb.com/v0/"
_HEX = re.compile(r"^[0-9a-f]{6}$")

NOT_FOUND_TTL_S = 6 * 3600  # "unknown" is remembered for a shorter time than a real answer
RETRY_AFTER_ERROR_S = 60  # after a network failure, do not ask about the same flight sooner
REQUEST_SPACING_S = 1.0
MAX_PENDING = 32
PRUNE_AFTER_S = 30 * 86400
MEM_LIMIT = 1000


# -- parsing ------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _airport(raw: Any) -> Airport | None:
    if not isinstance(raw, dict):
        return None
    try:
        return Airport(
            iata=_text(raw.get("iata_code")),
            icao=_text(raw.get("icao_code")),
            name=_text(raw.get("name")),
            municipality=_text(raw.get("municipality")),
            lat=float(raw["latitude"]),
            lon=float(raw["longitude"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_response(payload: Any) -> RouteInfo | None:
    """An adsbdb reply as a ``RouteInfo``, or None if it holds nothing useful."""
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        return None  # e.g. {"response": "unknown callsign"}
    aircraft = response.get("aircraft") if isinstance(response.get("aircraft"), dict) else {}
    route = response.get("flightroute") if isinstance(response.get("flightroute"), dict) else {}
    if not aircraft and not route:
        return None
    airline = route.get("airline") if isinstance(route.get("airline"), dict) else {}
    return RouteInfo(
        flight_number=_text(route.get("callsign_iata")),
        airline_name=_text(airline.get("name")),
        airline_iata=_text(airline.get("iata")),
        airline_icao=_text(airline.get("icao")),
        origin=_airport(route.get("origin")),
        destination=_airport(route.get("destination")),
        registration=_text(aircraft.get("registration")),
        type_code=_text(aircraft.get("icao_type")),
        type_name=_text(aircraft.get("type")),
        manufacturer=_text(aircraft.get("manufacturer")),
    )


def _to_json(info: RouteInfo | None) -> str | None:
    return None if info is None else json.dumps(dataclasses.asdict(info))


def _from_json(text: str | None) -> RouteInfo | None:
    if text is None:
        return None
    raw = json.loads(text)
    for key in ("origin", "destination"):
        raw[key] = Airport(**raw[key]) if raw.get(key) else None
    return RouteInfo(**raw)


# -- the resolver ---------------------------------------------------------------

Key = tuple[str, str]  # (hex, callsign)


class RouteResolver:
    def __init__(
        self,
        db_path: Path,
        ttl_days: Callable[[], int],
        *,
        get: Callable[[str], Any] = get_json,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = db_path
        self._ttl_days = ttl_days
        self._get = get
        self._now = now
        self._lock = threading.Lock()
        self._mem: dict[Key, tuple[RouteInfo | None, float]] = {}  # value, expires_at
        self._pending: set[Key] = set()
        self._retry_at: dict[Key, float] = {}
        self._queue: queue.Queue[Key] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._db: sqlite3.Connection | None = None  # only ever used by the resolving thread

    # -- called from the display loop (never blocks) ---------------------------

    def lookup(self, hex_id: str, callsign: str) -> RouteInfo | None:
        """What is known now; unknown flights are queued for the background thread."""
        key = (hex_id, callsign)
        now = self._now()
        with self._lock:
            entry = self._mem.get(key)
            if entry and entry[1] > now:
                return entry[0]
            if (
                key not in self._pending
                and len(self._pending) < MAX_PENDING
                and self._retry_at.get(key, 0.0) <= now
            ):
                self._pending.add(key)
                self._queue.put_nowait(key)
            return entry[0] if entry else None  # an expired answer beats no answer

    # -- background work ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="routes", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                key = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            asked_network = self.resolve(key)
            if asked_network:
                self._stop.wait(REQUEST_SPACING_S)
        if self._db is not None:
            self._db.close()

    def drain(self) -> None:
        """Resolve everything queued, on the calling thread (for tests and ``--once``)."""
        while True:
            try:
                key = self._queue.get_nowait()
            except queue.Empty:
                return
            self.resolve(key)

    def resolve(self, key: Key) -> bool:
        """Fill the cache for one key. Returns True if a network request was made."""
        hex_id, callsign = key
        try:
            cached = self._db_read(key)
            if cached is not None:
                self._remember(key, *cached)
                return False
            if not _HEX.match(hex_id):
                info: RouteInfo | None = None  # not an ICAO address: nothing to look up
            else:
                info = self._query(hex_id, callsign)
            self._db_write(key, info)
            self._remember(key, info, self._now())
            return True
        except HttpError as exc:
            with self._lock:
                self._retry_at[key] = self._now() + RETRY_AFTER_ERROR_S
            log.warning("routes: %s", exc)
            return True
        finally:
            with self._lock:
                self._pending.discard(key)

    def _query(self, hex_id: str, callsign: str) -> RouteInfo | None:
        url = BASE_URL + f"aircraft/{hex_id}"
        if callsign:
            url += "?callsign=" + urllib.parse.quote(callsign, safe="")
        try:
            return parse_response(self._get(url))
        except HttpError as exc:
            if exc.status != 404:
                raise
        if not callsign:
            return None
        # The aircraft is unknown to the database; its flight number may still be known.
        try:
            return parse_response(self._get(BASE_URL + "callsign/" + urllib.parse.quote(callsign, safe="")))
        except HttpError as exc:
            if exc.status != 404:
                raise
            return None

    def _remember(self, key: Key, info: RouteInfo | None, fetched_at: float) -> None:
        ttl = self._ttl_days() * 86400 if info is not None else NOT_FOUND_TTL_S
        with self._lock:
            if len(self._mem) >= MEM_LIMIT:
                now = self._now()
                self._mem = {k: v for k, v in self._mem.items() if v[1] > now}
            self._mem[key] = (info, fetched_at + ttl)

    # -- SQLite (used only from the resolving thread) ---------------------------------

    def _connection(self) -> sqlite3.Connection | None:
        if self._db is None:
            try:
                db = sqlite3.connect(self._db_path)
                db.execute(
                    "CREATE TABLE IF NOT EXISTS routes "
                    "(hex TEXT, callsign TEXT, payload TEXT, fetched REAL NOT NULL, "
                    "PRIMARY KEY (hex, callsign))"
                )
                db.execute("DELETE FROM routes WHERE fetched < ?", (self._now() - PRUNE_AFTER_S,))
                db.commit()
                self._db = db
            except sqlite3.Error as exc:
                log.warning("route cache unavailable, using memory only: %s", exc)
        return self._db

    def _db_read(self, key: Key) -> tuple[RouteInfo | None, float] | None:
        """(info, fetched_at) if a still-fresh row exists, else None."""
        db = self._connection()
        if db is None:
            return None
        try:
            row = db.execute(
                "SELECT payload, fetched FROM routes WHERE hex = ? AND callsign = ?", key
            ).fetchone()
            if row is None:
                return None
            payload, fetched = row
            ttl = self._ttl_days() * 86400 if payload is not None else NOT_FOUND_TTL_S
            return (_from_json(payload), fetched) if fetched + ttl > self._now() else None
        except (sqlite3.Error, ValueError, TypeError) as exc:
            log.warning("route cache read failed: %s", exc)
            return None

    def _db_write(self, key: Key, info: RouteInfo | None) -> None:
        db = self._connection()
        if db is None:
            return
        try:
            db.execute(
                "INSERT OR REPLACE INTO routes (hex, callsign, payload, fetched) VALUES (?, ?, ?, ?)",
                (*key, _to_json(info), self._now()),
            )
            db.commit()
        except sqlite3.Error as exc:
            log.warning("route cache write failed: %s", exc)
