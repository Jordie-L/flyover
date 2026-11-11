"""Distances, bearings and positions on a sphere."""

from __future__ import annotations

from math import acos, asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088
KM_PER_NM = 1.852
KM_PER_MILE = 1.609344

#: Dead reckoning stops after this long without a fresh report.
MAX_EXTRAPOLATION_S = 60.0


def knots_to_kmh(knots: float) -> float:
    return knots * KM_PER_NM


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    a = sin((p2 - p1) / 2) ** 2 + cos(p1) * cos(p2) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(min(1.0, sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, in [0, 360)."""
    p1, p2, dl = radians(lat1), radians(lat2), radians(lon2 - lon1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return degrees(atan2(y, x)) % 360


def destination_point(lat: float, lon: float, bearing: float, distance_km: float) -> tuple[float, float]:
    """The point reached by travelling ``distance_km`` from (lat, lon) on ``bearing``."""
    d, b, p1, l1 = distance_km / EARTH_RADIUS_KM, radians(bearing), radians(lat), radians(lon)
    p2 = asin(sin(p1) * cos(d) + cos(p1) * sin(d) * cos(b))
    l2 = l1 + atan2(sin(b) * sin(d) * cos(p1), cos(d) - sin(p1) * sin(p2))
    return degrees(p2), (degrees(l2) + 540) % 360 - 180


def distance_to_segment_km(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Shortest distance from a point to the great-circle segment a-b."""
    to_a = haversine_km(a[0], a[1], lat, lon)
    to_b = haversine_km(b[0], b[1], lat, lon)
    length = haversine_km(a[0], a[1], b[0], b[1])
    if length < 1e-6:
        return to_a
    theta_p = radians(bearing_deg(a[0], a[1], lat, lon))
    theta_b = radians(bearing_deg(a[0], a[1], b[0], b[1]))
    d_ap = to_a / EARTH_RADIUS_KM
    cross = asin(max(-1.0, min(1.0, sin(d_ap) * sin(theta_p - theta_b))))
    if cos(theta_p - theta_b) < 0:  # behind a
        return to_a
    along = acos(max(-1.0, min(1.0, cos(d_ap) / cos(cross)))) * EARTH_RADIUS_KM
    if along > length:  # beyond b
        return to_b
    return abs(cross) * EARTH_RADIUS_KM


def route_is_plausible(
    lat: float, lon: float, origin: tuple[float, float], destination: tuple[float, float]
) -> bool:
    """Is an aircraft at (lat, lon) believably flying between the two airports?

    Route databases key on the flight number and airlines reuse numbers, so
    the stored route can be for a different day's flight. Allow generous
    slack for departure and arrival procedures, holding and weather: either
    150 km or a fifth of the route length, whichever is larger.
    """
    length = haversine_km(*origin, *destination)
    return distance_to_segment_km(lat, lon, origin, destination) <= max(150.0, 0.2 * length)


def compass_octant(bearing: float) -> int:
    """0 = north, 1 = north-east, ... 7 = north-west."""
    return int(((bearing % 360) + 22.5) // 45) % 8
