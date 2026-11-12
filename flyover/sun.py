"""Where the sun is, for dimming the panel by daylight.

Only the elevation above the horizon is needed, and only to a degree or so, so this is the
NOAA solar-position approximation (Meeus), with no refraction correction and no dependencies.
It needs no network: the location and the clock are enough.
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import acos, asin, cos, degrees, radians, sin, tan


def elevation_deg(lat: float, lon: float, when: datetime) -> float:
    """Elevation of the sun above the horizon in degrees (negative when it is below).

    ``lat`` is north positive and ``lon`` east positive. ``when`` may be timezone-aware or
    naive; a naive time is taken as the system's local time.
    """
    utc = when.astimezone(timezone.utc)
    jd = utc.timestamp() / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0  # Julian centuries since J2000

    mean_long = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360
    mean_anom = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    ecc = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    m = radians(mean_anom)
    centre = (sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
              + sin(2 * m) * (0.019993 - 0.000101 * t)
              + sin(3 * m) * 0.000289)
    omega = radians(125.04 - 1934.136 * t)
    apparent_long = mean_long + centre - 0.00569 - 0.00478 * sin(omega)
    obliquity = (23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60) / 60
                 + 0.00256 * cos(omega))
    declination = asin(sin(radians(obliquity)) * sin(radians(apparent_long)))

    y = tan(radians(obliquity) / 2) ** 2
    l0 = radians(mean_long)
    equation_of_time_min = 4 * degrees(
        y * sin(2 * l0) - 2 * ecc * sin(m) + 4 * ecc * y * sin(m) * cos(2 * l0)
        - 0.5 * y * y * sin(4 * l0) - 1.25 * ecc * ecc * sin(2 * m)
    )

    minutes_utc = utc.hour * 60 + utc.minute + utc.second / 60 + utc.microsecond / 6e7
    solar_minutes = (minutes_utc + equation_of_time_min + 4 * lon) % 1440
    hour_angle = radians(solar_minutes / 4 - 180)

    phi = radians(lat)
    cos_zenith = sin(phi) * sin(declination) + cos(phi) * cos(declination) * cos(hour_angle)
    return 90.0 - degrees(acos(max(-1.0, min(1.0, cos_zenith))))
