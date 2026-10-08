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
    edits_per_handle = int(os.environ.get("EDIT_BENCH_EDITS_PER_HANDLE", "100"))
    cases = ((12, 1), (120, 8), (600, 64))
    print(f"machine={platform.platform()} python={sys.version.split()[0]} "
          f"repetitions={repetitions} edits_per_handle={edits_per_handle}")
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
        # Capability declarations are process initialization shared by both
        # backends, not the first edit call being measured.
        patch_algebra._semantics_dict(left)
        patch_algebra._semantics_dict(right)
        patch_algebra.semantics_of(patch_algebra.edit_patch.EditPatch.from_dict(left))
        patch_algebra.semantics_of(patch_algebra.edit_patch.EditPatch.from_dict(right))
        results = {}
        verdicts = {}
        for backend in ("python", "rust"):
            os.environ[edit_backend.BACKEND_ENV] = backend
            native = edit_backend.rust()
            if backend == "rust" and native is None:
                raise SystemExit("fresh ren_native extension unavailable")
            base_arg = base
            prepare_ms = 0.0
            if backend == "rust":
                prepare_started = time.perf_counter_ns()
                base_arg = edit_backend.snapshot_handle(base, right_ops)
                prepare_ms = (time.perf_counter_ns() - prepare_started) / 1e6

            def call():
                return patch_algebra.compose(left, right, base_arg)

            first_started = time.perf_counter_ns()
            verdict = call()
            first_ms = (time.perf_counter_ns() - first_started) / 1e6
            if verdict.verdict not in {"commute", "merge", "serialize",
                                       "rebase", "conflict"}:
                raise AssertionError(verdict)
            verdicts[backend] = (verdict.verdict, verdict.reason)
            warm_samples = []
            for _ in range(repetitions):
                started = time.perf_counter_ns()
                call()
                warm_samples.append((time.perf_counter_ns() - started) / 1e6)
            warm = statistics.median(warm_samples)
            results[backend] = (first_ms, warm)
            preparation = (f" snapshot_prepare_ms={prepare_ms:.4f}"
                           if backend == "rust" else "")
            print(f"  {backend}: first_ms={first_ms:.4f} "
                  f"warm_median_ms={warm:.4f}{preparation}")
        if verdicts["python"] != verdicts["rust"]:
            raise AssertionError(("compose parity", verdicts))
        py_ms, rust_ms = results["python"][1], results["rust"][1]
        amortized_compose = prepare_ms / edits_per_handle + rust_ms
        print(f"  warm_speedup={py_ms / rust_ms:.3f}x "
              f"rust_amortized_ms_per_edit_N={edits_per_handle}:"
              f"{amortized_compose:.4f}")
        operations = [{"op": "clip.set_property", "unique_id": f"clip-{i}",
                       "key": "ZoomX", "value": 1.0}
                      for i in range(patch_size)]
        os.environ[edit_backend.BACKEND_ENV] = "python"

        def python_loop():
            return ([edit_algebra.span(op, base) for op in operations],
                    [edit_algebra.verify(op, base) for op in operations])

        python_first_ms, python_loop_ms = timed(python_loop, repetitions)
        python_result = python_loop()
        os.environ[edit_backend.BACKEND_ENV] = "rust"
        native = edit_backend.rust()
        if native is None:
            raise SystemExit("fresh ren_native extension unavailable")
        prepare_started = time.perf_counter_ns()
        span_handle = edit_backend.snapshot_handle(base, operations)
        verify_handle = edit_backend.snapshot_handle(
            base, operations, include_transform=True)
        prepare_ms = (time.perf_counter_ns() - prepare_started) / 1e6

        def rust_batch():
            return (edit_algebra.spans(operations, span_handle),
                    edit_algebra.verifies(operations, verify_handle))

        rust_first_ms, rust_batch_ms = timed(rust_batch, repetitions)
        rust_result = rust_batch()
        if python_result != rust_result:
            raise AssertionError(("span/verify parity", clips, patch_size))
        print(f"  python_span_verify_first_ms={python_first_ms:.4f} "
              f"python_span_verify_loop_ms={python_loop_ms:.4f} "
              f"rust_batch_first_ms={rust_first_ms:.4f} "
              f"rust_resident_batch_ms={rust_batch_ms:.4f} "
              f"snapshot_prepare_ms={prepare_ms:.4f} "
              f"batch_speedup={python_loop_ms / rust_batch_ms:.3f}x "
              f"rust_amortized_ms_per_edit_N={edits_per_handle}:"
              f"{prepare_ms / edits_per_handle + rust_batch_ms:.4f}")


if __name__ == "__main__":
    main()
