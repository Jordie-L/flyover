"""Small pixel-art shapes: compass arrows, climb/descend marks, route arrow."""

from __future__ import annotations

from PIL import ImageDraw

from ..geo import compass_octant

# Unit steps for the eight compass directions, indexed from north, clockwise.
_STEPS = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


def compass_arrow(d: ImageDraw.ImageDraw, cx: int, cy: int, bearing: float, fill) -> None:
    """A 7x7-ish arrow centred on (cx, cy) pointing along ``bearing`` (degrees from north)."""
    k = compass_octant(bearing)
    vx, vy = _STEPS[k]
    length = 4 if k % 2 else 5  # diagonal steps cover more ground per pixel
    tail = (cx - vx * (length // 2), cy - vy * (length // 2))
    tip = (tail[0] + vx * length, tail[1] + vy * length)
    d.line((*tail, *tip), fill=fill)
    for side in (-1, 1):  # two barbs, swept back from the tip
        bx, by = _STEPS[(k + 4 + side) % 8]
        d.line((*tip, tip[0] + 2 * bx, tip[1] + 2 * by), fill=fill)


def trend_mark(d: ImageDraw.ImageDraw, cx: int, cy: int, rate_fpm: int | None, palette) -> None:
    """A 5 px mark: triangle up (climbing), down (descending) or a dash (level / unknown)."""
    if rate_fpm is not None and rate_fpm >= 300:
        d.polygon([(cx, cy - 2), (cx - 2, cy + 1), (cx + 2, cy + 1)], fill=palette.climb)
    elif rate_fpm is not None and rate_fpm <= -300:
        d.polygon([(cx, cy + 2), (cx - 2, cy - 1), (cx + 2, cy - 1)], fill=palette.descend)
    else:
        d.line((cx - 2, cy, cx + 2, cy), fill=palette.dim)


def route_arrow(d: ImageDraw.ImageDraw, x: int, cy: int, fill) -> int:
    """A 9 px wide right-pointing arrow starting at x. Returns its width."""
    d.line((x, cy, x + 8, cy), fill=fill)
    d.line((x + 5, cy - 3, x + 8, cy), fill=fill)
    d.line((x + 5, cy + 3, x + 8, cy), fill=fill)
    return 9
