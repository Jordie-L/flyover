"""The screen shown while an aircraft is overhead.

    ▌UA1234              +2     flight number (airline colour bar), other aircraft in range
      SFO → ORD                 where it is going, large
    B738         8.5k ▼         type, altitude, climbing or descending
    142kt  0.8mi  ↗             speed, distance from here, direction of travel
        United Airlines         airline and aircraft model, alternating
"""

from __future__ import annotations

import zlib

from PIL import Image, ImageDraw

from ...geo import KM_PER_MILE
from ...models import FlightView
from .. import fonts
from ..draw import compass_arrow, route_arrow, trend_mark
from ..theme import Palette
from .base import SIZE, Context, blank

W = SIZE[0]
ROTATE_S = 4  # seconds each bottom-line text stays up
BADGE_X = W - 9

AIRLINE_COLOURS = (
    (230, 60, 60), (60, 140, 255), (60, 200, 120), (255, 180, 40),
    (190, 90, 255), (40, 200, 220), (255, 110, 170), (160, 200, 60),
)


def format_altitude(alt_ft: int | None) -> str:
    if alt_ft is None:
        return "---"
    if alt_ft >= 10000:
        return f"{round(alt_ft / 1000)}k"
    if alt_ft >= 1000:
        return f"{alt_ft / 1000:.1f}k"
    return str(max(alt_ft, 0))


def format_distance(km: float, unit: str) -> str:
    value, suffix = (km / KM_PER_MILE, "mi") if unit == "mi" else (km, "km")
    return f"{value:.1f}{suffix}" if value < 10 else f"{round(value)}{suffix}"


def airline_colour(view: FlightView, palette: Palette):
    route = view.route
    code = (route.airline_iata or route.airline_icao) if route else ""
    if not code or palette.night:
        return palette.fg
    return AIRLINE_COLOURS[zlib.crc32(code.encode()) % len(AIRLINE_COLOURS)]


def _fit(text: str, font: fonts.BdfFont, width: int) -> str:
    while text and font.text_width(text) > width:
        text = text[:-1]
    return text


class FlightScene:
    name = "flight"

    def render(self, ctx: Context) -> Image.Image:
        view = ctx.flight
        if view is None:
            raise ValueError("FlightScene needs ctx.flight")
        img, d = blank()
        pal = ctx.palette
        self._header(d, view, pal)
        self._route(d, view, pal, y=14)
        self._type_altitude(d, view, pal, y=29)
        self._speed_distance(d, view, ctx, y=42)
        self._footer(d, view, ctx, y=54)
        return img

    @staticmethod
    def _header(d: ImageDraw.ImageDraw, view: FlightView, pal: Palette) -> None:
        mid, tiny = fonts.load_font(fonts.MEDIUM), fonts.load_font(fonts.TINY)
        d.rectangle((0, 1, 1, 9), fill=airline_colour(view, pal))
        mid.draw(d, 4, 1, _fit(view.display_number, mid, BADGE_X - 6), pal.fg)
        if view.others:
            tiny.draw(d, W - tiny.text_width(f"+{view.others}") - 1, 2, f"+{view.others}", pal.dim)

    @staticmethod
    def _route(d: ImageDraw.ImageDraw, view: FlightView, pal: Palette, y: int) -> None:
        route = view.route
        colour = pal.dim if view.route_plausible is False else pal.fg
        if route is None or not route.has_route:
            # No route known: say what the aircraft is, without repeating the header.
            a = view.aircraft
            label = a.registration if a.registration and a.registration != view.display_number else a.type_code
            bold = fonts.load_font(fonts.BOLD)
            bold.draw_centered(d, W // 2, y, _fit(label or a.hex.upper(), bold, W - 2), pal.fg)
            return
        origin, dest = route.origin.code, route.destination.code
        gap = 2
        for name in (fonts.BOLD, fonts.MEDIUM):  # 3-letter codes fit the bold font; 4-letter ICAO may not
            font = fonts.load_font(name)
            total = font.text_width(origin) + font.text_width(dest) + 9 + 2 * gap
            if total <= W - 2:
                break
        x = (W - total) // 2
        top = y + (13 - font.height) // 2
        x = font.draw(d, x, top, origin, colour) + gap
        x += route_arrow(d, x, top + font.height // 2, colour) + gap
        font.draw(d, x, top, dest, colour)
        if view.route_plausible is False:  # the stored route does not match where it is flying
            fonts.load_font(fonts.TINY).draw(d, W - 5, y, "?", pal.alert)

    @staticmethod
    def _type_altitude(d: ImageDraw.ImageDraw, view: FlightView, pal: Palette, y: int) -> None:
        mid = fonts.load_font(fonts.MEDIUM)
        a = view.aircraft
        mid.draw(d, 1, y, _fit(a.type_code or (view.route.type_code if view.route else ""), mid, 30), pal.fg)
        alt = format_altitude(a.alt_ft)
        x = W - 1 - 5 - 2 - mid.text_width(alt)
        mid.draw(d, x, y, alt, pal.fg)
        trend_mark(d, W - 4, y + 5, a.vert_rate_fpm, pal)

    @staticmethod
    def _speed_distance(d: ImageDraw.ImageDraw, view: FlightView, ctx: Context, y: int) -> None:
        small = fonts.load_font(fonts.SMALL)
        a, pal = view.aircraft, ctx.palette
        if a.ground_speed_kt is not None:
            small.draw(d, 1, y, f"{round(a.ground_speed_kt)}kt", pal.dim)
        small.draw(d, 29, y, format_distance(view.distance_km, ctx.config.display.distance_unit), pal.fg)
        if a.track_deg is not None:
            compass_arrow(d, W - 5, y + 3, a.track_deg, pal.fg)

    @staticmethod
    def _footer(d: ImageDraw.ImageDraw, view: FlightView, ctx: Context, y: int) -> None:
        tiny = fonts.load_font(fonts.TINY)
        route = view.route
        lines = []
        if route:
            if route.airline_name:
                lines.append(route.airline_name)
            model = f"{route.manufacturer} {route.type_name}".strip()
            if model:
                lines.append(model)
        if not lines:
            return
        text = lines[int(ctx.epoch // ROTATE_S) % len(lines)]
        tiny.draw_centered(d, W // 2, y, _fit(text, tiny, W - 2), ctx.palette.dim)
