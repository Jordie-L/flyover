"""Wi-Fi control through NetworkManager's ``nmcli``.

This is the only module that runs network commands. Everything else talks to
the ``NetworkBackend`` interface, so the state machine and the web UI are
tested against ``FakeBackend`` and a laptop can run the whole control
service with ``--fake-network``.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
from dataclasses import dataclass
from typing import Callable, Protocol

log = logging.getLogger(__name__)

HOTSPOT_PROFILE = "Flyover-Hotspot"
HOTSPOT_IP = "10.42.0.1"  # NetworkManager's address for a shared (hotspot) connection

#: Reachability probes: plain TCP to well-known anycast resolvers, so a DNS fault
#: cannot be mistaken for "no internet" and nothing about this device is sent.
PROBES = (("1.1.1.1", 443), ("9.9.9.9", 443), ("8.8.8.8", 443))


@dataclass(frozen=True)
class WifiNetwork:
    ssid: str
    signal: int  # 0-100
    secured: bool


class NetworkError(Exception):
    """A network command failed."""


class ConnectError(NetworkError):
    """Joining a network failed; the message is suitable to show to the user."""


class NetworkBackend(Protocol):
    def scan(self) -> list[WifiNetwork]: ...
    def connected_ssid(self) -> str | None:
        """The network joined as a client, or None. A running hotspot does not count."""
    def connect(self, ssid: str, password: str, hidden: bool = False) -> None: ...
    def reconnect_saved(self, timeout_s: float) -> bool:
        """Ask NetworkManager to rejoin a saved network. True if it connected in time."""
    def start_hotspot(self, ssid: str, password: str) -> None: ...
    def stop_hotspot(self) -> None: ...
    def hotspot_clients(self) -> int: ...
    def has_internet(self) -> bool: ...


# -- nmcli output ----------------------------------------------------------------------


def split_terse(line: str) -> list[str]:
    """Split one line of ``nmcli -t`` output on unescaped colons."""
    fields, current, chars = [], [], iter(line)
    for ch in chars:
        if ch == "\\":
            current.append(next(chars, ""))
        elif ch == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(ch)
    fields.append("".join(current))
    return fields


def parse_scan(output: str) -> list[WifiNetwork]:
    """Visible networks, strongest first, one entry per name; hidden networks are skipped."""
    best: dict[str, WifiNetwork] = {}
    for line in output.splitlines():
        fields = split_terse(line)
        if len(fields) < 3 or not fields[0]:
            continue
        ssid, signal, security = fields[0], fields[1], fields[2]
        net = WifiNetwork(ssid, int(signal) if signal.isdigit() else 0, security not in ("", "--"))
        if ssid not in best or net.signal > best[ssid].signal:
            best[ssid] = net
    return sorted(best.values(), key=lambda n: (-n.signal, n.ssid.lower()))


def friendly_connect_error(stderr: str) -> str:
    lowered = stderr.lower()
    if "secrets were required" in lowered or "password" in lowered or "(7)" in lowered:
        return "Wrong password"
    if "no network with ssid" in lowered:
        return "Network not found (this device only sees 2.4 GHz networks)"
    if "timeout" in lowered or "timed out" in lowered:
        return "The network did not respond"
    cleaned = re.sub(r"^Error:\s*", "", stderr.strip().splitlines()[-1] if stderr.strip() else "")
    return cleaned or "Could not connect"


# -- the real thing -----------------------------------------------------------------------


class NmcliBackend:
    def __init__(
        self,
        interface: str = "wlan0",
        *,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        probe: Callable[[str, int, float], bool] | None = None,
    ) -> None:
        self.iface = interface
        self._run = run
        self._probe = probe or _tcp_probe

    def _nmcli(self, *args: str, timeout: float = 30.0, check: bool = True) -> str:
        try:
            result = self._run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            raise NetworkError("nmcli is not installed") from None
        except subprocess.TimeoutExpired:
            raise NetworkError(f"nmcli {args[0]} timed out") from None
        if check and result.returncode != 0:
            raise NetworkError((result.stderr or result.stdout).strip() or f"nmcli {args[0]} failed")
        return result.stdout

    def scan(self) -> list[WifiNetwork]:
        out = self._nmcli("-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                          "ifname", self.iface, "--rescan", "yes", timeout=40)
        return parse_scan(out)

    def connected_ssid(self) -> str | None:
        out = self._nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "device", check=False)
        for line in out.splitlines():
            device, state, connection = (split_terse(line) + ["", "", ""])[:3]
            if device == self.iface and state == "connected" and connection != HOTSPOT_PROFILE:
                return connection or None
        return None

    def connect(self, ssid: str, password: str, hidden: bool = False) -> None:
        args = ["device", "wifi", "connect", ssid, "ifname", self.iface]
        if password:
            # The password is visible to local users in the process list for the few seconds
            # this takes. The device has no other users; see "Security" in the README.
            args += ["password", password]
        if hidden:
            args += ["hidden", "yes"]
        try:
            self._nmcli(*args, timeout=60)
        except NetworkError as exc:
            self._nmcli("connection", "delete", "id", ssid, check=False)  # drop the failed profile
            raise ConnectError(friendly_connect_error(str(exc))) from None

    def reconnect_saved(self, timeout_s: float) -> bool:
        try:
            self._nmcli("device", "connect", self.iface, timeout=timeout_s, check=False)
        except NetworkError:
            return False
        return self.connected_ssid() is not None

    def start_hotspot(self, ssid: str, password: str) -> None:
        self._nmcli("connection", "delete", "id", HOTSPOT_PROFILE, check=False)
        self._nmcli("device", "wifi", "hotspot", "ifname", self.iface, "con-name", HOTSPOT_PROFILE,
                    "ssid", ssid, "password", password, "band", "bg")
        # Never let NetworkManager bring the hotspot up by itself: this service decides.
        self._nmcli("connection", "modify", HOTSPOT_PROFILE, "connection.autoconnect", "no",
                    check=False)

    def stop_hotspot(self) -> None:
        self._nmcli("connection", "down", "id", HOTSPOT_PROFILE, check=False)

    def hotspot_clients(self) -> int:
        try:
            result = self._run(["iw", "dev", self.iface, "station", "dump"],
                               capture_output=True, text=True, timeout=10)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            log.warning("cannot count hotspot clients (is 'iw' installed?)")
            return 0
        return sum(1 for line in result.stdout.splitlines() if line.startswith("Station "))

    def has_internet(self) -> bool:
        return any(self._probe(host, port, 3.0) for host, port in PROBES)


def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# -- a stand-in for development and tests ----------------------------------------------------


class FakeBackend:
    """An in-memory network that behaves like a home with one router.

    The password for any secured network is ``PASSWORD``. Tests flip the public
    attributes to simulate outages.
    """

    PASSWORD = "correct-horse"

    def __init__(self, networks: list[WifiNetwork] | None = None, *, connected: str | None = None,
                 internet: bool = True) -> None:
        self.networks = networks if networks is not None else [
            WifiNetwork("HomeNet", 82, True), WifiNetwork("Neighbour", 41, True),
            WifiNetwork("CoffeeShop", 30, False),
        ]
        self.connected = connected
        self.internet = internet
        self.saved: set[str] = {connected} if connected else set()
        self.hotspot: tuple[str, str] | None = None
        self.clients = 0
        self.calls: list[str] = []
        self.hotspot_error: str | None = None

    def scan(self) -> list[WifiNetwork]:
        self.calls.append("scan")
        return list(self.networks)

    def connected_ssid(self) -> str | None:
        return self.connected

    def connect(self, ssid: str, password: str, hidden: bool = False) -> None:
        self.calls.append(f"connect:{ssid}")
        known = {n.ssid: n for n in self.networks}
        if ssid not in known:
            raise ConnectError("Network not found (this device only sees 2.4 GHz networks)")
        if known[ssid].secured and password != self.PASSWORD:
            raise ConnectError("Wrong password")
        self.connected = ssid
        self.saved.add(ssid)

    def reconnect_saved(self, timeout_s: float) -> bool:
        self.calls.append("reconnect")
        visible = {n.ssid for n in self.networks}
        for ssid in sorted(self.saved & visible):
            self.connected = ssid
            return True
        return False

    def start_hotspot(self, ssid: str, password: str) -> None:
        self.calls.append("hotspot_up")
        if self.hotspot_error:
            raise NetworkError(self.hotspot_error)
        self.hotspot = (ssid, password)
        self.connected = None

    def stop_hotspot(self) -> None:
        self.calls.append("hotspot_down")
        self.hotspot = None

    def hotspot_clients(self) -> int:
        return self.clients if self.hotspot else 0

    def has_internet(self) -> bool:
        return self.internet and self.connected is not None
