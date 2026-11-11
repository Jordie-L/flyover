"""Plain data types shared by the providers, the selector and the scenes."""

from __future__ import annotations

from dataclasses import dataclass

from . import geo


@dataclass(frozen=True)
class Aircraft:
    """One aircraft position report from a live ADS-B feed."""

    hex: str  # unique 24-bit radio address, lower-case hex
    callsign: str  # radio callsign such as "UAL1234" ("" if none)
    registration: str
    type_code: str  # ICAO type designator such as "B738"
    lat: float
    lon: float
    alt_ft: int | None  # barometric altitude; None if unknown
    ground_speed_kt: float | None
    track_deg: float | None  # direction of travel, degrees true
    vert_rate_fpm: int | None
    on_ground: bool
    observed_at: float  # unix time at which (lat, lon) was valid

    def position_at(self, now: float) -> tuple[float, float]:
        """Dead-reckon the position forward from the last report.

        A jet covers about a kilometre between polls, so without this the
        distance readout and the "is it overhead" test would lurch. Reports
        older than ``geo.MAX_EXTRAPOLATION_S`` are not extrapolated further.
        """
        if not self.ground_speed_kt or self.track_deg is None:
            return self.lat, self.lon
        elapsed = min(now - self.observed_at, geo.MAX_EXTRAPOLATION_S)
        if elapsed <= 0:
            return self.lat, self.lon
        return geo.destination_point(
            self.lat, self.lon, self.track_deg, geo.knots_to_kmh(self.ground_speed_kt) * elapsed / 3600
        )


@dataclass(frozen=True)
class Airport:
    iata: str
    icao: str
    name: str
    municipality: str
    lat: float
    lon: float

    @property
    def code(self) -> str:
        """The code travellers know (IATA), else the ICAO one."""
        return self.iata or self.icao


@dataclass(frozen=True)
class RouteInfo:
    """What the route database knows about one flight."""

    flight_number: str = ""  # as printed on a ticket, e.g. "UA1234"
    airline_name: str = ""
    airline_iata: str = ""
    airline_icao: str = ""
    origin: Airport | None = None
    destination: Airport | None = None
    registration: str = ""
    type_code: str = ""
    type_name: str = ""  # e.g. "737-800"
    manufacturer: str = ""

    @property
    def has_route(self) -> bool:
        return self.origin is not None and self.destination is not None


@dataclass(frozen=True)
class FlightView:
    """Everything the flight screen shows, resolved at one instant."""

    aircraft: Aircraft
    route: RouteInfo | None
    lat: float  # dead-reckoned position now
    lon: float
    distance_km: float  # from the display's location
    inside: bool  # within the configured radius right now (False while "held")
    others: int  # how many other aircraft are also inside the radius
    route_plausible: bool | None  # None when there is no route to check

    @property
    def display_number(self) -> str:
        """The flight number people recognise, falling back to the radio callsign."""
        if self.route and self.route.flight_number:
            return self.route.flight_number
        return self.aircraft.callsign or self.aircraft.registration or self.aircraft.hex.upper()
