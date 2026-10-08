#!/usr/bin/env python3
"""Measure wall time and Python overhead for Ren's media infrastructure.

Migration 5 of the captain's Rust-core plan.  Each candidate is timed on a
real-scale input and profiled to separate Python self-time from C calls.

Methodology: cProfile self-time (tt) is extracted per function.  Functions
whose filename is '~', '<...>' or '{...}' are C calls (built-ins, frozen
modules, C extensions); everything else is a Python frame.  The Python/C
ratio from the profile is applied to the real total time (measured without
the profiler) to estimate the Python overhead share.

Run with:  bin/vep scripts/measure_media_infrastructure.py
"""
from __future__ import annotations

import cProfile
import datetime
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Real source footage: 447MB camera file from a real project
BIG_CLIP = Path(
    "/Users/prajwal/.treehouse/video-editing-pilot-9487f5/1"
    "/video-editing-pilot/scratch-gemma-check/LCATL0013_excerpt120s.mp4"
)

# Real reel render: 110MB, 61s, 1080x1920
REEL_RENDER = Path(
    "/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data"
    "/vep-ren-rescore-120/eval/BASE/run/exports"
    "/EVAL_rescore120_20260926_20260926_204930_61s.mp4"
)


def is_c_call(func: tuple) -> bool:
    """A profiled function is a C call when its filename is '~', '<...>' or '{...}'."""
    filename = func[0]
    return filename == "~" or filename.startswith(("<", "{"))


def measure(label: str, fn, n: int = 10) -> tuple[float, float, float]:
    """Time fn n times, then profile to split Python self-time from C calls.

    Returns (total_ms, python_ms, c_ms).
    """
    times = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        times.append(t1 - t0)
    total_avg = sum(times) / len(times)

    pr = cProfile.Profile()
    pr.enable()
    for _ in range(n):
        fn()
    pr.disable()
    pr.create_stats()

    c_time = 0.0
    python_time = 0.0
    for func, (cc, nc, tt, ct, callers) in pr.stats.items():
        if is_c_call(func):
            c_time += tt
        else:
            python_time += tt

    total_profiled = c_time + python_time
    if total_profiled > 0:
        python_ratio = python_time / total_profiled
        c_ratio = c_time / total_profiled
    else:
        python_ratio = c_ratio = 0.0

    real_python = total_avg * python_ratio
    real_c = total_avg * c_ratio
    print(f"  {label:48s} {total_avg * 1000:9.2f} ms  "
          f"Python {real_python * 1000:8.2f} ms ({python_ratio * 100:5.1f}%)  "
          f"C {real_c * 1000:9.2f} ms ({c_ratio * 100:5.1f}%)")
    return total_avg, real_python, real_c


