"""Downscale the selected generated XMB artwork without redrawing it.

The three source PNGs and their exact generation prompts are retained beside
this script. Only PNG mode conversion and Lanczos resizing are performed.
Run with Pillow installed: python assets/generate_artwork.py
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
ASSETS = (
    ("ICON0", (144, 80), "RGB"),
    ("PIC0", (310, 180), "RGBA"),
    ("PIC1", (480, 272), "RGB"),
)


def main() -> None:
    for name, size, mode in ASSETS:
        source = HERE / "sources" / (name + "-generated.png")
        with Image.open(source) as original:
            original.load()
            if original.width < size[0] or original.height < size[1]:
                raise ValueError("The artwork source must not be upscaled: " + name)
            if name == "PIC0" and (
                    "A" not in original.getbands() or
                    original.getchannel("A").getextrema() != (0, 255)):
                raise ValueError("PIC0 must retain real generated transparency.")
            artwork = original.convert(mode).resize(size, Image.Resampling.LANCZOS)
            target = HERE / (name + ".PNG")
            artwork.save(target, format="PNG", optimize=True)
            print(f"{target.name}: {size[0]}x{size[1]} {mode}, {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
