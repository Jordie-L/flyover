from datetime import datetime, time

import pytest
from PIL import Image

from flyover.config import Display
from flyover.display import theme


@pytest.mark.parametrize(
    "now, start, end, expected",
    [
        (time(23, 0), "22:00", "07:00", True),
        (time(3, 0), "22:00", "07:00", True),    # after midnight
        (time(7, 0), "22:00", "07:00", False),   # end is exclusive
        (time(12, 0), "22:00", "07:00", False),
        (time(22, 0), "22:00", "07:00", True),   # start is inclusive
        (time(12, 0), "09:00", "17:00", True),   # same-day window
        (time(18, 0), "09:00", "17:00", False),
        (time(12, 0), "10:00", "10:00", False),  # empty window
    ],
)
def test_in_window(now, start, end, expected):
    assert theme.in_window(now, start, end) is expected


def test_mode_prefers_off_over_night_over_day():
    cfg = Display(night_start="22:00", night_end="07:00", quiet_start="01:00", quiet_end="05:00")

    def mode(hour, trusted=True):
        return theme.mode_for(datetime(2026, 1, 1, hour, 0), trusted, cfg)

    assert mode(12) == "day"
    assert mode(23) == "night"
    assert mode(3) == "off"
    assert mode(6) == "night"
    assert mode(3, trusted=False) == "day"  # a wrong clock cannot switch the panel off


def test_no_quiet_hours_when_unset():
    assert theme.mode_for(datetime(2026, 1, 1, 3), True, Display()) == "night"


def test_brightness_per_mode():
    cfg = Display(brightness=70, night_brightness=10)
    assert [theme.brightness_for(m, cfg) for m in ("day", "night", "off")] == [70, 10, 0]


def test_night_palette_recolours_icons_to_amber_and_keeps_transparency():
    icon = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    icon.putpixel((0, 0), (80, 140, 255, 255))
    tinted = theme.apply_palette(icon, theme.NIGHT)
    r, g, b, a = tinted.getpixel((0, 0))
    assert a == 255 and r > g > b  # amber: red strongest, blue weakest
    assert tinted.getpixel((1, 0))[3] == 0
    assert theme.apply_palette(icon, theme.DAY) is icon
