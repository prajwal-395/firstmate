"""Run the lanes a PLAN asks for, and answer the plan, not just the query.

`FootageIndex.search_report` answers one question with one fused score.
That is the right shape for a search box.  It is the wrong shape for an
editor's question with several parts, because a fused score cannot say
which part it satisfied and an empty answer cannot say which part was
never searched.

This module is the connection between the two.  It takes a query,
decomposes it (`requirements.decompose`), runs the lane each requirement
needs - the SHIPPED lanes, not copies of them - collapses the windows
that describe the same moment (`dedup.dedupe_windows`), attaches the
references that let somebody check each hit (`support.attach_support`),
and finally says whether an empty answer is "not there" or "not looked
for" (`coverage.absence_verdict`).

**The lanes are the ones that already ship.**  The text lane is
`FootageIndex.search_report`, the on-screen-text lane is
`FootageIndex.ocr_search`, the acoustic lane is
`FootageIndex.sound_search` and the visual lane is
`FrameIndex.search_ranges`.  This module decides WHICH of them to run
and reads what they return; it does not re-implement retrieval.

**The relational lane is not a free-text lane, and is not faked.**  The
project graph answers named questions ("reaction_shots", "broll_matches")
rather than free text, so a relational requirement is reported as
needing a NAMED graph query - never as searched-and-empty.

**The answer vocabulary belongs to the evidence stack.**  The four states
come from `evidence_query`, the same ones the typed Project Model query
uses, so a caller that already reads one reads this one the same way.
"""

from __future__ import annotations

from collections.abc import Mapping

from library.tools.retrieval.coverage import (
    absence_verdict,
    requirement_sufficiency,
)
from library.tools.retrieval.dedup import dedupe_windows
from library.tools.retrieval.requirements import (
    RetrievalPlan,
    RetrievalRequirement,
    decompose,
)
from library.tools.retrieval.support import attach_support, support_summary

LANE_TEXT = "text"
LANE_VISUAL = "visual"
LANE_OCR = "ocr"
LANE_SOUND = "sound"
LANE_TIME = "time"
LANE_RELATIONAL = "relational"


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _window_row(*, segment_id: str, clip_id: str, filename: str, kind: str,
                start, end, score, text: str = "", extra: Mapping | None = None
                ) -> dict:
    start_s = _num(start)
    end_s = _num(end)
    row = {
        "segment_id": segment_id,
        "clip_id": clip_id,
        "filename": filename,
        "kind": kind,
        "start": start_s,
        "end": end_s,
        "duration": round(end_s - start_s, 3) if (
            start_s is not None and end_s is not None) else None,
        "score": _num(score),
        "text": text or "",
    }
    if extra:
        row.update({key: value for key, value in extra.items()
                    if value is not None})
    return row


def _text_rows(report: Mapping) -> list[dict]:
    rows = []
    for hit in report.get("results") or ():
        rows.append(_window_row(
            segment_id=hit.get("segment_id", ""),
            clip_id=hit.get("clip_id", ""),
            filename=hit.get("filename", ""),
            kind=hit.get("kind", "speech"),
            start=hit.get("start"),
            end=hit.get("end"),
            score=hit.get("score"),
            text=hit.get("text", ""),
            extra={"dense_score": hit.get("dense_score"),
                   "lexical_score": hit.get("lexical_score"),
                   "word_hits": hit.get("word_hits")}))
    return rows


def _ocr_rows(report: Mapping) -> list[dict]:
    rows = []
    for hit in report.get("hits") or ():
        rows.append(_window_row(
            segment_id=f"{hit.get('clip_id')}#ocr#{hit.get('text')}",
            clip_id=hit.get("clip_id", ""),
            filename=hit.get("filename", ""),
            kind="ocr",
            start=hit.get("start"),
            end=hit.get("end"),
            score=hit.get("confidence"),
            text=hit.get("text", "")))
    return rows


def _sound_rows(report: Mapping) -> list[dict]:
    rows = []
    for hit in report.get("hits") or ():
        rows.append(_window_row(
            segment_id=f"{hit.get('clip_id')}#sound#{hit.get('label')}",
            clip_id=hit.get("clip_id", ""),
            filename=hit.get("filename", ""),
            kind="sound",
            start=hit.get("start"),
            end=hit.get("end"),
            score=hit.get("confidence"),
            text=hit.get("label", "")))
    return rows


