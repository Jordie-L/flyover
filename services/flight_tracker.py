"""Flight tracker — polls OpenSky Network with predictive triggering."""

import logging
import math
import queue
import threading
import time

import requests

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0

# Common ICAO→IATA mappings (especially airports near Grand Rapids and major hubs).
# For US airports not in this dict, we strip the leading "K" as a fallback.
ICAO_TO_IATA = {
    # Michigan
    "KGRR": "GRR", "KLAN": "LAN", "KDTW": "DTW", "KMBS": "MBS",
    "KAZO": "AZO", "KFNT": "FNT", "KTVC": "TVC", "KMKG": "MKG",
    "KCIU": "CIU", "KAPN": "APN", "KESC": "ESC", "KPLN": "PLN",
    "KMBL": "MBL", "KCAD": "CAD",
    # Major US hubs
    "KATL": "ATL", "KORD": "ORD", "KDFW": "DFW", "KDEN": "DEN",
    "KLAX": "LAX", "KJFK": "JFK", "KSFO": "SFO", "KSEA": "SEA",
    "KLAS": "LAS", "KMCO": "MCO", "KEWR": "EWR", "KMSP": "MSP",
    "KBOS": "BOS", "KPHL": "PHL", "KLGA": "LGA", "KFLL": "FLL",
    "KIAD": "IAD", "KDCA": "DCA", "KBWI": "BWI", "KSLC": "SLC",
    "KSAN": "SAN", "KTPA": "TPA", "KPDX": "PDX", "KSTL": "STL",
    "KBNA": "BNA", "KMCI": "MCI", "KRDU": "RDU", "KCLT": "CLT",
    "KPIT": "PIT", "KCMH": "CMH", "KIND": "IND", "KCVG": "CVG",
    "KMKE": "MKE", "KMDW": "MDW", "KAUS": "AUS", "KHOU": "HOU",
    "KIAH": "IAH", "KOAK": "OAK", "KSJC": "SJC", "KSMF": "SMF",
    "KONT": "ONT", "KPHX": "PHX", "KABQ": "ABQ", "KMEM": "MEM",
    "KPBI": "PBI", "KRSW": "RSW", "KSAT": "SAT", "KOMA": "OMA",
    "KDSM": "DSM", "KBUF": "BUF", "KSYR": "SYR", "KROC": "ROC",
    "KPVD": "PVD", "KBDL": "BDL", "KALB": "ALB", "KRIC": "RIC",
    "KORF": "ORF", "KJAX": "JAX", "KCHS": "CHS", "KSAV": "SAV",
    "KGSO": "GSO", "KLEX": "LEX", "KSDF": "SDF", "KDAY": "DAY",
    "KTOL": "TOL", "KCLE": "CLE",
    # Canada
    "CYYZ": "YYZ", "CYUL": "YUL", "CYVR": "YVR", "CYOW": "YOW",
    "CYWG": "YWG", "CYEG": "YEG", "CYYC": "YYC", "CYHZ": "YHZ",
    # Mexico
    "MMMX": "MEX", "MMUN": "CUN", "MMGL": "GDL",
    # Major international
    "EGLL": "LHR", "EGKK": "LGW", "EHAM": "AMS", "EDDF": "FRA",
    "LFPG": "CDG", "LEMD": "MAD", "LIRF": "FCO", "LSZH": "ZRH",
    "EIDW": "DUB", "LEBL": "BCN", "LPPT": "LIS", "EKCH": "CPH",
    "ESSA": "ARN", "EFHK": "HEL", "ENGM": "OSL", "EPWA": "WAW",
    "LOWW": "VIE", "LKPR": "PRG", "LHBP": "BUD", "LTFM": "IST",
    "OMDB": "DXB", "VHHH": "HKG", "WSSS": "SIN", "RJTT": "HND",
    "RJAA": "NRT", "RKSI": "ICN", "ZBAA": "PEK", "ZSPD": "PVG",
    "YSSY": "SYD", "YMML": "MEL", "NZAA": "AKL",
    "SBGR": "GRU", "SCEL": "SCL", "SAEZ": "EZE", "SKBO": "BOG",
    "MPTO": "PTY", "TNCM": "SXM", "TJSJ": "SJU", "MKJP": "KIN",
}


def icao_to_iata(icao_code):
    """Convert ICAO airport code to IATA. Returns best guess or original."""
    if not icao_code:
        return None
    icao_code = icao_code.strip().upper()

    # Direct lookup
    if icao_code in ICAO_TO_IATA:
        return ICAO_TO_IATA[icao_code]

    # US airports: K + 3 letter IATA code (e.g., KLAX → LAX)
    if len(icao_code) == 4 and icao_code.startswith("K") and icao_code[1:].isalpha():
        return icao_code[1:]

    # Canadian airports: CY + 2 letters → Y + 2 letters (e.g., CYYZ → YYZ)
    if len(icao_code) == 4 and icao_code.startswith("CY"):
        return icao_code[1:]

    # Return original ICAO if no conversion found
    return icao_code


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
    def __init__(self, config, flight_queue, db=None):
        self.config = config
        self.flight_queue = flight_queue
        self.db = db
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
        """Fetch route from OpenSky and convert ICAO codes to IATA."""
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
                    origin_icao = route[0]
                    dest_icao = route[-1]
                    origin = icao_to_iata(origin_icao)
                    dest = icao_to_iata(dest_icao)
                    logger.info(
                        "Route for %s: %s→%s (ICAO: %s→%s)",
                        callsign, origin, dest, origin_icao, dest_icao,
                    )
                    return origin, dest
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

                    # Log to database
                    if self.db:
                        try:
                            self.db.insert_flight(aircraft)
                        except Exception as e:
                            logger.error("DB insert failed: %s", e)

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
