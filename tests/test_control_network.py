import logging
import subprocess

import pytest

from flyover.config import parse_config
from flyover.control import nm
from flyover.control.netwatch import (DEGRADED_CHECK_S, HOTSPOT_BUSY_RECHECK_S, NetWatch, State)
from flyover.control.nm import FakeBackend, NmcliBackend, WifiNetwork

CFG = parse_config('[hotspot]\nssid = "Flyover-Setup"\npassword = "setup-pass-1"\nretry_minutes = 5\n')
NET = CFG.network  # boot 60 s, check 30 s, 3 failures, degraded 180 s


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds, watch, step=1.0):
        """Move time forward, ticking the watcher once per step like the real loop."""
        end = self.t + seconds
        while self.t < end:
            self.t += step
            watch.tick()


@pytest.fixture
def make():
    def build(backend=None, cfg=CFG):
        clock, published = Clock(), []
        backend = backend or FakeBackend()
        watch = NetWatch(backend, lambda: cfg, published.append, now=clock, wall_clock=lambda: 1e9)
        return watch, backend, clock, published
    return build


# -- boot ---------------------------------------------------------------------------


def test_boot_with_a_saved_network_goes_online(make):
    watch, backend, clock, published = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    assert watch.state == State.ONLINE
    assert published[-1]["internet"] is True and published[-1]["ssid"] == "HomeNet"
    assert published[-1]["hotspot"] is None


def test_unknown_internet_is_reported_as_unknown_while_booting(make):
    watch, _, _, published = make(FakeBackend())
    assert published[0]["state"] == "BOOT" and published[0]["internet"] is None


def test_boot_without_a_network_opens_the_hotspot_after_the_timeout(make):
    watch, backend, clock, published = make()
    clock.advance(NET.boot_timeout_s - 6, watch)
    assert watch.state == State.BOOT and backend.hotspot is None
    clock.advance(10, watch)
    assert watch.state == State.HOTSPOT
    assert backend.calls.count("scan") == 1 and backend.hotspot == ("Flyover-Setup", "setup-pass-1")
    assert backend.calls.index("scan") < backend.calls.index("hotspot_up")  # scan before the AP
    assert published[-1]["hotspot"] == {"ssid": "Flyover-Setup", "password": "setup-pass-1",
                                         "ip": nm.HOTSPOT_IP}
    assert [n.ssid for n in watch.snapshot()["networks"]] == ["HomeNet", "Neighbour", "CoffeeShop"]


def test_boot_connected_but_no_internet_degrades_instead_of_opening_the_hotspot(make):
    watch, backend, clock, _ = make(FakeBackend(connected="HomeNet", internet=False))
    clock.advance(NET.boot_timeout_s + 6, watch)
    assert watch.state == State.DEGRADED and backend.hotspot is None


# -- online / degraded -----------------------------------------------------------------


def test_a_brief_outage_does_not_degrade(make):
    watch, backend, clock, _ = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    backend.internet = False
    clock.advance(NET.check_interval_s * (NET.failures_before_degraded - 1), watch)
    assert watch.state == State.ONLINE
    backend.internet = True
    clock.advance(NET.check_interval_s * 2, watch)
    assert watch.state == State.ONLINE and watch._failures == 0


def test_repeated_failures_degrade_then_recovery_returns_online(make):
    watch, backend, clock, published = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    backend.internet = False
    clock.advance(NET.check_interval_s * NET.failures_before_degraded + 1, watch)
    assert watch.state == State.DEGRADED and published[-1]["internet"] is False
    backend.internet = True
    clock.advance(DEGRADED_CHECK_S + 1, watch)
    assert watch.state == State.ONLINE and published[-1]["internet"] is True


def test_a_long_outage_opens_the_hotspot_with_a_fresh_scan(make):
    watch, backend, clock, _ = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    backend.internet = False
    clock.advance(NET.check_interval_s * NET.failures_before_degraded + 1, watch)
    assert watch.state == State.DEGRADED
    clock.advance(NET.degraded_timeout_s + DEGRADED_CHECK_S, watch)
    assert watch.state == State.HOTSPOT and backend.hotspot is not None
    assert "scan" in backend.calls


# -- hotspot -------------------------------------------------------------------------------


def hotspot_watch(make):
    watch, backend, clock, published = make()
    clock.advance(NET.boot_timeout_s + 6, watch)
    assert watch.state == State.HOTSPOT
    return watch, backend, clock, published


def test_hotspot_retries_saved_networks_only_when_nobody_is_connected(make):
    watch, backend, clock, _ = hotspot_watch(make)
    backend.saved.add("HomeNet")  # the router came back
    backend.clients = 1  # but someone is using the setup page
    clock.advance(CFG.hotspot.retry_minutes * 60 + 1, watch)
    assert watch.state == State.HOTSPOT and "reconnect" not in backend.calls
    backend.clients = 0
    clock.advance(HOTSPOT_BUSY_RECHECK_S + 1, watch)
    assert watch.state == State.ONLINE and backend.hotspot is None


