"""Snapshot tests for every screen, plus end-to-end tests of the app loop.

Snapshots live in tests/snapshots. After an intentional change to a screen,
regenerate them with ``UPDATE_SNAPSHOTS=1 pytest tests/test_scenes_app.py``
and look at the diff in the gallery before committing.
"""

import os
import threading
import time
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from flyover.display.app import DisplayApp
from flyover.display.backends import BackendUnavailable, PreviewBackend, create_backend
from flyover.display.backends.png import PngBackend
from flyover.display.samples import build_samples, sample_weather
from flyover.display.scenes.base import SIZE
from flyover.config import Config
from flyover.state import RunPaths, read_json

SNAPSHOTS = Path(__file__).parent / "snapshots"
SAMPLES = build_samples()


@pytest.mark.parametrize("name, scene, ctx", SAMPLES, ids=[s[0] for s in SAMPLES])
def test_screen_matches_snapshot(name, scene, ctx):
    frame = scene.render(ctx)
    assert frame.size == SIZE and frame.mode == "RGB"
    path = SNAPSHOTS / f"{name}.png"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOTS.mkdir(exist_ok=True)
        frame.save(path)
    assert path.exists(), f"no snapshot for {name}; run with UPDATE_SNAPSHOTS=1"
    expected = Image.open(path).convert("RGB")
    assert ImageChops.difference(frame, expected).getbbox() is None, f"{name} changed"


def test_idle_clock_text():
    from flyover.display.scenes.idle import clock_text
    from flyover.display.samples import sample_context
    from datetime import datetime

    assert clock_text(sample_context(now=datetime(2026, 1, 1, 0, 5))) == ("12:05", "AM")
    assert clock_text(sample_context(now=datetime(2026, 1, 1, 12, 0))) == ("12:00", "PM")
    assert clock_text(sample_context(now=datetime(2026, 1, 1, 13, 7))) == ("1:07", "PM")
    assert clock_text(sample_context(time_trusted=False)) == ("--:--", "")


def test_stale_weather_is_hidden():
    from flyover.display.samples import EPOCH, sample_context
    from flyover.display.scenes.idle import IdleScene
    from flyover.providers.weather import MAX_AGE_S

    scene = IdleScene()
    fresh = scene.render(sample_context())
    stale_ctx = sample_context(epoch=EPOCH + MAX_AGE_S + 3600)
    assert ImageChops.difference(scene.render(stale_ctx), scene.render(sample_context(weather=None))).getbbox() is None
    assert ImageChops.difference(fresh, scene.render(stale_ctx)).getbbox() is not None


# -- backends ---------------------------------------------------------------


def test_preview_backend_writes_png_only_when_the_frame_changes(tmp_path):
    path = tmp_path / "frame.png"
    backend = PreviewBackend(path)
    a, b = Image.new("RGB", SIZE, (1, 2, 3)), Image.new("RGB", SIZE, (9, 9, 9))
    backend.show(a, 50)
    mtime = path.stat().st_mtime_ns
    backend.show(a.copy(), 20)  # same pixels, different brightness: no rewrite
    assert path.stat().st_mtime_ns == mtime
    backend.show(b, 50)
    assert Image.open(path).getpixel((0, 0)) == (9, 9, 9)


def test_png_backend_records_frames(tmp_path):
    backend = PngBackend(tmp_path)
    backend.show(Image.new("RGB", SIZE, (5, 5, 5)), 40)
    assert backend.frames[0][1] == 40 and (tmp_path / "frame-0001.png").exists()


def test_backend_selection_without_the_matrix_library(tmp_path):
    paths = RunPaths(tmp_path)
    assert create_backend("auto", Config(), paths).name == "preview"
    assert create_backend("preview", Config(), paths).name == "preview"
    with pytest.raises(BackendUnavailable, match="rpi-rgb-led-matrix"):
        create_backend("matrix", Config(), paths)


# -- the app loop -----------------------------------------------------------

CONFIG = '[location]\nlatitude = 41.9\nlongitude = -87.6\n'


@pytest.fixture
def make_app(tmp_path):
    def build(config_text=CONFIG, **kwargs):
        config = tmp_path / "config.toml"
        config.write_text(config_text)
        (tmp_path / "run").mkdir(exist_ok=True)
        (tmp_path / "data").mkdir(exist_ok=True)
        defaults = dict(backend=PngBackend(), fetch_weather=lambda lat, lon: sample_weather(),
                        time_trusted=lambda: True)
        return DisplayApp(config, RunPaths(tmp_path / "run"), tmp_path / "data",
                          **{**defaults, **kwargs})
    return build


