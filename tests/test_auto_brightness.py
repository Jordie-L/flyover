"""Dimming the panel with the sun: the solar position, the curve, and the wiring."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from flyover import sun
from flyover.config import ConfigError, Display, parse_config
from flyover.display import theme

DETROIT = (42.253, -83.334)
NY = ZoneInfo("America/Detroit")


def elevation(year, month, day, hour, minute=0, place=DETROIT):
    return sun.elevation_deg(*place, datetime(year, month, day, hour, minute, tzinfo=NY))


@pytest.mark.parametrize(
    "when, expected, tolerance",
    [
        # Solar noon is late here: the site is ~8 degrees west of its zone's meridian
        # (about 13:35 EDT in June, 12:31 EST in December, 13:41 EDT at the March equinox).
        ((2026, 6, 21, 13, 35), 90 - 42.253 + 23.44, 0.6),    # summer solstice: ~71 degrees
        ((2026, 12, 21, 12, 31), 90 - 42.253 - 23.44, 0.6),   # winter solstice: ~24 degrees
        ((2026, 3, 20, 13, 41), 90 - 42.253, 1.5),           # equinox: ~48 degrees
    ],
)
def test_solar_noon_elevation_matches_the_textbook_value(when, expected, tolerance):
    assert elevation(*when) == pytest.approx(expected, abs=tolerance)


def test_the_sun_is_down_at_midnight_and_up_at_noon():
    assert elevation(2026, 11, 11, 0) < -20
    assert elevation(2026, 11, 11, 12, 30) > 15


def test_sunrise_and_sunset_times_are_plausible_for_detroit_in_november():
    # Published values are about 07:10 and 17:10 local: the sun crosses the horizon there.
    assert elevation(2026, 11, 11, 6, 45) < 0 < elevation(2026, 11, 11, 7, 45)
    assert elevation(2026, 11, 11, 16, 45) > 0 > elevation(2026, 11, 11, 17, 45)


def test_naive_and_aware_times_agree_when_the_system_zone_is_given():
    aware = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    assert sun.elevation_deg(10, 20, aware) == pytest.approx(
        sun.elevation_deg(10, 20, aware.astimezone(NY)), abs=1e-9)


def test_the_southern_hemisphere_and_the_poles_do_not_break():
    assert -90 <= sun.elevation_deg(-33.9, 151.2, datetime(2026, 12, 21, 12, tzinfo=timezone.utc)) <= 90
    assert sun.elevation_deg(89.9, 0, datetime(2026, 12, 21, 12, tzinfo=timezone.utc)) < 0  # polar night


def test_daylight_curve_has_the_right_ends_and_never_leaves_the_range():
    assert theme.daylight_brightness(-30, 30, 80) == 30
    assert theme.daylight_brightness(theme.SUN_DIM_DEG, 30, 80) == 30
    assert theme.daylight_brightness(theme.SUN_FULL_DEG, 30, 80) == 80
    assert theme.daylight_brightness(60, 30, 80) == 80
    levels = [theme.daylight_brightness(e / 2, 30, 80) for e in range(-40, 80)]
    assert all(30 <= v <= 80 for v in levels)
    assert levels == sorted(levels)  # rises monotonically with the sun
    assert theme.daylight_brightness(0, 30, 80) > 30  # already brighter than the floor at sunset


def test_off_and_night_ignore_the_sun_and_day_without_a_sun_uses_the_fixed_level():
    cfg = Display(brightness=80, auto_brightness=True, auto_brightness_min=30, night_brightness=12)
    assert theme.brightness_for("off", cfg, 40.0) == 0
    assert theme.brightness_for("night", cfg, 40.0) == 12
    assert theme.brightness_for("day", cfg, None) == 80
    assert theme.brightness_for("day", cfg, -20.0) == 30
    assert theme.brightness_for("day", cfg, 40.0) == 80


def test_auto_brightness_is_opt_in():
    cfg = Display(brightness=80)
    assert theme.brightness_for("day", cfg, -20.0) == 80


@pytest.mark.parametrize(
    "text, message",
    [
        ("[display]\nauto_brightness_min = 0\n", "auto_brightness_min"),
        ("[display]\nauto_brightness_min = 101\n", "auto_brightness_min"),
        ("[display]\nauto_brightness = true\nbrightness = 20\nauto_brightness_min = 30\n", "must not be more"),
    ],
)
def test_config_rejects_bad_auto_brightness(text, message):
    with pytest.raises(ConfigError, match=message):
        parse_config(text)


def test_config_accepts_a_good_auto_brightness():
    cfg = parse_config("[display]\nauto_brightness = true\nbrightness = 80\nauto_brightness_min = 30\n")
    assert cfg.display.auto_brightness and cfg.display.auto_brightness_min == 30
