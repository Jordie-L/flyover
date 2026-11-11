"""The display program's main loop.

Each tick it builds a ``Context`` from the latest data, picks a scene, renders
it, hands the frame to the backend, and records a small status file for the
web UI. Network fetching lives in background ``Poller`` threads, so the loop
itself never waits on the network.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from .. import __version__, clock
from ..config import Config, ConfigError, load_config
from ..providers import weather
from ..providers.poller import Poller
from ..state import RunPaths, read_json, write_json_atomic
from .backends import Backend, create_backend
from .scenes.base import SIZE, Context, Scene, blank
from .scenes.boot import BootScene
from .scenes.idle import IdleScene
from .theme import brightness_for, mode_for, palette_for

log = logging.getLogger(__name__)

TICK_S = 0.5
BOOT_SECONDS = 2.0
WEATHER_INTERVAL_S = 15 * 60


class LocationNotSet(RuntimeError):
    """Weather cannot be fetched until the coordinates are configured."""


class DisplayApp:
    def __init__(
        self,
        config_path: Path,
        paths: RunPaths,
        data_dir: Path,
        *,
        backend_override: str | None = None,
        backend: Backend | None = None,
        fetch_weather: Callable[[float, float], weather.Weather] = weather.fetch,
        time_trusted: Callable[[], bool] = clock.time_is_trusted,
    ) -> None:
        self.config_path = config_path
        self.paths = paths
        self.data_dir = data_dir
        self._fetch_weather = fetch_weather
        self._time_trusted = time_trusted

        self.cfg: Config = load_config(config_path)
        self._config_mtime = self._mtime()
        self.backend = backend or create_backend(
            backend_override or self.cfg.display.backend, self.cfg, paths
        )
        self._saved_tz = clock.load_saved_timezone(data_dir)
        self._weather = Poller(
            "weather", self._poll_weather, WEATHER_INTERVAL_S, on_result=self._on_weather
        )
        self._idle: Scene = IdleScene()
        self._boot: Scene = BootScene()
        self._started_at = time.time()
        self._last_status: dict | None = None

    # -- config -----------------------------------------------------------

    def _mtime(self) -> float:
        try:
            return self.config_path.stat().st_mtime
        except OSError:
            return 0.0

    def _reload_config_if_changed(self) -> None:
        mtime = self._mtime()
        if mtime == self._config_mtime:
            return
        self._config_mtime = mtime
        try:
            new = load_config(self.config_path)
        except ConfigError as exc:
            log.error("keeping the previous configuration: %s", exc)
            return
        moved = new.location != self.cfg.location
        self.cfg = new
        log.info("configuration reloaded")
        if moved:
            self._weather.poke()

    # -- data -------------------------------------------------------------

    def _poll_weather(self) -> weather.Weather:
        loc = self.cfg.location
        if not self.cfg.location_is_set:
            raise LocationNotSet("location is not set")
        return self._fetch_weather(loc.latitude, loc.longitude)

    def _on_weather(self, value: weather.Weather) -> None:
        if value.timezone:
            self._saved_tz = value.timezone
            try:
                clock.save_timezone(self.data_dir, value.timezone)
            except OSError as exc:
                log.warning("cannot save the time zone: %s", exc)

    # -- one frame --------------------------------------------------------

    def _context(self) -> tuple[Context, str]:
        cfg = self.cfg
        now = clock.now_in(clock.resolve_timezone(cfg.location.timezone, self._saved_tz))
        trusted = self._time_trusted()
        mode = mode_for(now, trusted, cfg.display)
        ctx = Context(
            now=now,
            config=cfg,
            time_trusted=trusted,
            weather=self._weather.latest,
            net=read_json(self.paths.net),
            palette=palette_for(mode),
        )
        return ctx, mode

    def _choose_scene(self, ctx: Context) -> Scene:
        return self._idle

    def tick(self) -> None:
        self._reload_config_if_changed()
        ctx, mode = self._context()
        if mode == "off":
            scene_name, frame = "off", blank()[0]
        else:
            scene = self._choose_scene(ctx)
            scene_name, frame = scene.name, scene.render(ctx)
        self.backend.show(frame, brightness_for(mode, self.cfg.display))
        self._write_status(scene_name, mode, ctx)

    def _write_status(self, scene: str, mode: str, ctx: Context) -> None:
        w = self._weather.latest
        status = {
            "version": __version__,
            "started_at": self._started_at,
            "backend": self.backend.name,
            "scene": scene,
            "mode": mode,
            "brightness": brightness_for(mode, self.cfg.display),
            "time_trusted": ctx.time_trusted,
            "location_set": self.cfg.location_is_set,
            "weather": {
                "ok": w is not None and self._weather.error is None,
                "fetched_at": w.fetched_at if w else None,
                "error": self._weather.error,
            },
        }
        if status != self._last_status:  # only touch the disk when something changed
            write_json_atomic(self.paths.status, status)
            self._last_status = status

    # -- lifecycle --------------------------------------------------------

    def show_boot(self, stop: threading.Event) -> None:
        ctx, _ = self._context()
        self.backend.show(self._boot.render(ctx), self.cfg.display.brightness)
        stop.wait(BOOT_SECONDS)

    def run(self, stop: threading.Event) -> None:
        self._weather.start()
        try:
            self.show_boot(stop)
            while not stop.is_set():
                try:
                    self.tick()
                except Exception:  # a scene bug must not blank the display for good
                    log.exception("frame failed")
                stop.wait(TICK_S)
        finally:
            self.close()

    def run_once(self) -> None:
        """Fetch weather once (if possible), render one frame, and stop."""
        self._weather.step()
        self.tick()
        self.close()

    def close(self) -> None:
        self._weather.stop()
        self.backend.close()
