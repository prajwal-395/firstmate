"""One reel's take-pick question, answered in a single read.

Between reels a worker answers the same question by re-reading several
large records: the reel proposal (which takes the build will cut), the
captain's recorded trims (whether their anchors still resolve - the
freshness the Reel 17 defect proved must be loud), the boundary-snap
report (how far each stored edge would move), and the transcript (what
words sit at each edge). Each re-read re-pays hundreds of KB into
context, and the inter-reel gaps average ~19 min/reel
(`data/vep-where-the-reel-wall-clock-goes/report.md` rec #2).

This module prints all four in one output, for one reel. It OWNS no
decision and changes none:

* candidate takes are `reel_build.redundant_takes` - the build's own
  function, called, never reimplemented;
* freshness is `captain_edits.check_span_retime_freshness` - the
  build's own function, called, never reimplemented;
* snap deltas and pulled-in/dropped words are
  `reel_proposal.preview_snap` over the single moment - the same call
  the build's ask path makes, narrowed to one reel;
* boundary words are read off the already-loaded transcript dict, one
  pass, printed once per edge - including edges the snap does not
  move, which the snap report alone never names.

READ-ONLY. No Resolve, no model, no state write: the proposal, the
transcript, the edits and the tail authorizations are each read once
and nothing is repaired, rewritten or re-decided. A reel that fails
the snap still builds exactly as before.

The freshness probe is pre-clip, and that is stated rather than
hidden: the build matches anchors against `placements()` spans (ranges
subdivided by master-timeline clip, which needs Resolve open), while
this preview matches against `reel_ranges()` spans (the same master
ranges, undivided). An anchor fully inside a range matches identically
under both; an anchor straddling a clip cut inside a range can read
HELD here and CANNOT-APPLY at build time. The build re-checks before
it places, so the preview can only ever under-warn at a clip edge,
never overrule.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.ren_refusal import RenRefusal  # noqa: E402

FPS = 24000 / 1001
"""The frame rate the reel build judges freshness in (`reel_build.py`
`fps = 24000 / 1001` at both freshness call sites). Drift is judged in
FRAMES, so a preview at any other rate would disagree with the build
about what drifted - a second answer to one question."""

THRESHOLD_DEFAULT = 2.0
"""The snap-decision bar, in seconds. Spelled here rather than imported
so this module's `--threshold` default and `reel_proposal`'s
`SNAP_DECISION_SECONDS` cannot disagree silently: `test_...` pins
them equal."""

BOUNDARY_CONTEXT_WORDS = 8
"""Timed words shown on each side of an edge. Enough to place the cut
in its sentence, few enough that four edges stay one screen."""


class TakePickPreviewError(RenRefusal):
    """One reel's preview cannot be answered."""


def resolve_moment(moments: Sequence, reel: str):
    """The one proposal moment `reel` names, or refuse.

    Addressed by number (`21`), slug (`lucy-origin`), or full
    timeline name (`Reel 21 - lucy-origin`). A moment that matches
    nothing refuses rather than previewing a neighbour: a take-pick
    answered for the wrong reel is the confidently-wrong result this
    module exists to stop.
    """
    want = str(reel or "").strip()
    for moment in moments or []:
        number = str(int(getattr(moment, "number", -1)))
        slug = str(getattr(moment, "slug", "") or "")
        name = str(getattr(moment, "timeline_name", "") or "")
        if want in (number, slug, name):
            return moment
    known = ", ".join(f"{int(m.number)} ({m.slug})" for m in (moments or []))
    raise TakePickPreviewError(
        f"no reel {want!r} in the stored proposal",
        f"the proposal holds: {known or 'nothing'}",
        "name a reel by number, slug or full timeline name - "
        "`ren propose` publishes the list",
    )


def boundary_words(
    transcript: dict,
    when: float,
    before: int = BOUNDARY_CONTEXT_WORDS,
    after: int = BOUNDARY_CONTEXT_WORDS,
) -> dict:
    """The timed words around `when`, read off the loaded transcript.

    One pass over the transcript's word stream, no disk re-read: the
    caller already holds the transcript this walks. Returns
    `{"before": [...], "after": [...]}` token lists, each
    `{"word", "start", "end"}` in transcript order - `before` ends at
    the last word starting at or before `when`, `after` starts at the
    first word starting after it.
    """
    stream = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = str(word.get("word") or "").strip()
            if token and end > start:
                stream.append({"word": token, "start": start, "end": end})
    pivot = next(
        (i for i, w in enumerate(stream) if w["start"] > float(when) + 1e-6),
        len(stream),
    )
    return {
        "before": stream[max(0, pivot - before) : pivot],
        "after": stream[pivot : pivot + after],
    }


