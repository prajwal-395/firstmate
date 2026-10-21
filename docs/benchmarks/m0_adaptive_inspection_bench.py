"""The M0 arm: does the dense tier name the frame the event is on?

The M0 case is a person bringing a handheld device to their mouth, in
the first 48 seconds of IMG_1822.  The ground truth is the FROZEN
occurrence corpus
(`docs/benchmarks/2026-10-21-occurrence-identity-fixtures.json`): three
occurrences, each with the seconds it spans.

* BEFORE: the coarse tier's own answer - the coarse M2 sample nearest
  the occurrence.  This is the incumbent's boundary estimate, and the
  M0 baseline measured it as 2 to 14 frames off the adjudicated band.
* AFTER: the dense tier's answer - the frame the measured
  hand-to-lips distance is most extreme on, inside the window the
  plan chose from the engine's own measurements.

Both answers are scored against the same frozen occurrences, with the
same region-capped accounting the retrieval arm uses, so the two arms
are comparable.

The fixture's OWN memory is built first (M2, then M3, then M7), so
the coarse tier, the triggers and the dense tier all read the same
source's measurements.  The source is a Ren-owned scratch copy; the
captain's library is not read.

    bin/vep docs/benchmarks/m0_adaptive_inspection_bench.py \\
        --source <the scratch copy> --out-json <report.json>
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import adaptive_inspection, adaptive_signals
from library.tools import frame_exact, source_memory
from library.tools.retrieval import regions as regions_mod

#: The frozen M0 occurrences, in seconds, from the occurrence-identity
#: fixture corpus (F1-one-subject).  The bands are the adjudicated
#: spans; the frame indices the M0 baseline quoted (999-1011 and
#: 1409-1414) are these spans at 30 fps.
OCCURRENCES = [
    {"index": 1, "start_s": 17.0, "end_s": 19.0},
    {"index": 2, "start_s": 33.3, "end_s": 33.7},
    {"index": 3, "start_s": 47.0, "end_s": 47.4},
]

#: The frames the M0 baseline adjudicated, for the M0 case (occurrence
#: 2) and for occurrence 3.  Both are agent-adjudicated and
#: provisional; the corpus's seconds are the authority this bench
#: scores against, and these are the same spans in frames.
ADJUDICATED_FRAMES = {
    2: (999, 1011),
    3: (1409, 1414),
}


def _peak_rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6, 2)


def _as_moments(occurrences) -> list[dict]:
    """The frozen occurrences, as the retrieval scorer's wanted moments."""
    return [{"instances": [{"asset": "m0", "start": row["start_s"],
                            "end": row["end_s"]}]}
            for row in occurrences]


def _score(named_frames: list[dict], occurrences) -> dict:
    """Region-capped recall of the named frames against the occurrences.

    A named frame CREDITS an occurrence when it falls inside it, and
    one credit per occurrence however many frames land in it - the
    retrieval scorer's own rule, so the two arms are comparable.

    A named frame is an INSTANT and the scorer scores a WINDOW, so
    each named frame is scored as the one frame interval it occupies.
    """
    moments = _as_moments(occurrences)
    wanted = regions_mod.wanted_regions(moments)
    half = 0.5 / 30.0
    hits = [{"asset": "m0",
             "start": frame["pts_seconds"] - half,
             "end": frame["pts_seconds"] + half}
            for frame in named_frames
            if frame.get("pts_seconds") is not None]
    credited = regions_mod.credit_windows(moments, wanted, hits)
    per_frame = []
    for frame in named_frames:
        at = frame.get("pts_seconds")
        if at is None:
            per_frame.append({"frame_index": frame.get("frame_index"),
                              "occurrence": None, "frames_outside": None})
            continue
        inside = [row["index"] for row in occurrences
                  if row["start_s"] <= at <= row["end_s"]]
        outside = None
        if not inside:
            outside = round(min(
                min(abs(at - row["start_s"]), abs(at - row["end_s"]))
                for row in occurrences) * 30.0, 1)
        per_frame.append({"frame_index": frame.get("frame_index"),
                          "pts_seconds": at,
                          "occurrence": inside[0] if inside else None,
                          "frames_outside": (None if inside else outside)})
    return {
        "occurrences": len(occurrences),
        "occurrences_found": len(credited["regions_found"]),
        "region_recall": (round(len(credited["regions_found"])
                                 / len(occurrences), 4)
                          if occurrences else None),
        "excess_seconds": credited["excess_seconds"],
        "named_frames": per_frame,
    }


