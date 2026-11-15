"""Brand icon: a glowing ring (JARVIS-style) rendered with Pillow. Status variants recolor the ring."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

COLORS = {
    "idle": (0, 200, 255),        # cyan
    "listening": (255, 80, 80),   # red
    "transcribing": (255, 200, 0),
    "thinking": (170, 120, 255),  # violet
    "acting": (80, 255, 140),     # green
    "error": (255, 140, 0),
    "off": (110, 110, 110),
}


def render(size: int = 256, status: str = "idle") -> Image.Image:
    col = COLORS.get(status, COLORS["idle"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    # dark disc
    d.ellipse((size * 0.06, size * 0.06, size * 0.94, size * 0.94), fill=(12, 18, 28, 255))
    # outer glow rings
    for i, a in ((0.10, 90), (0.14, 160), (0.18, 255)):
        r = size * (0.44 - i * 0.6)
        d.ellipse((c - r, c - r, c + r, c + r), outline=(*col, a), width=max(2, size // 40))
    # core
    r = size * 0.17
    d.ellipse((c - r, c - r, c + r, c + r), fill=(*col, 255))
    r2 = size * 0.08
    d.ellipse((c - r2, c - r2, c + r2, c + r2), fill=(235, 250, 255, 255))
    # tick marks
    import math
    for k in range(12):
        ang = k * math.pi / 6
        r_in, r_out = size * 0.34, size * 0.40
        d.line((c + r_in * math.cos(ang), c + r_in * math.sin(ang), c + r_out * math.cos(ang), c + r_out * math.sin(ang)),
               fill=(*col, 200), width=max(1, size // 64))
    return img


def write_ico(path: Path, status: str = "idle") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    base = render(256, status)
    base.save(path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    return path


if __name__ == "__main__":
    import sys
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("launcher/jarvis.ico")
    print(write_ico(out))
