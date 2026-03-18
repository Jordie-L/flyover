"""Display service — drives the 32x32 RGB LED matrix."""

import collections
import logging
import os
import queue
import threading
import time
from datetime import datetime

from utils.brightness import get_brightness

logger = logging.getLogger(__name__)

FONT_DIR = os.path.expanduser("~/rpi-rgb-led-matrix/fonts")

# --- Weather pixel art sprites (8x8) ---
# Each sprite is a list of rows; each row is a list of (R,G,B) or None.
_N = None  # transparent
_Y = (255, 220, 0)    # yellow (sun)
_W = (255, 255, 255)   # white
_G = (130, 130, 130)   # gray (cloud)
_D = (80, 80, 80)      # dark gray
_B = (50, 120, 255)    # blue (rain drop)
_L = (255, 255, 50)    # lightning yellow

SPRITES = {
    # Clear sky — yellow sun with rays
    "clear": [
        [_N, _N, _N, _Y, _N, _N, _N, _N],
        [_N, _Y, _N, _N, _N, _Y, _N, _N],
        [_N, _N, _Y, _Y, _Y, _N, _N, _N],
        [_Y, _N, _Y, _Y, _Y, _N, _Y, _N],
        [_N, _N, _Y, _Y, _Y, _N, _N, _N],
        [_N, _Y, _N, _N, _N, _Y, _N, _N],
        [_N, _N, _N, _Y, _N, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Few clouds — sun peeking behind cloud
    "few_clouds": [
        [_N, _N, _Y, _Y, _N, _N, _N, _N],
        [_N, _Y, _Y, _Y, _Y, _N, _N, _N],
        [_N, _N, _Y, _Y, _G, _G, _N, _N],
        [_N, _N, _G, _G, _G, _G, _G, _N],
        [_N, _G, _G, _G, _G, _G, _G, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Cloudy/overcast — gray cloud
    "cloudy": [
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _N, _G, _G, _G, _N, _N, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_G, _G, _G, _G, _G, _G, _G, _N],
        [_G, _G, _G, _G, _G, _G, _G, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Rain — cloud with droplets
    "rain": [
        [_N, _N, _G, _G, _G, _N, _N, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_G, _G, _G, _G, _G, _G, _G, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _B, _N, _B, _N, _B, _N, _N],
        [_N, _N, _B, _N, _B, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Thunderstorm — dark cloud with lightning
    "thunder": [
        [_N, _N, _D, _D, _D, _N, _N, _N],
        [_N, _D, _D, _D, _D, _D, _N, _N],
        [_D, _D, _D, _D, _D, _D, _D, _N],
        [_N, _D, _D, _D, _D, _D, _N, _N],
        [_N, _N, _N, _L, _L, _N, _N, _N],
        [_N, _N, _L, _L, _N, _N, _N, _N],
        [_N, _N, _N, _L, _N, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Snow — cloud with white dots
    "snow": [
        [_N, _N, _G, _G, _G, _N, _N, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_G, _G, _G, _G, _G, _G, _G, _N],
        [_N, _G, _G, _G, _G, _G, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _W, _N, _W, _N, _W, _N, _N],
        [_N, _N, _W, _N, _W, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
    ],
    # Mist/fog — horizontal gray lines
    "mist": [
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_G, _G, _G, _G, _G, _G, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _G, _G, _G, _G, _G, _G, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_G, _G, _G, _G, _G, _N, _N, _N],
        [_N, _N, _N, _N, _N, _N, _N, _N],
        [_N, _G, _G, _G, _G, _N, _N, _N],
    ],
}

# Map OpenWeatherMap icon codes to sprite keys
ICON_MAP = {
    "01d": "clear", "01n": "clear",
    "02d": "few_clouds", "02n": "few_clouds",
    "03d": "cloudy", "03n": "cloudy",
    "04d": "cloudy", "04n": "cloudy",
    "09d": "rain", "09n": "rain",
    "10d": "rain", "10n": "rain",
    "11d": "thunder", "11n": "thunder",
    "13d": "snow", "13n": "snow",
    "50d": "mist", "50n": "mist",
}


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
        self._flight_queue_pending = collections.deque()
        self._flight_hold = config["flight_tracking"].get("display_duration_seconds", 30)
        self._flight_queued_hold = 10  # seconds per queued aircraft
        self._brightness_schedule = config["display"].get("brightness_schedule", [])
        self._last_brightness_check = 0
        self._last_clock_draw = 0

        self.matrix = None
        self.canvas = None
        self._fonts = {}

        mc = config["display"]["matrix"]
        self._rows = mc.get("rows", 32)
        self._cols = mc.get("cols", 32)

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
            ("large", "7x13B.bdf"),
            ("medium", "6x12.bdf"),
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

    def _center_x(self, text, font_width):
        """Compute x offset to horizontally center text."""
        text_width = len(text) * font_width
        return max(0, (self._cols - text_width) // 2)

    def _draw_sprite(self, sprite, x_off, y_off):
        """Draw an 8x8 pixel art sprite on the canvas."""
        for y, row in enumerate(sprite):
            for x, pixel in enumerate(row):
                if pixel is not None:
                    px = x_off + x
                    py = y_off + y
                    if 0 <= px < self._cols and 0 <= py < self._rows:
                        self.canvas.SetPixel(px, py, pixel[0], pixel[1], pixel[2])

    def _draw_clock(self):
        """Draw clock/weather mode on 32x32 display.

        Layout (top to bottom):
          Row 1-13:  Time (e.g. "12:47") — 7x13B font, white, centered
          Row 14-19: "AM"/"PM" — 4x6 font, dim white, centered
          Row 20-27: Temp (e.g. "47°F") — 5x8 font, cyan, centered
          Row 24-31: Weather icon (8x8) left + rain% right
        """
        g = self._graphics
        self.canvas.Clear()

        now = self._get_time()
        time_str = now.strftime("%-I:%M")
        am_pm = now.strftime("%p")

        white = g.Color(255, 255, 255)
        dim_white = g.Color(150, 150, 150)

        font_large = self._fonts.get("large")   # 7x13B
        font_small = self._fonts.get("small")   # 5x8
        font_tiny = self._fonts.get("tiny")     # 4x6

        # Time — centered, white
        if font_large:
            x = self._center_x(time_str, 7)
            g.DrawText(self.canvas, font_large, x, 12, white, time_str)

        # AM/PM — centered, dim
        if font_tiny:
            x = self._center_x(am_pm, 4)
            g.DrawText(self.canvas, font_tiny, x, 18, dim_white, am_pm)

        # Weather data
        weather = self.weather_service.current_weather if self.weather_service else None
        if weather:
            # Temperature — centered, cyan
            temp_str = f"{weather['temp_f']:.0f}F"
            cyan = g.Color(0, 200, 255)
            if font_small:
                x = self._center_x(temp_str, 5)
                g.DrawText(self.canvas, font_small, x, 26, cyan, temp_str)

            # Bottom row: weather icon (left) + rain % (right)
            icon_code = weather.get("icon", "")
            sprite_key = ICON_MAP.get(icon_code, "cloudy")
            sprite = SPRITES.get(sprite_key)
            if sprite:
                self._draw_sprite(sprite, 1, 24)

            rain_pct = weather.get("rain_chance_pct", 0)
            rain_str = f"{rain_pct:.0f}%"
            rain_color = g.Color(255, 180, 0)
            if font_tiny:
                g.DrawText(self.canvas, font_tiny, 18, 31, rain_color, rain_str)
        else:
            gray = g.Color(80, 80, 80)
            if font_tiny:
                g.DrawText(self.canvas, font_tiny, 4, 26, gray, "No wthr")

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _scroll_wipe_transition(self):
        """Brief scroll-up wipe transition (2-3 frames)."""
        if not self.matrix:
            return
        for shift in range(1, 4):
            temp = self.matrix.CreateFrameCanvas()
            # Shift current frame content up
            for y in range(self._rows - shift):
                for x in range(self._cols):
                    # We can't read pixels from the matrix, so just clear in steps
                    pass
            temp.Clear()
            self.matrix.SwapOnVSync(temp)
            time.sleep(0.04)

    def _draw_flight(self, flight):
        """Draw flight info on 32x32 display.

        With route data:
          Row ~10: Origin code, large green, centered
          Row ~16: ">" arrow, dim white, centered
          Row ~23: Destination code, large green, centered
          Row ~31: Callsign, tiny white, centered

        Without route (callsign only):
          Callsign large and centered
        """
        g = self._graphics
        self.canvas.Clear()

        origin = flight.get("origin")
        dest = flight.get("destination")
        callsign = flight.get("callsign", "")

        green = g.Color(0, 255, 80)
        white = g.Color(255, 255, 255)
        dim_white = g.Color(150, 150, 150)

        font_large = self._fonts.get("large")   # 7x13B
        font_medium = self._fonts.get("medium")  # 6x12
        font_small = self._fonts.get("small")   # 5x8
        font_tiny = self._fonts.get("tiny")     # 4x6

        if origin and dest:
            # Origin — top, centered
            if font_large:
                x = self._center_x(origin, 7)
                g.DrawText(self.canvas, font_large, x, 10, green, origin)

            # Arrow — middle, centered
            if font_small:
                x = self._center_x(">", 5)
                g.DrawText(self.canvas, font_small, x, 17, dim_white, ">")

            # Destination — bottom, centered
            if font_large:
                x = self._center_x(dest, 7)
                g.DrawText(self.canvas, font_large, x, 26, green, dest)

            # Callsign — very bottom
            if font_tiny and callsign:
                x = self._center_x(callsign[:8], 4)
                g.DrawText(self.canvas, font_tiny, x, 32, white, callsign[:8])
        elif callsign:
            # No route data — show callsign large and centered
            if font_large:
                x = self._center_x(callsign[:5], 7)
                g.DrawText(self.canvas, font_large, x, 20, green, callsign[:5])
            if font_tiny and len(callsign) > 5:
                x = self._center_x(callsign, 4)
                g.DrawText(self.canvas, font_tiny, x, 28, white, callsign[:8])
        else:
            # No data at all
            if font_small:
                g.DrawText(self.canvas, font_small, 2, 18, dim_white, "PLANE")

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _draw_setup_qr(self):
        """Draw QR code for captive portal setup on 32x32 display."""
        import qrcode

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

        w, h = qr_img.size
        # If QR fits with room for text, show "SCAN" above
        if h <= 26:
            y_offset = (self._rows - h - 6) // 2 + 6
            x_offset = (self._cols - w) // 2

            font_tiny = self._fonts.get("tiny")
            if font_tiny:
                g = self._graphics
                white = g.Color(255, 255, 255)
                x = self._center_x("SCAN", 4)
                g.DrawText(self.canvas, font_tiny, x, 6, white, "SCAN")
        else:
            x_offset = (self._cols - w) // 2
            y_offset = (self._rows - h) // 2

        for y in range(h):
            for x in range(w):
                r, gg, b = qr_img.getpixel((x, y))
                if r > 128:
                    px, py = x + x_offset, y + y_offset
                    if 0 <= px < self._cols and 0 <= py < self._rows:
                        self.canvas.SetPixel(px, py, 255, 255, 255)

        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def _check_flight_queue(self):
        """Check for new flight events and manage display priority."""
        # Drain all pending events from the inter-thread queue
        while True:
            try:
                flight = self.flight_queue.get_nowait()
                if self._current_flight is None:
                    # No active flight — display immediately
                    self._current_flight = flight
                    self._flight_display_until = time.time() + self._flight_hold
                    self.mode = self.MODE_FLIGHT
                    self._scroll_wipe_transition()
                    logger.info(
                        "Displaying flight: %s (%s>%s)",
                        flight.get("callsign"),
                        flight.get("origin"),
                        flight.get("destination"),
                    )
                elif flight["distance_km"] < self._current_flight["distance_km"]:
                    # Closer aircraft — preempt current display
                    self._flight_queue_pending.appendleft(self._current_flight)
                    self._current_flight = flight
                    self._flight_display_until = time.time() + self._flight_hold
                    self._scroll_wipe_transition()
                    logger.info(
                        "Preempting with closer flight: %s (%.1fkm)",
                        flight.get("callsign"), flight["distance_km"],
                    )
                else:
                    # Queue it for later
                    self._flight_queue_pending.append(flight)
            except queue.Empty:
                break

        # Check if current flight display time expired
        if self.mode == self.MODE_FLIGHT and time.time() > self._flight_display_until:
            if self._flight_queue_pending:
                # Show next queued aircraft (10s each)
                self._current_flight = self._flight_queue_pending.popleft()
                self._flight_display_until = time.time() + self._flight_queued_hold
                self._scroll_wipe_transition()
                logger.info(
                    "Next queued flight: %s",
                    self._current_flight.get("callsign"),
                )
            else:
                self._scroll_wipe_transition()
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
                # Clock mode: only redraw every 0.5s (no seconds displayed)
                now = time.time()
                if now - self._last_clock_draw >= 0.5:
                    self._draw_clock()
                    self._last_clock_draw = now

            time.sleep(0.1)

    def stop(self):
        self._stop.set()
        if self.matrix:
            self.matrix.Clear()
