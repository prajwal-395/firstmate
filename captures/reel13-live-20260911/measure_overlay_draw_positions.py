"""Where every overlay on a captured timeline actually DRAWS, from pixels."""
import json, subprocess, sys, os, tempfile
import numpy as np
from PIL import Image

CAP, DIRS, OUT = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
GAIN = 2.0            # measured draw gain on 1080x1920 (tight_box.DRAW_GAIN_1080x1920)
INTENT_BOTTOM = 1589  # 1920 - safe bottom inset 320 - CAPTION_LIFT_PX 11

d = json.load(open(CAP))
TW = int(d["timeline_settings"]["timelineResolutionWidth"])
TH = int(d["timeline_settings"]["timelineResolutionHeight"])

def find(name):
    for root in DIRS:
        for dirpath, _dn, files in os.walk(root):
            if name in files:
                return os.path.join(dirpath, name)
    return None

rows = []
tmp = tempfile.mkdtemp()
for t in d["tracks"]:
    if t["type"] != "video" or t["index"] not in (4, 5):
        continue
    for it in t["items"]:
        mpi = it["media_pool_item"] or {}
        src = mpi.get("file_path") or ""
        base = os.path.basename(src)
        path = src if os.path.exists(src) else find(base)
        if not path:
            rows.append({"track": t["name"], "item": base, "error": "artefact not found"})
            continue
        try:
            dur = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", path], capture_output=True,
                encoding="utf-8", check=True).stdout.strip())
        except Exception as e:
            rows.append({"track": t["name"], "item": base, "error": str(e)}); continue
        png = os.path.join(tmp, "f.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "%.3f" % (dur / 2),
                        "-i", path, "-frames:v", "1", "-c:v", "png",
                        "-pix_fmt", "rgba", png], check=True)
        a = np.array(Image.open(png).convert("RGBA"))[:, :, 3]
        ch, cw = a.shape
        ys, xs = np.where(a > 8)
        r = {"track": t["name"], "record_start": it["record_start_frame"],
             "item": base, "canvas": f"{cw}x{ch}",
             "tilt": it["properties"]["Tilt"], "pan": it["properties"]["Pan"],
             "zoom_y": it["properties"]["ZoomY"],
             "carriage": "full-frame" if (cw, ch) == (TW, TH) else "tight"}
        if len(ys) == 0:
            r["error"] = "no ink in the sampled frame"
            rows.append(r); continue
        r["ink_top_in_canvas"] = int(ys.min())
        r["ink_bottom_in_canvas"] = int(ys.max())
        # Scaling=1 draws native pixels centred; Tilt shifts by
        # -Tilt * (canvas_h / timeline_h) * GAIN  (positive Tilt = up)
        shift_y = -float(r["tilt"] or 0.0) * (ch / float(TH)) * GAIN
        canvas_top = TH / 2.0 - ch / 2.0 + shift_y
        r["screen_ink_top"] = round(canvas_top + ys.min(), 1)
        r["screen_ink_bottom"] = round(canvas_top + ys.max(), 1)
        r["off_bottom_edge"] = bool(r["screen_ink_bottom"] > TH)
        if t["index"] == 4:
            r["intent_bottom"] = INTENT_BOTTOM
            r["error_px_vs_intent"] = round(r["screen_ink_bottom"] - INTENT_BOTTOM, 1)
        rows.append(r)

json.dump({"timeline": d["timeline"], "frame": [TW, TH], "draw_gain": GAIN,
           "intent_caption_bottom": INTENT_BOTTOM, "overlays": rows},
          open(OUT, "w", encoding="utf-8"), indent=2)
print("wrote", OUT, "rows", len(rows))
