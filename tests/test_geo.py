import pytest

from flyover import geo

CHICAGO = (41.9742, -87.9073)  # ORD
SFO = (37.6213, -122.379)


def test_haversine_known_distance():
    # ORD to SFO is about 2,977 km along the great circle
    assert geo.haversine_km(*CHICAGO, *SFO) == pytest.approx(2977, rel=0.01)
    assert geo.haversine_km(*CHICAGO, *CHICAGO) == 0


@pytest.mark.parametrize(
    "target, expected",
    [((1, 0), 0), ((0, 1), 90), ((-1, 0), 180), ((0, -1), 270)],
)
def test_bearing_cardinals(target, expected):
    assert geo.bearing_deg(0, 0, *target) == pytest.approx(expected, abs=0.01)


def test_destination_point_round_trip():
    lat, lon = geo.destination_point(41.9, -87.7, 90.0, 10.0)
    assert geo.haversine_km(41.9, -87.7, lat, lon) == pytest.approx(10.0, abs=0.01)
    assert geo.bearing_deg(41.9, -87.7, lat, lon) == pytest.approx(90.0, abs=0.5)


def test_destination_point_wraps_longitude():
    lat, lon = geo.destination_point(0, 179.9, 90, 50)
    assert -180 <= lon <= 180 and lon < 0


def test_distance_to_segment():
    a, b = (0.0, 0.0), (0.0, 10.0)  # along the equator
    assert geo.distance_to_segment_km(0.0, 5.0, a, b) == pytest.approx(0, abs=0.01)
    assert geo.distance_to_segment_km(1.0, 5.0, a, b) == pytest.approx(111.2, rel=0.01)  # beside it
    assert geo.distance_to_segment_km(0.0, -2.0, a, b) == pytest.approx(geo.haversine_km(0, 0, 0, -2))
    assert geo.distance_to_segment_km(0.0, 12.0, a, b) == pytest.approx(geo.haversine_km(0, 10, 0, 12))
    assert geo.distance_to_segment_km(1.0, 1.0, a, a) == pytest.approx(geo.haversine_km(0, 0, 1, 1))


def test_route_plausibility():
    # Over Denver is on a believable SFO-ORD path; over Miami is not.
    assert geo.route_is_plausible(39.7, -104.9, SFO, CHICAGO)
    assert not geo.route_is_plausible(25.8, -80.3, SFO, CHICAGO)
    # Near either end is always fine, even if off the straight line (procedures, holding).
    assert geo.route_is_plausible(41.9, -87.7, SFO, CHICAGO)
    assert geo.route_is_plausible(37.9, -122.2, SFO, CHICAGO)


@pytest.mark.parametrize("bearing, octant",
                         [(0, 0), (22, 0), (23, 1), (90, 2), (180, 4), (270, 6), (359, 0), (337, 7)])
def test_compass_octant(bearing, octant):
    assert geo.compass_octant(bearing) == octant
