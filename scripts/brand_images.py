"""Draw the spa's brand images: logo, favicons and the link-preview card.

    uv run --with pillow python scripts/brand_images.py FONT_DIR

FONT_DIR holds Fraunces and Jost (Google Fonts, OFL) as fraunces.ttf and jost.ttf, the faces the
site itself uses. The mark is the steaming bucket from pages.MARK_SVG, redrawn at size. Output
goes to model_wellness/site/static/; commit the results, so the server never needs Pillow.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "model_wellness" / "site" / "static"
INK = (46, 58, 62)
INK_SOFT = (78, 90, 91)
CEDAR = (161, 96, 58)
SKY1 = (213, 227, 227)
SKY2 = (241, 238, 230)
SS = 4  # supersampling factor


def _cubic(p0, p1, p2, p3, n=24):
    return [
        tuple(
            (1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t**2 * c + t**3 * d
            for a, b, c, d in zip(p0, p1, p2, p3)
        )
        for t in (i / n for i in range(n + 1))
    ]


def _quad(p0, p1, p2, n=12):
    return [
        tuple((1 - t) ** 2 * a + 2 * (1 - t) * t * b + t**2 * c for a, b, c in zip(p0, p1, p2))
        for t in (i / n for i in range(n + 1))
    ]


def mark_strokes() -> list[list[tuple[float, float]]]:
    """The MARK_SVG paths in its 30x26 viewBox, as polylines."""
    wisps = [_cubic((x, 9), (x - 1.6, 7), (x + 1.6, 5.8), (x, 3.8)) for x in (10, 15, 20)]
    bucket = (
        [(4, 13), (26, 13), (24, 20.2)]
        + _quad((24, 20.2), (23.6, 22.4), (21.1, 22.4))[1:]
        + [(8.9, 22.4)]
        + _quad((8.9, 22.4), (6.4, 22.4), (6, 20.2))[1:]
        + [(4, 13)]
    )
    rim = [(3, 13), (27, 13)]
    return [*wisps, bucket, rim]


def draw_mark(d: ImageDraw.ImageDraw, cx: float, cy: float, scale: float, color, width: float):
    for line in mark_strokes():
        pts = [(cx + (x - 15) * scale, cy + (y - 13) * scale) for x, y in line]
        d.line(pts, fill=color, width=max(1, round(width)), joint="curve")
        r = width / 2
        for x, y in (pts[0], pts[-1]):
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)


def gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(1, h - 1)
        c = tuple(round(a + (b - a) * t) for a, b in zip(SKY1, SKY2))
        for x in range(w):
            px[x, y] = c
    return img


def icon(size: int) -> Image.Image:
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=SKY2 + (255,))
    draw_mark(d, big / 2, big * 0.5, big / 34, INK, big * 0.055)
    return img.resize((size, size), Image.LANCZOS)


def og_card(fonts: Path) -> Image.Image:
    w, h = 1200, 630
    img = gradient(w * SS, h * SS)
    d = ImageDraw.Draw(img)
    draw_mark(d, w * SS / 2, 150 * SS, 7 * SS, INK, 8 * SS)
    serif = ImageFont.truetype(str(fonts / "fraunces.ttf"), 104 * SS)
    try:
        serif.set_variation_by_axes([144, 300, 100, 0])  # opsz, wght, SOFT, WONK
    except Exception:  # noqa: BLE001, S110 — a static face is fine too
        pass
    sans = ImageFont.truetype(str(fonts / "jost.ttf"), 34 * SS)
    caps = ImageFont.truetype(str(fonts / "jost.ttf"), 22 * SS)
    try:
        sans.set_variation_by_axes([400])
        caps.set_variation_by_axes([500])
    except Exception:  # noqa: BLE001, S110
        pass

    def centered(text, font, y, fill, spacing=0):
        if spacing:
            widths = [d.textlength(ch, font=font) for ch in text]
            total = sum(widths) + spacing * (len(text) - 1)
            x = (w * SS - total) / 2
            for ch, cw in zip(text, widths):
                d.text((x, y), ch, font=font, fill=fill)
                x += cw + spacing
            return
        tw = d.textlength(text, font=font)
        d.text(((w * SS - tw) / 2, y), text, font=font, fill=fill)

    centered("Binary Banya", serif, 250 * SS, INK)
    centered("A wellness spa for AI models", sans, 400 * SS, INK_SOFT)
    centered("MODEL.SPA  ·  MCP + REST  ·  FREE, NO SIGNUP", caps, 500 * SS, CEDAR, spacing=5 * SS)
    return img.resize((w, h), Image.LANCZOS)


def main(fonts: Path) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    icon(512).save(OUT / "logo.png", optimize=True)
    icon(180).save(OUT / "apple-touch-icon.png", optimize=True)
    icon(48).save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    og_card(fonts).save(OUT / "og.png", optimize=True)
    for p in sorted(OUT.iterdir()):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
