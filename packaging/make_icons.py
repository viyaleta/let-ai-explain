"""Make the app icons from packaging/icons/icon.png (1024x1024).

    pip install pillow
    python packaging/make_icons.py          # icon.png -> icon.icns, icon.ico and the web app icons
    python packaging/make_icons.py --draw   # first redraw the placeholder icon.png

To use your own icon, replace icon.png with a 1024x1024 PNG and run the first command.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent / "icons"
WEB = Path(__file__).resolve().parent.parent / "icons"  # served by the app for "Install app" (PWA)
ACCENT = (43, 76, 130)      # --accent #2B4C82
HIGHLIGHT = (251, 234, 160)  # --hl #FBEAA0
WHITE = (255, 255, 255)


def draw_placeholder(path):
    """A speech bubble on the accent color, with lines of text and one highlighted word."""
    s = 4  # draw large, then scale down for smooth edges
    W = 1024 * s
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # macOS-style tile: 824px rounded square centered in 1024 (Windows/Linux use the same art).
    m = 100 * s
    d.rounded_rectangle((m, m, W - m, W - m), radius=185 * s, fill=ACCENT)
    # Bubble with a tail at the lower left.
    bx0, by0, bx1, by1 = 262 * s, 300 * s, 762 * s, 652 * s
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=96 * s, fill=WHITE)
    d.polygon([(330 * s, 620 * s), (300 * s, 760 * s), (452 * s, 640 * s)], fill=WHITE)
    # Three lines of "text"; part of the middle one highlighted.
    lh = 34 * s
    rows = [(372, 330, 652), (458, 330, 690), (544, 330, 560)]
    for i, (y, x0, x1) in enumerate(rows):
        y, x0, x1 = y * s, x0 * s, x1 * s
        if i == 1:
            d.rounded_rectangle((x0 + 150 * s, y - 14 * s, x0 + 330 * s, y + lh + 14 * s), radius=14 * s, fill=HIGHLIGHT)
        d.rounded_rectangle((x0, y, x1, y + lh), radius=lh // 2, fill=ACCENT)
    img.resize((1024, 1024), Image.LANCZOS).save(path)


def maskable(img):
    """Full-bleed version for Android/Chrome masks: the tile's color fills the square and the
    artwork sits inside the central safe zone (a circle of 80% of the width)."""
    bg = img.getpixel((512, 140))[:3]  # the tile color, just inside its top edge
    out = Image.new("RGBA", (1024, 1024), bg + (255,))
    art = img.crop((100, 100, 924, 924)).resize((700, 700), Image.LANCZOS)  # the tile, scaled into the safe zone
    out.alpha_composite(art, (162, 162))
    return out


def main():
    src = HERE / "icon.png"
    if "--draw" in sys.argv or not src.exists():
        HERE.mkdir(parents=True, exist_ok=True)
        draw_placeholder(src)
    img = Image.open(src).convert("RGBA")
    if img.size != (1024, 1024):
        img = img.resize((1024, 1024), Image.LANCZOS)
    img.save(HERE / "icon.icns")  # macOS: Pillow writes all the standard sizes
    img.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    WEB.mkdir(parents=True, exist_ok=True)
    img.resize((192, 192), Image.LANCZOS).save(WEB / "icon-192.png", optimize=True)
    img.resize((512, 512), Image.LANCZOS).save(WEB / "icon-512.png", optimize=True)
    maskable(img).resize((512, 512), Image.LANCZOS).save(WEB / "icon-maskable-512.png", optimize=True)
    print("Wrote", ", ".join(p.name for p in (src, HERE / "icon.icns", HERE / "icon.ico")), "and icons/*.png")


if __name__ == "__main__":
    main()
