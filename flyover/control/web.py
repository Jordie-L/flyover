"""The control service's HTTP server: status, settings, Wi-Fi setup, captive portal.

Built on the standard library: the pages are small and used by one person at
a time, so a framework would only cost memory on a 512 MB board.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..config import Config, ConfigError, ConfigStore
from ..state import RunPaths, read_json
from . import config_edit, pages
from .netwatch import NetWatch, State
from .nm import HOTSPOT_IP

log = logging.getLogger(__name__)

MAX_FORM_BYTES = 8192

#: While the setup hotspot is up, everything else redirects here: that is what
#: makes a phone pop up its "sign in to Wi-Fi" screen.
PORTAL_PATHS = frozenset({"/wifi", "/wifi/connect", "/wifi/rescan", "/api/net", "/health"})

_COORDS = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$")

# form field -> (section, key, type)
_SETTINGS = {
    "latitude": ("location", "latitude", float),
    "longitude": ("location", "longitude", float),
    "timezone": ("location", "timezone", str),
    "radius_km": ("flights", "radius_km", float),
    "max_altitude_ft": ("flights", "max_altitude_ft", int),
    "brightness": ("display", "brightness", int),
    "auto_brightness_min": ("display", "auto_brightness_min", int),
    "night_brightness": ("display", "night_brightness", int),
    "night_start": ("display", "night_start", str),
    "night_end": ("display", "night_end", str),
    "quiet_start": ("display", "quiet_start", str),
    "quiet_end": ("display", "quiet_end", str),
    "temperature_unit": ("display", "temperature_unit", str),
    "distance_unit": ("display", "distance_unit", str),
    "hotspot_ssid": ("hotspot", "ssid", str),
    "hotspot_password": ("hotspot", "password", str),
}


def host_is_local(host_header: str) -> bool:
    """True if a ``Host`` header names this device the way a person on the LAN would reach it.

    Accepts IP addresses, ``localhost``, ``*.local`` (mDNS) and single-label names. A public
    name such as ``attacker.example`` that has been rebound to this device's address is
    refused, which is what stops DNS-rebinding attacks from a web page.
    """
    host = host_header.strip().lower()
    if host.startswith("["):  # [v6]:port
        name = host[1:].split("]", 1)[0]
    else:
        name = host.rsplit(":", 1)[0] if ":" in host else host
    name = name.rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return name == "localhost" or name.endswith(".local") or "." not in name


class ControlApp:
    """What the HTTP handlers act on: the config file, the run files and the network watcher."""

    def __init__(self, store: ConfigStore, paths: RunPaths, netwatch: NetWatch) -> None:
        self.store = store
        self.paths = paths
        self.netwatch = netwatch
        self._job_lock = threading.Lock()
        self._job: threading.Thread | None = None

    def config(self) -> Config:
        return self.store.get()

    def save_settings(self, form: dict[str, str]) -> str:
        """Apply the settings form. Returns a message; raises ``ConfigError`` for bad input."""
        current = self.config()
        pasted = form.get("paste", "").strip()
        if pasted:
            match = _COORDS.match(pasted)
            if not match:
                raise ConfigError('Coordinates should look like "41.9742, -87.9073"')
            form = {**form, "latitude": match.group(1), "longitude": match.group(2)}

        values: dict[tuple[str, str], Any] = {}
        for name, (section, key, cast) in _SETTINGS.items():
            if name not in form:
                continue
            raw = form[name] if name == "hotspot_password" else form[name].strip()
            try:
                values[(section, key)] = cast(raw)
            except ValueError:
                raise ConfigError(f"{name.replace('_', ' ')} must be a number") from None
        values[("display", "clock_24h")] = form.get("clock_24h") == "1"  # an unchecked box is absent
        values[("display", "auto_brightness")] = form.get("auto_brightness") == "1"

        changes = {(sec, key): v for (sec, key), v in values.items()
                   if getattr(getattr(current, sec), key) != v}
        if not changes:
            return "No changes."
        config_edit.apply_changes(self.store.path, changes)
        return "Saved. The display applies changes within a second."

    # -- Wi-Fi jobs run in the background: the radio switches, so the HTTP reply cannot wait ----

    def _start_job(self, target, *args) -> bool:
        with self._job_lock:
            if self._job is not None and self._job.is_alive():
                return False
            self._job = threading.Thread(target=target, args=args, name="wifi-job", daemon=True)
            self._job.start()
            return True

    def connect(self, ssid: str, password: str, hidden: bool) -> str | None:
        """Start joining a network. Returns an error message, or None if the attempt began."""
        if not 1 <= len(ssid.encode()) <= 32:
            return "Choose a network."
        if ssid.startswith("-"):  # nmcli takes the name positionally and would read this as an option
            return "Network names starting with a dash are not supported."
        if password and not 8 <= len(password) <= 64:
            return "Wi-Fi passwords are 8 to 63 characters."
        if not self._start_job(self.netwatch.request_connect, ssid, password, hidden):
            return "Another connection attempt is still running."
        return None

    def rescan(self) -> bool:
        return self._start_job(self.netwatch.request_rescan)


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: ControlApp) -> None:
        super().__init__(address, Handler)
        self.app = app


class Handler(BaseHTTPRequestHandler):
    server: ControlServer
    server_version = "flyover"
    timeout = 15  # seconds a client may take to send its request; stops idle sockets pinning a thread

    def log_message(self, fmt: str, *args: Any) -> None:  # route access logs to logging, quietly
        log.debug("%s %s", self.address_string(), fmt % args)

    # -- plumbing ---------------------------------------------------------------------------

    def _send(self, status: int, body: bytes | str, ctype: str = "text/html; charset=utf-8",
              headers: dict[str, str] | None = None) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                         "script-src 'unsafe-inline'; connect-src 'self'; form-action 'self'")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self._send(HTTPStatus.SEE_OTHER, "", headers={"Location": location})

    def _json(self, obj: Any) -> None:
        self._send(HTTPStatus.OK, json.dumps(obj), "application/json")

    def _form(self) -> dict[str, str] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_FORM_BYTES:
            self._send(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Too large")
            return None
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    @property
    def _app(self) -> ControlApp:
        return self.server.app

    def _in_portal(self) -> bool:
        return self._app.netwatch.state in (State.HOTSPOT, State.CONNECTING)

    def _request_allowed(self) -> bool:
        """Refuse requests a web page on another site could be making on a visitor's behalf.

        There is no login, so the defence against cross-site form posts (CSRF) and DNS
        rebinding is to check where the request says it came from. Command-line clients
        send no ``Origin`` and are unaffected.
        """
        host = self.headers.get("Host", "")
        if host and not host_is_local(host):
            return False
        if self.command != "POST":
            return True
        origin = self.headers.get("Origin")
        if origin is not None and (origin == "null" or urllib.parse.urlsplit(origin).netloc.lower() != host.lower()):
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")

    def _dispatch(self, routes: dict[str, Any]) -> None:
        path = urllib.parse.urlsplit(self.path).path
        try:
            if self._in_portal() and path not in PORTAL_PATHS:
                self._redirect(f"http://{HOTSPOT_IP}/wifi")
            elif path != "/health" and not self._request_allowed():
                log.warning("refused %s %s (Host %r, Origin %r)", self.command, path,
                            self.headers.get("Host"), self.headers.get("Origin"))
                self._send(HTTPStatus.FORBIDDEN, "Forbidden", "text/plain")
            elif path in routes:
                routes[path]()
            else:
                self._send(HTTPStatus.NOT_FOUND, "Not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the phone left (it is switching networks); nothing to answer
        except Exception:
            log.exception("request failed: %s %s", self.command, path)
            self._send(HTTPStatus.INTERNAL_SERVER_ERROR, "Something went wrong.", "text/plain")

    def do_GET(self) -> None:
        self._dispatch({
            "/": self._page_status, "/settings": self._page_settings, "/wifi": self._page_wifi,
            "/frame.png": self._frame, "/api/status": self._api_status, "/api/net": self._api_net,
            "/health": lambda: self._send(HTTPStatus.OK, "ok", "text/plain"),
        })

    do_HEAD = do_GET

    def do_POST(self) -> None:
        self._dispatch({"/settings": self._post_settings, "/wifi/connect": self._post_connect,
                        "/wifi/rescan": self._post_rescan})

    # -- GET pages ------------------------------------------------------------------------------

    def _query(self) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).items()}

    def _page_status(self) -> None:
        paths = self._app.paths
        self._send(HTTPStatus.OK, pages.dashboard(read_json(paths.status), read_json(paths.net)))

    def _page_settings(self) -> None:
        self._send(HTTPStatus.OK, pages.settings(self._app.config(), self._query().get("saved")))

    def _page_wifi(self) -> None:
        q = self._query()
        self._send(HTTPStatus.OK, pages.wifi(self._app.netwatch.snapshot(), q.get("notice"), q.get("error"),
                                             q.get("connecting")))

    def _frame(self) -> None:
        try:
            data = self._app.paths.frame.read_bytes()
        except OSError:
            self._send(HTTPStatus.NOT_FOUND, "No frame yet", "text/plain")
            return
        self._send(HTTPStatus.OK, data, "image/png")

    def _api_status(self) -> None:
        paths = self._app.paths
        status, net = read_json(paths.status), read_json(paths.net)
        self._json({"status": status, "net": net, "flat": pages.flat_status(status, net)})

    def _api_net(self) -> None:
        snap = self._app.netwatch.snapshot()
        snap["networks"] = [n.__dict__ for n in snap.get("networks", [])]
        self._json(snap)

    # -- POST actions -----------------------------------------------------------------------------

    def _post_settings(self) -> None:
        form = self._form()
        if form is None:
            return
        try:
            notice = self._app.save_settings(form)
        except (ConfigError, OSError) as exc:
            cfg = self._app.config()
            self._send(HTTPStatus.BAD_REQUEST, pages.settings(cfg, None, str(exc)))
            return
        self._redirect("/settings?" + urllib.parse.urlencode({"saved": notice}))

    def _post_connect(self) -> None:
        form = self._form()
        if form is None:
            return
        ssid = form.get("ssid_manual", "").strip() or form.get("ssid", "")
        error = self._app.connect(ssid, form.get("password", ""), form.get("hidden") == "1")
        if error:
            self._redirect("/wifi?" + urllib.parse.urlencode({"error": error}))
        else:
            self._redirect("/wifi?" + urllib.parse.urlencode({"connecting": ssid}))

    def _post_rescan(self) -> None:
        started = self._app.rescan()
        note = ("Scanning. Rejoin the setup hotspot and reload this page in about 15 seconds."
                if started else "A Wi-Fi operation is already running.")
        self._redirect("/wifi?" + urllib.parse.urlencode({"notice": note}))
