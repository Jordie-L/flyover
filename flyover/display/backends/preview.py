"""Write each new frame to ``frame.png`` for the web UI.

Works anywhere, so the whole program can run on a Pi with no panel, on a
laptop, or remotely as a check on what a real panel is showing.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from ...state import write_bytes_atomic


class PreviewBackend:
    name = "preview"

    def __init__(self, path: Path) -> None:
        self._path = path
        self._last: bytes | None = None

    def show(self, image: Image.Image, brightness: int) -> None:
        pixels = image.tobytes()
        if pixels == self._last:
            return  # unchanged: skip the encode and the disk write
        self._last = pixels
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=False)
        write_bytes_atomic(self._path, buf.getvalue())

    def close(self) -> None:
        pass
