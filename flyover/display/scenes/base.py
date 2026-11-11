"""What every scene receives and returns.

A scene is a pure function of a ``Context``: it draws a fresh image and keeps
no state, so tests build a context by hand and need no clock or network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from PIL import Image, ImageDraw

from ...config import Config
from ...providers.weather import Weather
from ...state import internet_down
from ..theme import BLACK, DAY, Palette

SIZE = (64, 64)

DAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@dataclass
class Context:
    now: datetime
    config: Config
    time_trusted: bool = True
    weather: Weather | None = None
    net: Any = None  # parsed net.json, or None when absent
    palette: Palette = DAY
    epoch: float = field(default_factory=time.time)  # unix time, for weather staleness
    extra: dict[str, Any] = field(default_factory=dict)  # per-scene inputs (flights, QR)

    @property
    def offline(self) -> bool:
        return internet_down(self.net)

    @property
    def fresh_weather(self) -> Weather | None:
        w = self.weather
        return w if w is not None and not w.is_stale(self.epoch) else None


class Scene(Protocol):
    name: str

    def render(self, ctx: Context) -> Image.Image: ...


def blank() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", SIZE, BLACK)
    return img, ImageDraw.Draw(img)
