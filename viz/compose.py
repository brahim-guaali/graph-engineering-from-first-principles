"""Stack two replayed timelines (same clock) into one frame, with labels. Used for the comparison GIF.

    python viz/compose.py var/frames3/tl-loop var/frames3/tl-supersteps var/frames3/compare 95
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

top_dir, bottom_dir, out_dir, end = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])
out_dir.mkdir(parents=True, exist_ok=True)
font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 30)
small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
frames = sorted(top_dir.glob("f*.png"))
for i, f in enumerate(frames):
    a, b = Image.open(f), Image.open(bottom_dir / f.name)
    w, bar = max(a.width, b.width), 56
    img = Image.new("RGB", (w, bar * 2 + a.height + b.height + 40), "#f4f4f2")
    d = ImageDraw.Draw(img)
    t = end * i / (len(frames) - 1)
    d.text((24, 12), "Stage 1 · agent loop: lookups one after another", font=font, fill="#0b0b0b")
    d.text((w - 24, 16), f"t = {t:4.0f} s", font=small, fill="#52514e", anchor="ra")
    img.paste(a, (0, bar))
    y = bar + a.height + 20
    d.text((24, y + 12), "Stage 3 · supersteps: the three lookups in one round", font=font, fill="#0b0b0b")
    img.paste(b, (0, y + bar))
    img.save(out_dir / f.name)
print(len(frames), "frames")
