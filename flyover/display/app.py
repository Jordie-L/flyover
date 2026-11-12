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

from .. import __version__, clock, geo, sun
from ..config import Config, ConfigStore, LocationNotSet
from ..models import FlightView
from ..providers import weather
from ..providers.flights import FlightFeed
from ..providers.poller import Poller
from ..providers.routes import RouteResolver
from ..selector import Pick, Selector
from ..state import RunPaths, read_json, write_json_atomic
from . import fonts
from .backends import Backend, create_backend
from .scenes.base import Context, Scene, blank
from .scenes.boot import BootScene
from .scenes.flight import FlightScene
from .scenes.idle import IdleScene
from .scenes.wifi_setup import WifiSetupScene
from .theme import brightness_for, mode_for, palette_for

log = logging.getLogger(__name__)

TICK_S = 0.5
BOOT_SECONDS = 2.0
WEATHER_INTERVAL_S = 15 * 60


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
        epoch: Callable[[], float] = time.time,
        flight_feed: FlightFeed | None = None,
        routes: RouteResolver | None = None,
    ) -> None:
        self.paths = paths
        self.data_dir = data_dir
        self._fetch_weather = fetch_weather
        self._time_trusted = time_trusted
        self._epoch = epoch

        fonts.preload()  # before any privilege drop, and so a bad install fails here, loudly
        self._config = ConfigStore(config_path)
        self._last_location = self.cfg.location
        self.backend = backend or create_backend(
            backend_override or self.cfg.display.backend, self.cfg, paths
        )
        self._saved_tz = clock.load_saved_timezone(data_dir)
        self._weather = Poller(
            "weather", self._poll_weather, WEATHER_INTERVAL_S, on_result=self._on_weather
        )
        self._feed = flight_feed or FlightFeed(lambda: self.cfg)
        self._routes = routes or RouteResolver(
            data_dir / "routes.sqlite", lambda: self.cfg.flights.route_cache_days
        )
        self._selector = Selector()
        self._idle: Scene = IdleScene()
        self._flight: Scene = FlightScene()
        self._setup: Scene = WifiSetupScene()
        self._boot: Scene = BootScene()
        self._started_at = time.time()
        self._last_status: dict | None = None
        self._overhead_today: set[str] = set()
        self._today = ""

    # -- config -----------------------------------------------------------

    @property
    def cfg(self) -> Config:
        """The current configuration (re-read if the file changed)."""
        return self._config.get()

    def _check_location(self, cfg: Config) -> None:
        if cfg.location != self._last_location:
            self._last_location = cfg.location
            self._weather.poke()  # fetch for the new place right away
            self._feed.poke()

    # -- data -------------------------------------------------------------

    def _poll_weather(self) -> weather.Weather:
        cfg = self.cfg
        if not cfg.location_is_set:
            raise LocationNotSet("location is not set")
        return self._fetch_weather(cfg.location.latitude, cfg.location.longitude)

    def _on_weather(self, value: weather.Weather) -> None:
        if value.timezone:
            self._saved_tz = value.timezone
            try:
                clock.save_timezone(self.data_dir, value.timezone)
            except OSError as exc:
                log.warning("cannot save the time zone: %s", exc)

    # -- one frame --------------------------------------------------------

    def _context(self, cfg: Config) -> tuple[Context, str]:
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
            epoch=self._epoch(),
        )
        return ctx, mode

    def _flight_view(self, pick: Pick) -> FlightView:
        a = pick.aircraft
        route = self._routes.lookup(a.hex, a.callsign)
        plausible = None
        if route is not None and route.has_route:
            plausible = geo.route_is_plausible(
                pick.lat, pick.lon,
                (route.origin.lat, route.origin.lon),
                (route.destination.lat, route.destination.lon),
            )
        return FlightView(a, route, pick.lat, pick.lon, pick.distance_km, pick.inside,
                          pick.others, plausible)

    @staticmethod
    def _needs_setup_screen(net: object) -> bool:
        """True while the setup hotspot is up (with its details) or a connection is being made."""
        if not isinstance(net, dict):
            return False
        return net.get("state") == "CONNECTING" or (
            net.get("state") == "HOTSPOT" and isinstance(net.get("hotspot"), dict)
        )

    def _count_overhead(self, view: FlightView, ctx: Context) -> None:
        """Keep the day's tally of distinct aircraft seen overhead (resets at local midnight)."""
        today = ctx.now.date().isoformat()
        if today != self._today:
            self._today, self._overhead_today = today, set()
        if view.inside:
            self._overhead_today.add(view.aircraft.hex)

    def _choose_scene(self, ctx: Context) -> Scene:
        """Setup screen, else the overhead flight, else the idle screen.

        When it returns the flight scene, the flight to draw has been put in ``ctx.flight``.
        """
        if self._needs_setup_screen(ctx.net):
            return self._setup
        cfg = ctx.config
        pick = self._selector.update(
            self._feed.aircraft, ctx.epoch, (cfg.location.latitude, cfg.location.longitude), cfg.flights
        )
        if pick is None:
            return self._idle
        ctx.flight = self._flight_view(pick)
        self._count_overhead(ctx.flight, ctx)
        return self._flight

    def tick(self) -> None:
        cfg = self.cfg  # read once: every later step in this frame sees the same configuration
        self._check_location(cfg)
        ctx, mode = self._context(cfg)
        self._feed.quiet = mode == "off"
        if mode == "off":
            scene_name, frame = "off", blank()[0]
        else:
            scene = self._choose_scene(ctx)
            scene_name, frame = scene.name, scene.render(ctx)
        elevation = self._sun_elevation(cfg, ctx)
        brightness = brightness_for(mode, cfg.display, elevation)
        self.backend.show(frame, brightness)
        self._write_status(scene_name, mode, brightness, ctx)

    @staticmethod
    def _sun_elevation(cfg: Config, ctx: Context) -> float | None:
        """The sun's elevation, only when it is needed and can be trusted."""
        if not (cfg.display.auto_brightness and cfg.location_is_set and ctx.time_trusted):
            return None
        return sun.elevation_deg(cfg.location.latitude, cfg.location.longitude, ctx.now)

    def _write_status(self, scene: str, mode: str, brightness: int, ctx: Context) -> None:
        w = self._weather.latest
        flights = self._feed.poller
        status = {
            "version": __version__,
            "started_at": self._started_at,
            "backend": self.backend.name,
            "scene": scene,
            "mode": mode,
            "brightness": brightness,
            "time_trusted": ctx.time_trusted,
            "location_set": ctx.config.location_is_set,
            "weather": {
                "ok": w is not None and self._weather.error is None,
                "fetched_at": w.fetched_at if w else None,
                "error": self._weather.error,
            },
            "flights": {
                "ok": flights.latest is not None and flights.error is None,
                "error": flights.error,
                "in_range": len(self._feed.aircraft),
                "overhead_today": len(self._overhead_today),
            },
        }
        if status != self._last_status:  # only touch the disk when something changed
            write_json_atomic(self.paths.status, status)
            self._last_status = status

    # -- lifecycle --------------------------------------------------------

    def show_boot(self, stop: threading.Event) -> None:
        cfg = self.cfg
        ctx, _ = self._context(cfg)
        self.backend.show(self._boot.render(ctx), cfg.display.brightness)
        stop.wait(BOOT_SECONDS)

    def run(self, stop: threading.Event) -> None:
        self._weather.start()
        self._feed.start()
        self._routes.start()
        try:
            try:
                self.show_boot(stop)
            except Exception:  # the splash is cosmetic: never let it stop the real display
                log.exception("boot screen failed")
            last_error = None
            while not stop.is_set():
                try:
                    self.tick()
                    last_error = None
                except Exception as exc:  # a scene bug must not blank the display for good
                    if repr(exc) != last_error:  # ...nor flood the log: report each failure once
                        log.exception("frame failed")
                        last_error = repr(exc)
                stop.wait(TICK_S)
        finally:
            self.close()

    def run_once(self) -> None:
        """Fetch data once (if possible), render one frame, and stop."""
        self._weather.step()
        self._feed.poller.step()
        self.tick()  # queues route lookups for whatever is overhead
        self._routes.drain()
        self.tick()
        self.close()

    def close(self) -> None:
        for worker in (self._weather, self._feed, self._routes):
            worker.stop()
        self.backend.close()
