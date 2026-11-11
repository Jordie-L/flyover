"""A short splash while the program starts."""

from __future__ import annotations

from PIL import Image

from .. import fonts
from .base import SIZE, Context, blank


class BootScene:
    name = "boot"

    def render(self, ctx: Context) -> Image.Image:
        img, d = blank()
        bold = fonts.load_font(fonts.BOLD)
        small = fonts.load_font(fonts.SMALL)
        cx = SIZE[0] // 2
        bold.draw_centered(d, cx, 24, "FLYOVER", ctx.palette.fg)
        small.draw_centered(d, cx, 40, "starting", ctx.palette.dim)
        return img
