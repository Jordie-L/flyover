"""16x16 weather icons and the small "no internet" mark, drawn procedurally.

Shapes are built from a few ellipses and lines on a transparent RGBA canvas
(Pillow does not anti-alias these), so they stay crisp on the panel and need
no image files. Icons are full colour; ``theme.apply_palette`` tints them
amber at night.
"""

from __future__ import annotations

from functools import lru_cache

from PIL import Image, ImageDraw

SIZE = 16

_SUN = (255, 200, 40)
_RAY = (255, 160, 30)
_MOON = (235, 235, 175)
_CLOUD = (225, 230, 240)
_CLOUD_DARK = (140, 150, 170)
_RAIN = (70, 140, 255)
_SNOW = (240, 245, 255)
_BOLT = (255, 220, 40)
_FOG = (160, 170, 185)
_CLEAR = (0, 0, 0, 0)

#: Pixels of the 7x5 "Wi-Fi with no internet" mark, as (x, y).
WIFI_MARK_SIZE = (7, 5)
WIFI_MARK = (
    (1, 0), (2, 0), (3, 0), (4, 0), (5, 0),
    (0, 1), (6, 1),
    (2, 2), (3, 2), (4, 2),
    (1, 3), (5, 3),
    (3, 4),
)


def _sun(d: ImageDraw.ImageDraw, cx: int, cy: int, r: int, rays: bool) -> None:
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_SUN)
    if rays:
        far, near = r + 3, r + 2
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.line((cx + dx * near, cy + dy * near, cx + dx * far, cy + dy * far), fill=_RAY)
        diag = round((r + 2) * 0.72)
        for sx in (-1, 1):
            for sy in (-1, 1):
                d.point((cx + sx * diag, cy + sy * diag), fill=_RAY)


def _moon(d: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
    d.ellipse((ox, oy, ox + 9, oy + 9), fill=_MOON)
    d.ellipse((ox + 4, oy - 2, ox + 13, oy + 7), fill=_CLEAR)


def _cloud(d: ImageDraw.ImageDraw, ox: int, oy: int, color) -> None:
    """A 14x11 cloud whose top-left corner is (ox, oy)."""
    d.ellipse((ox, oy + 4, ox + 6, oy + 10), fill=color)
    d.ellipse((ox + 3, oy, ox + 10, oy + 8), fill=color)
    d.ellipse((ox + 7, oy + 3, ox + 13, oy + 10), fill=color)
    d.rectangle((ox + 3, oy + 6, ox + 10, oy + 10), fill=color)


def _drops(d: ImageDraw.ImageDraw) -> None:
    for x in (3, 7, 11):
        d.line((x + 1, 11, x, 13), fill=_RAIN)
    for x in (5, 9, 13):
        d.line((x + 1, 13, x, 15), fill=_RAIN)


def _flakes(d: ImageDraw.ImageDraw) -> None:
    for x, y in ((3, 12), (8, 12), (12, 12), (5, 14), (10, 14)):
        d.point((x, y), fill=_SNOW)


def _bolt(d: ImageDraw.ImageDraw) -> None:
    d.polygon([(9, 9), (5, 13), (8, 13), (6, 16), (11, 11), (8, 11)], fill=_BOLT)


def _build(name: str) -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), _CLEAR)
    d = ImageDraw.Draw(img)
    if name == "clear_day":
        _sun(d, 7, 7, 3, rays=True)
    elif name == "clear_night":
        _moon(d, 3, 3)
        d.point((12, 3), fill=_MOON)
        d.point((10, 12), fill=_MOON)
    elif name == "partly_day":
        _sun(d, 5, 5, 3, rays=False)
        for x, y in ((5, 0), (0, 5), (1, 1), (9, 1)):
            d.point((x, y), fill=_RAY)
        _cloud(d, 2, 5, _CLOUD)
    elif name == "partly_night":
        _moon(d, 1, 0)
        _cloud(d, 2, 5, _CLOUD_DARK)
    elif name == "fog":
        for y, (x0, x1) in zip((3, 6, 9, 12), ((2, 11), (4, 13), (1, 10), (3, 12)), strict=True):
            d.line((x0, y, x1, y), fill=_FOG)
    elif name == "rain":
        _cloud(d, 1, 0, _CLOUD_DARK)
        _drops(d)
    elif name == "snow":
        _cloud(d, 1, 0, _CLOUD_DARK)
        _flakes(d)
    elif name == "storm":
        _cloud(d, 1, 0, _CLOUD_DARK)
        _bolt(d)
    else:  # "cloud" and anything unknown
        _cloud(d, 1, 3, _CLOUD)
    return img


ICON_NAMES = (
    "clear_day", "clear_night", "partly_day", "partly_night",
    "cloud", "fog", "rain", "snow", "storm",
)


@lru_cache(maxsize=None)
def weather_icon(name: str) -> Image.Image:
    """The icon for ``name`` (see ``ICON_NAMES``). Treat the result as read-only."""
    return _build(name)
