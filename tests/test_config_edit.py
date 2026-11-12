import re
from pathlib import Path

import pytest

from flyover.config import ConfigError, parse_config
from flyover.control import config_edit
from flyover.control.config_edit import apply_changes, format_value, update_config_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (ROOT / "config.example.toml").read_text()


def test_value_formatting():
    assert format_value(True) == "true" and format_value(False) == "false"
    assert format_value(41.9) == "41.9" and format_value(60) == "60"
    assert format_value('say "hi"\\') == '"say \\"hi\\"\\\\"'
    with pytest.raises(ConfigError):
        format_value("line\nbreak")
    with pytest.raises(TypeError):
        format_value([1])


def test_edits_a_value_in_place_and_keeps_its_comment():
    out = update_config_text(EXAMPLE, {("display", "brightness"): 80})
    assert re.search(r"^brightness\s+= 80\s+# percent, daytime", out, re.M)  # value changed, comment kept
    assert parse_config(out).display.brightness == 80


def test_everything_else_is_untouched():
    out = update_config_text(EXAMPLE, {("display", "brightness"): 80})
    before, after = EXAMPLE.splitlines(), out.splitlines()
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(before) == len(after) and len(changed) == 1


def test_a_hash_inside_a_quoted_value_is_not_a_comment():
    text = '[hotspot]\nssid = "My # Net"   # the name\npassword = "change-me-now"\n'
    out = update_config_text(text, {("hotspot", "ssid"): "Other"})
    assert 'ssid = "Other"   # the name' in out
    out = update_config_text(text, {("hotspot", "password"): "pa#ss word1"})
    assert parse_config(out).hotspot.password == "pa#ss word1"


def test_keys_with_the_same_name_in_other_sections_are_not_touched():
    text = "[a]\nport = 1\n[web]\nport = 80\n"
    out = update_config_text(text, {("web", "port"): 8080})
    assert "[a]\nport = 1\n" in out and "port = 8080" in out


def test_a_commented_out_default_is_uncommented():
    out = update_config_text(EXAMPLE, {("location", "timezone"): "Europe/Paris"})
    assert 'timezone = "Europe/Paris"   # optional' in out  # the line's own comment is kept
    assert "# timezone" not in out
    assert parse_config(out).location.timezone == "Europe/Paris"
    assert out.count("timezone") == 1


def test_a_missing_key_is_added_to_its_section():
    text = "[display]\nbrightness = 50\n\n[web]\nport = 80\n"
    out = update_config_text(text, {("display", "clock_24h"): True})
    assert out.index("clock_24h = true") < out.index("[web]")
    assert parse_config(out).display.clock_24h


def test_a_missing_section_is_appended():
    out = update_config_text("[web]\nport = 80\n", {("display", "clock_24h"): True})
    assert out.rstrip().endswith("[display]\nclock_24h = true")
    assert update_config_text("", {("web", "port"): 81}).strip() == "[web]\nport = 81"


def test_an_invalid_result_is_refused():
    with pytest.raises(ConfigError, match="latitude"):
        update_config_text(EXAMPLE, {("location", "latitude"): 123.0})
    with pytest.raises(ConfigError, match="brightness"):
        update_config_text(EXAMPLE, {("display", "brightness"): 0})


def test_several_changes_at_once():
    out = update_config_text(EXAMPLE, {("location", "latitude"): 41.9, ("location", "longitude"): -87.6,
                                       ("display", "clock_24h"): True})
    cfg = parse_config(out)
    assert (cfg.location.latitude, cfg.location.longitude, cfg.display.clock_24h) == (41.9, -87.6, True)


def test_apply_changes_writes_atomically_and_keeps_permissions(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE)
    path.chmod(0o640)
    cfg = apply_changes(path, {("display", "brightness"): 33})
    assert cfg.display.brightness == 33 and parse_config(path.read_text()).display.brightness == 33
    assert path.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]
    with pytest.raises(ConfigError):
        apply_changes(path, {("display", "brightness"): 0})
    assert parse_config(path.read_text()).display.brightness == 33  # untouched by the failure


def test_command_line_interface(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE)
    assert config_edit.main([str(path), "hotspot.password=s3cret-pass", "display.clock_24h=true",
                             "location.latitude=41.5", "web.port=8080"]) == 0
    cfg = parse_config(path.read_text())
    assert (cfg.hotspot.password, cfg.display.clock_24h, cfg.location.latitude, cfg.web.port) == (
        "s3cret-pass", True, 41.5, 8080)
    assert config_edit.main([str(path), "display.brightness=0"]) == 1
    assert "brightness" in capsys.readouterr().err
    assert config_edit.main([str(path)]) == 2
    with pytest.raises(SystemExit):
        config_edit.main([str(path), "nonsense"])


def test_the_daylight_keys_are_added_to_a_config_written_before_they_existed():
    """Every Pi installed earlier has a config.toml without them; the settings page must cope."""
    old = ('[location]\nlatitude = 41.9\nlongitude = -87.6\n\n'
           '[display]\nbrightness = 60   # percent\nnight_start = "22:00"\n\n[web]\nport = 80\n')
    out = update_config_text(old, {("display", "auto_brightness"): True,
                                   ("display", "auto_brightness_min"): 25})
    cfg = parse_config(out)
    assert cfg.display.auto_brightness and cfg.display.auto_brightness_min == 25
    assert "brightness = 60   # percent" in out  # the existing line and its comment are untouched
    assert out.index("auto_brightness_min = 25") < out.index("[web]")


def test_editing_brightness_does_not_touch_the_keys_that_start_with_it():
    text = "[display]\nbrightness = 80\nauto_brightness = true\nauto_brightness_min = 30\n"
    out = update_config_text(text, {("display", "brightness"): 90})
    assert out == "[display]\nbrightness = 90\nauto_brightness = true\nauto_brightness_min = 30\n"
    out = update_config_text(text, {("display", "auto_brightness"): False})
    assert out == "[display]\nbrightness = 80\nauto_brightness = false\nauto_brightness_min = 30\n"
