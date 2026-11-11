"""Is the system time believable, and which time zone applies?

The Pi has no battery-backed clock. After a power cut it starts with a stale
time until it reaches an NTP server, and showing that would be worse than
showing nothing.
"""

from __future__ import annotations

from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SYSTEMD_DIR = Path("/run/systemd/system")
_TIMESYNC_MARKER = Path("/run/systemd/timesync/synchronized")


def time_is_trusted(
    systemd_dir: Path = _SYSTEMD_DIR, marker: Path = _TIMESYNC_MARKER
) -> bool:
    """True once systemd-timesyncd has synchronised the clock.

    Raspberry Pi OS uses timesyncd, which creates ``marker`` after a successful
    sync. On a machine without systemd (a laptop, a container) there is nothing
    to check, so the clock is assumed to be right.
    """
    if not systemd_dir.exists():
        return True
    return marker.exists()


def _zone(name: str) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def resolve_timezone(configured: str, saved: str) -> tzinfo | None:
    """Pick the time zone: config override, then the last one weather reported.

    Returns ``None`` for "use the system's local time".
    """
    return _zone(configured) or _zone(saved)


def now_in(tz: tzinfo | None) -> datetime:
    """Current wall-clock time in ``tz``, or local time when ``tz`` is None."""
    return datetime.now(tz) if tz is not None else datetime.now()


def load_saved_timezone(data_dir: Path) -> str:
    """The time zone name stored by an earlier run, or an empty string."""
    try:
        name = (data_dir / "timezone").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return name if _zone(name) else ""


def save_timezone(data_dir: Path, name: str) -> None:
    """Remember the time zone so an offline start still shows local time."""
    if not _zone(name):
        return
    path = data_dir / "timezone"
    try:
        if path.read_text(encoding="utf-8").strip() == name:
            return
    except OSError:
        pass
    path.write_text(name + "\n", encoding="utf-8")
