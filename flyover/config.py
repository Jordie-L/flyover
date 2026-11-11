"""Typed configuration loaded from ``config.toml``.

Every key has a default, so a partial file is fine. A bad value raises
``ConfigError`` with a message that names the section and key. Unknown keys
are logged and ignored so that an older install can read a newer file.
"""

from __future__ import annotations

import logging
import re
import tomllib
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class ConfigError(ValueError):
    """The configuration file is missing, unreadable or has a bad value."""


@dataclass(frozen=True)
class Location:
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = ""


@dataclass(frozen=True)
class Flights:
    provider: str = "airplanes_live"
    radius_km: float = 4.0
    max_altitude_ft: int = 15000
    hold_seconds: int = 10
    poll_idle_s: int = 10
    poll_active_s: int = 5
    route_cache_days: int = 7


@dataclass(frozen=True)
class Display:
    backend: str = "auto"
    brightness: int = 60
    night_brightness: int = 15
    night_start: str = "22:00"
    night_end: str = "07:00"
    quiet_start: str = ""
    quiet_end: str = ""
    clock_24h: bool = False
    temperature_unit: str = "F"
    distance_unit: str = "mi"


@dataclass(frozen=True)
class Matrix:
    rows: int = 64
    cols: int = 64
    hardware_mapping: str = "adafruit-hat"
    gpio_slowdown: int = 2
    pwm_bits: int = 11
    pwm_lsb_nanoseconds: int = 130
    limit_refresh_hz: int = 0
    panel_type: str = ""
    row_addr_type: int = 0
    rgb_sequence: str = "RGB"


@dataclass(frozen=True)
class Hotspot:
    ssid: str = "Flyover-Setup"
    password: str = "change-me-now"
    retry_minutes: int = 5


@dataclass(frozen=True)
class Network:
    boot_timeout_s: int = 60
    check_interval_s: int = 30
    failures_before_degraded: int = 3
    degraded_timeout_s: int = 180


@dataclass(frozen=True)
class Web:
    port: int = 80


@dataclass(frozen=True)
class Config:
    location: Location = field(default_factory=Location)
    flights: Flights = field(default_factory=Flights)
    display: Display = field(default_factory=Display)
    matrix: Matrix = field(default_factory=Matrix)
    hotspot: Hotspot = field(default_factory=Hotspot)
    network: Network = field(default_factory=Network)
    web: Web = field(default_factory=Web)

    @property
    def location_is_set(self) -> bool:
        """False while the coordinates are still the 0, 0 placeholder."""
        return self.location.latitude != 0.0 or self.location.longitude != 0.0


_SECTIONS: dict[str, type] = {
    "location": Location,
    "flights": Flights,
    "display": Display,
    "matrix": Matrix,
    "hotspot": Hotspot,
    "network": Network,
    "web": Web,
}


def _coerce(section: str, key: str, expected: type, value: Any) -> Any:
    """Check the TOML type of one value. TOML ints are accepted for floats."""
    ok = False
    if expected is bool:
        ok = isinstance(value, bool)
    elif expected is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif expected is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        if ok:
            value = float(value)
    elif expected is str:
        ok = isinstance(value, str)
    if not ok:
        raise ConfigError(
            f"[{section}] {key}: expected {expected.__name__}, got {value!r}"
        )
    return value


def _build(cls: type, section: str, raw: Any):
    if not isinstance(raw, dict):
        raise ConfigError(f"[{section}] must be a table")
    hints = get_type_hints(cls)
    known = {f.name for f in dataclasses.fields(cls)}
    for key in sorted(raw.keys() - known):
        log.warning("config: unknown key [%s] %s ignored", section, key)
    values = {k: _coerce(section, k, hints[k], raw[k]) for k in raw.keys() & known}
    return cls(**values)


def _require(ok: bool, section: str, key: str, message: str) -> None:
    if not ok:
        raise ConfigError(f"[{section}] {key}: {message}")


