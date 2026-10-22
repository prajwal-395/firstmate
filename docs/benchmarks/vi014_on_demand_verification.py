#!/usr/bin/env python3
"""VI-014: what on-demand verification costs, and what it finds.

The punch list asks for on-demand verification and names what it must
do: *"Bound the resource budget of these requests. Reuse already computed
observations and decoded media. Persist new evidence with accurate
provenance and coverage. Stop investigating when further analysis is
unlikely to affect the decision."*

This measures the lane on the two corpora the lane names:

**The M0 scenario** - the second occurrence of a repeated action on the
M0 fixture.  The ordinal resolution exists (``occurrence_identity`` and
``temporal_query.resolve_ordinal_reference``); what it does NOT do is
check the span it resolved against anything.  The fixture's own
occurrence set is a CANDIDATE set ("a candidate, never an answer"), so
the span the ordinal names rests on an inferred claim.

**One real reel plan** - the 31-reel plan of the scratch podcast
project, and the source spans it names.

Nothing here writes to the captain's media, and the real plan is READ.
The verdicts land in the source memory beside the measurements they
were made from.

    python3 docs/benchmarks/vi014_on_demand_verification.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import on_demand_verification as od
from library.tools import plan_span_verification as psv
from library.tools import source_memory

# ── The corpora ────────────────────────────────────────────────────

M0_MEMORY = Path("/private/tmp/ren-vi005/m0/memory")
"""The M0 fixture's own source memory: the coarse 2 Hz frame sample and
the candidate occurrence set of the device take."""

M0_DIGEST = "041638ddab37a0ccba4ab522ddac0ba525eb4e3f1e041e101e944a6c81725735"

M0_MODEL = Path("/tmp/m0-e2e/projects/m0-keys/project_model/snapshot.json")
"""The M0 project's own canonical Project Model: the ORDINAL occurrence
set the ordinal resolution reads ("the second time she opens the box"
resolves to occurrence 2 of a class, in time order)."""

M0_SECOND_OCCURRENCE = (33.30, 33.70)
"""The adjudicated band of the SECOND device-to-mouth onset on the M0
fixture, in seconds.

The band is AGENT-ADJUDICATED and PROVISIONAL: the baseline report
records that a human must re-adjudicate it before the fixture is used
as a scored benchmark.  It is used here as the CLAIM the lane is asked
to check, not as a ground truth.
"""

REEL_PROJECT = Path(os.path.expanduser(
    "~/Documents/content_stuff/video_projects/ren-scratch-l3wave"))
"""The scratch podcast project: 31 approved reels and their plan."""

MACHINE_MEMORY = Path(os.path.expanduser("~/.local/share/vep/source_memory"))
"""Where the podcast sources' own measurements live."""


def _m0_occurrences() -> list[dict]:
    """The fixture's ORDINAL occurrence set, in time order.

    The canonical Project Model is what the ordinal resolution reads:
    each event carries an ``occurrence`` block naming its class and its
    index, and the events are ordered by their own clock.
    """
    if not M0_MODEL.is_file():
        return []
    doc = json.loads(M0_MODEL.read_text(encoding="utf-8"))
    model = doc.get("model") or doc
    rows = []
    for event in model.get("events") or []:
        occurrence = event.get("occurrence") or {}
        if occurrence.get("class") != "hand_near_mouth":
            continue
        interval = event.get("interval") or {}
        ticks = 1000
        rows.append({"index": occurrence.get("index"),
                     "start": float(interval.get("start", 0)) / ticks,
                     "end": float(interval.get("end", 0)) / ticks,
                     "certainty": event.get("certainty")})
    return sorted(rows, key=lambda row: row["start"])


