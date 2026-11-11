import pytest

from flyover.providers import weather
from flyover.providers.http import HttpError
from flyover.providers.poller import Poller

REPLY = {
    "latitude": 41.9,
    "timezone": "America/Chicago",
    "current": {"time": "2026-11-09T07:30", "temperature_2m": 12.3, "weather_code": 2, "is_day": 1},
}


def test_parse_reply():
    w = weather.parse(REPLY, now=1000.0)
    assert (w.temperature_c, w.code, w.is_day, w.timezone, w.fetched_at) == (
        12.3, 2, True, "America/Chicago", 1000.0)
    assert w.icon == "partly_day"


@pytest.mark.parametrize("bad", [{}, {"current": {}}, {"current": {"temperature_2m": "warm",
                                                                   "weather_code": 1}}, None])
def test_parse_rejects_unusable_replies(bad):
    with pytest.raises(ValueError):
        weather.parse(bad, now=0)


def test_temperature_units_and_rounding():
    w = weather.parse(REPLY, now=0)
    assert w.temperature("C") == 12
    assert w.temperature("F") == 54
    cold = weather.parse({"current": {"temperature_2m": -4.0, "weather_code": 0}}, now=0)
    assert (cold.temperature("C"), cold.temperature("F")) == (-4, 25)


def test_staleness():
    w = weather.parse(REPLY, now=1000.0)
    assert not w.is_stale(1000.0 + weather.MAX_AGE_S)
    assert w.is_stale(1000.0 + weather.MAX_AGE_S + 1)


@pytest.mark.parametrize(
    "code, day, icon",
    [(0, True, "clear_day"), (0, False, "clear_night"), (1, True, "clear_day"),
     (2, False, "partly_night"), (3, True, "cloud"), (45, True, "fog"), (53, True, "rain"),
     (65, False, "rain"), (81, True, "rain"), (73, True, "snow"), (86, True, "snow"),
     (96, True, "storm"), (999, True, "cloud")],
)
def test_icon_mapping(code, day, icon):
    assert weather.icon_for(code, day) == icon


def test_every_icon_name_has_a_drawing():
    from flyover.display.icons import ICON_NAMES, weather_icon

    codes = (0, 2, 3, 45, 61, 71, 95)
    assert {weather.icon_for(c, d) for c in codes for d in (True, False)} <= set(ICON_NAMES)
    for name in ICON_NAMES:
        assert weather_icon(name).getbbox() is not None  # not blank


def test_fetch_builds_the_url_from_coordinates():
    seen = []
    w = weather.fetch(41.9, -87.6, get=lambda url: seen.append(url) or REPLY, now=lambda: 5.0)
    assert "latitude=41.9000" in seen[0] and "longitude=-87.6000" in seen[0]
    assert w.fetched_at == 5.0


class Flaky:
    """Fails ``failures`` times, then succeeds."""

    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise HttpError("down")
        return f"value-{self.calls}"


def test_poller_backs_off_doubling_up_to_the_cap_then_recovers():
    poller = Poller("t", Flaky(failures=6), interval_s=10, max_backoff_s=100)
    delays = [poller.step() for _ in range(6)]
    assert delays == [20, 40, 80, 100, 100, 100]
    assert poller.latest is None and poller.error == "down"
    assert poller.step() == 10  # success: back to the normal interval
    assert poller.latest == "value-7" and poller.error is None


def test_poller_keeps_the_last_good_value_through_failures():
    fetch = Flaky(failures=0)
    poller = Poller("t", fetch, interval_s=5)
    poller.step()
    fetch.failures = 10**6  # every later call fails
    poller.step()
    assert poller.latest == "value-1" and poller.error == "down"


def test_poller_result_handler_errors_do_not_break_polling():
    def boom(_):
        raise RuntimeError("handler")

    poller = Poller("t", lambda: 1, interval_s=5, on_result=boom)
    assert poller.step() == 5 and poller.latest == 1


def test_poller_thread_fetches_and_pokes_and_stops():
    import threading

    got = threading.Event()
    poller = Poller("t", lambda: 42, interval_s=3600, on_result=lambda v: got.set())
    poller.start()
    assert got.wait(2)
    got.clear()
    poller.poke()  # would otherwise wait an hour
    assert got.wait(2)
    poller.stop()
