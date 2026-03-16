"""Flight tracker — polls OpenSky Network with predictive triggering."""

import logging
import math
import queue
import threading
import time

import requests

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0


def haversine(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two lat/lon points."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def bearing_between(lat1, lon1, lat2, lon2):
    """Initial bearing from point 1 to point 2 in degrees."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


class FlightTracker:
    def __init__(self, config, flight_queue):
        self.config = config
        self.flight_queue = flight_queue
        self._stop = threading.Event()

        loc = config["location"]
        self.home_lat = loc["latitude"]
        self.home_lon = loc["longitude"]

        ft = config["flight_tracking"]
        self.outer_radius = ft["outer_radius_km"]
        self.inner_radius = ft["inner_radius_km"]
        self.max_altitude_ft = ft["max_altitude_ft"]
        self.poll_interval = ft["poll_interval_seconds"]
        self.lag_buffer = ft["api_lag_buffer_seconds"]

        # Bounding box for API query (slightly larger than outer ring)
        margin_deg = (self.outer_radius / 111.0) * 1.2
        self.bbox = {
            "lamin": self.home_lat - margin_deg,
            "lamax": self.home_lat + margin_deg,
            "lomin": self.home_lon - margin_deg,
            "lomax": self.home_lon + margin_deg,
        }

        # Tracking state
        self.tracked = {}  # icao24 -> aircraft dict
        self.displayed = set()  # icao24s already pushed to display
        self.miss_counts = {}  # icao24 -> consecutive misses

    def _fetch_states(self):
        try:
            resp = requests.get(
                "https://opensky-network.org/api/states/all",
                params=self.bbox,
                timeout=15,
            )
            if resp.status_code == 429:
                logger.warning("OpenSky rate limited, backing off")
                return None, True  # rate limited
            resp.raise_for_status()
            data = resp.json()
            return data.get("states", []), False
        except requests.RequestException as e:
            logger.error("OpenSky fetch failed: %s", e)
            return None, False

    def _fetch_route(self, callsign):
        callsign = callsign.strip()
        if not callsign:
            return None, None
        try:
            resp = requests.get(
                f"https://opensky-network.org/api/routes?callsign={callsign}",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                route = data.get("route", [])
                if len(route) >= 2:
                    return route[0], route[-1]
            return None, None
        except requests.RequestException:
            return None, None

    def _parse_aircraft(self, state):
        """Parse an OpenSky state vector into an aircraft dict."""
        if len(state) < 17:
            return None

        icao24 = state[0]
        callsign = (state[1] or "").strip()
        lon = state[5]
        lat = state[6]
        baro_alt = state[7]  # meters
        velocity = state[9]  # m/s
        heading = state[10]  # degrees

        if lat is None or lon is None or baro_alt is None:
            return None

        altitude_ft = baro_alt * 3.28084
        if altitude_ft > self.max_altitude_ft:
            return None

        distance = haversine(self.home_lat, self.home_lon, lat, lon)
        if distance > self.outer_radius:
            return None

        return {
            "icao24": icao24,
            "callsign": callsign,
            "latitude": lat,
            "longitude": lon,
            "altitude_ft": altitude_ft,
            "distance_km": distance,
            "velocity_ms": velocity or 0,
            "heading": heading or 0,
        }

    def _compute_eta_to_inner(self, aircraft):
        """Estimate seconds until aircraft reaches the inner ring."""
        dist = aircraft["distance_km"]
        if dist <= self.inner_radius:
            return 0

        speed_kms = aircraft["velocity_ms"] / 1000.0  # km/s
        if speed_kms <= 0:
            return float("inf")

        # Check if aircraft is heading toward home
        bearing_to_home = bearing_between(
            aircraft["latitude"], aircraft["longitude"],
            self.home_lat, self.home_lon,
        )
        heading_diff = abs(bearing_to_home - aircraft["heading"])
        if heading_diff > 180:
            heading_diff = 360 - heading_diff

        # If heading away (>90° off), ETA is infinite
        if heading_diff > 90:
            return float("inf")

        # Approximate closing speed using heading alignment
        closing_speed = speed_kms * math.cos(math.radians(heading_diff))
        if closing_speed <= 0:
            return float("inf")

        remaining = dist - self.inner_radius
        return remaining / closing_speed

    def _poll(self):
        states, rate_limited = self._fetch_states()
        if rate_limited:
            return self.poll_interval * 3  # back off
        if states is None:
            return self.poll_interval

        seen_icao24s = set()

        for state in states:
            aircraft = self._parse_aircraft(state)
            if aircraft is None:
                continue

            icao24 = aircraft["icao24"]
            seen_icao24s.add(icao24)
            self.miss_counts[icao24] = 0

            # New aircraft in tracking range
            if icao24 not in self.tracked:
                # Fetch route info
                origin, destination = self._fetch_route(aircraft["callsign"])
                aircraft["origin"] = origin
                aircraft["destination"] = destination
                self.tracked[icao24] = aircraft
                logger.info(
                    "Tracking %s (%s) at %.1fkm, %.0fft",
                    aircraft["callsign"] or icao24,
                    f"{origin}→{destination}" if origin else "no route",
                    aircraft["distance_km"],
                    aircraft["altitude_ft"],
                )
            else:
                # Update position but keep route data
                old = self.tracked[icao24]
                aircraft["origin"] = old.get("origin")
                aircraft["destination"] = old.get("destination")
                self.tracked[icao24] = aircraft

            # Check if we should push to display
            if icao24 not in self.displayed:
                eta = self._compute_eta_to_inner(aircraft)
                if eta <= self.lag_buffer or aircraft["distance_km"] <= self.inner_radius:
                    self.displayed.add(icao24)
                    try:
                        self.flight_queue.put_nowait(aircraft)
                        logger.info(
                            "Display trigger: %s (ETA=%.0fs, dist=%.1fkm)",
                            aircraft["callsign"] or icao24,
                            eta,
                            aircraft["distance_km"],
                        )
                    except queue.Full:
                        pass

        # Clean up aircraft no longer seen
        for icao24 in list(self.tracked.keys()):
            if icao24 not in seen_icao24s:
                self.miss_counts[icao24] = self.miss_counts.get(icao24, 0) + 1
                if self.miss_counts[icao24] >= 3:
                    logger.info("Lost tracking: %s", icao24)
                    del self.tracked[icao24]
                    self.displayed.discard(icao24)
                    self.miss_counts.pop(icao24, None)

        return self.poll_interval

    def run(self):
        logger.info("Flight tracker starting")
        while not self._stop.is_set():
            next_poll = self._poll()
            self._stop.wait(next_poll)

    def stop(self):
        self._stop.set()
