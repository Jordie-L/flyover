"""Keep frames in memory (and optionally on disk) for tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class PngBackend:
    name = "png"

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory
        self.frames: list[tuple[Image.Image, int]] = []

    @property
    def last(self) -> Image.Image | None:
        return self.frames[-1][0] if self.frames else None

    def show(self, image: Image.Image, brightness: int) -> None:
        self.frames.append((image.copy(), brightness))
        if self._dir is not None:
            image.save(self._dir / f"frame-{len(self.frames):04d}.png")

    def close(self) -> None:
        pass