def test_tick_renders_and_writes_status(make_app, tmp_path):
    app = make_app()
    app._weather.step()
    app.tick()
    status = read_json(tmp_path / "run" / "status.json")
    assert status["scene"] == "idle" and status["backend"] == "png"
    assert status["weather"]["ok"] and status["location_set"]
    assert app.backend.frames[-1][0].size == SIZE


def test_status_is_rewritten_only_on_change(make_app, tmp_path):
    app = make_app()
    app.tick()
    path = tmp_path / "run" / "status.json"
    first = path.stat().st_mtime_ns
    app.tick()
    assert path.stat().st_mtime_ns == first
    app._weather.step()  # weather arrives: status changes
    app.tick()
    assert path.stat().st_mtime_ns != first


def test_weather_time_zone_is_saved_for_the_next_offline_start(make_app, tmp_path):
    app = make_app()
    app._weather.step()
    assert (tmp_path / "data" / "timezone").read_text().strip() == "America/Chicago"
    again = make_app()  # a fresh start with no network yet
    assert again._saved_tz == "America/Chicago"


def test_unset_location_skips_weather_and_says_so(make_app, tmp_path):
    app = make_app(config_text="")
    app._weather.step()
    app.tick()
    status = read_json(tmp_path / "run" / "status.json")
    assert not status["location_set"] and "location" in status["weather"]["error"]


def test_quiet_hours_show_a_black_frame_at_zero_brightness(make_app):
    app = make_app(config_text='[display]\nnight_start = "00:00"\nnight_end = "00:00"\n'
                               'quiet_start = "00:00"\nquiet_end = "23:59"\n')
    app.tick()
    frame, brightness = app.backend.frames[-1]
    assert brightness == 0 and frame.getbbox() is None


def test_untrusted_time_shows_a_dim_placeholder_and_is_reported(make_app, tmp_path):
    from flyover.display.theme import DAY

    app = make_app(time_trusted=lambda: False)
    app.tick()
    clock_area = app.backend.frames[-1][0].crop((0, 7, 64, 27))
    colours = {rgb for _, rgb in clock_area.getcolors()} - {(0, 0, 0)}
    assert colours == {DAY.dim}  # "--:--" in the dim colour, never the bright clock colour
    assert read_json(tmp_path / "run" / "status.json")["time_trusted"] is False


def test_config_changes_are_picked_up_and_bad_ones_ignored(make_app, tmp_path):
    app = make_app()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG + "[display]\nclock_24h = true\n")
    os.utime(config, (1, 1_900_000_000))
    app.tick()
    assert app.cfg.display.clock_24h
    config.write_text("[display]\nbrightness = 0\n")  # invalid
    os.utime(config, (1, 1_900_000_100))
    app.tick()
    assert app.cfg.display.clock_24h  # previous config kept


def test_a_changed_location_triggers_a_new_weather_fetch(make_app, tmp_path):
    app = make_app()
    poked = []
    app._weather.poke = lambda: poked.append(1)
    config = tmp_path / "config.toml"
    config.write_text('[location]\nlatitude = 10\nlongitude = 20\n')
    os.utime(config, (1, 1_900_000_200))
    app.tick()
    assert poked


def test_run_loop_stops_cleanly(make_app, monkeypatch):
    import flyover.display.app as app_module

    monkeypatch.setattr(app_module, "BOOT_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "TICK_S", 0.01)
    app = make_app()
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(stop,))
    thread.start()
    while len(app.backend.frames) < 3:
        thread.join(0.01)
    stop.set()
    thread.join(5)
    assert not thread.is_alive()


def test_a_failing_scene_does_not_stop_the_loop(make_app, monkeypatch):
    import flyover.display.app as app_module

    monkeypatch.setattr(app_module, "BOOT_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "TICK_S", 0.01)
    app = make_app()
    calls = []
    real = app._choose_scene

    def flaky(ctx):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("scene bug")
        return real(ctx)

    app._choose_scene = flaky
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(stop,))
    thread.start()
    while len(app.backend.frames) < 3:
        thread.join(0.01)
    stop.set()
    thread.join(5)
    assert len(calls) >= 2


# -- flights through the app ------------------------------------------------------


@pytest.fixture
def flight_app(make_app, tmp_path):
    """An app whose feed reports one aircraft directly overhead and whose route API is faked."""
    import json

    from flyover.providers.flights import FlightFeed
    from flyover.providers.routes import RouteResolver

    fixtures = Path(__file__).parent / "fixtures"
    route_reply = json.loads((fixtures / "adsbdb_aircraft_with_route.json").read_text())
    sky = {"ac": [{"hex": "a1b2c3", "flight": "UAL1234", "t": "B738", "alt_baro": 2400, "gs": 0,
                   "lat": 41.9, "lon": -87.6, "baro_rate": -640, "track": 270}]}
    state = {"sky": sky}

    def build(config_text=CONFIG):
        app = make_app(config_text)
        feed = FlightFeed(lambda: app.cfg, get=lambda url: state["sky"], now=time.time)
        resolver = RouteResolver(tmp_path / "data" / "routes.sqlite", lambda: 7,
                                 get=lambda url: route_reply)
        app._feed, app._routes = feed, resolver
        return app, state

    return build


