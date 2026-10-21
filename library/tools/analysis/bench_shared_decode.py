"""Benchmark the shared decode against independent decodes.

PERF-007: two analysis lanes measured the same source frames at the same
geometry and each decoded the source in full.  This benchmark measures
what the shared decode changes, with the denominators the punchlist asks
for:

* **decode seconds** - what one decode costs, what the second lane's
  decode cost before, and what the shared read costs now;
* **peak memory** - the high-water mark of each arm, because the picture
  sampler used to hold the whole sample in memory;
* **cache bytes** - what the shared entry costs on disk;
* **outputs byte-identical** - the frames each command produces hash the
  same, and each lane's measurement is the same with sharing on and off.

Every arm runs in its own process, so the peak-memory figure belongs to
that arm alone.

    python3 -m library.tools.analysis.bench_shared_decode \\
        --source /path/to/a/scratch/copy.mp4 --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if __package__ in (None, ""):  # direct `python3 bench_shared_decode.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np

from library.tools import shared_decode
from library.tools.analysis import image_defects, picture_quality

ARMS = (
    "original_picture_command",
    "original_defects_command",
    "original_picture_lane",
    "shared_miss",
    "shared_hit",
    "picture_lane_off",
    "picture_lane_on",
    "defects_lane_off",
    "defects_lane_on",
)


def _peak_rss_bytes() -> int:
    """This process's peak resident memory, in bytes on every platform."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _original_picture_command(source: str, out_path: Path) -> list[str]:
    """The picture sampler's command, as it was before the shared decode."""
    width, height = picture_quality._display_dimensions(source)
    out_w, out_h = picture_quality._sample_dimensions(
        width, height, picture_quality.SAMPLE_SHORT_SIDE_PX)
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-vf", f"fps={picture_quality.SAMPLE_RATE_HZ},scale={out_w}:{out_h}",
        "-pix_fmt", "gray", "-f", "rawvideo", str(out_path),
    ]


def _original_defects_command(source: str, out_path: Path) -> list[str]:
    """The image-defects sampler's command, as it was before."""
    width, height, _duration = image_defects._dimensions(source)
    out_w, out_h = image_defects._sample_size(width, height)
    return [
        "ffmpeg", "-v", "error", "-i", str(source), "-vf",
        (f"fps={image_defects.SAMPLE_RATE_HZ},scale={out_w}:{out_h}:"
         "in_range=auto:out_range=full"),
        "-pix_fmt", "gray", "-f", "rawvideo", str(out_path),
    ]


def _original_sample_sharpness(video_path: str) -> dict:
    """The picture sampler's OLD implementation, for the comparison.

    It held the whole sample in memory - the raw buffer and a float32
    copy of it - which is the peak memory the shared decode removed.
    """
    width, height = picture_quality._display_dimensions(video_path)
    out_w, out_h = picture_quality._sample_dimensions(
        width, height, picture_quality.SAMPLE_SHORT_SIDE_PX)
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video_path),
         "-vf", f"fps={picture_quality.SAMPLE_RATE_HZ},scale={out_w}:{out_h}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=3600, check=False)
    frame_bytes = out_w * out_h
    n_frames = len(result.stdout) // frame_bytes if frame_bytes else 0
    if result.returncode != 0 or n_frames == 0:
        raise RuntimeError(f"ffmpeg could not decode {video_path}")
    frames = np.frombuffer(
        result.stdout[:n_frames * frame_bytes], dtype=np.uint8
    ).reshape(n_frames, out_h, out_w).astype(np.float32)
    return {
        "sample_rate_hz": picture_quality.SAMPLE_RATE_HZ,
        "sample_short_side_px": min(out_w, out_h),
        "values": [picture_quality._laplacian_variance(frame)
                   for frame in frames],
    }


def _recipe(source: str) -> shared_decode.Recipe:
    width, height = picture_quality._display_dimensions(source)
    out_w, out_h = picture_quality._sample_dimensions(
        width, height, picture_quality.SAMPLE_SHORT_SIDE_PX)
    return shared_decode.Recipe(
        rate_hz=picture_quality.SAMPLE_RATE_HZ, width=out_w, height=out_h)


def _run_command(command: list[str]) -> dict:
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            errors="replace", check=False)
    seconds = round(time.perf_counter() - started, 3)
    if result.returncode != 0:
        raise RuntimeError(f"{command[0]} failed: {result.stderr[-300:]}")
    return {"seconds": seconds}


