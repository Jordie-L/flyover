"""The network state machine.

    BOOT      wait for a saved network; no connection after boot_timeout_s -> HOTSPOT
    ONLINE    test the connection every check_interval_s; N misses in a row -> DEGRADED
    DEGRADED  let NetworkManager reconnect; still down after degraded_timeout_s -> HOTSPOT
    HOTSPOT   serve the setup page; every retry_minutes, if nobody is connected,
              try the saved networks again
    CONNECTING  (transient) joining the network the user picked

This class is the single owner of the Wi-Fi radio. The setup page's requests
go through ``request_connect`` / ``request_rescan`` and share one lock with the
timer-driven ``tick``, so two things never change the network at once.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from typing import Any, Callable

from ..config import Config
from .nm import HOTSPOT_IP, ConnectError, NetworkBackend, NetworkError, WifiNetwork

log = logging.getLogger(__name__)

BOOT_CHECK_S = 5.0  # how often to look for a connection while booting
DEGRADED_CHECK_S = 10.0  # faster than ONLINE: we want to notice recovery quickly
HOTSPOT_BUSY_RECHECK_S = 60.0  # when someone is using the hotspot, ask again later
HOTSPOT_RETRY_AFTER_FAILURE_S = 30.0
RECONNECT_TIMEOUT_S = 20.0


class State(str, enum.Enum):
    BOOT = "BOOT"
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    HOTSPOT = "HOTSPOT"
    CONNECTING = "CONNECTING"


class NetWatch:
    def __init__(
        self,
        backend: NetworkBackend,
        get_config: Callable[[], Config],
        publish: Callable[[dict[str, Any]], None],
        *,
        now: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._nm = backend
        self._cfg = get_config
        self._publish = publish
        self._now = now
        self._wall = wall_clock

        self._op_lock = threading.RLock()  # held while changing the network
        self._snap_lock = threading.Lock()
        self._state = State.BOOT
        self._since = now()
        self._next_check = now()
        self._failures = 0
        self._ssid: str | None = None
        self._error: str | None = None
        self._networks: list[WifiNetwork] = []
        self._hotspot_up = False
        self._next_hotspot_retry = 0.0
        self._last_published: dict[str, Any] | None = None
        self._snapshot: dict[str, Any] = {}
        self._emit()

    # -- reading ----------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The published state plus the scanned network list, safe to call from any thread."""
        with self._snap_lock:
            return dict(self._snapshot)

    @property
    def state(self) -> State:
        return self._state

    # -- the timer-driven part ------------------------------------------------------

    def tick(self) -> None:
        """Advance the state machine. Call about once a second."""
        with self._op_lock:
            now = self._now()
            handler = {
                State.BOOT: self._tick_boot,
                State.ONLINE: self._tick_online,
                State.DEGRADED: self._tick_degraded,
                State.HOTSPOT: self._tick_hotspot,
                State.CONNECTING: lambda now: None,
            }[self._state]
            handler(now)
            self._emit()

    def run(self, stop: threading.Event) -> None:
        last_error = None
        while not stop.is_set():
            try:
                self.tick()
                last_error = None
            except Exception as exc:  # the watcher must never die; log (once per distinct error)
                if repr(exc) != last_error:
                    log.exception("network watcher error")
                    last_error = repr(exc)
            stop.wait(1.0)
        with self._op_lock:
            if self._hotspot_up:
                self._safely(self._nm.stop_hotspot)

    def _tick_boot(self, now: float) -> None:
        if now < self._next_check:
            return
        self._next_check = now + BOOT_CHECK_S
        ssid = self._nm.connected_ssid()
        if ssid and self._nm.has_internet():
            self._go_online(ssid)
        elif now - self._since >= self._cfg().network.boot_timeout_s:
            if ssid:
                self._set(State.DEGRADED, ssid=ssid)
            else:
                self._open_hotspot(rescan=True)

    def _tick_online(self, now: float) -> None:
        if now < self._next_check:
            return
        net = self._cfg().network
        self._next_check = now + net.check_interval_s
        ssid = self._nm.connected_ssid()
        if ssid and self._nm.has_internet():
            self._failures, self._ssid, self._error = 0, ssid, None
            return
        self._failures += 1
        log.warning("connection check failed (%d of %d)", self._failures, net.failures_before_degraded)
        if self._failures >= net.failures_before_degraded:
            self._set(State.DEGRADED, ssid=ssid)

    def _tick_degraded(self, now: float) -> None:
        if now < self._next_check:
            return
        self._next_check = now + DEGRADED_CHECK_S
        ssid = self._nm.connected_ssid()
        if ssid and self._nm.has_internet():
            self._go_online(ssid)
        elif now - self._since >= self._cfg().network.degraded_timeout_s:
            log.warning("no connection for %d s: opening the setup hotspot", now - self._since)
            self._open_hotspot(rescan=True)

    def _tick_hotspot(self, now: float) -> None:
        if not self._hotspot_up:  # the hotspot failed to start earlier; keep trying
            if now >= self._next_hotspot_retry:
                self._open_hotspot(rescan=False)
            return
        if now < self._next_hotspot_retry:
            return
        if self._nm.hotspot_clients() > 0:
            self._next_hotspot_retry = now + HOTSPOT_BUSY_RECHECK_S  # someone is setting up
            return
        log.info("retrying saved networks")
        self._safely(self._nm.stop_hotspot)
        self._hotspot_up = False
        if self._rejoin_saved():
            return
        self._open_hotspot(rescan=True)

    # -- requests from the web UI ----------------------------------------------------------

    def request_connect(self, ssid: str, password: str, hidden: bool = False) -> tuple[bool, str]:
        """Join ``ssid``. Returns (success, message). Blocks until the attempt is finished."""
        with self._op_lock:
            previous = self._state
            self._set(State.CONNECTING, ssid=ssid, error=None)
            self._emit()
            if self._hotspot_up:
                self._safely(self._nm.stop_hotspot)  # one radio: it cannot be both
                self._hotspot_up = False
            try:
                self._nm.connect(ssid, password, hidden)
            except (ConnectError, NetworkError) as exc:
                message = str(exc) or "Could not connect"
                log.warning("connect to %r failed: %s", ssid, message)
                if previous != State.HOTSPOT and self._rejoin_saved():
                    self._error = message
                else:
                    self._open_hotspot(rescan=False)
                    self._error = message
                self._emit()
                return False, message
            self._go_online(ssid)
            self._emit()
            return True, f"Connected to {ssid}"

    def request_rescan(self) -> None:
        """Refresh the network list. In HOTSPOT mode this briefly closes the hotspot."""
        with self._op_lock:
            if self._state == State.HOTSPOT and self._hotspot_up:
                self._safely(self._nm.stop_hotspot)
                self._hotspot_up = False
                self._open_hotspot(rescan=True)
            else:
                self._scan()
            self._emit()

    # -- transitions ---------------------------------------------------------------------------

    def _set(self, state: State, *, ssid: str | None = None, error: str | None = None) -> None:
        if state != self._state:
            log.info("network: %s -> %s", self._state.value, state.value)
            self._state, self._since = state, self._now()
            if state == State.DEGRADED:  # watch for recovery at the faster pace from the start
                self._next_check = self._since + DEGRADED_CHECK_S
        self._ssid = ssid
        self._error = error

    def _go_online(self, ssid: str | None) -> None:
        self._failures = 0
        self._next_check = self._now() + self._cfg().network.check_interval_s
        self._set(State.ONLINE, ssid=ssid)

    def _rejoin_saved(self) -> bool:
        """Try the saved networks. Moves to ONLINE and returns True on success."""
        try:
            joined = self._nm.reconnect_saved(RECONNECT_TIMEOUT_S)
        except NetworkError as exc:
            log.warning("reconnect failed: %s", exc)
            return False
        ssid = self._nm.connected_ssid() if joined else None
        if ssid and self._nm.has_internet():
            self._go_online(ssid)
            return True
        return False

    def _scan(self) -> None:
        try:
            self._networks = self._nm.scan()
        except NetworkError as exc:
            log.warning("scan failed, keeping the previous list: %s", exc)

    def _open_hotspot(self, *, rescan: bool) -> None:
        if rescan:
            self._scan()  # must happen first: the radio cannot scan while it is an access point
        cfg = self._cfg().hotspot
        try:
            self._nm.start_hotspot(cfg.ssid, cfg.password)
        except NetworkError as exc:
            log.error("cannot start the hotspot: %s", exc)
            self._hotspot_up = False
            self._next_hotspot_retry = self._now() + HOTSPOT_RETRY_AFTER_FAILURE_S
            self._set(State.HOTSPOT, error=f"Hotspot failed: {exc}")
            return
        self._hotspot_up = True
        self._next_hotspot_retry = self._now() + cfg.retry_minutes * 60
        self._set(State.HOTSPOT)

    @staticmethod
    def _safely(action: Callable[[], Any]) -> None:
        try:
            action()
        except NetworkError as exc:
            log.warning("%s", exc)

    # -- publishing -------------------------------------------------------------------------------

    def _emit(self) -> None:
        hotspot = None
        if self._state == State.HOTSPOT and self._hotspot_up:
            cfg = self._cfg().hotspot
            hotspot = {"ssid": cfg.ssid, "password": cfg.password, "ip": HOTSPOT_IP}
        status = {
            "state": self._state.value,
            # None while the answer is not known yet, so the display shows no fault mark.
            "internet": {State.ONLINE: True, State.DEGRADED: False, State.HOTSPOT: False}.get(self._state),
            "ssid": self._ssid,
            "hotspot": hotspot,
            "error": self._error,
        }
        with self._snap_lock:
            self._snapshot = {**status, "networks": list(self._networks)}
        if status != self._last_published:
            self._last_published = status
            try:
                self._publish({**status, "updated": self._wall()})
            except OSError as exc:
                log.warning("cannot write the network state: %s", exc)
