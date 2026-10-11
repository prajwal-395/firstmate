"""Compare the S2 arms-d FFrames frames against the Remotion frames.

usage: compare.py <out_dir>  -> prints a table, writes metrics.json
       and sbs_<case>_<frame>.png side-by-sides.

The compositing both sides are measured on: FFrames frames are
PREMULTIPLIED (render_one_card_frames premultiplies overlays) while the
Remotion sequence PNGs are STRAIGHT alpha - each side is composited in
its own format over the same mid-grey (the trap real-b states: reading
both as premultiplied paints every translucent Remotion pixel too
bright and corrupts every fade-adjacent number).

  ink IoU   - IoU of the alpha>128 masks (geometry: same object, same
              place, same wrap).
  SSIM/PSNR - on both frames composited over mid-grey (settled frames).
  opacity   - mean alpha FF/RM over the alpha union (ramp frames).
  premult   - every channel <= alpha in the FFrames frame.
  curves    - per-frame ink px on both sides plus IoU, in metrics.json
              (the stagger check: steps must land on the same frames).
"""
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synth_props  # noqa: E402

GREY = np.array([128, 128, 128], dtype=np.float64)


def load_ff(path):
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64)
    rgb, alpha = a[..., :3], a[..., 3] / 255.0
    return rgb, alpha, True


def load_rm(path):
    a = np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64)
    rgb, alpha = a[..., :3], a[..., 3] / 255.0
    return rgb, alpha, False


def composite(rgb, alpha, premultiplied):
    a = alpha[..., None]
    if premultiplied:
        return rgb + GREY * (1.0 - a)
    return rgb * a + GREY * (1.0 - a)


def frame_paths(case_dir, case, engine, count):
    # Both sides carry production's 1-based canonical names; local
    # frame 0 is frame_000001.png.
    d = os.path.join(case_dir, f"{case}_{engine}")
    return [os.path.join(d, f"frame_{i + 1:06d}.png") for i in range(count)]


def count_frames(case_dir, case, engine):
    d = os.path.join(case_dir, f"{case}_{engine}")
    return len([f for f in os.listdir(d) if f.endswith(".png")])


def metrics_for(case_dir, case):
    n_ff = count_frames(case_dir, case, "ff")
    n_rm = count_frames(case_dir, case, "rm")
    assert n_ff == n_rm, f"{case}: ff={n_ff} rm={n_rm}"
    curves = []
    premult_ok = True
    for i in range(n_ff):
        rgb_f, al_f, _ = load_ff(frame_paths(case_dir, case, "ff", n_ff)[i])
        rgb_r, al_r, _ = load_rm(frame_paths(case_dir, case, "rm", n_rm)[i])
        ink_f = al_f > 128 / 255
        ink_r = al_r > 128 / 255
        union = int((ink_f | ink_r).sum())
        inter = int((ink_f & ink_r).sum())
        curves.append({
            "frame": i,
            "ink_ff": int(ink_f.sum()),
            "ink_rm": int(ink_r.sum()),
            "iou": (inter / union) if union else 1.0,
            "mean_alpha_ff": float(al_f.mean()),
            "mean_alpha_rm": float(al_r.mean()),
        })
        if bool((rgb_f > al_f[..., None] * 255 + 1).any()):
            premult_ok = False
    return n_ff, curves, premult_ok


def settled_frame(case_dir, case, n):
    # Mid-life, past every entrance and before every exit.
    return n // 2


def compare_settled(case_dir, case, n):
    from skimage.metrics import structural_similarity as ssim
    i = settled_frame(case_dir, case, n)
    rgb_f, al_f, _ = load_ff(frame_paths(case_dir, case, "ff", n)[i])
    rgb_r, al_r, _ = load_rm(frame_paths(case_dir, case, "rm", n)[i])
    comp_f = composite(rgb_f, al_f, True)
    comp_r = composite(rgb_r, al_r, False)
    ink_f = al_f > 128 / 255
    ink_r = al_r > 128 / 255
    union = (ink_f | ink_r)
    inter = (ink_f & ink_r)
    iou = float(inter.sum()) / union.sum() if union.sum() else 1.0
    data_range = 255.0
    s = ssim(comp_f.astype(np.float64), comp_r.astype(np.float64),
             channel_axis=2, data_range=data_range)
    mse = float(((comp_f - comp_r) ** 2).mean())
    import math
    psnr = (10 * math.log10(data_range ** 2 / mse)) if mse > 0 else float("inf")
    mask = (al_f > 8 / 255) | (al_r > 8 / 255)
    ratio = (float(al_f[mask].mean()) / float(al_r[mask].mean())
             if mask.any() and al_r[mask].mean() > 0 else None)
    # Side-by-side: RM left, FF right, cropped to the union ink bbox.
    ys, xs = np.nonzero(union)
    if len(xs):
        pad = 8
        x0, x1 = max(0, xs.min() - pad), min(comp_f.shape[1], xs.max() + pad)
        y0, y1 = max(0, ys.min() - pad), min(comp_f.shape[0], ys.max() + pad)
    else:
        x0, x1, y0, y1 = 0, comp_f.shape[1], 0, comp_f.shape[0]
    pair = np.concatenate([comp_r[y0:y1, x0:x1], comp_f[y0:y1, x0:x1]], axis=1)
    Image.fromarray(pair.astype(np.uint8)).save(
        os.path.join(case_dir, f"sbs_{case}_f{i}.png"))
    return {"frame": i, "ink_iou": round(iou, 4),
            "ssim": round(float(s), 4),
            "psnr_db": round(psnr, 1) if psnr != float("inf") else "inf",
            "opacity_ff_rm": round(ratio, 3) if ratio is not None else None}


def main():
    out_dir = sys.argv[1]
    results = {}
    for case in synth_props.CASES:
        n, curves, premult_ok = metrics_for(out_dir, case)
        settled = compare_settled(out_dir, case, n)
        settled["frames"] = n
        settled["premult_ff"] = premult_ok
        settled["curves"] = curves
        results[case] = settled
        print(f"{case} f{settled['frame']}: ink_iou={settled['ink_iou']} "
              f"ssim={settled['ssim']} psnr={settled['psnr_db']} "
              f"opacity={settled['opacity_ff_rm']} premult={premult_ok}")
    with open(os.path.join(out_dir, "metrics.json"), "w",
              encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print("wrote metrics.json")


if __name__ == "__main__":
    main()
