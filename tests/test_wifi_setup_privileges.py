import os
import pwd

import pytest

from flyover.display import privileges
from flyover.display.samples import sample_context
from flyover.display.scenes.wifi_setup import (H, QUIET, TOP, W, WifiSetupScene, qr_matrix,
                                               wifi_payload)

HOTSPOT = {"ssid": "Flyover-Setup", "password": "change-me-now", "ip": "10.42.0.1"}


@pytest.mark.parametrize("ssid, password, expected", [
    ("Home", "secret123", "WIFI:T:WPA;S:Home;P:secret123;;"),
    ("a;b", "p:w,d", r"WIFI:T:WPA;S:a\;b;P:p\:w\,d;;"),
    ('say "hi"', "back\\slash", r'WIFI:T:WPA;S:say \"hi\";P:back\\slash;;'),
])
def test_wifi_payload_escapes_special_characters(ssid, password, expected):
    assert wifi_payload(ssid, password) == expected


def test_the_default_hotspot_code_is_version_3_and_fits_the_panel():
    matrix = qr_matrix(wifi_payload(HOTSPOT["ssid"], HOTSPOT["password"]))
    assert len(matrix) == 29  # version 3 at the lowest error correction
    assert len(matrix) + 2 * QUIET <= W


def test_the_drawn_code_matches_the_matrix_pixel_for_pixel():
    frame = WifiSetupScene().render(sample_context(net={"state": "HOTSPOT", "hotspot": HOTSPOT}))
    matrix = qr_matrix(wifi_payload(HOTSPOT["ssid"], HOTSPOT["password"]))
    box = len(matrix) + 2 * QUIET
    x0 = (W - box) // 2
    for r, row in enumerate(matrix):
        for c, dark in enumerate(row):
            expected = (0, 0, 0) if dark else (255, 255, 255)
            assert frame.getpixel((x0 + QUIET + c, TOP + QUIET + r)) == expected
    # the quiet zone is white all around
    assert frame.getpixel((x0, TOP)) == (255, 255, 255)
    assert frame.getpixel((x0 + box - 1, TOP + box - 1)) == (255, 255, 255)


def test_the_code_stays_black_on_white_at_night():
    from flyover.display.theme import NIGHT

    frame = WifiSetupScene().render(sample_context(palette=NIGHT, net={"state": "HOTSPOT", "hotspot": HOTSPOT}))
    colours = {rgb for _, rgb in frame.crop((15, 1, 49, 34)).getcolors()}
    assert colours == {(0, 0, 0), (255, 255, 255)}


def test_the_largest_possible_hotspot_still_renders_within_the_panel():
    big = {"ssid": "s" * 32, "password": "p" * 63}
    frame = WifiSetupScene().render(sample_context(net={"state": "HOTSPOT", "hotspot": big}))
    assert frame.size == (W, H)
    assert len(qr_matrix(wifi_payload(**{k: big[k] for k in ("ssid", "password")}))) + 2 * QUIET <= W


def test_an_error_replaces_the_title():
    plain = WifiSetupScene().render(sample_context(net={"state": "HOTSPOT", "hotspot": HOTSPOT}))
    wrong = WifiSetupScene().render(sample_context(net={"state": "HOTSPOT", "hotspot": HOTSPOT,
                                                        "error": "Wrong password"}))
    assert plain.tobytes() != wrong.tobytes()
    from flyover.display.theme import DAY
    assert DAY.alert in {rgb for _, rgb in wrong.getcolors()}


def test_the_scene_needs_hotspot_details_unless_connecting():
    with pytest.raises(ValueError):
        WifiSetupScene().render(sample_context(net={"state": "HOTSPOT"}))
    WifiSetupScene().render(sample_context(net={"state": "CONNECTING", "ssid": "HomeNet"}))


# -- the app switches to it ---------------------------------------------------------------


def test_app_shows_the_setup_screen_only_while_the_hotspot_is_up(tmp_path):
    from flyover.display.app import DisplayApp
    from flyover.display.backends.png import PngBackend
    from flyover.state import RunPaths, write_json_atomic

    (tmp_path / "run").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "config.toml").write_text('[location]\nlatitude = 41.9\nlongitude = -87.6\n')
    paths = RunPaths(tmp_path / "run")
    app = DisplayApp(tmp_path / "config.toml", paths, tmp_path / "data", backend=PngBackend(),
                     time_trusted=lambda: True)

    def scene():
        app.tick()
        return app._last_status["scene"]

    assert scene() == "idle"
    write_json_atomic(paths.net, {"state": "HOTSPOT", "hotspot": HOTSPOT, "internet": False})
    assert scene() == "wifi_setup"
    write_json_atomic(paths.net, {"state": "CONNECTING", "ssid": "HomeNet"})
    assert scene() == "wifi_setup"
    write_json_atomic(paths.net, {"state": "HOTSPOT", "hotspot": None})  # hotspot failed to start
    assert scene() == "idle"
    write_json_atomic(paths.net, {"state": "ONLINE", "internet": True})
    assert scene() == "idle"


# -- privilege dropping ------------------------------------------------------------------------


def test_drop_privileges_does_nothing_when_not_root(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    assert privileges.drop_privileges() is False


def test_drop_privileges_switches_user_and_clears_groups(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "setgroups", lambda g: calls.append(("groups", list(g))))
    monkeypatch.setattr(os, "setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr(os, "setuid", lambda u: calls.append(("uid", u)))
    monkeypatch.setattr(pwd, "getpwnam", lambda name: pwd.struct_passwd(
        (name, "x", 1, 2, "", "/usr/sbin", "/usr/sbin/nologin")))
    assert privileges.drop_privileges("daemon") is True
    assert calls == [("groups", []), ("gid", 2), ("uid", 1)]  # groups and gid before uid, in this order


def test_drop_privileges_stays_root_for_an_unknown_user(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    assert privileges.drop_privileges("no-such-user-xyz") is False
