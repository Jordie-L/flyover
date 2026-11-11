"""Files the services exchange through the run directory.

The display and the control service share nothing but plain files, so every
write is atomic (write to a temp name, then rename) and every read tolerates
a missing or half-written file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunPaths:
    """Locations inside the run directory (``/run/flyover`` on the Pi)."""

    run_dir: Path

    @property
    def frame(self) -> Path:
        """The screen currently shown, as a 64x64 PNG, for the web preview."""
        return self.run_dir / "frame.png"

    @property
    def status(self) -> Path:
        """What the display is showing and whether its data sources are healthy."""
        return self.run_dir / "status.json"

    @property
    def net(self) -> Path:
        """Network state, written by the control service."""
        return self.run_dir / "net.json"


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json_atomic(path: Path, obj: Any) -> None:
    write_bytes_atomic(path, json.dumps(obj, indent=2, sort_keys=True).encode() + b"\n")


def read_json(path: Path, default: Any = None) -> Any:
    """Return the parsed file, or ``default`` if it is missing or not valid JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def internet_down(net: Any) -> bool:
    """True only when the control service has explicitly reported no internet.

    A missing file or a missing key means "unknown", which is not shown as a fault.
    """
    return isinstance(net, dict) and net.get("internet") is False
