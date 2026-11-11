import json
import sqlite3
from pathlib import Path

import pytest

from flyover.config import LocationNotSet, parse_config
from flyover.models import Aircraft
from flyover.providers import routes
from flyover.providers.flights import (FlightFeed, parse_aircraft, parse_reply,
                                       query_radius_nm)
from flyover.providers.http import HttpError

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def aircraft(**kw) -> Aircraft:
    base = dict(hex="a1b2c3", callsign="UAL1", registration="", type_code="", lat=41.9, lon=-87.7,
                alt_ft=3000, ground_speed_kt=0.0, track_deg=None, vert_rate_fpm=None,
                on_ground=False, observed_at=1000.0)
    return Aircraft(**{**base, **kw})


# -- position replies ----------------------------------------------------------


def test_parse_reply_keeps_usable_aircraft_and_normalises_fields():
    found = {a.hex: a for a in parse_reply(fixture("airplanes_live_point.json"), fetched_at=1000.0)}
    ual = found["a1b2c3"]
    assert (ual.callsign, ual.registration, ual.type_code) == ("UAL1234", "N37290", "B738")
    assert (ual.alt_ft, ual.vert_rate_fpm, ual.on_ground) == (2400, -640, False)
    assert ual.observed_at == pytest.approx(999.6)  # report was 0.4 s old
    assert found["abcdef"].alt_ft == 35000
    assert found["a0ffee"].on_ground and found["a0ffee"].alt_ft is None  # "ground" altitude
    assert found["abcdef"].observed_at == 1000.0  # no seen_pos: assume fresh
    assert "123456" not in found  # no position
    assert found["654321"].alt_ft is None  # kept, but altitude unknown
    # a "~" (non-ICAO) address is a different aircraft from the one with the same digits
    assert "~abcdef" in found and "abcdef" in found and len(found) == 5


def test_parse_reply_rejects_other_shapes():
    for bad in (None, [], {"msg": "rate limited"}, {"ac": "nope"}):
        with pytest.raises(ValueError):
            parse_reply(bad, 0)
    assert parse_aircraft({"hex": "abc123", "lat": "41.9", "lon": -87}, 0) is None  # string coordinate


def test_dead_reckoning_moves_the_aircraft_along_its_track():
    a = aircraft(lat=41.9, lon=-87.7, ground_speed_kt=450.0, track_deg=90.0, observed_at=1000.0)
    assert a.position_at(1000.0) == (41.9, -87.7)
    lat, lon = a.position_at(1010.0)  # 450 kt for 10 s = 2.315 km east
    from flyover.geo import haversine_km
    assert haversine_km(41.9, -87.7, lat, lon) == pytest.approx(2.315, abs=0.01)
    assert lon > -87.7
    # beyond the extrapolation limit it stops moving
    assert a.position_at(1000.0 + 60) == a.position_at(1000.0 + 600)
    # no speed or heading: stays put
    assert aircraft(ground_speed_kt=None, track_deg=90.0).position_at(2000.0) == (41.9, -87.7)


def test_query_radius_adds_a_buffer_and_respects_the_service_limit():
    assert query_radius_nm(4.0) == pytest.approx((4 + 10) / 1.852)
    assert query_radius_nm(50.0) < 250


# -- the feed --------------------------------------------------------------------


def make_feed(reply, cfg_text='[location]\nlatitude = 41.9\nlongitude = -87.7\n', now=1000.0):
    cfg = parse_config(cfg_text)
    urls = []

    def get(url):
        urls.append(url)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return FlightFeed(lambda: cfg, get=get, now=lambda: now), urls, cfg


def test_feed_requests_the_configured_circle_and_speeds_up_when_something_is_near():
    feed, urls, cfg = make_feed(fixture("airplanes_live_point.json"))
    assert feed.poller.step() == cfg.flights.poll_active_s  # an aircraft is within reach
    assert urls == ["https://api.airplanes.live/v2/point/41.9000/-87.7000/7.6"]
    assert len(feed.aircraft) == 5


def test_feed_idles_slowly_when_the_sky_is_empty_or_all_too_high():
    feed, _, cfg = make_feed({"ac": []})
    assert feed.poller.step() == cfg.flights.poll_idle_s
    high = {"ac": [{"hex": "aaaaaa", "lat": 41.9, "lon": -87.7, "alt_baro": 38000, "gs": 400}]}
    feed, _, cfg = make_feed(high)
    assert feed.poller.step() == cfg.flights.poll_idle_s


