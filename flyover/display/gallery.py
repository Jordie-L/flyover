"""Render every sample screen to enlarged PNGs and one contact sheet.

    python -m flyover.display.gallery --out docs/screens
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

from . import fonts
from .samples import build_samples
from .scenes.base import SIZE

BACKGROUND = (24, 24, 28)


def enlarge(image: Image.Image, scale: int, grid: bool = True) -> Image.Image:
    """Scale up with hard edges; optionally dim the gaps so pixels read as LEDs."""
    big = image.resize((SIZE[0] * scale, SIZE[1] * scale), Image.Resampling.NEAREST)
    if grid and scale >= 4:
        d = ImageDraw.Draw(big)
        for i in range(0, SIZE[0] * scale, scale):
            d.line((i, 0, i, big.height), fill=(0, 0, 0))
            d.line((0, i, big.width, i), fill=(0, 0, 0))
    return big


def contact_sheet(tiles: list[tuple[str, Image.Image]], scale: int, columns: int = 4) -> Image.Image:
    label_font = fonts.load_font(fonts.SMALL)
    pad, label_h = 12, 14
    cell_w, cell_h = SIZE[0] * scale, SIZE[1] * scale + label_h
    rows = -(-len(tiles) // columns)
    sheet = Image.new("RGB", (columns * (cell_w + pad) + pad, rows * (cell_h + pad) + pad), BACKGROUND)
    d = ImageDraw.Draw(sheet)
    for i, (name, tile) in enumerate(tiles):
        x = pad + (i % columns) * (cell_w + pad)
        y = pad + (i // columns) * (cell_h + pad)
        sheet.paste(enlarge(tile, scale), (x, y + label_h))
        label_font.draw(d, x, y + 2, name, (200, 200, 205))
    return sheet


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=Path("docs/screens"))
    parser.add_argument("--scale", type=int, default=6)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    tiles = []
    for name, scene, ctx in build_samples():
        frame = scene.render(ctx)
        tiles.append((name, frame))
        enlarge(frame, args.scale).save(args.out / f"{name}.png")
    contact_sheet(tiles, args.scale).save(args.out / "gallery.png")
    print(f"wrote {len(tiles)} screens and gallery.png to {args.out}")


if __name__ == "__main__":
    main()
