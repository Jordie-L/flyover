"""``python -m flyover.display``: run the display."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from ..config import ConfigError
from ..state import RunPaths
from .app import DisplayApp
from .backends import BackendUnavailable


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m flyover.display", description="Run the Flyover display.")
    p.add_argument("--config", type=Path, default=Path("/etc/flyover/config.toml"))
    p.add_argument("--backend", choices=("auto", "matrix", "preview"),
                   help="override [display] backend from the config file")
    p.add_argument("--run-dir", type=Path, default=Path("/run/flyover"),
                   help="where frame.png, status.json and net.json live")
    p.add_argument("--data-dir", type=Path, default=Path("/var/lib/flyover"),
                   help="where the route cache and last known time zone are kept")
    p.add_argument("--once", action="store_true", help="render a single frame and exit")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(levelname)s %(name)s: %(message)s")
    args.run_dir.mkdir(parents=True, exist_ok=True)
    args.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        app = DisplayApp(args.config, RunPaths(args.run_dir), args.data_dir,
                         backend_override=args.backend)
    except (ConfigError, BackendUnavailable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.once:
        app.run_once()
        return 0

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    app.run(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
