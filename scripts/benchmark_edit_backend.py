#!/usr/bin/env python3
"""Measure the complete edit composition call from Python to each backend.

Run through the firstmate verification wrapper as required for benchmarks:
    vep-verify-run.sh bin/vep scripts/benchmark_edit_backend.py
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
import time

from library.tools import edit_algebra, edit_backend, patch_algebra


def snapshot(clip_count: int) -> dict:
    clips = []
    for index in range(clip_count):
        start = index * 100
        clips.append({
            "unique_id": f"clip-{index}", "name": f"clip-{index}",
            "track_type": "video", "track_index": 1,
            "record_in": start, "record_out": start + 100,
            "duration": 100, "source_in_frame": 0,
            "source_out_frame": 100, "source_file": f"/pool/{index}.mov",
            "enabled": True, "speed": None,
            "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                          "ZoomY": 1.0},
            "keyframes": {}, "fusion": {"comp_names": []},
            "color": {"cdl": {}}, "markers": [],
        })
    return {
        "start_frame": 0, "end_frame": clip_count * 100,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": clips}],
        "markers": {"timeline": []},
    }


def patch(name: str, operations: list[dict], frame_end: int) -> dict:
    return {
        "id": name, "project": "Benchmark", "timeline": "Benchmark",
        "base_generation": 1, "capability": "reel.touchup",
        "affected_spans": [[0, frame_end]],
        "conflict_domains": ["picture_transform"],
        "operations": operations, "preconditions": [], "postconditions": [],
        "base_git_sha": "",
    }


def timed(call, repetitions: int) -> tuple[float, float]:
    cold_started = time.perf_counter_ns()
    call()
    cold_ms = (time.perf_counter_ns() - cold_started) / 1e6
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter_ns()
        call()
        samples.append((time.perf_counter_ns() - started) / 1e6)
    return cold_ms, statistics.median(samples)


def main() -> None:
    repetitions = int(os.environ.get("EDIT_BENCH_REPETITIONS", "100"))
    cases = ((12, 1), (120, 8), (600, 64))
    print(f"machine={platform.platform()} python={sys.version.split()[0]} "
          f"repetitions={repetitions}")
    for clips, patch_size in cases:
        base = snapshot(clips)
        left_ops = [{"op": "clip.set_property", "unique_id": f"clip-{i}",
                     "key": "ZoomX", "value": 1.1}
                    for i in range(patch_size)]
        right_ops = [{"op": "clip.set_property", "unique_id": f"clip-{i}",
                      "key": "Tilt", "value": 2.0}
                     for i in range(patch_size)]
        left = patch("left", left_ops, clips * 100)
        right = patch("right", right_ops, clips * 100)
        print(f"case clips={clips} operations_each={patch_size} "
              f"snapshot_bytes={len(json.dumps(base))}")
        results = {}
        for backend in ("python", "rust"):
            os.environ[edit_backend.BACKEND_ENV] = backend
            native = edit_backend.rust()
            if backend == "rust" and native is None:
                raise SystemExit("fresh ren_native extension unavailable")
            def call():
                return patch_algebra.compose(left, right, base)
            verdict = call()
            if verdict.verdict not in {"commute", "merge", "serialize",
                                       "rebase", "conflict"}:
                raise AssertionError(verdict)
            cold, warm = timed(call, repetitions)
            results[backend] = (cold, warm)
            print(f"  {backend}: first_ms={cold:.4f} warm_median_ms={warm:.4f}")
        py_ms, rust_ms = results["python"][1], results["rust"][1]
        print(f"  warm_speedup={py_ms / rust_ms:.3f}x")
        operations = [{"op": "clip.set_property", "unique_id": f"clip-{i}",
                       "key": "ZoomX", "value": 1.0}
                      for i in range(patch_size)]
        for backend in ("python", "rust"):
            os.environ[edit_backend.BACKEND_ENV] = backend
            def one_by_one():
                return ([edit_algebra.span(op, base) for op in operations],
                        [edit_algebra.verify(op, base) for op in operations])

            def batched():
                return (edit_algebra.spans(operations, base),
                        edit_algebra.verifies(operations, base))
            _, singles_ms = timed(one_by_one, repetitions)
            _, batched_ms = timed(batched, repetitions)
            print(f"  {backend}_span_verify_loop_ms={singles_ms:.4f} "
                  f"{backend}_span_verify_batch_ms={batched_ms:.4f} "
                  f"batch_speedup={singles_ms / batched_ms:.3f}x")


if __name__ == "__main__":
    main()
