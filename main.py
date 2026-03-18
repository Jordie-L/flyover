"""FlyOver — Raspberry Pi plane tracker, clock, and weather display."""

import logging
import os
import queue
import signal
import sys
import threading

import yaml

from services.display import DisplayService
from services.flight_tracker import FlightTracker
from services.weather import WeatherService
from services.web_server import WebServer
from services.wifi_manager import WiFiManager
from utils.db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("flyover")


def load_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


def main():
    logger.info("FlyOver starting up")
    config = load_config()

    # Set timezone
    tz = config.get("timezone", "America/Detroit")
    os.environ["TZ"] = tz

    # Initialize database
    db = Database(config["database"]["path"])

    # Periodic cleanup
    retention = config["web"].get("stats_retention_days", 90)
    db.cleanup_old_data(retention)

    # Shared flight event queue
    flight_queue = queue.Queue(maxsize=50)

    # Initialize services
    wifi_manager = WiFiManager(config)
    weather_service = WeatherService(config, db)
    flight_tracker = FlightTracker(config, flight_queue, db)
    display_service = DisplayService(config, flight_queue, weather_service, wifi_manager)
    web_server = WebServer(config, db, wifi_manager, weather_service)

    # Start all services as daemon threads
    services = {
        "wifi": wifi_manager,
        "weather": weather_service,
        "flight_tracker": flight_tracker,
        "display": display_service,
        "web": web_server,
    }

    threads = {}
    for name, service in services.items():
        t = threading.Thread(target=service.run, name=name, daemon=True)
        t.start()
        threads[name] = t
        logger.info("Started %s", name)

    # Graceful shutdown
    shutdown = threading.Event()

    def signal_handler(sig, frame):
        logger.info("Shutdown signal received")
        shutdown.set()
        for service in services.values():
            if hasattr(service, "stop"):
                service.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info("All services running")

    # Wait for shutdown signal
    try:
        shutdown.wait()
    except KeyboardInterrupt:
        pass

    logger.info("FlyOver shutting down")
    sys.exit(0)


if __name__ == "__main__":
    main()
