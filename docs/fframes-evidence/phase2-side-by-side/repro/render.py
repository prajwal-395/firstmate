"""Render one composition through one engine; keep only FRAME as a PNG.

usage: render.py <composition> <hf|ff> <out_dir>
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from props import FRAMES, props  # noqa: E402

name, engine, out_dir = sys.argv[1:4]
os.makedirs(out_dir, exist_ok=True)
avatar = os.path.join(out_dir, "avatar.png")
if not os.path.exists(avatar):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((30, 30, 170, 170), fill=(232, 72, 85, 255))
    d.rectangle((80, 80, 120, 120), fill=(255, 255, 255, 255))
    im.save(avatar)
import base64
uri = "data:image/png;base64," + base64.b64encode(open(avatar, "rb").read()).decode()
p = props(name, avatar_png=uri)
work = tempfile.mkdtemp(prefix=f"sbs-{engine}-")
frames_dir = os.path.join(work, "frames")
try:
    if engine == "hf":
        from library.tools import hyperframes_render as r
    else:
        from library.tools import fframes_render as r
    res = r.render_one_card_frames(name, p, frames_dir, work)
    frames = res["frames"]
    print(engine, name, len(frames), "frames")
    for f in FRAMES:
        suffix = "" if f == 45 else f"_f{f}"
        shutil.copy(frames[f], os.path.join(out_dir, f"{name}{suffix}_{engine}.png"))
finally:
    shutil.rmtree(work, ignore_errors=True)
