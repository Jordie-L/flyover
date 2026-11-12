import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from flyover.config import ConfigStore, parse_config
from flyover.control.netwatch import NetWatch, State
from flyover.control.nm import FakeBackend, WifiNetwork
from flyover.control.web import MAX_FORM_BYTES, ControlApp, ControlServer
from flyover.state import RunPaths, write_json_atomic

EXAMPLE = (Path(__file__).resolve().parents[1] / "config.example.toml").read_text()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def request(self, path, data=None, raw=None, headers=None):
        body = raw if raw is not None else (urllib.parse.urlencode(data).encode() if data is not None else None)
        req = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        try:
            with OPENER.open(req, timeout=10) as r:
                return r.status, r.read().decode(errors="replace"), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace"), dict(e.headers)

    def get(self, path):
        return self.request(path)

    def post(self, path, data, headers=None):
        return self.request(path, data=data, headers=headers)


class Ticker:
    t = 0.0

    def __call__(self):
        return self.t


@pytest.fixture
def stack(tmp_path):
    (tmp_path / "run").mkdir()
    config = tmp_path / "config.toml"
    config.write_text(EXAMPLE.replace("change-me-now", "setup-pass-1"))
    store = ConfigStore(config)
    backend, clock = FakeBackend(), Ticker()
    paths = RunPaths(tmp_path / "run")
    watch = NetWatch(backend, store.get, lambda net: write_json_atomic(paths.net, net), now=clock)
    app = ControlApp(store, paths, watch)
    server = ControlServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()

    class Stack:
        pass

    s = Stack()
    s.client, s.app, s.watch, s.backend, s.clock = Client(server.server_address[1]), app, watch, backend, clock
    s.config, s.paths = config, paths

    def open_hotspot():
        for _ in range(70):
            clock.t += 1
            watch.tick()
        assert watch.state == State.HOTSPOT

    s.open_hotspot = open_hotspot
    yield s
    server.shutdown()
    server.server_close()


def wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- status ---------------------------------------------------------------------------------


def test_status_page_and_api_without_a_running_display(stack):
    status, body, headers = stack.client.get("/")
    assert status == 200 and "Flyover" in body and "not running" in body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert stack.client.get("/frame.png")[0] == 404
    status, body, _ = stack.client.get("/api/status")
    assert status == 200 and json.loads(body)["flat"]["scene"] == "not running"


def test_frame_and_status_are_served_from_the_run_directory(stack):
    from PIL import Image

    Image.new("RGB", (64, 64), (1, 2, 3)).save(stack.paths.frame)
    write_json_atomic(stack.paths.status, {"scene": "flight", "mode": "day", "backend": "preview",
                                           "time_trusted": True, "weather": {"ok": True},
                                           "flights": {"ok": True, "overhead_today": 7}})
    status, data, headers = stack.client.request("/frame.png")
    assert status == 200 and headers["Content-Type"] == "image/png"
    page = stack.client.get("/")[1]
    assert "flight" in page and ">7<" in page and "synchronised" in page
    assert stack.client.get("/health")[1] == "ok"
    assert stack.client.get("/nope")[0] == 404


# -- settings ---------------------------------------------------------------------------------


def test_settings_form_shows_current_values(stack):
    status, body, _ = stack.client.get("/settings")
    assert status == 200 and 'name=latitude type=number value="0.0"' in body
    assert 'value="Flyover-Setup"' in body


def test_saving_settings_updates_the_file_and_keeps_comments(stack):
    status, _, headers = stack.client.post("/settings", {
        "latitude": "41.9", "longitude": "-87.6", "radius_km": "3", "clock_24h": "1",
        "brightness": "70", "hotspot_password": "a-new-password"})
    assert status == 303 and "Saved" in urllib.parse.unquote_plus(headers["Location"])
    text = stack.config.read_text()
    cfg = parse_config(text)
    assert (cfg.location.latitude, cfg.location.longitude, cfg.flights.radius_km) == (41.9, -87.6, 3.0)
    assert cfg.display.clock_24h and cfg.display.brightness == 70
    assert cfg.hotspot.password == "a-new-password"
    assert "# percent, daytime" in text  # comments survive
    assert stack.app.config().location.latitude == 41.9


def test_an_unchecked_box_turns_the_option_off(stack):
    stack.client.post("/settings", {"clock_24h": "1"})
    assert stack.app.config().display.clock_24h
    stack.client.post("/settings", {"brightness": "60"})  # no clock_24h field = unchecked
    assert not stack.app.config().display.clock_24h


def test_pasted_coordinates_fill_both_fields(stack):
    stack.client.post("/settings", {"paste": "41.9742, -87.9073"})
    loc = stack.app.config().location
    assert (loc.latitude, loc.longitude) == (41.9742, -87.9073)
    status, body, _ = stack.client.post("/settings", {"paste": "somewhere nice"})
    assert status == 400 and "Coordinates should look like" in body


