"""BDF bitmap fonts, drawn pixel for pixel.

Pillow's own text drawing is anti-aliased or limited to 256 characters, and
differs between versions. Reading the BDF files directly gives the same crisp
pixels on every machine, which is what a 64x64 LED panel (and the snapshot
tests) need.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw

# Font names, by cell size. See fonts/README.md.
TINY = "4x6"
SMALL = "5x7"
MEDIUM = "6x10"
BOLD = "7x13B"
CLOCK = "10x20"


@dataclass(frozen=True)
class Glyph:
    advance: int
    #: Lit pixels as (x, y), relative to the top-left of the text line.
    points: tuple[tuple[int, int], ...]


class BdfFont:
    def __init__(self, name: str, ascent: int, descent: int, glyphs: dict[int, Glyph],
                 default_char: int) -> None:
        self.name = name
        self.ascent = ascent
        self.descent = descent
        self._glyphs = glyphs
        self._default = default_char

    @property
    def height(self) -> int:
        return self.ascent + self.descent

    def glyph(self, ch: str) -> Glyph | None:
        code = ord(ch)
        return self._glyphs.get(code) or self._glyphs.get(self._default) or self._glyphs.get(ord("?"))

    def text_width(self, text: str) -> int:
        return sum(g.advance for g in map(self.glyph, text) if g is not None)

    def draw(self, draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill) -> int:
        """Draw ``text`` with its top-left at (x, y). Returns the x after the last glyph."""
        for ch in text:
            glyph = self.glyph(ch)
            if glyph is None:
                continue
            if glyph.points:
                draw.point([(x + px, y + py) for px, py in glyph.points], fill=fill)
            x += glyph.advance
        return x

    def draw_centered(self, draw: ImageDraw.ImageDraw, center_x: int, y: int, text: str, fill) -> None:
        self.draw(draw, center_x - self.text_width(text) // 2, y, text, fill)


def parse_bdf(name: str, text: str) -> BdfFont:
    """Parse the text of a BDF file."""
    lines = text.splitlines()
    ascent = descent = None
    bbox = (0, 0, 0, 0)
    default_char = -1
    i = 0
    # header: runs until the first STARTCHAR
    while i < len(lines) and not lines[i].startswith("STARTCHAR"):
        parts = lines[i].split()
        if parts and parts[0] == "FONTBOUNDINGBOX":
            bbox = tuple(int(v) for v in parts[1:5])  # type: ignore[assignment]
        elif parts and parts[0] == "FONT_ASCENT":
            ascent = int(parts[1])
        elif parts and parts[0] == "FONT_DESCENT":
            descent = int(parts[1])
        elif parts and parts[0] == "DEFAULT_CHAR":
            default_char = int(parts[1])
        i += 1
    width, height, _xoff, yoff = bbox
    if ascent is None:
        ascent = height + yoff
    if descent is None:
        descent = -yoff

    glyphs: dict[int, Glyph] = {}
    while i < len(lines):
        if not lines[i].startswith("STARTCHAR"):
            i += 1
            continue
        encoding = advance = None
        gw = gh = gx = gy = 0
        rows: list[str] = []
        i += 1
        while i < len(lines) and lines[i] != "ENDCHAR":
            parts = lines[i].split()
            if parts[0] == "ENCODING":
                encoding = int(parts[1])
            elif parts[0] == "DWIDTH":
                advance = int(parts[1])
            elif parts[0] == "BBX":
                gw, gh, gx, gy = (int(v) for v in parts[1:5])
            elif parts[0] == "BITMAP":
                for _ in range(gh):
                    i += 1
                    rows.append(lines[i].strip())
            i += 1
        if encoding is not None and encoding >= 0 and advance is not None:
            top = ascent - (gy + gh)  # line-relative y of the glyph's first row
            points = []
            for r, hex_row in enumerate(rows):
                bits = int(hex_row, 16) if hex_row else 0
                nbits = len(hex_row) * 4
                for c in range(gw):
                    if bits >> (nbits - 1 - c) & 1:
                        points.append((gx + c, top + r))
            glyphs[encoding] = Glyph(advance=advance, points=tuple(points))
        i += 1
    return BdfFont(name, ascent, descent, glyphs, default_char)


def font_dir() -> Path:
    """Where the .bdf files live: ``$FLYOVER_FONT_DIR``, else ``fonts/`` in the repository."""
    override = os.environ.get("FLYOVER_FONT_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[2] / "fonts"


@lru_cache(maxsize=None)
def load_font(name: str) -> BdfFont:
    path = font_dir() / f"{name}.bdf"
    try:
        text = path.read_text(encoding="latin-1")
    except OSError:
        raise FileNotFoundError(
            f"font file {path} not found (set FLYOVER_FONT_DIR if the fonts live elsewhere)"
        ) from None
    return parse_bdf(name, text)
