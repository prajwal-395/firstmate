"""Side-by-side of HF (left) and FF (right) for one composition, cropped.

usage: view.py <out_dir> <composition> [dest.png]
Frames are premultiplied (overlays) or opaque (FullFrameCard); both are
composited over the same mid-grey ground so dark outlines stay visible.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

out_dir, name = sys.argv[1:3]
dest = sys.argv[3] if len(sys.argv) > 3 else os.path.join(out_dir, f"{name}_sbs.png")
GROUND = np.array([90, 96, 110], dtype=np.float32)


def load(engine):
    a = np.asarray(Image.open(os.path.join(out_dir, f"{name}_{engine}.png")).convert("RGBA"),
                   dtype=np.float32)
    alpha = a[..., 3:4] / 255.0
    rgb = a[..., :3] + GROUND * (1 - alpha)  # premultiplied over
    return np.clip(rgb, 0, 255).astype(np.uint8), a[..., 3]


hf, ha = load("hf")
ff, fa = load("ff")
mask = (ha > 8) | (fa > 8)
if name == "FullFrameCard":
    bg = np.array([0x1A, 0x1A, 0x2E])
    mask = (np.abs(hf.astype(int) - bg).sum(-1) > 30) | (np.abs(ff.astype(int) - bg).sum(-1) > 30)
ys, xs = np.nonzero(mask)
pad = 40
y0, y1 = max(0, ys.min() - pad), min(hf.shape[0], ys.max() + pad)
x0, x1 = max(0, xs.min() - pad), min(hf.shape[1], xs.max() + pad)
crops = [Image.fromarray(img[y0:y1, x0:x1]) for img in (hf, ff)]
w, h = crops[0].size
scale = max(1, min(3, 1000 // max(w, 1)))
crops = [c.resize((w * scale, h * scale), Image.NEAREST) for c in crops]
W2, H2 = crops[0].size
canvas = Image.new("RGB", (W2 * 2 + 20, H2 + 40), (255, 255, 255))
canvas.paste(crops[0], (0, 40))
canvas.paste(crops[1], (W2 + 20, 40))
d = ImageDraw.Draw(canvas)
d.text((8, 10), f"{name}  HyperFrames  crop ({x0},{y0})-({x1},{y1})", fill=(0, 0, 0))
d.text((W2 + 28, 10), f"{name}  FFrames", fill=(0, 0, 0))
canvas.save(dest)
print(dest)
