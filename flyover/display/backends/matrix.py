"""Drive the HUB75 LED panel through hzeller's rpi-rgb-led-matrix.

Importing this module fails with ``ImportError`` when the driver's Python
binding is not installed; ``backends.create_backend`` treats that as "no
matrix here" and falls back to the preview backend.

The driver needs root only while it initialises the hardware, so it is told
not to drop privileges itself: ``display.privileges`` does that once, after
everything has started.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

from rgbmatrix import RGBMatrix, RGBMatrixOptions  # type: ignore[import-not-found]

from ...config import Matrix
from ..scenes.base import SIZE
from .preview import PreviewBackend

log = logging.getLogger(__name__)


def build_options(cfg: Matrix) -> RGBMatrixOptions:
    """Translate the ``[matrix]`` config section into the driver's options."""
    options = RGBMatrixOptions()
    options.rows = cfg.rows
    options.cols = cfg.cols
    options.chain_length = 1
    options.parallel = 1
    options.hardware_mapping = cfg.hardware_mapping
    options.gpio_slowdown = cfg.gpio_slowdown
    options.pwm_bits = cfg.pwm_bits
    options.pwm_lsb_nanoseconds = cfg.pwm_lsb_nanoseconds
    options.limit_refresh_rate_hz = cfg.limit_refresh_hz
    options.row_address_type = cfg.row_addr_type
    options.led_rgb_sequence = cfg.rgb_sequence
    if cfg.panel_type:
        options.panel_type = cfg.panel_type
    options.drop_privileges = False
    return options


class MatrixBackend:
    name = "matrix"

    def __init__(self, cfg: Matrix, preview_path: Path) -> None:
        self._size = (cfg.cols, cfg.rows)
        if self._size != SIZE:
            log.warning("the screens are drawn for %dx%d but the panel is %dx%d: cropping or padding",
                        *SIZE, *self._size)
        self._matrix = RGBMatrix(options=build_options(cfg))
        self._canvas = self._matrix.CreateFrameCanvas()
        # The web UI shows the picture the panel shows, so the preview file is written here too.
        self._preview = PreviewBackend(preview_path)
        self._last: tuple[bytes, int] | None = None

    def show(self, image: Image.Image, brightness: int) -> None:
        key = (image.tobytes(), brightness)
        if key == self._last:
            return  # nothing new: leave the panel's refresh thread alone
        self._last = key
        if brightness <= 0:  # the driver's minimum is 1, so "off" is a black picture
            image, brightness = Image.new("RGB", image.size), 1
        self._matrix.brightness = brightness
        self._canvas.SetImage(self._fit(image))
        self._canvas = self._matrix.SwapOnVSync(self._canvas)
        self._preview.show(image, brightness)

    def _fit(self, image: Image.Image) -> Image.Image:
        if image.size == self._size:
            return image
        canvas = Image.new("RGB", self._size)
        canvas.paste(image, (0, 0))
        return canvas

    def close(self) -> None:
        matrix, self._matrix = getattr(self, "_matrix", None), None
        if matrix is not None:
            matrix.Clear()
