"""Render every S2 arms-d case through FFrames and Remotion.

usage: render.py <out_dir>

FFrames: resolve_props + render_one_card_frames (premultiplied frames,
the adapted pixels the carriage receives). Remotion: the 4.06 CLI
sequence recipe (npx remotion render MotionGraphics --props --sequence),
output to /tmp (census G8: this Remotion build takes everything after
the last dot in the full output path as the extension, so a dotted
worktree path cannot hold sequence output). Renders run one at a time;
hold the verify lock around the whole script, not per case.

The website asset reaches each side the way production reaches it:
FFrames by absolute path (resolve_props keeps absolute files as given),
Remotion staged under public/brand/ (remotion_brand_linker.link_brand_assets
copies it there; the staged copy is removed afterwards). Same pixels.

Writes: <case>_ff/frame_%06d.png and <case>_rm/frame_%06d.png per case.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
REMOTION_DIR = os.path.join(REPO_ROOT, "remotion-subtitles")
PUBLIC_BRAND = os.path.join(REMOTION_DIR, "public", "brand")

import synth_props  # noqa: E402


def render_ff(case, props, case_dir):
    from library.tools import fframes_render as r
    frames_dir = os.path.join(case_dir, f"{case}_ff")
    work = tempfile.mkdtemp(prefix="s2d-ff-")
    try:
        res = r.render_one_card_frames("MotionGraphics", props,
                                       frames_dir, work)
        print("ff", case, res["frame_count"], "frames")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return frames_dir


def render_rm(case, props, case_dir, tmp_root):
    staged = None
    for element in props.get("elements", []):
        asset = element.get("asset")
        if asset and os.path.isabs(asset) and os.path.isfile(asset):
            staged = os.path.join(PUBLIC_BRAND, "s2-arms-d-capture.png")
            os.makedirs(PUBLIC_BRAND, exist_ok=True)
            shutil.copy(asset, staged)
            element["asset"] = "brand/s2-arms-d-capture.png"
    props_path = os.path.join(tmp_root, f"{case}_props.json")
    with open(props_path, "w", encoding="utf-8") as fh:
        json.dump(props, fh)
    dest = os.path.join(tmp_root, f"{case}_rm")
    shutil.rmtree(dest, ignore_errors=True)
    os.makedirs(dest, exist_ok=True)
    try:
        proc = subprocess.run(
            ["npx", "remotion", "render", "MotionGraphics", dest,
             "--props", props_path,
             "--image-format", "png",
             "--sequence",
             "--image-sequence-pattern", "frame-[frame].png"],
            cwd=REMOTION_DIR,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=300, check=False,
        )
        if proc.returncode != 0:
            print(f"RM RENDER FAILED for {case}:\n{proc.stderr[-2000:]}")
            raise RuntimeError(f"remotion render failed for {case}")
        frames_dir = os.path.join(case_dir, f"{case}_rm")
        shutil.rmtree(frames_dir, ignore_errors=True)
        os.makedirs(frames_dir, exist_ok=True)
        # Sequence frames are 0-based frame-N.png: rename to the shared
        # 1-based six-digit names production canonicalizes to
        # (`_canonicalize_motion_frames`: enumerate(staged, 1)).
        import re
        files = sorted(
            (f for f in os.listdir(dest) if f.endswith(".png")),
            key=lambda name: int(re.search(r"(\d+)", name).group(1)),
        )
        for i, name in enumerate(files, 1):
            shutil.copy(os.path.join(dest, name),
                        os.path.join(frames_dir, f"frame_{i:06d}.png"))
        print("rm", case, len(files), "frames")
    finally:
        shutil.rmtree(dest, ignore_errors=True)
        if staged and os.path.isfile(staged):
            os.remove(staged)
    return frames_dir


def main():
    out_dir = sys.argv[1]
    os.makedirs(out_dir, exist_ok=True)
    capture = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "capture.png")
    if not os.path.isfile(capture):
        raise RuntimeError("run make_capture.py first")
    tmp_root = tempfile.mkdtemp(prefix="s2d-rm-")
    try:
        for case in synth_props.CASES:
            builder = getattr(synth_props, case)
            if case.startswith("website"):
                ff_props = builder(capture)
                rm_props = builder(capture)
            else:
                ff_props = builder()
                rm_props = builder()
            render_ff(case, ff_props, out_dir)
            render_rm(case, rm_props, out_dir, tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
