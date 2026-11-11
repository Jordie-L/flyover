"""Fixed example states for every screen.

Used by the gallery (to produce the pictures in the docs) and by the snapshot
tests, so a screen only has to be described once.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ..config import Config, Display
from ..providers.weather import Weather
from .scenes.base import Context, Scene
from .scenes.boot import BootScene
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


def build_samples() -> list[tuple[str, Scene, Context]]:
    """(name, scene, context) for every state worth looking at."""
    idle = IdleScene()
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
    ]
