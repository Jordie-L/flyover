"""SQLite database management with WAL mode and thread-safe writes."""

import os
import sqlite3
import threading


class Database:
    """Thread-safe SQLite database wrapper."""

    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS flights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icao24 TEXT NOT NULL,
                    callsign TEXT,
                    flight_number TEXT,
                    origin TEXT,
                    destination TEXT,
                    altitude_ft REAL,
                    distance_km REAL,
                    latitude REAL,
                    longitude REAL,
                    heading REAL,
                    velocity_ms REAL,
                    first_seen_ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                    displayed_ts DATETIME,
                    departed_ts DATETIME
                );

                CREATE TABLE IF NOT EXISTS weather_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    temp_f REAL,
                    feels_like_f REAL,
                    humidity INTEGER,
                    description TEXT,
                    icon TEXT,
                    rain_chance_pct REAL
                );

                CREATE TABLE IF NOT EXISTS stats_cache (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_ts DATETIME DEFAULT CURRENT_TIMESTAMP
                );
            """)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def execute(self, query, params=None):
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(query, params or ())
                conn.commit()
                return cursor

    def fetchone(self, query, params=None):
        with self._connect() as conn:
            return conn.execute(query, params or ()).fetchone()

    def fetchall(self, query, params=None):
        with self._connect() as conn:
            return conn.execute(query, params or ()).fetchall()

    def insert_flight(self, flight_data):
        return self.execute(
            """INSERT INTO flights
               (icao24, callsign, flight_number, origin, destination,
                altitude_ft, distance_km, latitude, longitude, heading,
                velocity_ms, displayed_ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                flight_data.get("icao24"),
                flight_data.get("callsign"),
                flight_data.get("flight_number"),
                flight_data.get("origin"),
                flight_data.get("destination"),
                flight_data.get("altitude_ft"),
                flight_data.get("distance_km"),
                flight_data.get("latitude"),
                flight_data.get("longitude"),
                flight_data.get("heading"),
                flight_data.get("velocity_ms"),
            ),
        )

    def insert_weather(self, weather_data):
        return self.execute(
            """INSERT INTO weather_log
               (temp_f, feels_like_f, humidity, description, icon, rain_chance_pct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                weather_data.get("temp_f"),
                weather_data.get("feels_like_f"),
                weather_data.get("humidity"),
                weather_data.get("description"),
                weather_data.get("icon"),
                weather_data.get("rain_chance_pct"),
            ),
        )

    def get_recent_flights(self, limit=20):
        return self.fetchall(
            """SELECT * FROM flights
               ORDER BY displayed_ts DESC LIMIT ?""",
            (limit,),
        )

    def get_flight_stats(self):
        today = self.fetchone(
            """SELECT COUNT(*) as count FROM flights
               WHERE date(displayed_ts) = date('now', 'localtime')"""
        )
        week = self.fetchone(
            """SELECT COUNT(*) as count FROM flights
               WHERE displayed_ts >= datetime('now', '-7 days', 'localtime')"""
        )
        total = self.fetchone("SELECT COUNT(*) as count FROM flights")
        most_common_route = self.fetchone(
            """SELECT origin || '→' || destination as route, COUNT(*) as count
               FROM flights WHERE origin IS NOT NULL AND destination IS NOT NULL
               GROUP BY origin, destination ORDER BY count DESC LIMIT 1"""
        )
        most_common_flight = self.fetchone(
            """SELECT callsign, COUNT(*) as count FROM flights
               WHERE callsign IS NOT NULL AND callsign != ''
               GROUP BY callsign ORDER BY count DESC LIMIT 1"""
        )
        most_common_origin = self.fetchone(
            """SELECT origin, COUNT(*) as count FROM flights
               WHERE origin IS NOT NULL
               GROUP BY origin ORDER BY count DESC LIMIT 1"""
        )
        most_common_dest = self.fetchone(
            """SELECT destination, COUNT(*) as count FROM flights
               WHERE destination IS NOT NULL
               GROUP BY destination ORDER BY count DESC LIMIT 1"""
        )
        return {
            "today": today["count"] if today else 0,
            "week": week["count"] if week else 0,
            "total": total["count"] if total else 0,
            "most_common_route": dict(most_common_route) if most_common_route else None,
            "most_common_flight": dict(most_common_flight) if most_common_flight else None,
            "most_common_origin": dict(most_common_origin) if most_common_origin else None,
            "most_common_destination": dict(most_common_dest) if most_common_dest else None,
        }

    def get_weather_history(self, hours=24):
        return self.fetchall(
            """SELECT * FROM weather_log
               WHERE timestamp >= datetime('now', ? || ' hours')
               ORDER BY timestamp ASC""",
            (str(-hours),),
        )

    def cleanup_old_data(self, retention_days=90):
        self.execute(
            """DELETE FROM flights
               WHERE displayed_ts < datetime('now', ? || ' days')""",
            (str(-retention_days),),
        )
        self.execute(
            """DELETE FROM weather_log
               WHERE timestamp < datetime('now', ? || ' days')""",
            (str(-retention_days),),
        )
