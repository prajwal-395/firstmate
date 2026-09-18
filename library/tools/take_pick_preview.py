"""One-shot take-pick preview: what a lane re-reads by hand, printed once.

A lane picking a take currently reconstructs the same four facts with
dozens of inline ``python3 -c`` calls before recording anything - once
per candidate, at whatever a turn costs that day.  Measured on the
batch-5 lane record (``ses_f4b92eb63ffeHewCrUHLvdWEuQ``, 429 tool
calls): segment windows with speaker and text (calls 264-266, 334-335,
357-359), word head/tail tables per segment (266, 358-359), source
provenance per segment (188), the recorded keep exclusions and
insistences touching the span with the resulting keep ranges (208, 268,
270, 317, 337, 361), and the boundary-snap cascade the build would
otherwise surface only on a finished timeline (333, 356, 381, via the
lane's own ``prep.py`` wrapping ``preview_snap``).

This command prints those four together for one reel: the freshness
check (which recorded strikes and insistences touch this span), the
boundary-snap deltas (reused from ``reel_proposal.preview_snap``,
never re-derived), the cutter's current cuts with the keep-range
cascade the build would play, and the words.

IT PRINTS, IT DOES NOT DECIDE.  The take-pick is the model's judgement
and stays there: no section names a take to keep, to strike, or to
prefer, and no output ranks one telling over another.  ``duplicate_takes``
findings are reported with both texts and both timecodes, exactly as
``reel_proposal`` reports them - a measurement with its evidence, not a
pick.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from library.tools.reel_proposal import (
    SNAP_DECISION_SECONDS,
    duplicate_takes,
    preview_snap,
    render_snap_preview,
)


def _overlaps(a_start: float, a_end: float,
              b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def _segments_in_body(transcript: dict, start: float,
                      end: float) -> List[dict]:
    out = []
    for segment in (transcript or {}).get("segments") or ():
        try:
            b = float(segment.get("timeline_start",
                                  segment.get("start", 0.0)))
            e = float(segment.get("timeline_end",
                                  segment.get("end", 0.0)))
        except (TypeError, ValueError):
            continue
        if e > start and b < end:
            out.append(segment)
    out.sort(key=lambda s: float(s.get("timeline_start",
                                       s.get("start", 0.0))))
    return out


def preview_take_pick(project_folder: str, number: int,
                      threshold: float = SNAP_DECISION_SECONDS) -> Dict[str, Any]:
    """The four lane reads for one reel, as data.  Read-only.

    Raises ``LookupError`` for an unknown reel number and
    ``FileNotFoundError``/``ValueError`` where the stored proposal or
    the transcript is missing or unreadable - the caller turns those
    into a refusal rather than previewing nothing.
    """
    from library.tools import reel_build as rb
    from library.tools import transcript_corrections as tc
    from library.tools.reel_proposal import proposal_path, read_proposal
    from library.tools.timeline_transcript import transcript_path

    moments = read_proposal(str(proposal_path(project_folder)))
    moment = next((m for m in moments if int(m.number) == int(number)),
                  None)
    if moment is None:
        available = sorted(int(m.number) for m in moments)
        raise LookupError(
            f"no reel {number} in the stored proposal "
            f"(available: {available})")

    transcript_file = transcript_path(project_folder)
    transcript = json.loads(Path(transcript_file).read_text(
        encoding="utf-8"))

    body_start = float(moment.timeline_start)
    body_end = float(moment.timeline_end)
    cta = getattr(moment, "call_to_action", None)
    cta_span = None
    if cta is not None:
        cta_span = (float(cta.timeline_start), float(cta.timeline_end))

    exclusions = tc.keep_exclusions(project_folder)
    insistences = tc.keep_insistences(project_folder)
    touching_exclusions = tc.exclusion_cuts_for_span(
        body_start, body_end, exclusions)
    touching_insistences = tc.insisted_spans_for_span(
        body_start, body_end, insistences)

    cuts = rb.redundant_takes(body_start, body_end, transcript)
    _kept_cuts, withdrawn = rb.withdraw_insisted_cuts(
        cuts, touching_insistences)
    cascade_error = None
    keep_ranges: List[tuple] = []
    try:
        keep_ranges = rb.reel_ranges(
            moment, transcript,
            extra_cuts=touching_exclusions,
            insisted_spans=touching_insistences)
    except Exception as exc:  # noqa: BLE001 - the refusal IS the report
        cascade_error = str(exc)

    snap_report = preview_snap([moment], transcript, threshold)
    takes = duplicate_takes(body_start, body_end, transcript)

    return {
        "reel": int(moment.number),
        "slug": moment.slug,
        "approval": moment.approval.value,
        "reason": moment.reason,
        "body": [body_start, body_end],
        "cta": list(cta_span) if cta_span is not None else None,
        "threshold": float(threshold),
        "recorded_exclusions": len(exclusions),
        "recorded_insistences": len(insistences),
        "exclusions": [
            {"id": ident, "start": s, "end": e,
             "reason": next(
                 (x.get("reason", "") for x in exclusions
                  if str(x.get("id", "")) == str(ident)), "")}
            for s, e, ident in touching_exclusions
        ],
        "insistences": [
            {"id": ident, "start": s, "end": e,
             "reason": next(
                 (x.get("reason", "") for x in insistences
                  if str(x.get("id", "")) == str(ident)), "")}
            for s, e, ident in touching_insistences
        ],
        "cutter_cuts": [(float(c.dropped_start), float(c.dropped_end))
                        for c in cuts],
        "withdrawn": [((float(c.dropped_start), float(c.dropped_end)),
                       ident)
                      for c, ident in withdrawn],
        "keep_ranges": [(float(s), float(e)) for s, e in keep_ranges],
        "kept_total": round(sum(e - s for s, e in keep_ranges), 2),
        "cascade_error": cascade_error,
        "snap": snap_report,
        "takes": takes,
        "segments": _segments_in_body(transcript, body_start, body_end),
    }


def _first_line(text: str, limit: int = 160) -> str:
    line = (text or "").split("\n")[0].strip()
    return line[:limit]


def render_take_pick_preview(report: Dict[str, Any]) -> str:
    """The preview as lines, in the order a lane reads them.

    Freshness first (what recorded state touches this span), then the
    cutter and its cascade (what the build would play), then the snap
    (reused verbatim from ``render_snap_preview``), then the words.
    Nothing here names a take to keep or to strike.
    """
    number = int(report["reel"])
    body_start, body_end = (float(report["body"][0]),
                            float(report["body"][1]))
    lines = [
        f"take-pick preview: Reel {number:02d} "
        f"{report.get('slug', '')} "
        f"(approval: {report.get('approval', '?')})",
        f"  body {body_start:.3f}-{body_end:.3f} "
        f"({body_end - body_start:.2f}s)",
    ]
    if report.get("cta") is not None:
        cta_start, cta_end = (float(report["cta"][0]),
                              float(report["cta"][1]))
        lines.append(f"  cta  {cta_start:.3f}-{cta_end:.3f} "
                     f"({cta_end - cta_start:.2f}s), placed whole")
    else:
        lines.append("  cta  none declared")
    if report.get("reason"):
        lines.append(f"  ruled: {_first_line(report['reason'], 200)}")

    lines.append(
        f"freshness: {report.get('recorded_exclusions', 0)} keep "
        f"exclusion(s) recorded, "
        f"{len(report.get('exclusions') or [])} touch(es) this body; "
        f"{report.get('recorded_insistences', 0)} keep insistence(s) "
        f"recorded, "
        f"{len(report.get('insistences') or [])} touch(es) this body")
    for item in report.get("exclusions") or []:
        lines.append(
            f"  exclusion {item.get('id', '')} "
            f"{float(item['start']):.3f}-{float(item['end']):.3f}: "
            f"{_first_line(item.get('reason', ''))}")
    for item in report.get("insistences") or []:
        lines.append(
            f"  insistence {item.get('id', '')} "
            f"{float(item['start']):.3f}-{float(item['end']):.3f}: "
            f"{_first_line(item.get('reason', ''))}")
    if not report.get("exclusions") and not report.get("insistences"):
        lines.append("  no recorded strike or insistence touches "
                     "this body")

    cuts = report.get("cutter_cuts") or []
    lines.append(f"cutter: redundant_takes drops "
                 f"{len(cuts)} range(s) in this body")
    for s, e in cuts:
        lines.append(f"  cut {s:.3f}-{e:.3f}")
    for (s, e), ident in report.get("withdrawn") or []:
        lines.append(f"  withdrawn by insistence {ident}: "
                     f"cut {s:.3f}-{e:.3f}")
    if not cuts:
        lines.append("  the cutter drops nothing in this body")

    if report.get("cascade_error"):
        lines.append("cascade (what the build would play, before the "
                     "build):")
        lines.append(f"  REFUSES: {report['cascade_error']}")
    else:
        ranges = report.get("keep_ranges") or []
        lines.append("cascade (what the build would play, before the "
                     "build):")
        for s, e in ranges:
            lines.append(f"  keep {s:.2f}-{e:.2f} ({e - s:.2f}s)")
        lines.append(f"  kept total: {report.get('kept_total', 0):.2f}s "
                     f"across {len(ranges)} range(s)")

    lines.append(render_snap_preview(report.get("snap") or {}))

    takes = report.get("takes") or []
    lines.append(f"flagged duplicate takes in this body: {len(takes)}")
    for finding in takes:
        lines.append(
            f"  [{finding.get('band', '')}] "
            f"{float(finding['first_start']):.2f}-"
            f"{float(finding['first_end']):.2f} "
            f"\"{(finding.get('first_text') or '')[:80]}\" <-> "
            f"{float(finding['second_start']):.2f}-"
            f"{float(finding['second_end']):.2f} "
            f"\"{(finding.get('second_text') or '')[:80]}\" "
            f"({finding.get('similarity', '')})")

    segments: Sequence[dict] = report.get("segments") or []
    lines.append(f"words in this body ({len(segments)} segment(s)):")
    for segment in segments:
        try:
            b = float(segment.get("timeline_start",
                                  segment.get("start", 0.0)))
            e = float(segment.get("timeline_end",
                                  segment.get("end", 0.0)))
        except (TypeError, ValueError):
            continue
        bound = bool(segment.get("resolve_item_id"))
        lines.append(
            f"  [{b:.2f}-{e:.2f}] "
            f"{'bound' if bound else 'unbound'} "
            f"{segment.get('speaker', '?')}: "
            f"{(segment.get('text') or '')[:200]}")
        source = segment.get("source_file")
        if source:
            try:
                src_start = float(segment.get("source_start", 0.0))
                src_end = float(segment.get("source_end", 0.0))
                lines.append(
                    f"    src: {Path(source).name} "
                    f"[{src_start:.3f}->{src_end:.3f}]")
            except (TypeError, ValueError):
                pass
        tokens = []
        for word in segment.get("words") or ():
            try:
                ws, we = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if we > ws:
                tokens.append(
                    f"{word.get('word', '')}[{ws:.2f}-{we:.2f}]")
        if tokens:
            lines.append("    words: " + " ".join(tokens))
    if not segments:
        lines.append("  no transcript segments overlap this body")
    return "\n".join(lines)


def main(argv=None) -> int:
    """`python3 -m library.tools.take_pick_preview <project> <reel>`.

    Prints the freshness check, the cutter's cascade, the
    boundary-snap deltas and the words for one reel, and exits 0.  A
    preview that started naming a take would be a creative decision
    the pipeline is not allowed to make (AGENTS.md 10.5), so this
    prints measurements only.  Missing proposal or transcript, or an
    unknown reel number, refuses (exit 2) rather than previewing
    nothing.
    """
    import sys

    parser = argparse.ArgumentParser(
        prog="library.tools.take_pick_preview",
        description="Print what a lane re-reads by hand before "
                    "picking a take: freshness, cascade, snap deltas, "
                    "words. Prints only - the take-pick stays the "
                    "model's judgement.")
    parser.add_argument("project_folder")
    parser.add_argument("reel", type=int)
    parser.add_argument("--threshold", type=float,
                        default=SNAP_DECISION_SECONDS)
    args = parser.parse_args(
        list(sys.argv[1:] if argv is None else argv))
    try:
        report = preview_take_pick(
            args.project_folder, args.reel, args.threshold)
    except LookupError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"REFUSED: no readable proposal or transcript: {exc}",
              file=sys.stderr)
        return 2
    print(render_take_pick_preview(report))
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
