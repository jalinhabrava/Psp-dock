"""Build a review contact sheet from the portable renderer's PPM snapshots.

Usage: python assets/render_preview.py /tmp/psp-dock-ui-frames docs/preview.png
The images are not used by the PSP binary; they are just a reproducible review.
"""
from pathlib import Path
import sys

from PIL import Image, ImageDraw


def main() -> None:
    source, output = map(Path, sys.argv[1:3])
    titles = (
        "01 · Preparación", "02 · Activando HID", "03 · Conectar USB",
        "04 · Modo mando", "05 · Recuperación de error", "06 · Salida",
    )
    preview = Image.new("RGB", (1000, 924), (8, 17, 29))
    draw = ImageDraw.Draw(preview)
    for i, title in enumerate(titles):
        x = 8 + (i % 2) * 500
        y = 6 + (i // 2) * 306
        draw.text((x + 9, y + 4), title, fill=(174, 225, 240))
        img = Image.open(source / f"state-{i}.ppm").convert("RGB")
        preview.paste(img, (x, y + 26))
    output.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output, optimize=True)
    print(f"Preview saved to {output}")


if __name__ == "__main__":
    main()