def test_a_failed_retry_rescans_and_reopens_the_hotspot(make):
    watch, backend, clock, _ = hotspot_watch(make)
    scans = backend.calls.count("scan")
    clock.advance(CFG.hotspot.retry_minutes * 60 + 1, watch)
    assert watch.state == State.HOTSPOT and backend.hotspot is not None
    assert backend.calls.count("scan") == scans + 1


def test_connecting_with_the_right_password_goes_online(make):
    watch, backend, clock, published = hotspot_watch(make)
    ok, message = watch.request_connect("HomeNet", FakeBackend.PASSWORD)
    assert ok and "HomeNet" in message
    assert watch.state == State.ONLINE and backend.hotspot is None
    assert published[-1]["ssid"] == "HomeNet" and published[-1]["hotspot"] is None
    assert "CONNECTING" in [p["state"] for p in published]


def test_the_wrong_password_brings_the_hotspot_back_with_the_reason(make):
    watch, backend, clock, published = hotspot_watch(make)
    ok, message = watch.request_connect("HomeNet", "nope")
    assert not ok and message == "Wrong password"
    assert watch.state == State.HOTSPOT and backend.hotspot is not None
    assert published[-1]["error"] == "Wrong password" and published[-1]["hotspot"] is not None
    assert backend.calls.count("scan") == 1  # no second scan: the list was kept


def test_a_failed_change_from_the_lan_rejoins_the_old_network(make):
    watch, backend, clock, _ = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    ok, message = watch.request_connect("Neighbour", "wrong")
    assert not ok and watch.state == State.ONLINE and backend.hotspot is None
    assert backend.connected == "HomeNet"
    assert watch.snapshot()["error"] == message


def test_rescan_in_hotspot_mode_closes_and_reopens_it(make):
    watch, backend, clock, _ = hotspot_watch(make)
    backend.networks = [WifiNetwork("NewNet", 70, True)]
    watch.request_rescan()
    assert [n.ssid for n in watch.snapshot()["networks"]] == ["NewNet"]
    assert backend.hotspot is not None and watch.state == State.HOTSPOT
    assert backend.calls[-3:] == ["hotspot_down", "scan", "hotspot_up"]


def test_hotspot_start_failure_is_reported_and_retried(make):
    backend = FakeBackend()
    backend.hotspot_error = "device busy"
    watch, backend, clock, published = make(backend)
    clock.advance(NET.boot_timeout_s + 6, watch)
    assert watch.state == State.HOTSPOT and published[-1]["hotspot"] is None
    assert "device busy" in published[-1]["error"]
    backend.hotspot_error = None
    clock.advance(40, watch)
    assert published[-1]["hotspot"] is not None and published[-1]["error"] is None


def test_the_hotspot_credentials_follow_config_changes(make):
    cfgs = [CFG]
    clock, published = Clock(), []
    backend = FakeBackend()
    watch = NetWatch(backend, lambda: cfgs[0], published.append, now=clock)
    cfgs[0] = parse_config('[hotspot]\nssid = "Other"\npassword = "another-pass"\n')
    clock.advance(NET.boot_timeout_s + 6, watch)
    assert backend.hotspot == ("Other", "another-pass")


def test_unchanged_state_is_not_republished(make):
    watch, backend, clock, published = make(FakeBackend(connected="HomeNet"))
    clock.advance(1, watch)
    count = len(published)
    clock.advance(20, watch)
    assert len(published) == count


def test_run_loop_survives_an_exception_and_stops(make, caplog):
    import threading

    watch, backend, _, _ = make()
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("driver exploded")

    backend.connected_ssid = boom
    stop = threading.Event()
    watch._now = lambda: 100.0  # past the boot timeout so the check runs immediately
    thread = threading.Thread(target=watch.run, args=(stop,))
    with caplog.at_level(logging.ERROR):
        thread.start()
        while not calls:
            thread.join(0.01)
        stop.set()
        thread.join(5)
    assert not thread.is_alive() and "network watcher error" in caplog.text


# -- nmcli parsing and the real backend with a fake subprocess --------------------------------


def test_split_terse_handles_escaped_colons():
    assert nm.split_terse(r"My\:Net:78:WPA2") == ["My:Net", "78", "WPA2"]
    assert nm.split_terse("a::c") == ["a", "", "c"]
    assert nm.split_terse(r"back\\slash:1:--") == ["back\\slash", "1", "--"]


