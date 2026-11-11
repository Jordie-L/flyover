import pytest
from PIL import Image, ImageDraw

from flyover.display import fonts

# A two-glyph font: "A" is a 3x3 corner shape, "B" is a single pixel.
TINY_BDF = """STARTFONT 2.1
FONTBOUNDINGBOX 4 5 0 -1
STARTPROPERTIES 3
FONT_ASCENT 4
FONT_DESCENT 1
DEFAULT_CHAR 66
ENDPROPERTIES
CHARS 2
STARTCHAR A
ENCODING 65
SWIDTH 600 0
DWIDTH 4 0
BBX 3 3 0 0
BITMAP
E0
80
80
ENDCHAR
STARTCHAR B
ENCODING 66
DWIDTH 2 0
BBX 1 1 1 2
BITMAP
80
ENDCHAR
ENDFONT
"""


def test_parse_bdf_metrics_and_points():
    font = fonts.parse_bdf("t", TINY_BDF)
    assert (font.ascent, font.descent, font.height) == (4, 1, 5)
    a = font.glyph("A")
    # glyph bottom is at baseline (ascent 4): 3 rows -> line rows 1..3
    assert set(a.points) == {(0, 1), (1, 1), (2, 1), (0, 2), (0, 3)}
    assert a.advance == 4
    # offsets: x offset 1, y offset 2 -> one pixel at (1, ascent-(2+1)) = (1, 1)
    assert font.glyph("B").points == ((1, 1),)


def test_unknown_characters_fall_back_to_the_default_char():
    font = fonts.parse_bdf("t", TINY_BDF)
    assert font.glyph("Z") is font.glyph("B")
    assert font.text_width("AZ") == 4 + 2


def test_draw_places_pixels_and_returns_the_next_x():
    font = fonts.parse_bdf("t", TINY_BDF)
    img = Image.new("RGB", (8, 6))
    end = font.draw(ImageDraw.Draw(img), 2, 1, "A", (255, 0, 0))
    assert end == 6
    assert img.getpixel((2, 2)) == (255, 0, 0)  # glyph (0, 1) shifted by (2, 1)
    assert img.getpixel((4, 2)) == (255, 0, 0)
    assert img.getpixel((3, 3)) == (0, 0, 0)


def test_bundled_fonts_load_and_have_the_glyphs_we_use():
    for name, height in ((fonts.TINY, 6), (fonts.SMALL, 7), (fonts.MEDIUM, 10),
                         (fonts.BOLD, 13), (fonts.CLOCK, 20)):
        font = fonts.load_font(name)
        assert font.height == height
        for ch in "0123456789:-AMP\N{DEGREE SIGN}":
            assert font.glyph(ch).advance > 0
    assert fonts.load_font(fonts.CLOCK).glyph("0").advance == 10


def test_missing_font_has_a_helpful_error(monkeypatch, tmp_path):
    monkeypatch.setenv("FLYOVER_FONT_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="FLYOVER_FONT_DIR"):
        fonts.load_font("nonexistent")
