"""Display service — drives the 64x64 RGB LED matrix."""

import logging
import os
import queue
import threading
import time
from datetime import datetime

from PIL import Image

from utils.brightness import get_brightness

logger = logging.getLogger(__name__)

# Font paths relative to the rgbmatrix installation
FONT_DIR = os.path.expanduser("~/rpi-rgb-led-matrix/fonts")


class DisplayService:
    """Manages the LED matrix display with clock, flight, and setup modes."""

    MODE_CLOCK = "clock"
    MODE_FLIGHT = "flight"
    MODE_SETUP = "setup"

    def __init__(self, config, flight_queue, weather_service, wifi_manager):
        self.config = config
        self.flight_queue = flight_queue
        self.weather_service = weather_service
        self.wifi_manager = wifi_manager
        self._stop = threading.Event()

        self.mode = self.MODE_CLOCK
        self._flight_display_until = 0
        self._current_flight = None
        self._flight_hold = config["flight_tracking"].get("display_duration_seconds", 30)
        self._brightness_schedule = config["display"].get("brightness_schedule", [])
        self._last_brightness_check = 0

        self.matrix = None
        self.canvas = None
        self._fonts = {}
        self._tz = None

    def _init_matrix(self):
        """Initialize the RGB matrix hardware."""
        from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

        self._graphics = graphics

        mc = self.config["display"]["matrix"]
        options = RGBMatrixOptions()
        options.rows = mc["rows"]
        options.cols = mc["cols"]
        options.chain_length = mc.get("chain_length", 1)
        options.parallel = mc.get("parallel", 1)
        options.hardware_mapping = mc["hardware_mapping"]
        options.gpio_slowdown = mc.get("gpio_slowdown", 4)
        options.drop_privileges = False

        self.matrix = RGBMatrix(options=options)
        self.canvas = self.matrix.CreateFrameCanvas()

        # Load BDF fonts
        for name, filename in [
            ("large", "9x18B.bdf"),
            ("medium", "7x13B.bdf"),
            ("small", "5x8.bdf"),
            ("tiny", "4x6.bdf"),
        ]:
            font = graphics.Font()
            font_path = os.path.join(FONT_DIR, filename)
            if os.path.exists(font_path):
                font.LoadFont(font_path)
                self._fonts[name] = font
                logger.info("Loaded font: %s", font_path)
            else:
                logger.warning("Font not found: %s", font_path)

    def _update_brightness(self):
        """Update matrix brightness from schedule (every 60s)."""
        now = time.time()
        if now - self._last_brightness_check < 60:
            return
        self._last_brightness_check = now

        brightness = get_brightness(self._brightness_schedule)
        if self.matrix:
            self.matrix.brightness = brightness

    def _get_time(self):
        """Get current time in configured timezone."""
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(self.config.get("timezone", "America/Detroit"))
            return datetime.now(tz)
        except Exception:
            return datetime.now()

    def _draw_clock(self):
        """Draw clock/weather mode."""
        g = self._graphics
        self.canvas.Clear()

        now = self._get_time()
        time_str = now.strftime("%-I:%M")
        am_pm = now.strftime("%p").lower()

        # Time — large white text
        white = g.Color(255, 255, 255)
        font_large = self._fonts.get("large")
        font_medium = self._fonts.get("medium")
        font_small = self._fonts.get("small")
        font_tiny = self._fonts.get("tiny")

        if font_large:
            g.DrawText(self.canvas, font_large, 2, 18, white, time_str)
        if font_tiny:
            g.DrawText(self.canvas, font_tiny, 50, 18, white, am_pm)

        # Weather data
        weather = self.weather_service.current_weather
        if weather:
            temp_str = f"{weather['temp_f']:.0f}°F"
            rain_str = f"{weather['rain_chance_pct']:.0f}%"

            cyan = g.Color(0, 200, 255)
            if font_medium:
                g.DrawText(self.canvas, font_medium, 2, 38, cyan, temp_str)

            # Rain chance: yellow if <50%, orange if >=50%
            rain_pct = weather["rain_chance_pct"]
            if rain_pct >= 50:
                rain_color = g.Color(255, 140, 0)
            else:
                rain_color = g.Color(255, 255, 0)

            if font_small:
                g.DrawText(self.canvas, font_small, 2, 52, rain_color, f"Rain:{rain_str}")
        else:
            # No weather data yet
            gray = g.Color(80, 80, 80)
            if font_small:
                g.DrawText(self.canvas, font_small, 2, 38, gray, "No weather")

        # Date at bottom
        date_str = now.strftime("%b %-d")
        dim_white = g.Color(120, 120, 120)
        if font_tiny:
            g.DrawText(self.canvas, font_tiny, 2, 62, dim_white, date_str)

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _draw_flight(self, flight):
        """Draw flight info mode."""
        g = self._graphics
        self.canvas.Clear()

        origin = flight.get("origin", "???")
        dest = flight.get("destination", "???")
        callsign = flight.get("callsign", "")

        green = g.Color(0, 255, 0)
        white = g.Color(255, 255, 255)
        dim_green = g.Color(0, 150, 0)

        font_large = self._fonts.get("large")
        font_medium = self._fonts.get("medium")
        font_small = self._fonts.get("small")

        # Origin airport code — top
        if font_large and origin:
            g.DrawText(self.canvas, font_large, 2, 18, green, origin)

        # Arrow in middle
        if font_medium:
            g.DrawText(self.canvas, font_medium, 24, 33, dim_green, "->")

        # Destination airport code
        if font_large and dest:
            g.DrawText(self.canvas, font_large, 2, 48, green, dest)

        # Callsign at bottom
        if font_small and callsign:
            g.DrawText(self.canvas, font_small, 2, 60, white, callsign)

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _draw_setup_qr(self):
        """Draw QR code for captive portal setup."""
        import qrcode

        g = self._graphics
        self.canvas.Clear()

        # Generate QR code for portal URL
        ip = self.wifi_manager.get_ap_ip() if self.wifi_manager else "192.168.4.1"
        url = f"http://{ip}"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="white", back_color="black")
        qr_img = qr_img.convert("RGB")

        # Center QR on matrix
        w, h = qr_img.size
        x_offset = (64 - w) // 2
        y_offset = max(0, (48 - h) // 2)  # leave room for text at bottom

        for y in range(h):
            for x in range(w):
                r, gg, b = qr_img.getpixel((x, y))
                if r > 128:  # white pixel
                    self.canvas.SetPixel(x + x_offset, y + y_offset, 255, 255, 255)

        # "SCAN TO SETUP" text at bottom
        white = g.Color(255, 255, 255)
        font_tiny = self._fonts.get("tiny")
        if font_tiny:
            g.DrawText(self.canvas, font_tiny, 2, 60, white, "SCAN TO SETUP")

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _check_flight_queue(self):
        """Check for new flight events."""
        try:
            flight = self.flight_queue.get_nowait()
            # Prioritize closer/lower aircraft
            if (
                self._current_flight is None
                or flight["distance_km"] < self._current_flight["distance_km"]
            ):
                self._current_flight = flight
                self._flight_display_until = time.time() + self._flight_hold
                self.mode = self.MODE_FLIGHT
                logger.info(
                    "Displaying flight: %s (%s→%s)",
                    flight.get("callsign"),
                    flight.get("origin"),
                    flight.get("destination"),
                )
        except queue.Empty:
            pass

        # Check if flight display time expired
        if self.mode == self.MODE_FLIGHT and time.time() > self._flight_display_until:
            self.mode = self.MODE_CLOCK
            self._current_flight = None

    def run(self):
        logger.info("Display service starting")
        self._init_matrix()

        while not self._stop.is_set():
            self._update_brightness()
            self._check_flight_queue()

            # Determine mode
            wifi_mode = self.wifi_manager.get_mode() if self.wifi_manager else "client"
            if wifi_mode == "ap" and self.mode != self.MODE_FLIGHT:
                self.mode = self.MODE_SETUP

            if self.mode == self.MODE_FLIGHT and self._current_flight:
                self._draw_flight(self._current_flight)
            elif self.mode == self.MODE_SETUP:
                self._draw_setup_qr()
            else:
                self._draw_clock()

            # ~30 FPS would be overkill for this display; 10 FPS is fine
            time.sleep(0.1)

    def stop(self):
        self._stop.set()
        if self.matrix:
            self.matrix.Clear()
