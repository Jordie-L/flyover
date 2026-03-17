"""Display service — drives the 32x32 RGB LED matrix."""

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

        # Load BDF fonts — smaller fonts for 32x32
        for name, filename in [
            ("large", "7x13B.bdf"),
            ("medium", "5x8.bdf"),
            ("small", "4x6.bdf"),
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
        """Draw clock/weather mode on 32x32 display.

        Layout:
          Row ~10: Time (e.g. "1:23p") in large font
          Row ~20: Temp (e.g. "72°F") in medium font
          Row ~28: Rain % (e.g. "R:15%") in small font
        """
        g = self._graphics
        self.canvas.Clear()

        now = self._get_time()
        time_str = now.strftime("%-I:%M")
        am_pm = now.strftime("%p")[0].lower()  # just "a" or "p" to save space

        white = g.Color(255, 255, 255)
        font_large = self._fonts.get("large")
        font_medium = self._fonts.get("medium")
        font_small = self._fonts.get("small")

        # Time — large white text
        if font_large:
            g.DrawText(self.canvas, font_large, 1, 11, white, time_str)
            # AM/PM indicator — small, tucked right
            if font_small:
                g.DrawText(self.canvas, font_small, 28, 11, white, am_pm)

        # Weather data
        weather = self.weather_service.current_weather
        if weather:
            temp_str = f"{weather['temp_f']:.0f}°F"
            rain_pct = weather["rain_chance_pct"]
            rain_str = f"R:{rain_pct:.0f}%"

            cyan = g.Color(0, 200, 255)
            if font_medium:
                g.DrawText(self.canvas, font_medium, 1, 21, cyan, temp_str)

            rain_color = g.Color(255, 140, 0) if rain_pct >= 50 else g.Color(255, 255, 0)
            if font_small:
                g.DrawText(self.canvas, font_small, 1, 29, rain_color, rain_str)
        else:
            gray = g.Color(80, 80, 80)
            if font_small:
                g.DrawText(self.canvas, font_small, 1, 21, gray, "No wthr")

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _draw_flight(self, flight):
        """Draw flight info on 32x32 display.

        Layout:
          Row ~8:  Origin code (e.g. "LAX")
          Row ~16: Arrow ">"
          Row ~24: Destination code (e.g. "GRR")
          Row ~31: Callsign in small font
        """
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

        if font_large and origin:
            g.DrawText(self.canvas, font_large, 1, 10, green, origin)

        if font_medium:
            g.DrawText(self.canvas, font_medium, 12, 18, dim_green, ">")

        if font_large and dest:
            g.DrawText(self.canvas, font_large, 1, 26, green, dest)

        if font_small and callsign:
            g.DrawText(self.canvas, font_small, 1, 31, white, callsign[:8])

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _draw_setup_qr(self):
        """Draw QR code for captive portal setup on 32x32 display."""
        import qrcode

        g = self._graphics
        self.canvas.Clear()

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

        # Center QR on 32x32 matrix
        w, h = qr_img.size
        x_offset = (32 - w) // 2
        y_offset = (32 - h) // 2

        for y in range(h):
            for x in range(w):
                r, gg, b = qr_img.getpixel((x, y))
                if r > 128:
                    self.canvas.SetPixel(x + x_offset, y + y_offset, 255, 255, 255)

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

            # ~10 FPS is fine for this display
            time.sleep(0.1)

    def stop(self):
        self._stop.set()
        if self.matrix:
            self.matrix.Clear()