def test_feed_uses_the_configured_provider():
    feed, urls, _ = make_feed({"ac": []}, '[location]\nlatitude = 1\nlongitude = 1\n'
                                         '[flights]\nprovider = "adsb_lol"\n')
    feed.poller.step()
    assert urls[0].startswith("https://api.adsb.lol/v2/point/")


def test_quiet_hours_make_no_requests():
    feed, urls, _ = make_feed(fixture("airplanes_live_point.json"))
    feed.quiet = True
    feed.poller.step()
    assert urls == [] and feed.aircraft == []


def test_feed_without_a_location_reports_why():
    feed, urls, _ = make_feed({"ac": []}, "")
    feed.poller.step()
    assert urls == [] and "location" in feed.poller.error
    with pytest.raises(LocationNotSet):
        feed._fetch()


def test_feed_failure_keeps_the_last_aircraft_and_backs_off():
    feed, _, cfg = make_feed(fixture("airplanes_live_point.json"))
    feed.poller.step()
    feed._get = lambda url: (_ for _ in ()).throw(HttpError("down"))
    assert feed.poller.step() == cfg.flights.poll_active_s * 2
    assert len(feed.aircraft) == 5 and feed.poller.error == "down"


# -- route replies -----------------------------------------------------------------


def test_parse_full_route_response():
    info = routes.parse_response(fixture("adsbdb_aircraft_with_route.json"))
    assert info.flight_number == "UA1234" and info.airline_name == "United Airlines"
    assert (info.origin.code, info.destination.code) == ("SFO", "ORD")
    assert info.destination.lat == pytest.approx(41.9742)
    assert (info.registration, info.type_code, info.type_name, info.manufacturer) == (
        "N37290", "B738", "737-824", "Boeing")
    assert info.has_route


def test_parse_aircraft_only_response_has_no_route():
    info = routes.parse_response(fixture("adsbdb_aircraft_only.json"))
    assert info.type_code == "C172" and not info.has_route and info.flight_number == ""


@pytest.mark.parametrize("payload", [fixture("adsbdb_unknown.json"), None, {}, {"response": {}}, []])
def test_parse_unknown_is_none(payload):
    assert routes.parse_response(payload) is None


def test_parse_tolerates_a_malformed_airport():
    payload = fixture("adsbdb_aircraft_with_route.json")
    payload["response"]["flightroute"]["origin"] = {"iata_code": "SFO"}  # no coordinates
    info = routes.parse_response(payload)
    assert info.origin is None and info.destination is not None and not info.has_route


def test_route_info_survives_the_json_round_trip():
    info = routes.parse_response(fixture("adsbdb_aircraft_with_route.json"))
    assert routes._from_json(routes._to_json(info)) == info
    assert routes._from_json(routes._to_json(None)) is None


# -- the resolver ---------------------------------------------------------------------


class FakeApi:
    """Serves adsbdb replies by URL and records every request."""

    def __init__(self, responses):
        self.responses, self.urls = responses, []

    def __call__(self, url):
        self.urls.append(url)
        for fragment, reply in self.responses.items():
            if fragment in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise HttpError("HTTP 404", status=404)


def make_resolver(tmp_path, api, clock):
    return routes.RouteResolver(tmp_path / "routes.sqlite", lambda: 7, get=api, now=lambda: clock[0])


def test_lookup_returns_none_then_the_answer_after_resolving(tmp_path):
    api = FakeApi({"aircraft/a1b2c3": fixture("adsbdb_aircraft_with_route.json")})
    resolver = make_resolver(tmp_path, api, [1000.0])
    assert resolver.lookup("a1b2c3", "UAL1234") is None  # queued, not blocking
    assert resolver.lookup("a1b2c3", "UAL1234") is None
    resolver.drain()
    assert resolver.lookup("a1b2c3", "UAL1234").flight_number == "UA1234"
    assert api.urls == ["https://api.adsbdb.com/v0/aircraft/a1b2c3?callsign=UAL1234"]  # queued once


def test_unknown_aircraft_falls_back_to_a_callsign_lookup(tmp_path):
    route_only = fixture("adsbdb_aircraft_with_route.json")
    route_only["response"].pop("aircraft")
    api = FakeApi({"callsign/UAL1234": route_only})  # the aircraft endpoint 404s
    resolver = make_resolver(tmp_path, api, [1000.0])
    resolver.lookup("a1b2c3", "UAL1234")
    resolver.drain()
    assert resolver.lookup("a1b2c3", "UAL1234").has_route
    assert len(api.urls) == 2 and api.urls[1].endswith("callsign/UAL1234")


