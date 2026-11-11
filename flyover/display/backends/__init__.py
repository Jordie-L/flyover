"""Display backends and the logic that picks one."""

from __future__ import annotations

import importlib
import logging

from ...config import Config
from ...state import RunPaths
from .base import Backend, BackendUnavailable
from .preview import PreviewBackend

log = logging.getLogger(__name__)

__all__ = ["Backend", "BackendUnavailable", "PreviewBackend", "create_backend"]


def _matrix_backend_class():
    """The LED matrix backend class, or None if its library is not installed."""
    try:
        module = importlib.import_module(".matrix", __package__)
    except ImportError as exc:
        log.debug("matrix backend unavailable: %s", exc)
        return None
    return module.MatrixBackend


def create_backend(choice: str, cfg: Config, paths: RunPaths) -> Backend:
    """Build the backend named by ``choice`` ("auto", "matrix" or "preview").

    "auto" uses the LED matrix when its library can be imported, otherwise the
    preview. Naming "matrix" on a machine without the library is an error
    rather than a silent fallback, so a broken install is noticed.
    """
    if choice == "preview":
        return PreviewBackend(paths.frame)
    matrix_cls = _matrix_backend_class()
    if matrix_cls is None:
        if choice == "matrix":
            raise BackendUnavailable(
                "the matrix backend needs the rpi-rgb-led-matrix Python binding "
                "(run deploy/install.sh on the Pi, or use --backend preview)"
            )
        log.info("rpi-rgb-led-matrix is not installed; using the preview backend")
        return PreviewBackend(paths.frame)
    return matrix_cls(cfg.matrix, paths.frame)
