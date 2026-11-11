"""The LED matrix backend, against a stand-in for the driver.

The real driver only runs on a Pi with a panel, so this checks what we control: how config
becomes driver options, brightness handling, and not redrawing unchanged frames. Behaviour of
the panel itself is verified on the device (see docs/PI_INTEGRATION.md).
"""

import sys
import types

import pytest
from PIL import Image

from flyover.config import Config, Matrix


class FakeOptions:
    pass


class FakeCanvas:
    def __init__(self):
        self.images = []

    def SetImage(self, image):
        self.images.append(image)


class FakeMatrix:
    instances = []

    def __init__(self, options):
        self.options, self.brightness, self.cleared = options, None, False
        self.canvas, self.swaps = FakeCanvas(), 0
        FakeMatrix.instances.append(self)

    def CreateFrameCanvas(self):
        return self.canvas

    def SwapOnVSync(self, canvas):
        self.swaps += 1
        return canvas

    def Clear(self):
        self.cleared = True


@pytest.fixture
def backend_module(monkeypatch):
    FakeMatrix.instances.clear()
    stub = types.ModuleType("rgbmatrix")
    stub.RGBMatrix, stub.RGBMatrixOptions = FakeMatrix, FakeOptions
    monkeypatch.setitem(sys.modules, "rgbmatrix", stub)
    monkeypatch.delitem(sys.modules, "flyover.display.backends.matrix", raising=False)
    from flyover.display.backends import matrix
    yield matrix
    sys.modules.pop("flyover.display.backends.matrix", None)


def test_config_becomes_driver_options(backend_module):
    cfg = Matrix(rows=64, cols=64, hardware_mapping="adafruit-hat-pwm", gpio_slowdown=3, pwm_bits=9,
                 pwm_lsb_nanoseconds=200, limit_refresh_hz=120, row_addr_type=1, rgb_sequence="BGR",
                 panel_type="FM6126A")
    o = backend_module.build_options(cfg)
    assert (o.rows, o.cols, o.chain_length, o.parallel) == (64, 64, 1, 1)
    assert (o.hardware_mapping, o.gpio_slowdown, o.pwm_bits) == ("adafruit-hat-pwm", 3, 9)
    assert (o.pwm_lsb_nanoseconds, o.limit_refresh_rate_hz, o.row_address_type) == (200, 120, 1)
    assert (o.led_rgb_sequence, o.panel_type) == ("BGR", "FM6126A")
    assert o.drop_privileges is False  # we drop privileges ourselves, after startup


def test_panel_type_is_left_unset_when_empty(backend_module):
    assert not hasattr(backend_module.build_options(Matrix()), "panel_type")


def make(backend_module, tmp_path, cfg=None):
    return backend_module.MatrixBackend(cfg or Matrix(), tmp_path / "frame.png")


def test_show_sets_brightness_draws_and_swaps(backend_module, tmp_path):
    backend = make(backend_module, tmp_path)
    img = Image.new("RGB", (64, 64), (9, 9, 9))
    backend.show(img, 40)
    fake = FakeMatrix.instances[0]
    assert fake.brightness == 40 and fake.swaps == 1 and fake.canvas.images == [img]
    assert Image.open(tmp_path / "frame.png").getpixel((0, 0)) == (9, 9, 9)  # the web UI preview


def test_an_unchanged_frame_is_not_redrawn(backend_module, tmp_path):
    backend = make(backend_module, tmp_path)
    img = Image.new("RGB", (64, 64), (9, 9, 9))
    backend.show(img, 40)
    backend.show(img.copy(), 40)
    assert FakeMatrix.instances[0].swaps == 1
    backend.show(img, 20)  # same picture, new brightness: redraw
    assert FakeMatrix.instances[0].swaps == 2 and FakeMatrix.instances[0].brightness == 20


def test_zero_brightness_means_a_black_picture_at_the_driver_minimum(backend_module, tmp_path):
    backend = make(backend_module, tmp_path)
    backend.show(Image.new("RGB", (64, 64), (255, 255, 255)), 0)
    fake = FakeMatrix.instances[0]
    assert fake.brightness == 1 and fake.canvas.images[0].getbbox() is None


def test_a_different_panel_size_pads_or_crops_instead_of_failing(backend_module, tmp_path, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        backend = make(backend_module, tmp_path, Matrix(rows=32, cols=64))
    assert "64x64 but the panel is 64x32" in caplog.text
    backend.show(Image.new("RGB", (64, 64), (1, 1, 1)), 50)
    assert FakeMatrix.instances[0].canvas.images[0].size == (64, 32)


def test_close_clears_the_panel_and_is_safe_twice(backend_module, tmp_path):
    backend = make(backend_module, tmp_path)
    backend.close()
    backend.close()
    assert FakeMatrix.instances[0].cleared


def test_auto_selection_uses_the_matrix_when_the_driver_is_present(backend_module, tmp_path):
    from flyover.display.backends import create_backend
    from flyover.state import RunPaths

    paths = RunPaths(tmp_path)
    assert create_backend("auto", Config(), paths).name == "matrix"
    assert create_backend("matrix", Config(), paths).name == "matrix"
    assert create_backend("preview", Config(), paths).name == "preview"
