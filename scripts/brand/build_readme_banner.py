"""Generate the PSP Dock README hero using the approved PSP Dock XMB logo.

Only the source PNG from this repository is used.  The background is drawn
locally; no Sony artwork, game assets, or network download is required.
Run with Python + Pillow: python scripts/brand/build_readme_banner.py
"""
from __future__ import annotations

from math import pi, sin
from pathlib import Path
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
LOGO = ROOT / "homebrew/native-dock/assets/PIC0.PNG"
W, H = 1600, 520
COPY = {
    "en": {
        "filename": "readme-hero.png",
        "eyebrow": "PSP REIMAGINED",
        "tagline": "ONE CABLE. A NEW WAY TO PLAY.",
        "title1": "Your PSP deserves",
        "title2": "the big screen.",
        "subtitle": "Your favorite games. A fresh way to play.",
        "chips": [
            ("XMB on your PC", 663, 401, 238),
            ("Your PSP, your controller", 918, 401, 296),
            ("Your saves go with you", 1232, 401, 316),
        ],
    },
    "es": {
        "filename": "readme-hero-es.png",
        "eyebrow": "TU PSP, MÁS VIVA",
        "tagline": "UN CABLE. OTRA FORMA DE JUGAR.",
        "title1": "Tu PSP se merece",
        "title2": "una pantalla grande.",
        "subtitle": "Tus juegos de siempre. Una experiencia nueva.",
        "chips": [
            ("XMB en el PC", 663, 401, 238),
            ("Tu PSP es el mando", 918, 401, 296),
            ("Tus partidas, contigo", 1232, 401, 316),
        ],
    },
}


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    file = "segoeuib.ttf" if bold else "segoeui.ttf"
    candidate = Path("C:/Windows/Fonts") / file
    if candidate.is_file():
        return ImageFont.truetype(str(candidate), size)
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def make_banner(locale: str) -> None:
    copy = COPY[locale]
    # Deep ocean/ink palette from the PSP Dock application, not a screenshot.
    base = Image.new("RGB", (W, H))
    pixels = base.load()
    for y in range(H):
        for x in range(W):
            t = x / W
            v = y / H
            # Gentle diagonal blue-teal gradient with controlled contrast.
            r = int(3 + 4 * t + 2 * (1 - v))
            g = int(17 + 18 * t + 6 * (1 - v))
            b = int(31 + 24 * t + 8 * (1 - v))
            pixels[x, y] = (r, g, b)

    # Large soft glows keep the hero premium without sacrificing legibility.
    glow = Image.new("RGBA", (W, H))
    g = ImageDraw.Draw(glow)
    g.ellipse((-200, 180, 740, 910), fill=(10, 214, 207, 53))
    g.ellipse((1090, -380, 1930, 430), fill=(24, 132, 172, 68))
    glow = glow.filter(ImageFilter.GaussianBlur(115))
    base = Image.alpha_composite(base.convert("RGBA"), glow)

    lines = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(lines)
    # Original abstract waves, not the Sony XMB graphic.
    for k in range(5):
        points = [
            (x, int(380 + k * 31 + 32 * sin((x / W) * 2.55 * pi + k * .62)))
            for x in range(0, W + 1, 5)
        ]
        d.line(points, fill=(82, 244, 223, max(0, 90 - k * 17)), width=2)
    for k in range(4):
        points = [
            (x, int(130 + k * 24 + 47 * sin((x / W) * 2 * pi + k * .46)))
            for x in range(890, W + 1, 5)
        ]
        d.line(points, fill=(73, 172, 215, max(0, 34 - k * 6)), width=2)
    random.seed(6)
    for _ in range(125):
        x = random.randint(12, W - 12)
        y = random.randint(12, H - 12)
        if x > 600 and 90 < y < 365:
            continue
        d.ellipse((x, y, x + 1, y + 1), fill=(177, 229, 237, random.randint(14, 45)))
    base = Image.alpha_composite(base, lines)

    overlay = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((56, 69, 550, 448), radius=38,
                        fill=(2, 9, 18, 137), outline=(95, 224, 220, 50), width=2)
    d.rounded_rectangle((97, 98, 253, 132), radius=16, fill=(14, 133, 144, 95))
    d.text((116, 104), copy["eyebrow"], font=font(15, bold=True),
           fill=(171, 252, 235, 238))
    logo = Image.open(LOGO).convert("RGBA")
    logo.thumbnail((405, 225), Image.Resampling.LANCZOS)
    overlay.alpha_composite(logo, (int(303 - logo.width / 2), 150))
    d.text((119, 386), copy["tagline"], font=font(18, bold=True),
           fill=(173, 212, 223, 225))
    # A little brand accent and a single readable marketing message.
    d.rounded_rectangle((598, 116, 608, 327), radius=5, fill=(25, 224, 208, 235))
    d.text((660, 112), copy["title1"], font=font(65, bold=True),
           fill=(247, 253, 255, 255))
    d.text((660, 193), copy["title2"], font=font(65, bold=True),
           fill=(100, 249, 224, 255))
    d.text((663, 297), copy["subtitle"], font=font(27),
           fill=(207, 232, 239, 244))
    chips = copy["chips"]
    for label, x1, y1, width in chips:
        d.rounded_rectangle((x1, y1, x1 + width, y1 + 52), radius=23,
                            fill=(12, 61, 79, 213), outline=(65, 178, 179, 102))
        d.text((x1 + 21, y1 + 13), label, font=font(19, bold=True),
               fill=(232, 249, 252, 255))
    base = Image.alpha_composite(base, overlay).convert("RGB")
    output = ROOT / "assets" / copy["filename"]
    output.parent.mkdir(parents=True, exist_ok=True)
    base.save(output, optimize=True)
    print(f"Banner {locale}: {output} ({base.width}x{base.height}, {output.stat().st_size:,} bytes)")


if __name__ == "__main__":
    for language in COPY:
        make_banner(language)
