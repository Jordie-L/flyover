"""The default screen: clock, date and weather."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import fonts
from ..icons import SIZE as ICON_SIZE, WIFI_MARK, WIFI_MARK_SIZE, weather_icon
from ..theme import apply_palette
from .base import DAYS, MONTHS, SIZE, Context, blank

_COLON_ADVANCE = 6  # the 10x20 colon sits in a 10 px cell, which looks gappy


def clock_text(ctx: Context) -> tuple[str, str]:
    """(digits, "AM"/"PM" or ""), or ("--:--", "") while the time is untrusted."""
    if not ctx.time_trusted:
        return "--:--", ""
    now = ctx.now
    if ctx.config.display.clock_24h:
        return f"{now.hour:02d}:{now.minute:02d}", ""
    return f"{now.hour % 12 or 12}:{now.minute:02d}", "AM" if now.hour < 12 else "PM"


def _clock_width(text: str, font: fonts.BdfFont) -> int:
    cell = font.glyph("0").advance
    return sum(_COLON_ADVANCE if ch == ":" else cell for ch in text)


def _draw_clock(d: ImageDraw.ImageDraw, x: int, y: int, text: str, font: fonts.BdfFont, fill) -> int:
    cell = font.glyph("0").advance
    for ch in text:
        if ch == ":":
            font.draw(d, x - (cell - _COLON_ADVANCE) // 2, y, ch, fill)
            x += _COLON_ADVANCE
        else:
            font.draw(d, x, y, ch, fill)
            x += cell
    return x


class IdleScene:
    name = "idle"

    def render(self, ctx: Context) -> Image.Image:
        img, d = blank()
        pal = ctx.palette
        big = fonts.load_font(fonts.CLOCK)
        mid = fonts.load_font(fonts.MEDIUM)
        tiny = fonts.load_font(fonts.TINY)

        # Clock, with AM/PM as a small suffix; the pair is centred together.
        digits, suffix = clock_text(ctx)
        width = _clock_width(digits, big)
        gap = 2
        total = width + (gap + tiny.text_width(suffix) if suffix else 0)
        x = (SIZE[0] - total) // 2
        y = 7
        _draw_clock(d, x, y, digits, big, pal.fg if ctx.time_trusted else pal.dim)
        if suffix:
            tiny.draw(d, x + width + gap, y + big.ascent - tiny.ascent, suffix, pal.dim)

        if ctx.time_trusted:
            now = ctx.now
            date = f"{DAYS[now.weekday()]} {MONTHS[now.month - 1]} {now.day}"
            mid.draw_centered(d, SIZE[0] // 2, 31, date, pal.dim)

        self._weather(img, d, ctx)
        if ctx.offline:
            for px, py in WIFI_MARK:
                d.point((SIZE[0] - WIFI_MARK_SIZE[0] - 2 + px, 2 + py), fill=pal.alert)
        return img

    @staticmethod
    def _weather(img: Image.Image, d: ImageDraw.ImageDraw, ctx: Context) -> None:
        weather = ctx.fresh_weather
        if weather is None:
            return
        font = fonts.load_font(fonts.BOLD)
        unit = ctx.config.display.temperature_unit
        text = f"{weather.temperature(unit)}\N{DEGREE SIGN}{unit}"
        gap = 4
        total = ICON_SIZE + gap + font.text_width(text)
        x = (SIZE[0] - total) // 2
        y = 46
        icon = apply_palette(weather_icon(weather.icon), ctx.palette)
        img.paste(icon, (x, y), icon)
        font.draw(d, x + ICON_SIZE + gap, y + (ICON_SIZE - font.height) // 2, text, ctx.palette.fg)