def test_an_overhead_aircraft_switches_to_the_flight_scene(flight_app, tmp_path):
    app, _ = flight_app()
    app._feed.poller.step()
    app.tick()
    status = read_json(tmp_path / "run" / "status.json")
    assert status["scene"] == "flight"
    assert status["flights"] == {"ok": True, "error": None, "in_range": 1, "overhead_today": 1}
    before = app.backend.frames[-1][0]
    app._routes.drain()  # the route arrives from the background lookup
    app.tick()
    assert app.backend.frames[-1][0].tobytes() != before.tobytes()  # SFO > ORD now drawn


def test_the_display_returns_to_idle_once_the_hold_time_has_passed(flight_app):
    app, state = flight_app()
    clock = [time.time()]
    app._epoch = lambda: clock[0]
    app._feed.poller.step()
    app.tick()
    assert app._last_status["scene"] == "flight"
    state["sky"] = {"ac": []}
    app._feed.poller.step()
    clock[0] += app.cfg.flights.hold_seconds - 1  # still held
    app.tick()
    assert app._last_status["scene"] == "flight"
    clock[0] += 2  # hold expired
    app.tick()
    assert app._last_status["scene"] == "idle"


def test_quiet_hours_pause_the_flight_feed(flight_app):
    cfg = ('[location]\nlatitude = 41.9\nlongitude = -87.6\n[display]\n'
           'quiet_start = "00:00"\nquiet_end = "23:59"\n')
    app, _ = flight_app(cfg)
    app.tick()
    assert app._feed.quiet and app._last_status["scene"] == "off"


def test_without_a_location_the_clock_still_runs_and_the_feed_explains_why(make_app):
    app = make_app(config_text="")
    app._feed.poller.step()
    assert "location" in app._feed.poller.error
    app.tick()  # still renders the clock
    assert app._last_status["scene"] == "idle"


def test_a_persistent_failure_is_logged_once_not_twice_a_second(make_app, monkeypatch, caplog):
    import logging

    import flyover.display.app as app_module

    monkeypatch.setattr(app_module, "BOOT_SECONDS", 0.0)
    monkeypatch.setattr(app_module, "TICK_S", 0.005)
    app = make_app()
    app.backend.show = lambda *a: (_ for _ in ()).throw(OSError("read-only file system"))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(stop,))
    with caplog.at_level(logging.ERROR):
        thread.start()
        time.sleep(0.2)  # dozens of failing ticks
        stop.set()
        thread.join(5)
    assert caplog.text.count("frame failed") == 1


@pytest.mark.parametrize(
    "hour, trusted, expected",
    [(13, True, 80), (16, True, None), (13, False, 80)],  # None: somewhere between the two ends
)
def test_auto_brightness_follows_the_sun_by_day(make_app, tmp_path, monkeypatch, hour, trusted, expected):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import flyover.display.app as app_module

    tz = ZoneInfo("America/Chicago")
    monkeypatch.setattr(app_module.clock, "now_in",
                        lambda _tz: datetime(2026, 11, 11, hour, 40, tzinfo=tz))
    config = (CONFIG + '[display]\nbrightness = 80\nauto_brightness = true\nauto_brightness_min = 30\n'
              'night_start = "21:00"\nnight_end = "06:00"\n')
    app = make_app(config_text=config, time_trusted=lambda: trusted)
    app.tick()
    brightness = read_json(tmp_path / "run" / "status.json")["brightness"]
    if expected is None:
        assert 30 < brightness < 80  # dusk: dimming, but not yet at the floor
    elif trusted:
        assert brightness == expected  # noon-ish: full
    else:
        assert brightness == 80  # an untrusted clock cannot place the sun: the fixed level


def test_auto_brightness_reaches_the_floor_before_night_mode_starts(make_app, tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import flyover.display.app as app_module

    tz = ZoneInfo("America/Chicago")
    monkeypatch.setattr(app_module.clock, "now_in", lambda _tz: datetime(2026, 11, 11, 19, 30, tzinfo=tz))
    config = (CONFIG + '[display]\nbrightness = 80\nauto_brightness = true\nauto_brightness_min = 30\n'
              'night_start = "21:00"\nnight_end = "06:00"\n')
    app = make_app(config_text=config)
    app.tick()
    status = read_json(tmp_path / "run" / "status.json")
    assert status["mode"] == "day" and status["brightness"] == 30