def _visual_rows(report: Mapping) -> list[dict]:
    rows = []
    for entry in report.get("ranges") or ():
        rows.append(_window_row(
            segment_id=f"{entry.get('clip_id')}#frames#{entry.get('start')}",
            clip_id=entry.get("clip_id", ""),
            filename=entry.get("filename", ""),
            kind="frames",
            start=entry.get("start"),
            end=entry.get("end"),
            score=entry.get("best_score"),
            text="",
            extra={"n_frames": entry.get("n_frames")}))
    return rows


LANE_KINDS = {
    LANE_TEXT: ("speech", "action", "scene", "camera", "object", "chunk"),
    LANE_VISUAL: ("frames",),
    LANE_OCR: ("ocr",),
    LANE_SOUND: ("sound",),
    LANE_TIME: (),
    LANE_RELATIONAL: (),
}
"""Which hit kinds answer which lane."""


def _lane_wanted(plan: RetrievalPlan, lane: str) -> bool:
    return any(requirement.lane == lane and requirement.mandatory
               for requirement in plan.requirements)


def _requirement_status(requirement: RetrievalRequirement, coverage: Mapping,
                        lane_ran: bool, hits: int) -> str:
    """What happened to ONE requirement.

    `answered` - the lane ran and returned something.
    `not_observed` - the lane ran over the whole scope and found nothing.
    `not_analyzed` - the lane never ran, or the measurement it needs was
    never made.
    `partial_coverage` - the measurement exists on some sources only.
    `not_attested` - no coverage report attests this requirement.
    `needs_named_query` - the relation is answered by a named project
    graph query rather than by free text.
    """
    if requirement.lane == LANE_RELATIONAL:
        return "needs_named_query" if not hits else "answered"
    if hits:
        return "answered"
    verdict = requirement_sufficiency(requirement, coverage, lane_ran=lane_ran)
    if not verdict["attestable"]:
        return "not_attested"
    if not verdict["covered"]:
        return "partial_coverage" if verdict["clips"] else "not_analyzed"
    return "not_observed"


