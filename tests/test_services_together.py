"""The display and control services share only files in the run directory. This wires both up in
one process, with a real HTTP server, and checks that they understand each other."""

import io
import threading
import urllib.request

import pytest
from PIL import Image

from flyover.config import ConfigStore
from flyover.control.netwatch import NetWatch, State
from flyover.control.nm import FakeBackend
from flyover.control.web import ControlApp, ControlServer
from flyover.display.app import DisplayApp
from flyover.display.backends import PreviewBackend
from flyover.display.samples import sample_weather
from flyover.state import RunPaths, read_json, write_json_atomic

CONFIG = '[location]\nlatitude = 41.9\nlongitude = -87.6\n[hotspot]\npassword = "setup-pass-1"\n'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def both(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "data").mkdir()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG)
    paths = RunPaths(tmp_path / "run")

    store = ConfigStore(config)
    clock, backend = Clock(), FakeBackend()
    watch = NetWatch(backend, store.get, lambda net: write_json_atomic(paths.net, net), now=clock)
    server = ControlServer(("127.0.0.1", 0), ControlApp(store, paths, watch))
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()

    display = DisplayApp(config, paths, tmp_path / "data", backend=PreviewBackend(paths.frame),
                         fetch_weather=lambda lat, lon: sample_weather(), time_trusted=lambda: True)
    display._weather.step()

    class Both:
        pass

    b = Both()
    b.watch, b.backend, b.clock, b.display, b.paths = watch, backend, clock, display, paths
    b.port = server.server_address[1]

    def fetch(path):
        with OPENER.open(f"http://127.0.0.1:{b.port}{path}", timeout=10) as r:
            return r.read()

    def advance(seconds):
        for _ in range(int(seconds)):
            clock.t += 1
            watch.tick()

    b.fetch, b.advance = fetch, advance
    yield b
    server.shutdown()
    server.server_close()


def test_hotspot_flows_from_the_control_service_to_the_panel_and_back(both):
    both.display.tick()
    assert both.display._last_status["scene"] == "idle"

    both.advance(70)  # no network: the control service opens the setup hotspot
    assert both.watch.state == State.HOTSPOT
    net = read_json(both.paths.net)
    assert net["hotspot"]["password"] == "setup-pass-1"

    both.display.tick()  # the display reads net.json and shows the QR screen
    assert both.display._last_status["scene"] == "wifi_setup"
    frame = Image.open(both.paths.frame)
    assert frame.size == (64, 64) and frame.getpixel((32, 3)) == (255, 255, 255)  # QR quiet zone

    ok, _ = both.watch.request_connect("HomeNet", FakeBackend.PASSWORD)
    assert ok
    both.display.tick()
    status = read_json(both.paths.status)
    assert status["scene"] == "idle" and status["weather"]["ok"]
    served = Image.open(io.BytesIO(both.fetch("/frame.png")))  # the web UI serves the display's frame
    assert served.tobytes() == Image.open(both.paths.frame).tobytes()
    assert served.getpixel((32, 3)) == (0, 0, 0)  # no QR code any more


def test_the_web_ui_reports_what_the_display_reports(both):
    both.display.tick()
    both.advance(2)
    page = both.fetch("/").decode()
    assert "idle" in page and "preview" in page and "synchronised" in page
    import json

    flat = json.loads(both.fetch("/api/status"))["flat"]
    assert flat["scene"] == "idle" and flat["backend"] == "preview" and flat["network"] == "BOOT"


def test_an_offline_report_puts_the_mark_on_the_idle_screen(both):
    both.backend.connected = "HomeNet"
    both.advance(10)
    assert both.watch.state == State.ONLINE
    both.display.tick()
    mark_pixel = (59, 2)  # a lit pixel of the 7x5 Wi-Fi mark, top right
    assert Image.open(both.paths.frame).getpixel(mark_pixel) == (0, 0, 0)

    both.backend.internet = False
    both.advance(100)  # three failed 30 s checks: DEGRADED, well before the hotspot opens
    assert both.watch.state == State.DEGRADED
    both.display.tick()
    assert Image.open(both.paths.frame).getpixel(mark_pixel) != (0, 0, 0)
