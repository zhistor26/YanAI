"""Generate 400x400 LPK icon.png for YanAI."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "icon.png"
SIZE = 400


def main() -> None:
    im = Image.new("RGB", (SIZE, SIZE), "#fff7ed")
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((40, 40, 360, 360), radius=48, fill="#f97316")
    draw.rounded_rectangle((96, 96, 304, 304), radius=36, fill="#fffbeb")

    try:
        font = ImageFont.truetype("arial.ttf", 120)
    except OSError:
        font = ImageFont.load_default()

    draw.text((118, 118), "颜", fill="#9a3412", font=font)
    im.save(OUT, format="PNG", optimize=True, compress_level=9)
    print(f"icon -> {OUT.name} ({OUT.stat().st_size} bytes, {SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