@pytest.mark.parametrize("data, fragment", [
    ({"latitude": "abc"}, "latitude must be a number"),
    ({"latitude": "95"}, "latitude"),
    ({"brightness": "0"}, "brightness"),
    ({"max_altitude_ft": "1.5"}, "max altitude ft must be a number"),
    ({"hotspot_password": "short"}, "password"),
    ({"quiet_start": "23:00"}, "quiet_start"),
])
def test_invalid_settings_are_rejected_without_touching_the_file(stack, data, fragment):
    before = stack.config.read_text()
    status, body, _ = stack.client.post("/settings", data)
    assert status == 400 and fragment in body
    assert stack.config.read_text() == before


def test_saving_the_same_values_reports_no_changes_and_leaves_the_file_alone(stack):
    mtime = stack.config.stat().st_mtime_ns
    _, _, headers = stack.client.post("/settings", {"brightness": "60", "timezone": ""})
    assert "No changes" in urllib.parse.unquote_plus(headers["Location"])
    assert stack.config.stat().st_mtime_ns == mtime


def test_oversized_forms_are_refused(stack):
    status, _, _ = stack.client.request("/settings", raw=b"x=" + b"a" * (MAX_FORM_BYTES + 10))
    assert status == 413


# -- wi-fi and the captive portal ------------------------------------------------------------------


def test_wifi_page_lists_networks_and_escapes_hostile_names(stack):
    stack.backend.networks = [WifiNetwork("<script>alert(1)</script>", 80, True),
                              WifiNetwork('Cafe "Open"', 40, False)]
    stack.open_hotspot()
    status, body, _ = stack.client.get("/wifi")
    assert status == 200
    assert "<script>alert(1)</script>" not in body and "&lt;script&gt;" in body
    assert "&quot;Open&quot;" in body and "open" in body
    assert "<nav>" not in body  # no navigation inside the portal


def test_everything_redirects_to_the_setup_page_while_the_hotspot_is_up(stack):
    stack.open_hotspot()
    for path in ("/", "/settings", "/generate_204", "/hotspot-detect.html", "/connecttest.txt"):
        status, _, headers = stack.client.get(path)
        assert status == 303 and headers["Location"] == "http://10.42.0.1/wifi", path
    assert stack.client.get("/wifi")[0] == 200 and stack.client.get("/health")[0] == 200


def test_the_normal_pages_return_once_the_network_is_up(stack):
    stack.backend.connected = "HomeNet"
    stack.clock.t += 1
    stack.watch.tick()
    assert stack.watch.state == State.ONLINE
    assert stack.client.get("/")[0] == 200 and stack.client.get("/generate_204")[0] == 404
    assert "Connected to HomeNet" in stack.client.get("/wifi")[1]


def test_connecting_with_the_right_password(stack):
    stack.open_hotspot()
    status, _, headers = stack.client.post("/wifi/connect", {"ssid": "HomeNet", "password": FakeBackend.PASSWORD})
    assert status == 303 and "connecting=HomeNet" in headers["Location"]
    assert wait_for(lambda: stack.watch.state == State.ONLINE)
    assert stack.backend.connected == "HomeNet"


def test_a_wrong_password_shows_the_reason_after_the_hotspot_returns(stack):
    stack.open_hotspot()
    stack.client.post("/wifi/connect", {"ssid": "HomeNet", "password": "wrong-password"})
    assert wait_for(lambda: stack.watch.snapshot().get("error") == "Wrong password")
    assert stack.watch.state == State.HOTSPOT
    body = stack.client.get("/wifi")[1]
    assert "Wrong password" in body
    api = json.loads(stack.client.get("/api/net")[1])
    assert api["state"] == "HOTSPOT" and api["networks"][0]["ssid"] == "HomeNet"


def test_a_typed_network_name_overrides_the_dropdown(stack):
    stack.backend.networks.append(WifiNetwork("Hidden Net", 10, True))
    stack.open_hotspot()
    stack.client.post("/wifi/connect", {"ssid": "", "ssid_manual": "Hidden Net",
                                        "password": FakeBackend.PASSWORD, "hidden": "1"})
    assert wait_for(lambda: stack.backend.connected == "Hidden Net")


@pytest.mark.parametrize("data, message", [
    ({"ssid": "", "password": ""}, "Choose a network"),
    ({"ssid": "x" * 40, "password": ""}, "Choose a network"),
    ({"ssid": "HomeNet", "password": "short"}, "8 to 63"),
    ({"ssid": "--help", "password": ""}, "starting with a dash"),
])
def test_bad_connect_requests_are_refused_up_front(stack, data, message):
    stack.open_hotspot()
    _, _, headers = stack.client.post("/wifi/connect", data)
    assert message in urllib.parse.unquote_plus(headers["Location"])
    assert "CONNECTING" not in stack.watch.state.value