def preview_take(
    project_folder: str, reel: str, threshold: float = THRESHOLD_DEFAULT
) -> dict:
    """Everything a take-pick for `reel` needs, read once each.

    Four disk reads - proposal, transcript, captain's edits, tail
    authorizations - then the build's own functions over what they
    returned. Raises `TakePickPreviewError` where a read fails or the
    reel is unknown, so a preview of nothing is a refusal, never an
    empty report that reads like a clean one.
    """
    from library.tools import captain_edits as _edits
    from library.tools import reel_proposal as _proposal
    from library.tools.tail_extend_authorization import (
        AuthorizationError,
        load_authorizations,
    )
    from library.tools.timeline_transcript import (
        transcript_path as _transcript_path,
    )

    try:
        moments = _proposal.read_proposal(str(_proposal.proposal_path(project_folder)))
    except (OSError, ValueError, _proposal.ProposalError) as exc:
        raise TakePickPreviewError(
            f"no readable reel proposal: {exc}",
            "the proposal is what `ren propose` publishes",
            "run `ren propose` first, then preview the reel",
        ) from exc
    moment = resolve_moment(moments, reel)
    try:
        transcript = json.loads(
            Path(_transcript_path(project_folder)).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise TakePickPreviewError(
            f"no readable transcript: {exc}",
            "the transcript is what the snap, the takes and the "
            "freshness all resolve against",
            "run the pipeline through the temporal index first, then preview the reel",
        ) from exc
    try:
        edits = _edits.load_edits(project_folder)
    except _edits.CaptainEditError as exc:
        raise TakePickPreviewError(
            f"captain_edits cannot be read: {exc}. A recorded trim "
            f"the preview cannot read must refuse, never preview "
            f"silently past it.",
            "the file the captain writes is unreadable",
            "fix the edit file, then preview the reel",
        ) from exc
    try:
        authorizations = load_authorizations(project_folder)
    except AuthorizationError as exc:
        raise TakePickPreviewError(
            f"{exc}",
            "a recorded yes the preview cannot read must refuse",
            "fix the authorizations file, then preview the reel",
        ) from exc

    # The build's own take scan, narrowed to this reel's body. What
    # the build will cut, in the build's own words (`Cut.basis`).
    from library.tools import reel_build as _build

    takes = _build.redundant_takes(
        moment.timeline_start, moment.timeline_end, transcript
    )

    # The build's own ranges, as freshness spans. Pre-clip (see the
    # module docstring): `placements()` needs the live Resolve
    # master, which a between-reels preview must not require.
    ranges = _build.reel_ranges(moment, transcript)
    spans = [{"master": (float(start), float(end))} for start, end in ranges]
    drifted, stale = _edits.check_span_retime_freshness(
        spans, transcript, edits, fps=FPS
    )

    # The build's own snap preview, narrowed to this moment - so this
    # report and the build agree word for word on every delta.
    snap = _proposal.preview_snap(
        [moment],
        transcript,
        float(threshold),
        tail_extend_authorizations=authorizations,
    )

    moves = {
        m["boundary"]: m for m in (snap.get("moments") or [{}])[0].get("moves") or []
    }
    edges = [
        ("body_start", float(moment.timeline_start)),
        ("body_end", float(moment.timeline_end)),
    ]
    cta = getattr(moment, "call_to_action", None)
    if cta is not None:
        edges += [
            ("cta_start", float(cta.timeline_start)),
            ("cta_end", float(cta.timeline_end)),
        ]
    boundaries = []
    for boundary, ruled in edges:
        move = moves.get(boundary) or {}
        snapped = float(move.get("now", ruled))
        words = boundary_words(transcript, snapped)
        boundaries.append(
            {
                "boundary": boundary,
                "ruled": ruled,
                "snapped": snapped,
                "delta": snapped - ruled,
                "needs_decision": bool(move.get("needs_decision", False)),
                "words_before": words["before"],
                "words_after": words["after"],
            }
        )

    return {
        "reel": int(moment.number),
        "slug": str(moment.slug),
        "timeline_name": str(moment.timeline_name),
        "approval": str(moment.approval.value),
        "body": [float(moment.timeline_start), float(moment.timeline_end)],
        "closer": (
            [float(cta.timeline_start), float(cta.timeline_end)]
            if cta is not None
            else None
        ),
        "threshold": float(threshold),
        "takes": [
            {
                "dropped_start": float(c.dropped_start),
                "dropped_end": float(c.dropped_end),
                "dropped_text": str(c.dropped_text),
                "kept_start": float(c.kept_start),
                "kept_end": float(c.kept_end),
                "kept_text": str(c.kept_text),
                "speaker": c.speaker,
                "basis": str(c.basis),
            }
            for c in takes
        ],
        "freshness": {"drifted": list(drifted), "stale": list(stale)},
        "snap": snap,
        "boundaries": boundaries,
    }


def _quote(tokens: Sequence[dict], limit: int = 90) -> str:
    text = " ".join(t["word"] for t in tokens)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def render_take_preview(report: dict) -> str:
    """The report as lines: takes, freshness, snap deltas, words."""
    lines = [
        f"take-pick: Reel {report['reel']:02d} - {report['slug']} "
        f"({report['approval']})",
        f"  body {report['body'][0]:.3f}s - {report['body'][1]:.3f}s"
        + (
            f", closes {report['closer'][0]:.3f}s - {report['closer'][1]:.3f}s"
            if report.get("closer")
            else ", no closer"
        ),
    ]
    takes = report.get("takes") or []
    if takes:
        lines.append(f"  takes: {len(takes)} candidate cut(s) the build will make")
        for take in takes:
            lines.append(
                f"    drop {take['dropped_start']:.3f}s - "
                f"{take['dropped_end']:.3f}s "
                f'"{_quote([{"word": w} for w in take["dropped_text"].split()])}"'
                f" -> keep {take['kept_start']:.3f}s - "
                f"{take['kept_end']:.3f}s"
                + (f" [{take['speaker']}]" if take.get("speaker") else "")
            )
            if take.get("basis"):
                lines.append(f"      why: {take['basis']}")
    else:
        lines.append("  takes: none - the build plays the body whole")
    drifted = (report.get("freshness") or {}).get("drifted") or []
    stale = (report.get("freshness") or {}).get("stale") or []
    if drifted or stale:
        lines.append(f"  freshness: {len(drifted)} drifted, {len(stale)} stale trim(s)")
        for record in drifted:
            lines.append(f"    DRIFTED: {record.get('reason', '')}")
        for record in stale:
            lines.append(f"    STALE: {record.get('reason', '')}")
    else:
        lines.append(
            "  freshness: clean - every recorded trim still "
            "resolves where it was recorded"
        )
    snap = report.get("snap") or {}
    lines.append(
        f"  snap: {snap.get('moved', 0)} boundar(y/ies) would "
        f"move, {snap.get('flagged', 0)} need(s) a decision "
        f"(over {float(report.get('threshold', 0.0)):.1f}s)"
    )
    for edge in report.get("boundaries") or []:
        context = (
            f"{_quote(edge['words_before'])} | {_quote(edge['words_after'])}"
        ).strip()
        lines.append(
            f"    {edge['boundary']} {edge['ruled']:.3f}s -> "
            f"{edge['snapped']:.3f}s ({edge['delta']:+.3f}s)"
            + (" NEEDS DECISION" if edge.get("needs_decision") else "")
            + (f"  words: {context}" if context != "|" else "")
        )
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`ren take-pick <project> --reel 21`: the one-shot preview.

    Four records read once, one output printed. Exit 0 with the
    report; exit 2 REFUSED where a record is missing or the reel is
    unknown, rather than previewing nothing. Nothing is written, no
    Resolve, no model.
    """
    parser = argparse.ArgumentParser(
        prog="take-pick-preview",
        description="Print one reel's candidate takes, trim "
        "freshness, boundary-snap deltas and boundary "
        "words in a single output. Read-only: the build "
        "decides nothing differently for this having run.",
    )
    parser.add_argument("project_folder")
    parser.add_argument(
        "--reel", required=True, help="Reel number, slug or full timeline name"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=THRESHOLD_DEFAULT,
        help="Snap moves over this many seconds need a decision (default 2.0)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the report as JSON instead of text"
    )
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        report = preview_take(args.project_folder, args.reel, args.threshold)
    except TakePickPreviewError as exc:
        print(exc.render(), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(render_take_preview(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