def search_footage(project_folder, query: str, *, top_k: int = 5,
                    mode: str = "hybrid", floor: float | None = None,
                    index=None, frame_index=None, merge_gap_s: float = 0.0,
                    use_visual: bool = True, coverage: Mapping | None = None,
                    sources: Mapping | None = None) -> dict:
    """Answer a query as a PLAN, across the lanes the plan names.

    `index` and `frame_index` may be passed in when the caller already
    has them; otherwise the shipped ones are loaded for this project.
    `use_visual` turns the frame lane off, for a caller that only wants
    the measured text and sound lanes.  `coverage` and `sources` may be
    passed in when the caller already read them, and are read from the
    project when they are not.

    The name is `search_footage` and not `search` because this MODULE is
    called `search`: a package attribute called `search` that held a
    function would shadow the module, and `from library.tools.retrieval
    import search` would then hand a caller the function where they
    asked for the module.
    """
    from library.tools.analysis import footage_query, footage_segments

    plan = decompose(query)
    if index is None:
        index = footage_query.FootageIndex(project_folder)

    lanes: dict[str, dict] = {}
    rows: list[dict] = []
    lanes_ran: list[str] = []

    text_report = index.search_report(query, top_k=top_k, mode=mode, floor=floor)
    lanes[LANE_TEXT] = {
        "lane": LANE_TEXT,
        "answer_status": text_report.get("answer_status"),
        "returned": len(text_report.get("results") or ()),
        "error": text_report.get("error"),
    }
    lanes_ran.append(LANE_TEXT)
    rows.extend(_text_rows(text_report))

    if _lane_wanted(plan, LANE_OCR):
        ocr_report = index.ocr_search(query, top_k=top_k)
        lanes[LANE_OCR] = {
            "lane": LANE_OCR,
            "ran": not ocr_report.get("error"),
            "returned": len(ocr_report.get("hits") or ()),
            "error": ocr_report.get("error"),
            "available_texts": ocr_report.get("available_texts", []),
        }
        if not ocr_report.get("error"):
            lanes_ran.append(LANE_OCR)
        rows.extend(_ocr_rows(ocr_report))

    if _lane_wanted(plan, LANE_SOUND):
        sound_report = index.sound_search(query, top_k=top_k)
        lanes[LANE_SOUND] = {
            "lane": LANE_SOUND,
            "ran": not sound_report.get("error"),
            "returned": len(sound_report.get("hits") or ()),
            "error": sound_report.get("error"),
            "available_labels": sound_report.get("available_labels", []),
        }
        if not sound_report.get("error"):
            lanes_ran.append(LANE_SOUND)
        rows.extend(_sound_rows(sound_report))

    if use_visual and _lane_wanted(plan, LANE_VISUAL):
        visual_report, visual_error = _visual_report(project_folder, query,
                                                      top_k, frame_index)
        lanes[LANE_VISUAL] = {
            "lane": LANE_VISUAL,
            "ran": visual_error is None,
            "returned": len(visual_report.get("ranges") or ()),
            "error": visual_error,
        }
        if visual_error is None:
            lanes_ran.append(LANE_VISUAL)
        rows.extend(_visual_rows(visual_report))

    if _lane_wanted(plan, LANE_TIME):
        lanes[LANE_TIME] = {"lane": LANE_TIME, "ran": True, "returned": 0,
                            "error": None}

    if _lane_wanted(plan, LANE_RELATIONAL):
        lanes[LANE_RELATIONAL] = {
            "lane": LANE_RELATIONAL,
            "ran": False,
            "returned": 0,
            "error": ("the project graph answers named questions "
                      "(reaction_shots, broll_matches, coverage), not free "
                      "text; issue the named query for this relation"),
        }

    deduped, dedup_report = dedupe_windows(rows, merge_gap_s=merge_gap_s)

    if sources is None:
        sources = footage_query.clip_sources(project_folder)
    frames = _frames(project_folder, frame_index)
    hits = attach_support(deduped, sources=sources, frames=frames)

    if coverage is None:
        coverage = footage_segments.coverage_report(project_folder)
    absence = absence_verdict(plan, found=bool(hits), coverage=coverage,
                               lanes_ran=lanes_ran)

    per_requirement = []
    for requirement in plan.requirements:
        kinds = LANE_KINDS.get(requirement.lane, ())
        lane_hits = sum(1 for row in hits if row.get("kind") in kinds)
        per_requirement.append({
            **requirement.to_dict(),
            "hits": lane_hits,
            "status": _requirement_status(requirement, coverage,
                                          requirement.lane in lanes_ran, lane_hits),
        })

    return {
        "query": query,
        "plan": plan.to_dict(),
        "requirements": per_requirement,
        "hits": hits,
        "lanes": lanes,
        "lanes_ran": lanes_ran,
        "dedup": dedup_report,
        "support": support_summary(hits),
        "answer_status": absence["status"],
        "answer_state": absence["state"],
        "absence": absence,
        "coverage": coverage,
        "text_report": {
            "answer_status": text_report.get("answer_status"),
            "weak": text_report.get("weak", []),
            "best_rejected": text_report.get("best_rejected"),
            "error": text_report.get("error"),
            "degraded": text_report.get("degraded"),
        },
    }


def _visual_report(project_folder, query: str, top_k: int, frame_index):
    """The frame lane's report, or the reason it could not run."""
    from library.tools.analysis.footage_frames import FrameIndex
    from library.tools.ren_refusal import RenRefusal
    try:
        index = frame_index if frame_index is not None else FrameIndex(project_folder)
        report = index.search_ranges(query, top_ranges=top_k)
    except RenRefusal as refused:
        return {}, refused.render()
    except (OSError, KeyError, ValueError) as error:
        return {}, f"the frame lane could not run: {error}"
    return report, None


def _frames(project_folder, frame_index) -> list:
    """The cached stills, when a frame index exists."""
    from library.tools.analysis.footage_frames import FrameIndex
    from library.tools.ren_refusal import RenRefusal
    try:
        index = frame_index if frame_index is not None else FrameIndex(project_folder)
        return index.frames
    except (RenRefusal, OSError, KeyError, ValueError):
        return []