def _coarse_answer(coarse_times: list[float], fps: float,
                    measured_times: set[float]) -> list[dict]:
    """The coarse tier's own answer: the coarse sample nearest an occurrence.

    A coarse sample the coarse tier MEASURED NOTHING at cannot be the
    coarse tier's answer: the M0 fixture's sample at 18.367 s carries
    no face, so the engine measured no hand-to-lips distance there and
    has nothing to say about that instant.  Such a sample is not
    offered as the answer, and the report says so.
    """
    out = []
    for row in OCCURRENCES:
        target = (row["start_s"] + row["end_s"]) / 2.0
        usable = [t for t in coarse_times
                  if any(abs(t - m) < 1e-6 for m in measured_times)]
        if not usable:
            continue
        nearest = min(usable, key=lambda t: abs(t - target))
        out.append({"pts_seconds": nearest,
                    "frame_index": int(round(nearest * fps)),
                    "coarse_sample": True,
                    "measured": True})
    return out


def _build_memory(source: str, root: str | None) -> str:
    """The fixture's own memory: M2, then M3, then M7.

    The coarse tier, the triggers and the dense tier must all read the
    SAME source's measurements, or the arms are not comparable.
    """
    from library.tools import event_spans, footage_identity, person_measurements
    digest = footage_identity.fingerprint(source)["content_digest"]
    source_memory.build_frames(source, digest, root=root)
    person_measurements.build_source_persons(source, digest, root=root)
    event_spans.build_source_events(digest, source, root=root)
    return digest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True,
                        help="the scratch copy of the M0 clip")
    parser.add_argument("--memory-root", default=None,
                        help="where the source memory is read from and "
                             "written to (default: the machine store)")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--work-dir", default="/tmp/ren-vi005-m0")
    args = parser.parse_args(argv)

    root = args.memory_root
    source = os.path.abspath(args.source)
    digest = _build_memory(source, root)

    m2 = source_memory.read_m2(digest, root)
    if m2 is None:
        raise SystemExit(f"no M2 sample for {digest}")
    coarse = list(m2.get("frames") or [])
    coarse_times = [float(f["t"]) for f in coarse]
    fps = 30.0
    timebase = frame_exact.probe_timebase(source)
    # The recording's own length, which is NOT the last coarse sample's
    # time: the sample is a subset of the recording, and a plan that
    # believes otherwise stops the dense tier short of the end.
    duration = (float((m2.get("timebase") or {}).get("total_frames") or 0)
                / fps) or (float(coarse_times[-1]) if coarse_times else 0.0)

    report: dict[str, Any] = {
        "policy_version": adaptive_inspection.POLICY_VERSION,
        "source": source,
        "digest": digest,
        "coarse_samples": len(coarse),
        "coarse_cadence_seconds": (round(coarse_times[1] - coarse_times[0], 3)
                                    if len(coarse_times) > 1 else None),
        "duration_seconds": round(duration, 3),
        "occurrences": OCCURRENCES,
        "adjudicated_frames": {str(k): list(v)
                               for k, v in ADJUDICATED_FRAMES.items()},
    }

    # BEFORE: the coarse tier's own answer.
    # The coarse samples the engine actually MEASURED a hand-to-lips
    # distance at, read off the M7 record: a sample with no face
    # carries no measurement, so it cannot be an answer.
    from library.tools import event_spans
    m7 = event_spans.read_m7(digest, root) or {}
    measured_times = set()
    for body in (m7.get("candidates") or {}).values():
        for span in body.get("spans") or ():
            for frame in span.get("frames") or ():
                at = frame.get("t")
                if at is not None and frame.get("d") is not None:
                    measured_times.add(float(at))
    report["coarse_samples_measured"] = len(measured_times)
    report["before"] = _score(
        _coarse_answer(coarse_times, fps, measured_times), OCCURRENCES)

    # The triggers the engine's own measurements carry.
    measured = adaptive_signals.measured_triggers(
        digest, root=root, duration_seconds=duration)
    report["signals"] = measured["signals"]

    # The plan: which intervals the dense tier will inspect.
    plan = adaptive_inspection.plan_inspection(
        duration, fps, coarse_times, measured["triggers"])
    report["plan"] = {
        "dense_windows": plan["dense"]["window_count"],
        "covered_seconds": plan["dense"]["covered_seconds"],
        "budget_seconds": plan["dense"]["coverage_budget_seconds"],
        "deferred": len(plan["deferred"]),
        "skipped_static": len(plan["skipped_static"]),
    }

    # AFTER: the dense tier's answer.
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    inspection = adaptive_inspection.run_inspection(
        plan, source, timebase, out_dir=str(work / "dense"))
    report["after_cost"] = {
        "wall_seconds": round(time.perf_counter() - started, 1),
        **inspection["cost"],
    }
    named = [w["boundary"] for w in inspection["windows"] if w.get("boundary")]
    report["after"] = _score(named, OCCURRENCES)
    report["peak_rss_mb"] = _peak_rss_mb()

    Path(args.out_json).write_text(json.dumps(report, indent=1) + "\n",
                                    encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
