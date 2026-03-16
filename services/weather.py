"""Weather service — polls OpenWeatherMap and stores readings."""

import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)


class WeatherService:
    def __init__(self, config, db):
        self.config = config
        self.db = db
        self._current_weather = None
        self._lock = threading.Lock()
        self._stop = threading.Event()

        self.api_key = config["weather"].get("openweathermap_api_key", "")
        self.lat = config["location"]["latitude"]
        self.lon = config["location"]["longitude"]
        self.poll_interval = config["weather"].get("poll_interval_minutes", 15) * 60

    @property
    def current_weather(self):
        with self._lock:
            return self._current_weather

    def _fetch_weather(self):
        if not self.api_key:
            logger.warning("No OpenWeatherMap API key configured")
            return None

        try:
            # Current weather
            resp = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={
                    "lat": self.lat,
                    "lon": self.lon,
                    "appid": self.api_key,
                    "units": "imperial",
                },
                timeout=10,
            )
            resp.raise_for_status()
            current = resp.json()

            # Forecast for rain chance
            forecast_resp = requests.get(
                "https://api.openweathermap.org/data/2.5/forecast",
                params={
                    "lat": self.lat,
                    "lon": self.lon,
                    "appid": self.api_key,
                    "units": "imperial",
                    "cnt": 4,  # next 12 hours in 3hr blocks
                },
                timeout=10,
            )
            forecast_resp.raise_for_status()
            forecast = forecast_resp.json()

            # Max rain probability from upcoming forecasts
            rain_chance = 0
            for item in forecast.get("list", []):
                pop = item.get("pop", 0) * 100  # probability of precipitation
                rain_chance = max(rain_chance, pop)

            weather_data = {
                "temp_f": current["main"]["temp"],
                "feels_like_f": current["main"]["feels_like"],
                "humidity": current["main"]["humidity"],
                "description": current["weather"][0]["description"],
                "icon": current["weather"][0]["icon"],
                "rain_chance_pct": rain_chance,
            }

            # Store in DB
            self.db.insert_weather(weather_data)

            with self._lock:
                self._current_weather = weather_data

            logger.info(
                "Weather updated: %.0f°F, %d%% rain",
                weather_data["temp_f"],
                weather_data["rain_chance_pct"],
            )
            return weather_data

        except requests.RequestException as e:
            logger.error("Weather fetch failed: %s", e)
            return None

    def run(self):
        logger.info("Weather service starting")
        while not self._stop.is_set():
            self._fetch_weather()
            self._stop.wait(self.poll_interval)

    def stop(self):
        self._stop.set()