def _validate(cfg: Config) -> None:
    loc = cfg.location
    _require(-90 <= loc.latitude <= 90, "location", "latitude", "must be between -90 and 90")
    _require(-180 <= loc.longitude <= 180, "location", "longitude", "must be between -180 and 180")
    if loc.timezone:
        try:
            ZoneInfo(loc.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(
                f"[location] timezone: unknown time zone {loc.timezone!r} "
                '(use a name such as "America/Chicago")'
            ) from None

    fl = cfg.flights
    _require(fl.provider in ("airplanes_live", "adsb_lol"), "flights", "provider",
             'must be "airplanes_live" or "adsb_lol"')
    _require(0.5 <= fl.radius_km <= 50, "flights", "radius_km", "must be between 0.5 and 50")
    _require(0 <= fl.max_altitude_ft <= 60000, "flights", "max_altitude_ft",
             "must be between 0 and 60000")
    _require(fl.hold_seconds >= 0, "flights", "hold_seconds", "must be 0 or more")
    _require(fl.poll_idle_s >= 1, "flights", "poll_idle_s", "must be 1 or more")
    _require(fl.poll_active_s >= 1, "flights", "poll_active_s", "must be 1 or more")
    _require(fl.route_cache_days >= 1, "flights", "route_cache_days", "must be 1 or more")

    d = cfg.display
    _require(d.backend in ("auto", "matrix", "preview"), "display", "backend",
             'must be "auto", "matrix" or "preview"')
    _require(1 <= d.brightness <= 100, "display", "brightness", "must be between 1 and 100")
    _require(0 <= d.night_brightness <= 100, "display", "night_brightness",
             "must be between 0 and 100")
    for key in ("night_start", "night_end"):
        _require(bool(_HHMM.match(getattr(d, key))), "display", key, 'must look like "22:00"')
    for key in ("quiet_start", "quiet_end"):
        value = getattr(d, key)
        _require(value == "" or bool(_HHMM.match(value)), "display", key,
                 'must look like "23:30", or be empty')
    _require((d.quiet_start == "") == (d.quiet_end == ""), "display", "quiet_start",
             "quiet_start and quiet_end must be set together")
    _require(d.temperature_unit in ("F", "C"), "display", "temperature_unit", 'must be "F" or "C"')
    _require(d.distance_unit in ("mi", "km"), "display", "distance_unit", 'must be "mi" or "km"')

    m = cfg.matrix
    _require(m.rows in (16, 32, 64), "matrix", "rows", "must be 16, 32 or 64")
    _require(m.cols >= 1, "matrix", "cols", "must be 1 or more")
    _require(bool(m.hardware_mapping), "matrix", "hardware_mapping", "must not be empty")
    _require(0 <= m.gpio_slowdown <= 4, "matrix", "gpio_slowdown", "must be between 0 and 4")
    _require(1 <= m.pwm_bits <= 11, "matrix", "pwm_bits", "must be between 1 and 11")
    _require(50 <= m.pwm_lsb_nanoseconds <= 3000, "matrix", "pwm_lsb_nanoseconds",
             "must be between 50 and 3000")
    _require(m.limit_refresh_hz >= 0, "matrix", "limit_refresh_hz", "must be 0 or more")
    _require(sorted(m.rgb_sequence) == ["B", "G", "R"], "matrix", "rgb_sequence",
             'must be an ordering of R, G and B, such as "RGB" or "BGR"')

    h = cfg.hotspot
    _require(1 <= len(h.ssid.encode()) <= 32, "hotspot", "ssid", "must be 1 to 32 bytes long")
    _require(8 <= len(h.password) <= 63, "hotspot", "password", "must be 8 to 63 characters")
    _require(h.retry_minutes >= 1, "hotspot", "retry_minutes", "must be 1 or more")

    n = cfg.network
    for key in ("boot_timeout_s", "check_interval_s", "failures_before_degraded",
                "degraded_timeout_s"):
        _require(getattr(n, key) >= 1, "network", key, "must be 1 or more")

    _require(1 <= cfg.web.port <= 65535, "web", "port", "must be between 1 and 65535")


def parse_config(text: str) -> Config:
    """Parse and validate TOML text."""
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"not valid TOML: {exc}") from None
    for name in sorted(raw.keys() - _SECTIONS.keys()):
        log.warning("config: unknown section [%s] ignored", name)
    sections = {
        name: _build(cls, name, raw[name]) for name, cls in _SECTIONS.items() if name in raw
    }
    cfg = Config(**sections)
    _validate(cfg)
    return cfg


def load_config(path: str | Path) -> Config:
    """Read, parse and validate a config file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        return parse_config(text)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None
