import pytest

from flyover import geo
from flyover.config import Flights
from flyover.display.scenes.flight import (FlightScene, airline_colour, format_altitude,
                                           format_distance)
from flyover.display.samples import sample_context, sample_flight
from flyover.display.theme import DAY, NIGHT
from flyover.models import Aircraft
from flyover.selector import SWITCH_RATIO, Selector

HOME = (41.9, -87.7)
FLIGHTS = Flights(radius_km=4.0, max_altitude_ft=15000, hold_seconds=10)
NOW = 1000.0


def plane(hex_id="aaaaaa", km_east=0.0, **kw) -> Aircraft:
    """A stationary aircraft ``km_east`` kilometres east of home."""
    lat, lon = geo.destination_point(*HOME, 90.0, km_east)
    base = dict(hex=hex_id, callsign=hex_id.upper(), registration="", type_code="B738", lat=lat,
                lon=lon, alt_ft=3000, ground_speed_kt=0.0, track_deg=None, vert_rate_fpm=0,
                on_ground=False, observed_at=NOW)
    return Aircraft(**{**base, **kw})


def update(selector, aircraft, now=NOW, flights=FLIGHTS):
    return selector.update(aircraft, now, HOME, flights)


def test_nothing_in_range_gives_nothing():
    assert update(Selector(), []) is None
    assert update(Selector(), [plane(km_east=20)]) is None  # outside the radius


def test_an_aircraft_inside_the_radius_is_picked_with_its_distance():
    pick = update(Selector(), [plane(km_east=2.0)])
    assert pick.aircraft.hex == "aaaaaa" and pick.inside and pick.others == 0
    assert pick.distance_km == pytest.approx(2.0, abs=0.01)


@pytest.mark.parametrize("kw", [dict(on_ground=True), dict(alt_ft=None), dict(alt_ft=15001)])
def test_ignored_aircraft(kw):
    assert update(Selector(), [plane(km_east=1.0, **kw)]) is None


def test_closest_wins_and_others_are_counted():
    pick = update(Selector(), [plane("aaaaaa", 3.0), plane("bbbbbb", 1.0), plane("cccccc", 2.0)])
    assert pick.aircraft.hex == "bbbbbb" and pick.others == 2


def test_no_flip_flopping_between_similar_distances():
    s = Selector()
    assert update(s, [plane("aaaaaa", 2.0)]).aircraft.hex == "aaaaaa"
    # b is a little closer than a, but not closer than SWITCH_RATIO of a's distance
    close = 2.0 * SWITCH_RATIO + 0.1
    assert update(s, [plane("aaaaaa", 2.0), plane("bbbbbb", close)]).aircraft.hex == "aaaaaa"
    # clearly closer: switch
    assert update(s, [plane("aaaaaa", 2.0), plane("bbbbbb", 0.5)]).aircraft.hex == "bbbbbb"


def test_a_departed_aircraft_is_held_then_released():
    s = Selector()
    update(s, [plane(km_east=2.0)], now=NOW)
    held = update(s, [plane(km_east=6.0)], now=NOW + 5)  # outside the radius now
    assert held.aircraft.hex == "aaaaaa" and not held.inside
    assert held.distance_km == pytest.approx(6.0, abs=0.01)
    assert update(s, [plane(km_east=6.0)], now=NOW + 11) is None  # hold_seconds elapsed


def test_hold_survives_the_aircraft_dropping_out_of_the_feed():
    s = Selector()
    update(s, [plane(km_east=2.0)], now=NOW)
    held = update(s, [], now=NOW + 5)
    assert held is not None and held.aircraft.hex == "aaaaaa" and not held.inside
    assert update(s, [], now=NOW + 11) is None


def test_a_new_arrival_replaces_a_held_aircraft_immediately():
    s = Selector()
    update(s, [plane("aaaaaa", 2.0)], now=NOW)
    pick = update(s, [plane("aaaaaa", 6.0), plane("bbbbbb", 3.0)], now=NOW + 2)
    assert pick.aircraft.hex == "bbbbbb" and pick.inside


def test_dead_reckoning_brings_an_approaching_aircraft_inside():
    # 6 km west of home heading east at 450 kt (231 m/s): inside the 4 km radius after ~9 s
    a = plane(km_east=-6.0, ground_speed_kt=450.0, track_deg=90.0)
    s = Selector()
    assert update(s, [a], now=NOW) is None
    assert update(s, [a], now=NOW + 12).inside


# -- formatting and the scene -----------------------------------------------------------


@pytest.mark.parametrize("alt, text", [(None, "---"), (0, "0"), (-50, "0"), (850, "850"),
                                       (1000, "1.0k"), (2400, "2.4k"), (9999, "10.0k"),
                                       (10000, "10k"), (35000, "35k")])
def test_format_altitude(alt, text):
    assert format_altitude(alt) == text


@pytest.mark.parametrize("km, unit, text", [(1.287, "mi", "0.8mi"), (16.1, "mi", "10mi"),
                                            (1.3, "km", "1.3km"), (25.4, "km", "25km")])
def test_format_distance(km, unit, text):
    assert format_distance(km, unit) == text


def test_airline_colour_is_stable_per_airline_and_amber_at_night():
    view = sample_flight()
    assert airline_colour(view, DAY) == airline_colour(sample_flight(), DAY)
    assert airline_colour(view, NIGHT) == NIGHT.fg
    assert airline_colour(sample_flight(route=None), DAY) == DAY.fg


def test_flight_view_display_number_falls_back_gracefully():
    assert sample_flight().display_number == "UA1234"
    assert sample_flight(route=None).display_number == "UAL1234"
    from dataclasses import replace
    bare = replace(sample_flight().aircraft, callsign="", registration="")
    assert sample_flight(route=None, aircraft=bare).display_number == "A1B2C3"


def test_flight_scene_requires_a_flight():
    with pytest.raises(ValueError):
        FlightScene().render(sample_context())


def test_long_text_is_trimmed_to_fit():
    from dataclasses import replace
    view = sample_flight()
    long_route = replace(view.route, airline_name="A Very Long Airline Name Indeed")
    frame = FlightScene().render(sample_context(flight=replace(view, route=long_route)))
    # nothing may be drawn in the right-most column of the footer row
    assert frame.crop((63, 54, 64, 60)).getbbox() is None
