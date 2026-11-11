"""Fixed example states for every screen.

Used by the gallery (to produce the pictures in the docs) and by the snapshot
tests, so a screen only has to be described once.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..config import Config, Display
from ..models import Aircraft, Airport, FlightView, RouteInfo
from ..providers.weather import Weather
from .scenes.base import Context, Scene
from .scenes.boot import BootScene
from .scenes.flight import FlightScene
from .scenes.idle import IdleScene
from .theme import NIGHT

#: A fixed "now" so the pictures never change from run to run.
NOW = datetime(2026, 11, 9, 7, 42, 0)
EPOCH = NOW.timestamp()


def sample_weather(code: int = 2, temp_c: float = 12.3, is_day: bool = True) -> Weather:
    return Weather(temperature_c=temp_c, code=code, is_day=is_day,
                   timezone="America/Chicago", fetched_at=EPOCH - 60)


def sample_context(**overrides) -> Context:
    base = dict(now=NOW, config=Config(), time_trusted=True,
                weather=sample_weather(), epoch=EPOCH)
    base.update(overrides)
    return Context(**base)


SFO = Airport("SFO", "KSFO", "San Francisco International", "San Francisco", 37.6213, -122.379)
ORD = Airport("ORD", "KORD", "Chicago O'Hare International", "Chicago", 41.9742, -87.9073)
MDW = Airport("MDW", "KMDW", "Chicago Midway International", "Chicago", 41.7868, -87.7522)


def sample_flight(**overrides) -> FlightView:
    """A United 737 descending into Chicago, 1.3 km away."""
    aircraft = Aircraft(
        hex="a1b2c3", callsign="UAL1234", registration="N37290", type_code="B738",
        lat=41.9, lon=-87.7, alt_ft=2400, ground_speed_kt=142.0, track_deg=270.0,
        vert_rate_fpm=-640, on_ground=False, observed_at=EPOCH,
    )
    route = RouteInfo(
        flight_number="UA1234", airline_name="United Airlines", airline_iata="UA",
        airline_icao="UAL", origin=SFO, destination=ORD, registration="N37290",
        type_code="B738", type_name="737-824", manufacturer="Boeing",
    )
    fields = dict(aircraft=aircraft, route=route, lat=41.9, lon=-87.7, distance_km=1.3,
                  inside=True, others=0, route_plausible=True)
    fields.update(overrides)
    return FlightView(**fields)


def build_samples() -> list[tuple[str, Scene, Context]]:
    """(name, scene, context) for every state worth looking at."""
    idle = IdleScene()
    flight = FlightScene()
    base = sample_flight()
    climbing = replace(base.aircraft, alt_ft=35000, vert_rate_fpm=1800, track_deg=45.0,
                       ground_speed_kt=451.0, callsign="DAL88", type_code="A359")
    private = replace(base.aircraft, callsign="N123AB", registration="N123AB", type_code="C172",
                      alt_ft=950, ground_speed_kt=96.0, vert_rate_fpm=0, track_deg=135.0)
    airline_route = replace(base.route, flight_number="DL88", airline_name="Delta Air Lines",
                            airline_iata="DL", airline_icao="DAL", manufacturer="Airbus",
                            type_name="A350-900")
    four_letter = replace(base.route, origin=replace(SFO, iata=""), destination=replace(ORD, iata=""))
    afternoon = datetime(2026, 11, 9, 15, 5)
    h24 = replace(Config(), display=replace(Display(), clock_24h=True, temperature_unit="C"))
    return [
        ("boot", BootScene(), sample_context()),
        ("idle_day", idle, sample_context()),
        ("idle_pm", idle, sample_context(now=afternoon, weather=sample_weather(0))),
        ("idle_24h_celsius", idle, sample_context(now=afternoon, config=h24,
                                                  weather=sample_weather(63, -4.0))),
        ("idle_night", idle, sample_context(palette=NIGHT, weather=sample_weather(0, 8.0, False))),
        ("idle_no_time", idle, sample_context(time_trusted=False)),
        ("idle_no_weather", idle, sample_context(weather=None)),
        ("idle_offline", idle, sample_context(net={"internet": False})),
        ("flight_descending", flight, sample_context(flight=base)),
        ("flight_climbing_others", flight, sample_context(flight=replace(
            base, aircraft=climbing, route=airline_route, others=2, distance_km=3.4,
            route_plausible=True))),
        ("flight_aircraft_page", flight, sample_context(flight=base, epoch=EPOCH + 4)),
        ("flight_no_route", flight, sample_context(flight=replace(
            base, aircraft=private, route=None, route_plausible=None, distance_km=0.4))),
        ("flight_route_unverified", flight, sample_context(flight=replace(
            base, route_plausible=False, distance_km=12.0))),
        ("flight_icao_codes", flight, sample_context(flight=replace(base, route=four_letter))),
        ("flight_night", flight, sample_context(flight=base, palette=NIGHT)),
    ]
