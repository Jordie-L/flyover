"""Colours, and the day / night / off decision.

Night mode is amber only: it is easier on the eyes in a dark room, and the
same palette is used for every screen so scenes never pick colours themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

from PIL import Image, ImageOps

from ..config import Display

Color = tuple[int, int, int]


@dataclass(frozen=True)
class Palette:
    name: str
    fg: Color  # main text
    dim: Color  # secondary text
    alert: Color  # fault marks
    night: bool
    climb: Color = (120, 230, 140)  # climbing
    descend: Color = (255, 170, 60)  # descending


DAY = Palette("day", fg=(255, 240, 215), dim=(140, 145, 160), alert=(255, 60, 40), night=False)
NIGHT = Palette("night", fg=(255, 150, 20), dim=(110, 60, 0), alert=(190, 40, 0), night=True,
                climb=(255, 150, 20), descend=(255, 150, 20))

BLACK: Color = (0, 0, 0)


def palette_for(mode: str) -> Palette:
    return NIGHT if mode == "night" else DAY


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_window(now: time, start: str, end: str) -> bool:
    """Is ``now`` in [start, end)? The window may cross midnight. start == end is empty."""
    s, e = parse_hhmm(start), parse_hhmm(end)
    if s == e:
        return False
    if s < e:
        return s <= now < e
    return now >= s or now < e


def mode_for(now: datetime, time_trusted: bool, cfg: Display) -> str:
    """"off" during quiet hours, "night" during night hours, otherwise "day".

    With an untrusted clock the windows cannot be evaluated, so the display
    stays in day mode.
    """
    if not time_trusted:
        return "day"
    t = now.time().replace(second=0, microsecond=0)
    if cfg.quiet_start and in_window(t, cfg.quiet_start, cfg.quiet_end):
        return "off"
    if in_window(t, cfg.night_start, cfg.night_end):
        return "night"
    return "day"


def brightness_for(mode: str, cfg: Display) -> int:
    """Panel brightness in percent for ``mode``."""
    if mode == "off":
        return 0
    return cfg.night_brightness if mode == "night" else cfg.brightness


def apply_palette(icon: Image.Image, palette: Palette) -> Image.Image:
    """Recolour a full-colour RGBA icon to the palette (amber by night)."""
    if not palette.night:
        return icon
    tinted = ImageOps.colorize(icon.convert("L"), black=BLACK, white=palette.fg).convert("RGBA")
    tinted.putalpha(icon.getchannel("A"))
    return tinted
