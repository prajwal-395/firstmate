"""Where every overlay on a captured timeline actually DRAWS, from pixels.

Kept in the pipeline (moved out of a per-project captures/ drop zone):
given any `capture_timeline.py` state file plus search roots for the
overlay artefacts, it reports each overlay's canvas, stored transform,
ink box inside its own artefact, and where that ink lands on the
delivery frame against the caption-row intent.  The gain and the intent
row are read off the engine's own constants for the capture's frame
size - never hardcoded for one incident's 1080x1920 reels.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if os.environ.get("REPO"):
    sys.path.insert(0, os.environ["REPO"])
elif str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from library.tools.safe_area import safe_area_for_frame
from library.tools.subtitle_style import (
    CAPTION_LIFT_PX, caption_row_px, project_caption_row)
from library.tools.tight_box import draw_gain

import numpy as np
from PIL import Image

CAP, DIRS, OUT = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
#: Optional 4th argument: the PROJECT whose caption row these overlays
#: were rendered to. Without it this grades against the engine's own
#: row, which is the wrong ruler for any project that declares one -
#: it would report every correctly placed caption as 220px out.
PROJECT = sys.argv[4] if len(sys.argv) > 4 else None

d = json.load(open(CAP))
TW = int(d["timeline_settings"]["timelineResolutionWidth"])
TH = int(d["timeline_settings"]["timelineResolutionHeight"])

# The measured draw gain for THIS capture's frame
# (tight_box.draw_gain: 2.0 on the probed 1080x1920, 1.0 elsewhere).
GAIN = draw_gain(TW, TH)
# The caption row the engine intends: frame bottom, minus the safe-area
# bottom inset for this frame size, lifted by CAPTION_LIFT_PX.
# On 1080x1920 that is 1920 - 320 - 1 = 1599.
#
# A project may DECLARE its own row instead
# (`subtitle_style.project_caption_row`), and then that is the ruler:
# the captain names a place on the delivered frame and every carriage
# computes its transform from it, so grading against the engine's row
# would measure the declaration rather than the placement.
INTENT_BOTTOM = TH - safe_area_for_frame(TW, TH).bottom - CAPTION_LIFT_PX
INTENT_SOURCE = "engine"
if PROJECT:
    declared_row = project_caption_row(PROJECT)
    if declared_row is not None:
        INTENT_BOTTOM = caption_row_px(declared_row, TH)
        INTENT_SOURCE = f"project ({declared_row})"

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
           "intent_caption_bottom": INTENT_BOTTOM,
           "intent_row_source": INTENT_SOURCE, "overlays": rows},
          open(OUT, "w", encoding="utf-8"), indent=2)
print("wrote", OUT, "rows", len(rows))