def test_parse_scan_dedupes_sorts_and_flags_security():
    out = "\n".join(["HomeNet:40:WPA2", "HomeNet:80:WPA2", ":50:WPA2", "Open Cafe:60:", "Mixed:70:WPA1 WPA2",
                     "Bad:abc:--", "short"])
    nets = nm.parse_scan(out)
    assert [(n.ssid, n.signal, n.secured) for n in nets] == [
        ("HomeNet", 80, True), ("Mixed", 70, True), ("Open Cafe", 60, False), ("Bad", 0, False)]


@pytest.mark.parametrize("stderr, expected", [
    ("Error: Connection activation failed: (7) Secrets were required, but not provided.", "Wrong password"),
    ("Error: No network with SSID 'x' found.", "Network not found (this device only sees 2.4 GHz networks)"),
    ("Error: Timeout expired (90 seconds)", "The network did not respond"),
    ("Error: Something odd happened.", "Something odd happened."),
    ("", "Could not connect"),
])
def test_friendly_connect_errors(stderr, expected):
    assert nm.friendly_connect_error(stderr) == expected


class FakeRun:
    """Stands in for subprocess.run: matches on the argument list."""

    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        for needle, (code, out, err) in self.replies.items():
            if needle in " ".join(argv):
                return subprocess.CompletedProcess(argv, code, out, err)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_nmcli_scan_and_connected_ssid():
    run = FakeRun({
        "wifi list": (0, "HomeNet:80:WPA2\nCafe:50:\n", ""),
        "-f DEVICE,STATE,CONNECTION": (0, "eth0:unavailable:\nwlan0:connected:HomeNet\n", ""),
    })
    backend = NmcliBackend(run=run)
    assert [n.ssid for n in backend.scan()] == ["HomeNet", "Cafe"]
    assert backend.connected_ssid() == "HomeNet"
    assert "--rescan" in run.calls[0] and "wlan0" in run.calls[0]


def test_a_running_hotspot_does_not_count_as_connected():
    run = FakeRun({"-f DEVICE,STATE,CONNECTION": (0, f"wlan0:connected:{nm.HOTSPOT_PROFILE}\n", "")})
    assert NmcliBackend(run=run).connected_ssid() is None


def test_nmcli_connect_builds_the_command_and_cleans_up_on_failure():
    run = FakeRun({"wifi connect": (4, "", "Error: Connection activation failed: (7) Secrets were required")})
    backend = NmcliBackend(run=run)
    with pytest.raises(nm.ConnectError, match="Wrong password"):
        backend.connect("Home Net", "pw123456", hidden=True)
    connect = run.calls[0]
    assert connect[:5] == ["nmcli", "device", "wifi", "connect", "Home Net"]
    assert connect[-4:] == ["password", "pw123456", "hidden", "yes"]
    assert run.calls[1][:4] == ["nmcli", "connection", "delete", "id"]  # failed profile removed


def test_nmcli_connect_to_an_open_network_sends_no_password():
    run = FakeRun({})
    NmcliBackend(run=run).connect("Cafe", "")
    assert "password" not in run.calls[0]


def test_nmcli_hotspot_commands():
    run = FakeRun({})
    backend = NmcliBackend(run=run)
    backend.start_hotspot("Flyover-Setup", "setup-pass-1")
    joined = [" ".join(c) for c in run.calls]
    assert any("device wifi hotspot ifname wlan0" in j and "band bg" in j and "setup-pass-1" in j
               for j in joined)
    assert any("connection.autoconnect no" in j for j in joined)
    backend.stop_hotspot()
    assert joined[0].startswith("nmcli connection delete id Flyover-Hotspot")
    assert " ".join(run.calls[-1]) == f"nmcli connection down id {nm.HOTSPOT_PROFILE}"


def test_nmcli_failures_surface_as_network_errors():
    backend = NmcliBackend(run=FakeRun({"hotspot": (1, "", "Error: device busy")}))
    with pytest.raises(nm.NetworkError, match="device busy"):
        backend.start_hotspot("a", "bbbbbbbb")

    def missing(*a, **k):
        raise FileNotFoundError

    with pytest.raises(nm.NetworkError, match="not installed"):
        NmcliBackend(run=missing).scan()


def test_hotspot_client_count_from_iw():
    out = "Station aa:bb:cc:dd:ee:01 (on wlan0)\n\tinactive time: 10 ms\nStation aa:bb:cc:dd:ee:02 (on wlan0)\n"
    assert NmcliBackend(run=FakeRun({"station dump": (0, out, "")})).hotspot_clients() == 2

    def missing(*a, **k):
        raise FileNotFoundError

    assert NmcliBackend(run=missing).hotspot_clients() == 0


def test_internet_probe_succeeds_if_any_target_answers():
    seen = []

    def probe(host, port, timeout):
        seen.append(host)
        return host == "9.9.9.9"

    assert NmcliBackend(probe=probe).has_internet()
    assert seen == ["1.1.1.1", "9.9.9.9"]  # stops at the first success
    assert not NmcliBackend(probe=lambda *a: False).has_internet()