def _m0_measurement() -> dict:
    """What on-demand verification costs and answers on the M0 fixture.

    Two things are measured on the M0 material, because the ordinal
    claim rests on both:

    * **The ordinal answer.**  The canonical Project Model numbers the
      repeated occurrences of one action, so "the second time she opens
      the box" resolves to occurrence 2.  Every one of those answers
      carries ``certainty: inferred`` and ``coverage: sampled``: the
      ordinal answer is an INFERENCE over a coarse candidate set, which
      is exactly the claim class the lane exists for.
    * **The span the ordinal names.**  The lane is asked for MORE
      FRAMES on that exact span, and then for a LONGER SEGMENT around
      it, so the report says what the fixture's own sample can and
      cannot settle.
    """
    result: dict = {"status": "measured"}

    occurrences = _m0_occurrences()
    if occurrences:
        second = occurrences[1] if len(occurrences) > 1 else occurrences[0]
        result["ordinal"] = {
            "class": "hand_near_mouth",
            "occurrences_in_the_set": len(occurrences),
            "second_occurrence_index": second.get("index"),
            "second_occurrence_certainty": second.get("certainty"),
            "second_occurrence_span": [round(second.get("start", 0.0), 3),
                                       round(second.get("end", 0.0), 3)],
        }

    index_path = M0_MEMORY / M0_DIGEST / "frames.index.json"
    if not index_path.is_file():
        result["frames"] = {"status": "unavailable",
                            "reason": f"{index_path} is not on this machine"}
        return result
    index = json.loads(index_path.read_text(encoding="utf-8"))
    sample = [(float(f["t"]), f.get("frame_index"))
              for f in (index.get("frames") or [])]
    start, end = M0_SECOND_OCCURRENCE

    store = od.EvidenceStore.for_source(M0_DIGEST, M0_MEMORY)
    requests = {}
    for kind in (od.RequestKind.MORE_FRAMES, od.RequestKind.LONGER_SEGMENT):
        span = od.Span(source_file=str(index.get("source_file") or
                                       M0_MEMORY / M0_DIGEST),
                       start_s=start, end_s=end, content_digest=M0_DIGEST)
        request = od.Request(
            span=span,
            claim="the person brings the device to their mouth here",
            kind=kind,
            decision="the cut lands on the SECOND occurrence",
            budget_seconds=30.0)
        started = time.perf_counter()
        outcome = od.verify(request, store)
        elapsed = round(time.perf_counter() - started, 3)
        again = od.verify(request, store)
        requests[kind.value] = {
            "verdict": outcome.verdict.value,
            "stop_reason": outcome.stop_reason.value,
            "detail": outcome.detail,
            "coverage": outcome.coverage.to_dict(),
            "seconds": elapsed,
            "repeat_seconds": again.seconds,
            "repeat_reused": again.reused,
        }

    inside = [t for t, _ in sample if start <= t <= end]
    nearest = min(sample, key=lambda row: min(abs(row[0] - start),
                                              abs(row[0] - end))) if sample else None
    result["frames"] = {
        "source_frames_sampled": len(sample),
        "sample_rate_hz_nominal": index.get("rate_hz_nominal"),
        "largest_gap_seconds": round(
            max((b[0] - a[0] for a, b in zip(sample, sample[1:])), default=0.0), 3),
        "second_occurrence_band": [start, end],
        "band_status": "agent-adjudicated and provisional",
        "frames_inside_the_band": inside,
        "nearest_sample_frame": {"t": nearest[0], "frame_index": nearest[1]}
        if nearest else None,
        "nearest_frame_error_seconds": round(
            min(abs(nearest[0] - start), abs(nearest[0] - end)), 3)
        if nearest else None,
        "requests": requests,
    }
    return result


def _reel_measurement() -> dict:
    """What on-demand verification costs and answers on the real plan."""
    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import transcript_path

    if not REEL_PROJECT.is_dir():
        return {"status": "unavailable",
                "reason": f"{REEL_PROJECT} is not on this machine"}
    moments = read_proposal(proposal_path(str(REEL_PROJECT)))
    transcript = json.loads(
        transcript_path(str(REEL_PROJECT)).read_text(encoding="utf-8"))

    started = time.perf_counter()
    report = psv.verify_plan_spans(moments, transcript, str(REEL_PROJECT),
                                    budget_seconds=300.0,
                                    memory_root=str(MACHINE_MEMORY))
    cold = round(time.perf_counter() - started, 3)

    # The same sweep again: every verdict is recorded, so the second
    # sweep answers from the record and measures nothing.
    started = time.perf_counter()
    again = psv.verify_plan_spans(moments, transcript, str(REEL_PROJECT),
                                  budget_seconds=300.0,
                                  memory_root=str(MACHINE_MEMORY))
    warm = round(time.perf_counter() - started, 3)

    summary = report.to_dict()["summary"]
    return {
        "status": "measured",
        "moments": len(moments),
        "spans_total": summary["spans_total"],
        "spans_checked": summary["spans_checked"],
        "spans_unverified": summary["spans_unverified"],
        "supported": summary["supported"],
        "contradicted": summary["contradicted"],
        "unresolved": summary["unresolved"],
        "cold_seconds": cold,
        "warm_seconds": warm,
        "contradicted_findings": [
            {"reel": f.reel_number, "file": os.path.basename(f.source_file),
             "span": [f.source_start, f.source_end], "detail": f.detail}
            for f in report.by_verdict(od.Verdict.CONTRADICTED)],
    }


def main() -> int:
    result = {"m0": _m0_measurement(), "reel_plan": _reel_measurement()}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