def count_call_sites(name: str) -> int:
    """Count call sites for a function in library/."""
    result = subprocess.run(
        ["grep", "-rn", f"{name}(", str(REPO / "library/"), "--include=*.py"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    lines = [l for l in result.stdout.strip().split("\n")
             if l and f"def {name}" not in l]
    return len(lines)


def main() -> None:
    if not BIG_CLIP.exists():
        print(f"SKIP: big clip not found at {BIG_CLIP}")
        return
    if not REEL_RENDER.exists():
        print(f"SKIP: reel render not found at {REEL_RENDER}")
        return

    from library.tools.footage_identity import fingerprint as footage_fingerprint
    from library.tools.planning_reuse import _hash_file
    from library.tools.reel_rebuild_need import _digest_tree
    from library.tools.render_cache import renderer_fingerprint

    print("=" * 100)
    print(f"Ren media infrastructure measurement ({datetime.date.today()}, real scale)")
    print("=" * 100)

    # ── 1. Content fingerprinting ─────────────────────────────────────
    print("\n[1] Content fingerprinting")
    print(f"  file: {BIG_CLIP.name} ({BIG_CLIP.stat().st_size / 1e6:.1f} MB)")
    print("  fingerprint reads: first 1MiB + last 1MiB (samples, not whole file)")
    measure("footage_identity.fingerprint (447MB)",
            lambda: footage_fingerprint(str(BIG_CLIP)), n=20)

    # ── 2. Cache-identity hashing ─────────────────────────────────────
    print("\n[2] Cache-identity hashing")
    print("  _hash_file reads: whole file (called on code/config, not media)")
    measure("planning_reuse._hash_file (447MB)",
            lambda: _hash_file(BIG_CLIP), n=20)

    # ── 3. Media probes ───────────────────────────────────────────────
    print("\n[3] Media probes")
    from library.tools.analysis.vision_pipeline_v3 import probe_clip
    measure("vision_pipeline_v3.probe_clip (447MB)",
            lambda: probe_clip(BIG_CLIP), n=10)

    # ── 4. Render cache ───────────────────────────────────────────────
    print("\n[4] Render cache")
    remotion_dir = str(REPO / "remotion-subtitles")
    measure("render_cache.renderer_fingerprint",
            lambda: renderer_fingerprint(remotion_dir), n=20)

    # ── 5. Reel rebuild need ──────────────────────────────────────────
    print("\n[5] Reel rebuild need")
    measure("reel_rebuild_need._digest_tree",
            lambda: _digest_tree(hashlib.sha256(), Path(remotion_dir),
                                Path(remotion_dir)), n=20)

    # ── 6. Frame diff verification ────────────────────────────────────
    print("\n[6] Frame diff verification (real reel render)")
    import numpy as np

    from library.tools.render_qa import _probe_video_size, _stream_raw_frames

    size = _probe_video_size(str(REEL_RENDER))
    print(f"  render: {REEL_RENDER.name} ({REEL_RENDER.stat().st_size / 1e6:.1f} MB, {size[0]}x{size[1]})")

    def frame_diff_real():
        diffs = []
        prev = None
        for frame in _stream_raw_frames(
                str(REEL_RENDER), "gray", 1, size[0], size[1], 2.0, scaled=False):
            cur = frame[0].astype(np.float32)
            if prev is not None:
                diffs.append(float(np.mean(np.abs(cur - prev))))
            prev = cur
        return diffs

    measure("render_qa frame diff (real reel, 2fps)", frame_diff_real, n=3)

    # ── 7. Interval algebra ───────────────────────────────────────────
    print("\n[7] Interval algebra")
    from library.tools.dirty_regions import _merge
    spans = [
        {"domain": ["video", "audio", "captions"][i % 3],
         "start_frame": i * 100, "end_frame": i * 100 + 50,
         "core_start_frame": i * 100 + 5, "core_end_frame": i * 100 + 45}
        for i in range(100)
    ]
    measure("dirty_regions._merge (100 spans)", lambda: _merge(spans), n=1000)

    # ── 8. JSON parsing ───────────────────────────────────────────────
    print("\n[8] JSON parsing")
    large_manifest = {
        "blocks": [
            {"clip_id": f"clip_{i:03d}", "source_start": i * 10.0,
             "source_end": i * 10.0 + 5.0,
             "word_timestamps": [
                 {"word": f"word_{j}", "start": j * 0.5, "end": j * 0.5 + 0.3}
                 for j in range(20)
             ]}
            for i in range(200)
        ],
    }
    manifest_json = json.dumps(large_manifest)
    print(f"  manifest JSON: {len(manifest_json)} bytes")
    measure("json.loads (218KB manifest)", lambda: json.loads(manifest_json), n=50)

    # ── 9. BM25 (footage search) ──────────────────────────────────────
    print("\n[9] BM25 (footage search)")
    import random

    from library.tools.analysis.footage_query import _BM25
    random.seed(42)
    words = ["the", "video", "shows", "a", "person", "speaking", "about",
             "topic", "camera", "cuts", "to", "shot", "another", "angle"]
    documents = [
        " ".join(random.choice(words) for _ in range(random.randint(30, 80)))
        for _ in range(1000)
    ]
    bm25 = _BM25(documents)
    measure("_BM25.scores (1000 docs, 8 terms)",
            lambda: bm25.scores("the video shows a person speaking about topic"),
            n=200)

    # ── Call site counts ──────────────────────────────────────────────
    print("\n" + "=" * 100)
    print("Call site counts (in library/)")
    print("=" * 100)
    for name in ["fingerprint", "_hash_file", "probe_clip",
                 "_stream_raw_frames", "renderer_fingerprint", "_digest_tree"]:
        count = count_call_sites(name)
        print(f"  {name:30s} {count:3d} call sites")

    print("\n" + "=" * 100)
    print("Done.")
    print("=" * 100)


if __name__ == "__main__":
    main()
