"""Web server — captive portal + stats dashboard."""

import json
import logging
import threading

from flask import Flask, jsonify, redirect, render_template, request

logger = logging.getLogger(__name__)


class WebServer:
    def __init__(self, config, db, wifi_manager, weather_service):
        self.config = config
        self.db = db
        self.wifi_manager = wifi_manager
        self.weather_service = weather_service
        self.port = config["web"].get("port", 80)

        self.app = Flask(
            __name__,
            template_folder="/home/user/flyover/web/templates",
            static_folder="/home/user/flyover/web/static",
        )
        self._register_routes()

    def _register_routes(self):
        app = self.app

        # Captive portal auto-detection
        @app.route("/generate_204")
        @app.route("/hotspot-detect.html")
        @app.route("/connecttest.txt")
        def captive_portal_detect():
            if self.wifi_manager.get_mode() == "ap":
                return redirect("/setup")
            return "", 204

        @app.route("/")
        def index():
            mode = self.wifi_manager.get_mode() if self.wifi_manager else "client"
            if mode == "ap":
                return redirect("/setup")
            return redirect("/dashboard")

        @app.route("/setup")
        def setup():
            ssids = self.wifi_manager.get_cached_ssids() if self.wifi_manager else []
            return render_template("setup.html", ssids=ssids)

        @app.route("/api/connect", methods=["POST"])
        def api_connect():
            data = request.get_json()
            ssid = data.get("ssid", "")
            password = data.get("password", "")
            if not ssid:
                return jsonify({"success": False, "error": "SSID required"}), 400

            success = self.wifi_manager.connect_to_network(ssid, password)
            return jsonify({"success": success})

        @app.route("/api/rescan", methods=["POST"])
        def api_rescan():
            ssids = self.wifi_manager.rescan_networks()
            return jsonify({"ssids": ssids})

        @app.route("/api/status")
        def api_status():
            return jsonify({
                "mode": self.wifi_manager.get_mode() if self.wifi_manager else "unknown",
                "weather": self.weather_service.current_weather if self.weather_service else None,
            })

        @app.route("/dashboard")
        def dashboard():
            stats = self.db.get_flight_stats()
            recent = self.db.get_recent_flights(20)
            weather = self.weather_service.current_weather if self.weather_service else None
            weather_history = self.db.get_weather_history(24)

            # Build sparkline data
            temps = [dict(row)["temp_f"] for row in weather_history] if weather_history else []

            return render_template(
                "dashboard.html",
                stats=stats,
                recent_flights=[dict(row) for row in recent],
                weather=weather,
                temp_sparkline=json.dumps(temps),
            )

        @app.route("/api/stats")
        def api_stats():
            stats = self.db.get_flight_stats()
            recent = self.db.get_recent_flights(20)
            weather = self.weather_service.current_weather if self.weather_service else None
            return jsonify({
                "stats": stats,
                "recent_flights": [dict(row) for row in recent],
                "weather": weather,
            })

    def run(self):
        logger.info("Web server starting on port %d", self.port)
        self.app.run(
            host="0.0.0.0",
            port=self.port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    def stop(self):
        pass  # Flask doesn't have a clean shutdown; process exit handles it
