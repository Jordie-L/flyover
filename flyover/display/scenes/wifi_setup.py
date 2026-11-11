"""The setup screens: a QR code that joins the hotspot, and "connecting".

    ┌─────────┐
    │  QR     │   scan with a phone camera to join the hotspot
    └─────────┘
    WIFI SETUP
    Flyover-Setup           hotspot name
    change-me-now           hotspot password

The QR code is drawn black on white with a two-module quiet zone, whatever
the day/night palette: phone cameras need the contrast, and the panel's own
brightness setting still dims it.
"""

from __future__ import annotations

import segno
from PIL import Image, ImageDraw

from .. import fonts
from .base import SIZE, Context, blank

W, H = SIZE
QUIET = 2  # modules of white around the code
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
TOP = 1


def wifi_payload(ssid: str, password: str) -> str:
    """The text phones recognise as "join this Wi-Fi network" (the WIFI: URI scheme)."""
    def escape(text: str) -> str:
        for ch in '\\;,:"':
            text = text.replace(ch, "\\" + ch)
        return text
    return f"WIFI:T:WPA;S:{escape(ssid)};P:{escape(password)};;"


def qr_matrix(payload: str) -> tuple[bytearray, ...]:
    """The code's modules (1 = dark), without quiet zone. Lowest error correction keeps it small."""
    return segno.make(payload, error="l", micro=False, boost_error=False).matrix


def _fit(text: str, font: fonts.BdfFont, width: int) -> str:
    while text and font.text_width(text) > width:
        text = text[:-1]
    return text


def _short_error(error: str | None) -> str:
    if not error:
        return ""
    return "BAD PASSWORD" if "password" in error.lower() else "TRY AGAIN"


class WifiSetupScene:
    name = "wifi_setup"

    def render(self, ctx: Context) -> Image.Image:
        net = ctx.net if isinstance(ctx.net, dict) else {}
        if net.get("state") == "CONNECTING":
            return self._connecting(ctx, net)
        hotspot = net.get("hotspot")
        if not isinstance(hotspot, dict):
            raise ValueError("WifiSetupScene needs the hotspot details in net.json")
        return self._hotspot(ctx, hotspot, net.get("error"))

    @staticmethod
    def _connecting(ctx: Context, net: dict) -> Image.Image:
        img, d = blank()
        pal = ctx.palette
        medium, small = fonts.load_font(fonts.MEDIUM), fonts.load_font(fonts.SMALL)
        medium.draw_centered(d, W // 2, 20, "CONNECTING", pal.fg)
        small.draw_centered(d, W // 2, 36, _fit(str(net.get("ssid") or ""), small, W - 2), pal.dim)
        return img

    @staticmethod
    def _hotspot(ctx: Context, hotspot: dict, error: str | None) -> Image.Image:
        img, d = blank()
        pal = ctx.palette
        ssid, password = str(hotspot.get("ssid", "")), str(hotspot.get("password", ""))
        medium, small, tiny = (fonts.load_font(n) for n in (fonts.MEDIUM, fonts.SMALL, fonts.TINY))

        y = TOP
        matrix = qr_matrix(wifi_payload(ssid, password))
        box = len(matrix) + 2 * QUIET
        if box <= W and box + TOP <= H - 10:
            x0 = (W - box) // 2
            d.rectangle((x0, y, x0 + box - 1, y + box - 1), fill=WHITE)
            for r, row in enumerate(matrix):
                for c, dark in enumerate(row):
                    if dark:
                        d.point((x0 + QUIET + c, y + QUIET + r), fill=BLACK)
            y += box + 2

        short = _short_error(error)
        lines = [
            (small, short, pal.alert) if short else (medium, "WIFI SETUP", pal.fg),
            (tiny, ssid, pal.dim),  # 4 px wide: 15 characters fit, enough for the default names
            (tiny, password, pal.fg),
        ]
        for font, text, colour in lines:
            if y + font.height > H:
                break  # a big code leaves little room: drop what does not fit
            font.draw_centered(d, W // 2, y, _fit(text, font, W - 2), colour)
            y += font.height + 1
        return img
