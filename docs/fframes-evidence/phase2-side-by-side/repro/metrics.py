"""Frame-matched parity checks between the HyperFrames and FFrames frames.

usage: metrics.py <out_dir>  -> prints a table, writes metrics.json

Mean pixel error over a mostly-empty frame says nothing, so every check
here is taken INSIDE the text and is built to fail on the defects seen
before: words out of order or on the wrong line, a hairline face, a
missing outline, shadow or active-word colour, and a broken alpha.

  words      - per-word glyph boxes in reading order (lines from the row
               profile, words from column gaps wider than 0.2 of the
               line's ink height), matched one to one; worst centre
               offset in px.
  word IoU   - glyph-mask IoU inside each HyperFrames word box: does the
               same word sit in the same place at the same weight there
               (robust to where a 1px-different gap splits words).
  coverage   - bright fill pixels / text bbox area (glyph weight).
  outline    - opaque dark pixels (alpha>200, luma<60) touching the text.
  shadow     - soft dark pixels (8<alpha<=200, luma<60).
  accent     - fill pixels in the accent's colour (warm, light yellow).
  ink IoU    - IoU of the alpha>128 masks.
  alpha      - premultiplied invariant (every channel <= alpha) holds.
  opacity    - mean alpha ratio FF/HF over the union bbox (fade frames).
"""
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
from props import ALL  # noqa: E402

out_dir = sys.argv[1]
GROUND = np.array([0x1A, 0x1A, 0x2E])


def load(name, engine, suffix=""):
    a = np.asarray(Image.open(os.path.join(out_dir, f"{name}{suffix}_{engine}.png")).convert("RGBA"),
                   dtype=np.int32)
    rgb, alpha = a[..., :3], a[..., 3]
    if name == "FullFrameCard":  # opaque over its ground: ink is what differs from it
        diff = np.abs(rgb - GROUND).sum(-1)
        alpha = np.clip(diff * 3, 0, 255)
        rgb = np.where(alpha[..., None] > 0, rgb, 0)
        return rgb, alpha, True
    return rgb, alpha, False


def straight(rgb, alpha):
    a = np.maximum(alpha, 1)[..., None]
    return np.clip(rgb * 255 // a, 0, 255)


def luma(rgb):
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def segments(profile, min_gap):
    on = profile > 0
    runs, start, gap = [], None, 0
    for i, v in enumerate(on):
        if v:
            if start is None:
                start = i
            gap = 0
            end = i
        elif start is not None:
            gap += 1
            if gap > min_gap:
                runs.append((start, end))
                start, gap = None, 0
    if start is not None:
        runs.append((start, end))
    return runs


def word_boxes(fill):
    """Word boxes in reading order: lines from the row profile, words
    from column gaps wider than 0.2 of the line's ink height."""
    boxes = []
    for y0, y1 in segments(fill.sum(1), 3):
        line = fill[y0:y1 + 1]
        lh = y1 - y0 + 1
        for x0, x1 in segments(line.sum(0), max(3, int(0.2 * lh))):
            ys = np.nonzero(line[:, x0:x1 + 1].sum(1))[0]
            boxes.append((x0, y0 + int(ys.min()), x1, y0 + int(ys.max())))
    return boxes


def measure(name, engine, suffix=""):
    rgb, alpha, opaque = load(name, engine, suffix)
    s = rgb if opaque else straight(rgb, alpha)
    L = luma(s)
    fill = (alpha > 200) & (L > 140)
    dark_opaque = (alpha > 200) & (L < 60)
    dark_soft = (alpha > 8) & (alpha <= 200) & (L < 60)
    accent = fill & (s[..., 0] > 225) & (s[..., 1] > 215) & (s[..., 2] > 140) & (s[..., 2] < 215)
    ys, xs = np.nonzero(fill)
    bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None
    area = (bbox[2] - bbox[0] + 1) * (bbox[3] - bbox[1] + 1) if bbox else 1
    premult_ok = True if opaque else bool((rgb <= alpha[..., None]).all())
    glyph = (alpha > 96) & (L > 110)
    return dict(rgb=rgb, alpha=alpha, glyph=glyph, words=word_boxes(glyph), bbox=bbox,
                coverage=float(fill.sum()) / area, outline=int(dark_opaque.sum()),
                shadow=int(dark_soft.sum()), accent=int(accent.sum()), premult=premult_ok)


def compare(name, suffix=""):
    hf, ff = measure(name, "hf", suffix), measure(name, "ff", suffix)
    out = {"frame": suffix or "_f45"}
    out["hf_words"], out["ff_words"] = len(hf["words"]), len(ff["words"])
    if hf["words"] and len(hf["words"]) == len(ff["words"]):
        offs = []
        for a, b in zip(hf["words"], ff["words"]):
            ca = ((a[0] + a[2]) / 2, (a[1] + a[3]) / 2)
            cb = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
            offs.append(max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1])))
        out["word_offset_max_px"] = round(max(offs), 1)
        out["word_order_match"] = True
    else:
        out["word_offset_max_px"] = None
        out["word_order_match"] = False
    ious = []
    for x0, y0, x1, y1 in hf["words"]:
        a = hf["glyph"][y0 - 1:y1 + 2, x0 - 1:x1 + 2]
        b = ff["glyph"][y0 - 1:y1 + 2, x0 - 1:x1 + 2]
        union = (a | b).sum()
        ious.append(float((a & b).sum()) / union if union else 1.0)
    out["word_iou_min"] = round(min(ious), 3) if ious else None
    out["word_iou_mean"] = round(sum(ious) / len(ious), 3) if ious else None
    out["hf_bbox"], out["ff_bbox"] = hf["bbox"], ff["bbox"]
    out["coverage_hf"], out["coverage_ff"] = round(hf["coverage"], 3), round(ff["coverage"], 3)
    for k in ("outline", "shadow", "accent"):
        out[f"{k}_hf"], out[f"{k}_ff"] = hf[k], ff[k]
    ink_h, ink_f = hf["alpha"] > 128, ff["alpha"] > 128
    union = (ink_h | ink_f).sum()
    out["ink_iou"] = round(float((ink_h & ink_f).sum()) / union, 3) if union else None
    mask = (hf["alpha"] > 8) | (ff["alpha"] > 8)
    out["opacity_ratio"] = round(float(ff["alpha"][mask].mean()) / max(1e-6, float(hf["alpha"][mask].mean())), 3) if mask.any() else None
    out["alpha_premultiplied_hf"], out["alpha_premultiplied_ff"] = hf["premult"], ff["premult"]
    return out


results = {}
for name in ALL:
    results[name] = [compare(name, "_f3"), compare(name)]
json.dump(results, open(os.path.join(out_dir, "metrics.json"), "w"), indent=1)
cols = ["frame", "hf_words", "ff_words", "word_order_match", "word_offset_max_px", "word_iou_min", "word_iou_mean", "coverage_hf",
        "coverage_ff", "outline_hf", "outline_ff", "shadow_hf", "shadow_ff", "accent_hf",
        "accent_ff", "ink_iou", "opacity_ratio", "alpha_premultiplied_ff"]
for name, rows in results.items():
    for r in rows:
        print(name, " ".join(f"{c}={r[c]}" for c in cols))
