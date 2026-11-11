"""The interface between a drawn frame and wherever it ends up."""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class Backend(Protocol):
    name: str

    def show(self, image: Image.Image, brightness: int) -> None:
        """Present a 64x64 RGB image. ``brightness`` is 0-100 (0 means dark)."""

    def close(self) -> None:
        """Release the hardware. Safe to call twice."""


class BackendUnavailable(RuntimeError):
    """The requested backend cannot run on this machine."""