def test_only_one_wifi_operation_runs_at_a_time(stack):
    release = threading.Event()
    real_connect = stack.backend.connect

    def slow_connect(*args, **kwargs):
        release.wait(5)
        return real_connect(*args, **kwargs)

    stack.backend.connect = slow_connect
    stack.open_hotspot()
    stack.client.post("/wifi/connect", {"ssid": "HomeNet", "password": FakeBackend.PASSWORD})
    assert wait_for(lambda: stack.watch.state == State.CONNECTING)
    _, _, headers = stack.client.post("/wifi/connect", {"ssid": "HomeNet", "password": FakeBackend.PASSWORD})
    assert "still running" in urllib.parse.unquote_plus(headers["Location"])
    release.set()
    assert wait_for(lambda: stack.watch.state == State.ONLINE)


def test_rescan_runs_in_the_background(stack):
    stack.open_hotspot()
    stack.backend.networks = [WifiNetwork("Fresh", 90, True)]
    _, _, headers = stack.client.post("/wifi/rescan", {})
    assert "Scanning" in urllib.parse.unquote_plus(headers["Location"])
    assert wait_for(lambda: [n.ssid for n in stack.watch.snapshot()["networks"]] == ["Fresh"])


def test_a_handler_crash_returns_500_and_the_server_keeps_working(stack, monkeypatch):
    monkeypatch.setattr(stack.app, "config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert stack.client.get("/settings")[0] == 500
    monkeypatch.undo()
    assert stack.client.get("/settings")[0] == 200


def test_idle_connections_time_out_instead_of_holding_a_thread(stack):
    import socket

    from flyover.control.web import Handler

    assert 0 < Handler.timeout <= 30
    sock = socket.create_connection(("127.0.0.1", int(stack.client.base.rsplit(":", 1)[1])))
    sock.close()  # a silent client that leaves must not break the server
    assert stack.client.get("/health")[0] == 200


# -- requests from other web sites ---------------------------------------------------------------


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Host": "attacker.example"},
])
def test_cross_site_posts_are_refused_and_change_nothing(stack, headers):
    before = stack.config.read_text()
    status, _, _ = stack.client.post("/settings", {"brightness": "7"}, headers=headers)
    assert status == 403 and stack.config.read_text() == before
    status, _, _ = stack.client.post("/wifi/connect", {"ssid": "HomeNet", "password": "x"}, headers=headers)
    assert status == 403 and "connect:HomeNet" not in stack.backend.calls


def test_a_rebound_host_name_cannot_read_the_pages_either(stack):
    assert stack.client.request("/settings", headers={"Host": "attacker.example"})[0] == 403
    assert stack.client.request("/health", headers={"Host": "attacker.example"})[0] == 200


def test_posts_from_the_devices_own_pages_are_accepted(stack):
    host = stack.client.base.removeprefix("http://")
    same_origin = {"Origin": stack.client.base, "Sec-Fetch-Site": "same-origin", "Host": host}
    assert stack.client.post("/settings", {"brightness": "9"}, headers=same_origin)[0] == 303
    mdns = {"Origin": "http://flyover.local", "Host": "flyover.local"}
    assert stack.client.post("/settings", {"brightness": "10"}, headers=mdns)[0] == 303


@pytest.mark.parametrize("host", ["10.42.0.1", "192.168.1.20:80", "flyover.local", "flyover", "localhost:8080",
                                  "[::1]:80"])
def test_host_names_for_a_device_on_the_lan_are_local(host):
    from flyover.control.web import host_is_local
    assert host_is_local(host)


@pytest.mark.parametrize("host", ["attacker.example", "evil.com:80", "flyover.example.com"])
def test_public_host_names_are_not_local(host):
    from flyover.control.web import host_is_local
    assert not host_is_local(host)


def test_daylight_dimming_is_switched_on_and_off_from_the_settings_page(stack):
    assert "name=auto_brightness value=1>" in stack.client.get("/settings")[1]  # off, unticked
    status, _, _ = stack.client.post("/settings", {"brightness": "80", "auto_brightness": "1",
                                                   "auto_brightness_min": "25"})
    assert status == 303
    display = stack.app.config().display
    assert display.auto_brightness and display.auto_brightness_min == 25
    assert "name=auto_brightness value=1 checked>" in stack.client.get("/settings")[1]
    stack.client.post("/settings", {"brightness": "80", "auto_brightness_min": "25"})  # box unticked
    assert not stack.app.config().display.auto_brightness


@pytest.mark.parametrize("data, fragment", [
    ({"brightness": "20", "auto_brightness": "1", "auto_brightness_min": "30"}, "must not be more than brightness"),
    ({"auto_brightness_min": "0"}, "auto_brightness_min"),
    ({"auto_brightness_min": "dim"}, "auto brightness min must be a number"),
])
def test_bad_daylight_dimming_settings_are_refused_and_change_nothing(stack, data, fragment):
    before = stack.config.read_text()
    status, body, _ = stack.client.post("/settings", data)
    assert status == 400 and fragment in body
    assert stack.config.read_text() == before
