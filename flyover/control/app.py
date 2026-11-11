"""Start the control service: the web server and the network watcher."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from ..config import ConfigError, ConfigStore
from ..state import RunPaths, write_json_atomic
from .netwatch import NetWatch
from .nm import FakeBackend, NetworkBackend, NmcliBackend
from .web import ControlApp, ControlServer

log = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m flyover.control", description="Run the Flyover control service.")
    p.add_argument("--config", type=Path, default=Path("/etc/flyover/config.toml"))
    p.add_argument("--run-dir", type=Path, default=Path("/run/flyover"))
    p.add_argument("--host", default="0.0.0.0", help="address to listen on")
    p.add_argument("--port", type=int, help="override [web] port from the config file")
    p.add_argument("--interface", default="wlan0", help="Wi-Fi interface NetworkManager manages")
    p.add_argument("--fake-network", action="store_true",
                   help="simulate Wi-Fi instead of running nmcli (for development on a laptop)")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p.parse_args(argv)


def build(args: argparse.Namespace) -> tuple[ControlApp, NetWatch]:
    paths = RunPaths(args.run_dir)
    store = ConfigStore(args.config)
    backend: NetworkBackend = FakeBackend() if args.fake_network else NmcliBackend(args.interface)
    watch = NetWatch(backend, store.get, lambda net: write_json_atomic(paths.net, net))
    return ControlApp(store, paths, watch), watch


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    try:
        app, watch = build(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    port = args.port or app.config().web.port
    try:
        server = ControlServer((args.host, port), app)
    except OSError as exc:
        print(f"error: cannot listen on {args.host}:{port}: {exc}", file=sys.stderr)
        return 2

    stop = threading.Event()
    watcher = threading.Thread(target=watch.run, args=(stop,), name="netwatch", daemon=True)
    watcher.start()

    def shutdown(*_: object) -> None:
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)
    log.info("control service listening on %s:%d", args.host, port)
    try:
        server.serve_forever()
    finally:
        stop.set()
        watcher.join(timeout=10)
        server.server_close()
    return 0
