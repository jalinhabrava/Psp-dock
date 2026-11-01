"""Reproducible XMB art for PSP Dock: ICON0 144x80, PIC0 310x180, PIC1 480x272.

All geometry is original vector-style drawing. Nothing is taken from a game's
own XMB art, and no external media or font files are embedded in the PBP.
Run with Pillow installed and DejaVu Sans available on the host.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


HERE = Path(__file__).resolve().parent
S = 3  # Supersampling for small LCD screens.


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for root in (
        os.getenv("PSP_DOCK_FONT_ROOT", ""),
        "/usr/share/fonts/truetype/dejavu",
        "C:/Windows/Fonts",
    ):
        path = Path(root) / name
        if path.is_file():
            return ImageFont.truetype(str(path), size * S)
    raise FileNotFoundError(f"Install DejaVu fonts: missing {name}")


def gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    pix = image.load()
    for y in range(height):
        for x in range(width):
            a = (x / width) * 0.62 + (y / height) * 0.19
            glow1 = max(0.0, 1 - ((x / width - 0.76) ** 2 / 0.32 ** 2 +
                                  (y / height - 0.60) ** 2 / 0.78 ** 2))
            glow2 = max(0.0, 1 - ((x / width - 0.93) ** 2 / 0.40 ** 2 +
                                  (y / height - 0.05) ** 2 / 0.70 ** 2))
            pix[x, y] = (
                int(5 + 7 * a + 2 * glow1 + 9 * glow2),
                int(17 + 17 * a + 17 * glow1 + 4 * glow2),
                int(35 + 23 * a + 19 * glow1 + 28 * glow2), 255
            )
    return image


def blend(base: Image.Image, layer: Image.Image) -> None:
    base.alpha_composite(layer)


def waves(canvas: Image.Image) -> None:
    w, h = canvas.size
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for n in range(5):
        points = []
        for x in range(0, w + 6, 4):
            y = int(h * (0.48 + n * 0.09) - math.sin(x / w * math.pi * 1.45 + n * .36) * h * .23)
            points.append((x, y))
        d.line(points, fill=(81, 207, 237, 33 - 4 * n), width=S)
    d.ellipse((w * .56, -h * .85, w * 1.42, h * .75), outline=(104, 221, 241, 29), width=S)
    d.ellipse((w * .44, -h * .97, w * 1.61, h * .85), outline=(107, 141, 255, 27), width=S)
    for x in range(16 * S, w, 23 * S):
        for y in range(15 * S, h, 22 * S):
            if ((x + y) // (S * 23)) % 3 == 0:
                d.ellipse((x, y, x + S, y + S), fill=(175, 218, 248, 24))
    blend(canvas, layer)


def psp_device(canvas: Image.Image, cx: int, cy: int, scale: float = 1.0) -> None:
    """A minimal, original PSP silhouette with a lit LCD, not Sony artwork."""
    z = S * scale
    pos = lambda x, y: (int(cx + x * z), int(cy + y * z))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((*pos(-99, -40), *pos(99, 40)), radius=int(29 * z),
                        fill=(8, 18, 36, 251), outline=(116, 175, 215, 154), width=max(1, int(1.5 * z)))
    d.rounded_rectangle((*pos(-49, -34), *pos(49, 31)), radius=int(5 * z),
                        fill=(11, 43, 72, 255), outline=(64, 193, 228, 220), width=max(1, int(z)))
    d.rounded_rectangle((*pos(-43, -28), *pos(43, 25)), radius=int(3 * z), fill=(12, 68, 100, 255))
    d.line([pos(-29, 5), pos(30, -14)], fill=(75, 209, 245, 85), width=max(1, int(3 * z)))
    d.ellipse((*pos(-4, -9), *pos(4, -1)), fill=(154, 238, 255, 233))
    # D-pad and face buttons.
    d.rounded_rectangle((*pos(-76, -15), *pos(-69, 12)), radius=max(1, int(z)), fill=(98, 143, 186, 220))
    d.rounded_rectangle((*pos(-85, -6), *pos(-60, 2)), radius=max(1, int(z)), fill=(98, 143, 186, 220))
    for dx, dy in ((71, -13), (83, 0), (59, 0), (71, 13)):
        d.ellipse((*pos(dx - 4, dy - 4), *pos(dx + 4, dy + 4)),
                  fill=(36, 80, 120, 225), outline=(129, 218, 246, 150), width=max(1, int(z)))
    d.ellipse((*pos(-69, 19), *pos(-57, 31)), outline=(121, 163, 207, 135), width=max(1, int(z)))
    d.line([pos(-12, 35), pos(12, 35)], fill=(73, 213, 228, 150), width=max(1, int(z)))
    blend(canvas, layer)


def monitor(canvas: Image.Image, cx: int, cy: int, scale: float = 1.0) -> None:
    z = S * scale
    pos = lambda x, y: (int(cx + x * z), int(cy + y * z))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((*pos(-47, -32), *pos(47, 24)), radius=int(5 * z),
                        fill=(10, 24, 47, 245), outline=(124, 206, 240, 205), width=max(1, int(2 * z)))
    d.rounded_rectangle((*pos(-41, -26), *pos(41, 17)), radius=int(3 * z), fill=(18, 80, 109, 200))
    d.line([pos(-19, 5), pos(21, -12)], fill=(122, 226, 246, 165), width=max(1, int(3 * z)))
    d.line([pos(0, 24), pos(0, 36)], fill=(119, 187, 226, 195), width=max(1, int(2 * z)))
    d.line([pos(-20, 36), pos(20, 36)], fill=(119, 187, 226, 195), width=max(1, int(2 * z)))
    blend(canvas, layer)


def pic1() -> None:
    w, h = 480 * S, 272 * S
    picture = gradient(w, h)
    waves(picture)
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.line([(26 * S, 37 * S), (48 * S, 37 * S)], fill=(87, 235, 225, 245), width=2 * S)
    d.text((26 * S, 53 * S), "PSP", font=font("DejaVuSans-Bold.ttf", 34), fill=(236, 252, 255, 255))
    d.text((29 * S, 95 * S), "D O C K", font=font("DejaVuSans.ttf", 16), fill=(157, 225, 240, 248))
    d.text((29 * S, 133 * S), "TU PSP. TU PC.", font=font("DejaVuSans.ttf", 10), fill=(134, 193, 211, 255))
    d.line([(29 * S, 156 * S), (162 * S, 156 * S)], fill=(104, 190, 218, 100), width=S)
    d.text((29 * S, 173 * S), "JUEGOS  /  GUARDADOS  /  MANDO", font=font("DejaVuSans.ttf", 7),
           fill=(114, 176, 192, 223))
    d.text((29 * S, 244 * S), "UN SOLO DOCK", font=font("DejaVuSans.ttf", 8), fill=(115, 180, 203, 214))
    blend(picture, layer)
    # The device is on the right so XMB game metadata remains readable on the left.
    monitor(picture, 387 * S, 110 * S, .76)
    psp_device(picture, 318 * S, 190 * S, .92)
    wire = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(wire)
    pts = [(329*S,222*S),(345*S,240*S),(384*S,240*S),(409*S,228*S),(419*S,181*S)]
    d.line(pts, fill=(50, 194, 240, 115), width=3*S, joint="curve")
    d.ellipse((413*S,175*S,425*S,187*S), fill=(110, 232, 246, 248))
    blend(picture, wire)
    picture.convert("RGB").resize((480, 272), Image.Resampling.LANCZOS).save(HERE / "PIC1.PNG", optimize=True)


def icon() -> None:
    w, h = 144 * S, 80 * S
    picture = gradient(w, h)
    waves(picture)
    layer = Image.new("RGBA", picture.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((2*S, 2*S, 141*S, 77*S), radius=12*S,
                        outline=(100, 229, 235, 158), width=S)
    d.line([(15*S, 17*S), (40*S, 17*S)], fill=(82, 242, 224, 255), width=2*S)
    d.text((13*S, 22*S), "PSP", font=font("DejaVuSans-Bold.ttf", 21), fill=(242, 251, 255, 255))
    d.text((14*S, 49*S), "D O C K", font=font("DejaVuSans.ttf", 9), fill=(139, 230, 233, 250))
    blend(picture, layer)
    # Compact link glyph to survive XMB thumbnail scaling.
    monitor(picture, 115*S, 33*S, .26)
    psp_device(picture, 99*S, 62*S, .29)
    picture.convert("RGB").resize((144, 80), Image.Resampling.LANCZOS).save(HERE / "ICON0.PNG", optimize=True)


def pic0() -> None:
    source = Image.open(HERE / "PIC1.PNG")
    source.resize((310, 180), Image.Resampling.LANCZOS).save(HERE / "PIC0.PNG", optimize=True)


if __name__ == "__main__":
    pic1()
    icon()
    pic0()
    for name in ("ICON0.PNG", "PIC0.PNG", "PIC1.PNG"):
        p = HERE / name
        print(f"{name} {Image.open(p).size}: {p.stat().st_size} bytes")