def test_unknown_everywhere_is_cached_for_a_shorter_time(tmp_path):
    clock = [1000.0]
    api = FakeApi({})
    resolver = make_resolver(tmp_path, api, clock)
    resolver.lookup("a1b2c3", "ZZZ9")
    resolver.drain()
    assert resolver.lookup("a1b2c3", "ZZZ9") is None and len(api.urls) == 2
    clock[0] += routes.NOT_FOUND_TTL_S + 1  # expired: ask again
    resolver.lookup("a1b2c3", "ZZZ9")
    resolver.drain()
    assert len(api.urls) == 4


def test_results_survive_a_restart_through_sqlite(tmp_path):
    clock = [1000.0]
    api = FakeApi({"aircraft/a1b2c3": fixture("adsbdb_aircraft_with_route.json")})
    first = make_resolver(tmp_path, api, clock)
    first.lookup("a1b2c3", "UAL1234")
    first.drain()
    second = make_resolver(tmp_path, FakeApi({}), clock)  # new process, empty memory, offline
    second.lookup("a1b2c3", "UAL1234")
    second.drain()
    assert second.lookup("a1b2c3", "UAL1234").flight_number == "UA1234"
    clock[0] += 8 * 86400  # past the 7-day lifetime
    third_api = FakeApi({"aircraft/a1b2c3": fixture("adsbdb_aircraft_only.json")})
    third = make_resolver(tmp_path, third_api, clock)
    third.lookup("a1b2c3", "UAL1234")
    third.drain()
    assert third_api.urls and third.lookup("a1b2c3", "UAL1234").type_code == "C172"


def test_network_errors_are_not_cached_and_are_retried_after_a_pause(tmp_path):
    clock = [1000.0]
    api = FakeApi({"aircraft/": HttpError("cannot reach", status=None)})
    resolver = make_resolver(tmp_path, api, clock)
    resolver.lookup("a1b2c3", "UAL1")
    resolver.drain()
    assert resolver.lookup("a1b2c3", "UAL1") is None
    resolver.drain()
    assert len(api.urls) == 1  # still inside the retry pause: not queued again
    clock[0] += routes.RETRY_AFTER_ERROR_S + 1
    api.responses = {"aircraft/": fixture("adsbdb_aircraft_with_route.json")}
    resolver.lookup("a1b2c3", "UAL1")
    resolver.drain()
    assert resolver.lookup("a1b2c3", "UAL1").has_route


def test_non_icao_addresses_are_not_looked_up(tmp_path):
    api = FakeApi({})
    resolver = make_resolver(tmp_path, api, [1000.0])
    resolver.lookup("zzzzzz", "X1")
    resolver.drain()
    assert api.urls == [] and resolver.lookup("zzzzzz", "X1") is None


def test_an_unusable_cache_file_degrades_to_memory_only(tmp_path):
    (tmp_path / "routes.sqlite").mkdir()  # a directory where the database file should be
    api = FakeApi({"aircraft/a1b2c3": fixture("adsbdb_aircraft_with_route.json")})
    resolver = make_resolver(tmp_path, api, [1000.0])
    resolver.lookup("a1b2c3", "UAL1234")
    resolver.drain()
    assert resolver.lookup("a1b2c3", "UAL1234").has_route


def test_old_rows_are_pruned_on_open(tmp_path):
    clock = [1000.0]
    db = sqlite3.connect(tmp_path / "routes.sqlite")
    db.execute("CREATE TABLE routes (hex TEXT, callsign TEXT, payload TEXT, fetched REAL NOT NULL, "
               "PRIMARY KEY (hex, callsign))")
    db.execute("INSERT INTO routes VALUES ('aaaaaa', 'OLD1', NULL, 0)")
    db.commit()
    db.close()
    clock[0] = routes.PRUNE_AFTER_S + 10
    resolver = make_resolver(tmp_path, FakeApi({}), clock)
    resolver._connection()
    rows = resolver._db.execute("SELECT COUNT(*) FROM routes").fetchone()[0]
    assert rows == 0


def test_resolver_thread_end_to_end(tmp_path):
    import time

    api = FakeApi({"aircraft/a1b2c3": fixture("adsbdb_aircraft_with_route.json")})
    resolver = make_resolver(tmp_path, api, [time.time()])
    resolver.start()
    resolver.lookup("a1b2c3", "UAL1234")
    deadline = time.time() + 5
    while resolver.lookup("a1b2c3", "UAL1234") is None and time.time() < deadline:
        time.sleep(0.02)
    resolver.stop()
    assert resolver.lookup("a1b2c3", "UAL1234").has_route
