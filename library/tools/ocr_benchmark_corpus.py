"""Real-footage OCR benchmark corpus builder (GEO-013, GEO-014).

Samples frames from the two bounded sources the captain allows -
the podcast media and the Apple 001 material - and writes a frozen
frame list with calibration/held-out splits and category tags.

Footage claims stay bounded (brief §4): settings are chosen on the
calibration split and reported once on held-out. Splits are by CLIP,
never by frame, so no clip appears in both.

Categories (GEO-014): lower_third, caption, slide, chart, label,
computer_interface, plus plain scene_text. A frame may carry several.

Usage:
    bin/vep -m library.tools.ocr_benchmark_corpus --out corpus.json

The output is the frozen input to agent labelling: each frame names
its source clip, timestamp, category hints (to label, not to trust)
and an empty `labels` array the labellers fill. Agent labels land as
`{frame_id: [labeller_a, labeller_b, tie_break]}` per the standing
captain rule (2 blind labellers on different families + tie-breaker).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

#: Bounded footage roots. Nothing outside these two directories may
#: enter the corpus.
PODCAST_FOOTAGE_ENV = "PODCAST_FOOTAGE_ROOT"
APPLE_001_ENV = "APPLE_001_ROOT"

DEFAULT_PODCAST_ROOT = str(
    Path.home() / "Documents/work_stuff/Lucie consulting"
    / "Social Media/podcast media")
DEFAULT_APPLE_ROOT = str(
    Path.home() / "Documents/content_stuff/post a day keeps the apple away"
    / "001/raw")

CATEGORIES = ("lower_third", "caption", "slide", "chart", "label",
              "computer_interface", "scene_text")

VIDEO_SUFFIXES = {".mov", ".mp4", ".mxf", ".m4v"}


def _clips(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir()
                  if p.suffix.lower() in VIDEO_SUFFIXES and p.is_file())


def _duration_seconds(path: Path) -> float | None:
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return total / fps if fps > 0 else None


def build_corpus(podcast_root: Path, apple_root: Path,
                 samples_per_clip: int = 6,
                 held_out_fraction: float = 0.3,
                 seed: str = "ren-ocr-ge013-v1") -> dict:
    """Build the frozen frame list. Deterministic in file order."""
    clips: list[dict] = []
    for source, root in (("podcast", podcast_root), ("apple_001", apple_root)):
        for path in _clips(root):
            clips.append({"source": source, "path": str(path),
                          "clip": path.stem})
    clips.sort(key=lambda row: (row["source"], row["clip"]))
    if not clips:
        raise SystemExit(
            f"no footage found under {podcast_root} or {apple_root}; "
            "set PODCAST_FOOTAGE_ROOT / APPLE_001_ROOT")
    # Split by clip: every Nth clip (by order) goes held-out.
    stride = max(1, round(1 / held_out_fraction)) if held_out_fraction else 0
    frames: list[dict] = []
    for index, clip in enumerate(clips):
        split = ("held_out" if stride and index % stride == stride - 1
                 else "calibration")
        duration = _duration_seconds(Path(clip["path"])) or 0.0
        for sample in range(samples_per_clip):
            at = (duration * (sample + 0.5) / samples_per_clip
                  if duration > 0 else float(sample))
            frame_id = hashlib.sha256(
                f"{seed}:{clip['source']}:{clip['clip']}:{sample}".encode()
            ).hexdigest()[:12]
            frames.append({
                "id": f"{clip['source']}-{clip['clip']}-{sample:02d}-{frame_id}",
                "source": clip["source"],
                "clip": clip["clip"],
                "path": clip["path"],
                "at_seconds": round(at, 3),
                "split": split,
                # Hints for the labellers, never trusted as labels.
                "category_hints": [],
                "labels": [],
                "predictions": [],
            })
    return {
        "schema": "ren_ocr_corpus/1",
        "seed": seed,
        "sources": {"podcast": str(podcast_root),
                    "apple_001": str(apple_root)},
        "splits": {"calibration": sum(f["split"] == "calibration"
                                      for f in frames),
                   "held_out": sum(f["split"] == "held_out"
                                   for f in frames)},
        "categories": list(CATEGORIES),
        "frames": frames,
    }


def extract_frame_images(corpus: dict, out_dir: Path,
                         width: int = 1280) -> int:
    """Render one JPEG per corpus frame with ffmpeg. Returns count."""
    import subprocess

    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for frame in corpus["frames"]:
        dest = out_dir / f"{frame['id']}.jpg"
        if dest.exists():
            count += 1
            continue
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(frame["at_seconds"]), "-i", frame["path"],
               "-frames:v", "1", "-vf", f"scale={width}:-1",
               str(dest)]
        result = subprocess.run(cmd, capture_output=True, text=True,
                                encoding="utf-8", check=False)
        if result.returncode == 0 and dest.exists():
            count += 1
    return count


def main() -> None:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--images", type=Path, default=None)
    parser.add_argument("--samples-per-clip", type=int, default=6)
    parser.add_argument("--held-out-fraction", type=float, default=0.3)
    parser.add_argument("--podcast-root", type=Path, default=None)
    parser.add_argument("--apple-root", type=Path, default=None)
    args = parser.parse_args()
    podcast = args.podcast_root or Path(
        os.environ.get(PODCAST_FOOTAGE_ENV, DEFAULT_PODCAST_ROOT))
    apple = args.apple_root or Path(
        os.environ.get(APPLE_001_ENV, DEFAULT_APPLE_ROOT))
    corpus = build_corpus(podcast, apple,
                          samples_per_clip=args.samples_per_clip,
                          held_out_fraction=args.held_out_fraction)
    args.out.write_text(json.dumps(corpus, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({"frames": len(corpus["frames"]),
                      "splits": corpus["splits"],
                      "out": str(args.out)}))
    if args.images:
        print(json.dumps({"images": extract_frame_images(
            corpus, args.images)}))


if __name__ == "__main__":
    main()
