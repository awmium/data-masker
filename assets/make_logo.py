"""Draws the Data Masker mark: a '#' of four slanted gradient strokes and the cursor block, Awmium style.

Writes mark.svg (transparent, for the UI), icon.svg (warm-black tile) and icon.png / icon.ico (window and taskbar).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent
STOPS = [(0, "#5951D8"), (0.2562, "#843AB5"), (0.4975, "#C23584"), (0.7438, "#E2306C"), (1, "#FD1D1D")]
GROUND = "#141311"
CURSOR = "#ECEAE4"

# geometry on a 512 grid; slant follows the monogram's rising diagonal
S = 54
LEAN = 70
VERTS = [
    [(160, 404), (160 + S, 404), (230 + S, 96), (230, 96)],
    [(276, 404), (276 + S, 404), (346 + S, 96), (346, 96)],
]
HORIZ = [
    [(126, 184), (412, 184), (400, 184 + S), (114, 184 + S)],
    [(102, 288), (388, 288), (376, 288 + S), (90, 288 + S)],
]
CURSOR_BOX = (402, 352, 402 + 40, 352 + 52)
GRAD_FROM, GRAD_TO = (90, 420), (430, 90)


def _poly(pts):
    return " ".join(f"{x},{y}" for x, y in pts)


def svg(tile: bool) -> str:
    stops = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in STOPS)
    shapes = "".join(f'<polygon points="{_poly(p)}" fill="url(#dm-g)"/>' for p in VERTS + HORIZ)
    x0, y0, x1, y1 = CURSOR_BOX
    cursor = f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="{CURSOR if tile else "currentColor"}"/>'
    bg = f'<rect width="512" height="512" rx="112" fill="{GROUND}"/>' if tile else ""
    view = "0 0 512 512" if tile else "86 92 360 316"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" role="img" aria-label="Data Masker">'
        f'<defs><linearGradient id="dm-g" gradientUnits="userSpaceOnUse" x1="{GRAD_FROM[0]}" y1="{GRAD_FROM[1]}" '
        f'x2="{GRAD_TO[0]}" y2="{GRAD_TO[1]}">{stops}</linearGradient></defs>{bg}{shapes}{cursor}</svg>\n'
    )


def _hex(c):
    return np.array([int(c[i:i + 2], 16) for i in (1, 3, 5)], float)


def png(size: int) -> Image.Image:
    k = 4
    n = size * k
    f = n / 512
    yy, xx = np.mgrid[0:n, 0:n] / f
    d = np.array(GRAD_TO, float) - np.array(GRAD_FROM, float)
    t = np.clip(((xx - GRAD_FROM[0]) * d[0] + (yy - GRAD_FROM[1]) * d[1]) / (d @ d), 0, 1)
    offs = [o for o, _ in STOPS]
    cols = np.stack([_hex(c) for _, c in STOPS])
    grad = np.stack([np.interp(t, offs, cols[:, i]) for i in range(3)], axis=-1).astype(np.uint8)

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    tile = Image.new("L", (n, n), 0)
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, n - 1, n - 1], radius=112 * f, fill=255)
    img.paste(Image.new("RGBA", (n, n), tuple(int(v) for v in _hex(GROUND)) + (255,)), mask=tile)

    mask = Image.new("L", (n, n), 0)
    md = ImageDraw.Draw(mask)
    for p in VERTS + HORIZ:
        md.polygon([(x * f, y * f) for x, y in p], fill=255)
    img.paste(Image.fromarray(grad, "RGB").convert("RGBA"), mask=mask)
    x0, y0, x1, y1 = CURSOR_BOX
    ImageDraw.Draw(img).rectangle([x0 * f, y0 * f, x1 * f, y1 * f], fill=tuple(int(v) for v in _hex(CURSOR)) + (255,))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    (HERE / "mark.svg").write_text(svg(False), encoding="utf-8")
    (HERE / "icon.svg").write_text(svg(True), encoding="utf-8")
    big = png(512)
    big.save(HERE / "icon.png")
    big.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("written:", ", ".join(p.name for p in sorted(HERE.glob("icon.*")) + [HERE / "mark.svg"]))
