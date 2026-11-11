import logging
import re
from pathlib import Path

import pytest

from flyover.config import Config, ConfigError, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_empty_file_gives_defaults():
    assert parse_config("") == Config()


def test_partial_file_overrides_only_what_it_names():
    cfg = parse_config("[location]\nlatitude = 41.9\nlongitude = -87.6\n[display]\nclock_24h = true\n")
    assert cfg.location.latitude == 41.9
    assert cfg.display.clock_24h is True
    assert cfg.display.brightness == Config().display.brightness
    assert cfg.location_is_set


def test_integers_are_accepted_for_floats():
    assert parse_config("[location]\nlatitude = 41\n").location.latitude == 41.0


def test_unset_location_is_reported():
    assert not Config().location_is_set


def test_unknown_keys_warn_but_load(caplog):
    with caplog.at_level(logging.WARNING):
        cfg = parse_config("[display]\nbrightnes = 5\n[wat]\nx = 1\n")
    assert cfg == Config()
    assert "brightnes" in caplog.text and "wat" in caplog.text


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("[location]\nlatitude = 91\n", "[location] latitude"),
        ("[location]\nlongitude = -181\n", "[location] longitude"),
        ("[location]\ntimezone = \"Mars/Base\"\n", "[location] timezone"),
        ("[location]\nlatitude = \"north\"\n", "[location] latitude: expected float"),
        ("[display]\nclock_24h = 1\n", "[display] clock_24h: expected bool"),
        ("[display]\nbrightness = true\n", "[display] brightness: expected int"),
        ("[display]\nbrightness = 0\n", "[display] brightness"),
        ("[display]\nnight_start = \"9pm\"\n", "[display] night_start"),
        ("[display]\nquiet_start = \"23:00\"\n", "[display] quiet_start"),
        ("[display]\ntemperature_unit = \"K\"\n", "[display] temperature_unit"),
        ("[flights]\nprovider = \"nope\"\n", "[flights] provider"),
        ("[flights]\nradius_km = 0.1\n", "[flights] radius_km"),
        ("[matrix]\nrows = 48\n", "[matrix] rows"),
        ("[matrix]\nrgb_sequence = \"RRG\"\n", "[matrix] rgb_sequence"),
        ("[hotspot]\npassword = \"short\"\n", "[hotspot] password"),
        ("[hotspot]\nssid = \"\"\n", "[hotspot] ssid"),
        ("[web]\nport = 0\n", "[web] port"),
        ("[display]\n= nope", "not valid TOML"),
        ("[display]\n[display]\n", "not valid TOML"),
    ],
)
def test_bad_values_name_the_key(text, fragment):
    with pytest.raises(ConfigError, match=re.escape(fragment)):
        parse_config(text)


def test_section_must_be_a_table():
    with pytest.raises(ConfigError, match=r"\[display\] must be a table"):
        parse_config("display = 3\n")


def test_quiet_hours_may_be_set_together():
    cfg = parse_config('[display]\nquiet_start = "23:30"\nquiet_end = "05:00"\n')
    assert cfg.display.quiet_start == "23:30"


def test_load_config_reports_missing_file_and_prefixes_path(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[web]\nport = 0\n")
    with pytest.raises(ConfigError, match=re.escape(str(bad))):
        load_config(bad)


def test_example_file_equals_the_defaults():
    assert load_config(ROOT / "config.example.toml") == Config()


def test_readme_config_block_matches_example_file():
    """The README documents the config; it must not drift from the real file."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Configuration reference", 1)[1]
    block = re.search(r"```toml\n(.*?)```", section, re.S).group(1)
    assert block == (ROOT / "config.example.toml").read_text(encoding="utf-8")
