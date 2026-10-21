"""PERF-008: what a targeted request costs with the analysis cache, and without.

The measurement the punch list asks for: "analysis seconds and cache hits
for a single targeted edit request, before vs after, with identical edit
output".

**The scenario.**  A targeted edit request names a window of the footage
and asks what is in it.  The engine answers it.  Then the SAME request is
asked again over a WIDER window - the edit that named the moment, asked
with more of the cut around it.  The wider window contains the frames the
first answer measured.

**The two arms.**

* **before** - the analysis cache is off (``REN_ANALYSIS_CACHE=off``), so
  the wider request measures every frame of the wider window again.  This
  is what the engine did before the cache existed, and it is measured by
  the same code, so the two arms differ only in the cache.
* **after** - the analysis cache is on, so the wider request reuses the
  frames the first answer measured and measures only the frames it
  missed.

**The fidelity half.**  The two arms must produce the SAME answer: the
same detections on the same frames, and the same coverage map.  A
difference is reported as a failure, not as a number - a cheaper answer
that says something else is not a cheaper answer.

Usage::

    python3 -m library.tools.analysis.bench_demand_analysis \
        --source /tmp/perf008-scratch/probe90.mp4 \
        --phrase "cell phone" \
        --first 0-30 --second 0-60 \
        --root /tmp/perf008-scratch/memory

The run prints a table and writes the same numbers as JSON.  The media is
read; the cache is written under ``--root``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # direct `python3 bench_demand_analysis.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import analysis_cache
from library.tools.analysis import object_grounding as og


def _window(text: str) -> tuple[float, float]:
    start, _, end = str(text).partition("-")
    return float(start), float(end)


def _answer(result: dict[str, Any]) -> dict[str, Any]:
    """The part of an answer that must not change.

    The detections and the coverage map, without the cost and without the
    cache's own bookkeeping: those are what a reader of the answer sees.
    """
    return {
        "detections": [
            {key: value for key, value in row.items() if key != "image"}
            for row in result.get("detections") or []
        ],
        "coverage": {
            key: value for key, value in (result.get("coverage") or {}).items()
            if key not in {"frames_inspected", "frame_indices"}
        },
    }


def _rows(result: dict[str, Any], phrases: list[str]) -> list[dict[str, Any]]:
    """The Project Model rows one answer files - the edit's own input.

    The answer and the rows are compared separately: the rows are what a
    later edit reads, and a cheaper answer that files different rows is
    not a cheaper answer.
    """
    model = {"id": "model-1", "observations": [], "entities": [],
             "provenance": [], "object_classes": [], "tracks": []}
    observations, _provenance = og.build_observations(
        model=model, digest="digest-1", asset_id="asset-1",
        clock_id="clock-1", phrases=phrases, result=result)
    return [{"id": row.get("id"),
             "interval": row.get("interval"),
             "value": og.value_of(row)} for row in observations]


def _measure(source: str, phrases: list[str], start: float, end: float,
             producer: og.Producer, root: Path, *, enabled: bool,
             sample_fps: float) -> dict[str, Any]:
    """One pass over one window, with the cache on or off."""
    from library.tools import footage_identity

    digest = footage_identity.fingerprint(source)["content_digest"]
    key = og.analysis_key(source_digest=digest, producer=producer,
                          phrases=phrases, sample_fps=sample_fps)
    cache = analysis_cache.FrameCache(digest, key, root=root, enabled=enabled)
    started = time.perf_counter()
    result = og.detect_window(
        source, phrases=phrases, start_s=start, end_s=end, producer=producer,
        sample_fps=sample_fps, cache=cache, source_digest=digest)
    elapsed = time.perf_counter() - started
    return {
        "wall_seconds": round(elapsed, 3),
        "cost_seconds": result.get("cost_seconds"),
        "frames_measured": len(cache.measured),
        "frames_reused": len(cache.reused),
        "cache_hits": len(cache.reused),
        "answer": _answer(result),
        "rows": _rows(result, phrases),
    }


def _arm(source: str, phrases: list[str], first: tuple[float, float],
         second: tuple[float, float], producer: og.Producer, root: Path,
         *, enabled: bool, sample_fps: float) -> dict[str, Any]:
    """The narrow request, then the wider one, in one arm."""
    narrow = _measure(source, phrases, first[0], first[1], producer, root,
                      enabled=enabled, sample_fps=sample_fps)
    wide = _measure(source, phrases, second[0], second[1], producer, root,
                    enabled=enabled, sample_fps=sample_fps)
    return {"narrow": narrow, "wide": wide}


def run(source: str, phrases: list[str], first: tuple[float, float],
        second: tuple[float, float], *, root: Path,
        producer_id: str = "owlv2_named", sample_fps: float = 1.0,
        keep_cache: bool = False) -> dict[str, Any]:
    """Both arms, and the comparison between them."""
    producer = og.PRODUCERS[producer_id]
    if not keep_cache:
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    # The model is loaded once, before either arm: a load is a cost of
    # the FIRST measurement on this machine, not of the request, and
    # leaving it inside the first arm would charge the "before" arm for
    # something both arms share.
    warmup = _measure(source, phrases, first[0], first[0] + 1.0, producer,
                      root, enabled=False, sample_fps=sample_fps)
    before = _arm(source, phrases, first, second, producer, root,
                  enabled=False, sample_fps=sample_fps)
    after = _arm(source, phrases, first, second, producer, root,
                 enabled=True, sample_fps=sample_fps)
    # The same request asked again: every frame it needs is already
    # measured, so the pass costs the reading and nothing else.
    repeat = _measure(source, phrases, second[0], second[1], producer, root,
                      enabled=True, sample_fps=sample_fps)
    identical = before["wide"]["answer"] == after["wide"]["answer"]
    identical_rows = before["wide"]["rows"] == after["wide"]["rows"]
    before_seconds = float(before["wide"]["cost_seconds"] or 0.0)
    after_seconds = float(after["wide"]["cost_seconds"] or 0.0)
    return {
        "source": source,
        "phrases": phrases,
        "producer": producer_id,
        "sample_rate_hz": sample_fps,
        "windows": {"narrow": list(first), "wide": list(second)},
        "warmup_seconds": warmup["wall_seconds"],
        "before": before,
        "after": after,
        "repeat": repeat,
        "identical_answer": identical,
        "identical_rows": identical_rows,
        "analysis_seconds": {
            "before": before_seconds,
            "after": after_seconds,
            "saved": round(before_seconds - after_seconds, 3),
            "speedup": round(before_seconds / after_seconds, 3)
            if after_seconds > 0 else None,
        },
        "cache_hits": {
            "before": before["wide"]["cache_hits"],
            "after": after["wide"]["cache_hits"],
        },
        "frames": {
            "before": before["wide"]["frames_measured"],
            "after": after["wide"]["frames_measured"],
            "reused": after["wide"]["frames_reused"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench_demand_analysis",
        description=("What a targeted object request costs with the "
                     "analysis cache and without it."))
    parser.add_argument("--source", required=True,
                        help="the media the request is about")
    parser.add_argument("--phrase", action="append", default=None,
                        help="the object the request names (repeatable)")
    parser.add_argument("--first", default="0-30",
                        help="the narrower window (START-END seconds)")
    parser.add_argument("--second", default="0-60",
                        help="the wider window (START-END seconds)")
    parser.add_argument("--root", default="/tmp/perf008-scratch/memory",
                        help="where the analysis cache is written")
    parser.add_argument("--producer", default="owlv2_named",
                        choices=sorted(og.PRODUCERS))
    parser.add_argument("--sample-fps", type=float,
                        default=og.DEFAULT_SAMPLE_FPS)
    parser.add_argument("--json", action="store_true",
                        help="print the numbers as JSON")
    parser.add_argument("--keep-cache", action="store_true",
                        help="do not clear the cache before the run")
    args = parser.parse_args(argv)
    phrases = args.phrase or ["cell phone"]
    report = run(args.source, phrases, _window(args.first), _window(args.second),
                 root=Path(args.root).expanduser(),
                 producer_id=args.producer, sample_fps=args.sample_fps,
                 keep_cache=args.keep_cache)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"source            {report['source']}")
        print(f"phrases           {', '.join(report['phrases'])}")
        print(f"producer          {report['producer']}")
        print(f"narrow window     {report['windows']['narrow']}")
        print(f"wide window       {report['windows']['wide']}")
        print()
        print("arm     frames measured   frames reused   analysis seconds")
        for name in ("before", "after"):
            narrow = report[name]["narrow"]
            wide = report[name]["wide"]
            print(f"{name:<7} {wide['frames_measured']:>15}   "
                  f"{wide['frames_reused']:>13}   "
                  f"{wide['cost_seconds']:>16}")
            print(f"        (narrow request: {narrow['frames_measured']} frames, "
                  f"{narrow['cost_seconds']} s)")
        print()
        print(f"analysis seconds   {report['analysis_seconds']['before']} -> "
              f"{report['analysis_seconds']['after']} "
              f"(saved {report['analysis_seconds']['saved']}, "
              f"x{report['analysis_seconds']['speedup']})")
        print(f"cache hits         {report['cache_hits']['before']} -> "
              f"{report['cache_hits']['after']}")
        print(f"same answer        {report['identical_answer']}")
        print(f"same rows          {report['identical_rows']}")
        print(f"the same request again: "
              f"{report['repeat']['frames_reused']} frames reused, "
              f"{report['repeat']['cost_seconds']} s")
    return 0 if report["identical_answer"] and report["identical_rows"] else 1


if __name__ == "__main__":
    sys.exit(main())