def run_arm(arm: str, source: str, root: str | None) -> dict:
    """One arm of the benchmark, measured in this process."""
    started = time.perf_counter()
    recipe = _recipe(source)
    scratch = Path(tempfile.mkdtemp(prefix="ren-bench-"))
    record: dict = {"arm": arm}

    if arm == "original_picture_command":
        out = scratch / "picture.gray"
        record.update(_run_command(_original_picture_command(source, out)))
        record["frames_sha256"] = _sha256(out)
        record["bytes"] = out.stat().st_size
    elif arm == "original_defects_command":
        out = scratch / "defects.gray"
        record.update(_run_command(_original_defects_command(source, out)))
        record["frames_sha256"] = _sha256(out)
        record["bytes"] = out.stat().st_size
    elif arm in ("shared_miss", "shared_hit"):
        frames = shared_decode.sampled_frames(source, recipe, root=root)
        record["seconds"] = round(time.perf_counter() - started, 3)
        record["frames_sha256"] = _sha256(frames.path)
        record["bytes"] = frames.bytes_written
        record["cache_hit"] = frames.cache_hit
        record["frame_count"] = frames.frame_count
    elif arm == "original_picture_lane":
        values = _original_sample_sharpness(source)
        record["seconds"] = round(time.perf_counter() - started, 3)
        record["output_sha256"] = _sha256_text(
            json.dumps(values, sort_keys=True))
        record["frame_count"] = len(values["values"])
    elif arm in ("picture_lane_off", "picture_lane_on"):
        values = picture_quality.sample_sharpness(source)
        record["seconds"] = round(time.perf_counter() - started, 3)
        record["output_sha256"] = _sha256_text(
            json.dumps(values, sort_keys=True))
        record["frame_count"] = len(values["values"])
    elif arm in ("defects_lane_off", "defects_lane_on"):
        measured = image_defects.measure_source(source)
        record["seconds"] = round(time.perf_counter() - started, 3)
        record["output_sha256"] = _sha256_text(
            json.dumps(measured, sort_keys=True))
    else:
        raise SystemExit(f"unknown arm {arm!r}")

    record["peak_rss_bytes"] = _peak_rss_bytes()
    return record


def _child(arm: str, source: str, root: str | None) -> dict:
    """One arm, in its own process, so the peak memory is its own."""
    environment = dict(os.environ)
    environment["REN_SHARED_DECODE"] = "off" if arm.endswith("_off") else "on"
    if arm in ("shared_miss",):
        environment["REN_SHARED_DECODE"] = "on"
    command = [sys.executable, "-m",
               "library.tools.analysis.bench_shared_decode",
               "--arm", arm, "--source", str(source)]
    if root:
        command += ["--root", str(root)]
    result = subprocess.run(command, capture_output=True, encoding="utf-8",
                            errors="replace", check=False, env=environment,
                            cwd=str(Path(__file__).resolve().parents[3]))
    if result.returncode != 0:
        raise RuntimeError(f"arm {arm} failed: {result.stderr[-600:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _clear_cache(source: str, root: str | None) -> None:
    """Start the shared arms from an empty cache, so the miss is a miss."""
    shared_decode.purge(source, _recipe(source), root=root)


def benchmark(source: str, root: str | None = None) -> dict:
    """Every arm, in its own process, and the comparison between them."""
    results: dict[str, dict] = {}
    for arm in ARMS:
        if arm == "shared_miss":
            _clear_cache(source, root)
        results[arm] = _child(arm, source, root)

    frames = {arm: results[arm].get("frames_sha256")
              for arm in ("original_picture_command",
                          "original_defects_command", "shared_miss")}
    frames_identical = len(set(frames.values())) == 1 and None not in frames.values()
    picture_identical = (results["picture_lane_off"].get("output_sha256")
                         == results["picture_lane_on"].get("output_sha256"))
    original_identical = (results["original_picture_lane"].get("output_sha256")
                          == results["picture_lane_on"].get("output_sha256"))
    defects_identical = (results["defects_lane_off"].get("output_sha256")
                          == results["defects_lane_on"].get("output_sha256"))
    return {
        "source": str(source),
        "shared_decode_root": str(shared_decode.cache_root(root)),
        "arms": results,
        "frames_sha256": frames,
        "frames_byte_identical": frames_identical,
        "picture_output_identical": picture_identical,
        "defects_output_identical": defects_identical,
        "original_picture_output_identical": original_identical,
        "original_picture_peak_rss_bytes": results["original_picture_lane"][
            "peak_rss_bytes"],
        "shared_picture_peak_rss_bytes": results["picture_lane_on"][
            "peak_rss_bytes"],
        "independent_decode_seconds": round(
            results["original_picture_command"]["seconds"]
            + results["original_defects_command"]["seconds"], 3),
        "shared_decode_seconds": results["shared_miss"]["seconds"],
        "shared_read_seconds": results["shared_hit"]["seconds"],
        "cache_bytes": results["shared_miss"]["bytes"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--root", default=None,
                        help="the shared decode cache root (scratch)")
    parser.add_argument("--arm", default=None, choices=ARMS,
                        help="run one arm in this process and print it")
    parser.add_argument("--json", default=None)
    args = parser.parse_args(argv)

    if args.arm:
        print(json.dumps(run_arm(args.arm, args.source, args.root),
                         sort_keys=True))
        return 0

    report = benchmark(args.source, args.root)
    print(json.dumps(report, indent=1, sort_keys=True))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(report, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0 if (report["frames_byte_identical"]
                 and report["picture_output_identical"]
                 and report["defects_output_identical"]
                 and report["original_picture_output_identical"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
