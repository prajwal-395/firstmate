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

Which lanes run is the retrieval contract's decision
(`contract.route_plan`), not an inline `if` per lane: the text lane runs
as `primary` when the plan asks for words and as `backstop` when it does
not but a requirement no other lane answered still could be.

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

from library.tools.retrieval import contract as retrieval_contract
from library.tools.retrieval.contract import (
    LANE_OCR,
    LANE_RELATIONAL,
    LANE_SOUND,
    LANE_TEXT,
    LANE_TIME,
    LANE_VISUAL,
)
from library.tools.retrieval.coverage import (
    absence_verdict,
    requirement_sufficiency,
)
from library.tools.retrieval.dedup import dedupe_windows
from library.tools.retrieval.requirements import (
    RetrievalRequirement,
    decompose,
)
from library.tools.retrieval.support import attach_support, support_summary


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

_KIND_LANE = {kind: lane for lane, kinds in LANE_KINDS.items()
              for kind in kinds}
"""The lane that owns each hit kind, for the evidence envelope."""


def _requirement_status(requirement: RetrievalRequirement, coverage: Mapping,
                        lane_ran: bool, hits: int,
                        lane_refused: bool = False) -> str:
    """What happened to ONE requirement.

    `answered` - the lane ran and returned something.
    `not_observed` - the lane ran over the whole scope and found nothing.
    `not_analyzed` - the lane never ran, or the measurement it needs was
    never made.
    `partial_coverage` - the measurement exists on some sources only.
    `not_attested` - no coverage report attests this requirement.
    `needs_named_query` - the relation is answered by a named project
    graph query rather than by free text.
    `refused` - the lane REFUSED the question: it says it cannot answer
    it (the frame lane refuses an action query, because a still frame
    cannot establish a gesture).  A refusal is not an observation: it
    must never be reported as `not_observed`, which claims the lane
    looked and saw nothing.
    """
    if requirement.lane == LANE_RELATIONAL:
        return "needs_named_query" if not hits else "answered"
    if lane_refused:
        return "refused"
    if hits:
        return "answered"
    verdict = requirement_sufficiency(requirement, coverage, lane_ran=lane_ran)
    if not verdict["attestable"]:
        return "not_attested"
    if not verdict["covered"]:
        return "partial_coverage" if verdict["clips"] else "not_analyzed"
    return "not_observed"


def _run_text_lane(index, query: str, *, top_k: int, mode: str,
                    floor: float | None, filters: Mapping | None,
                    policy: str, backstop_for: list | None = None) -> tuple:
    """The text embedding pass, and what it ran as.

    `policy` is the contract's verdict: `primary` (the plan asks for
    words) or `backstop` (a requirement no other lane answered still
    could be).  Either way the caller's facet `filters` are applied
    BEFORE ranking, so deterministic filtering precedes the expensive
    pass rather than trimming its answer after it.  `filters` is only
    passed through when set, so a duck-typed index that does not take
    the facet mapping keeps working.
    """
    call_kwargs: dict = {}
    if filters is not None:
        call_kwargs["filters"] = filters
    text_report = index.search_report(query, top_k=top_k, mode=mode,
                                      floor=floor, **call_kwargs)
    lane = {
        "lane": LANE_TEXT,
        "answer_status": text_report.get("answer_status"),
        "returned": len(text_report.get("results") or ()),
        "error": text_report.get("error"),
        # What the lane's answer MEANS, and the scope it measured: the
        # plan-level absence below is a claim about the whole plan, and
        # this is the per-lane claim under it.
        "abstention": text_report.get("abstention"),
        "policy": policy,
    }
    if filters:
        lane["filters"] = dict(filters)
    if backstop_for:
        lane["backstop_for"] = list(backstop_for)
    return text_report, lane


def search_footage(project_folder, query: str, *, top_k: int = 5,
                    mode: str = "hybrid", floor: float | None = None,
                    index=None, frame_index=None, merge_gap_s: float = 0.0,
                    use_visual: bool = True, coverage: Mapping | None = None,
                    sources: Mapping | None = None,
                    filters: Mapping | None = None) -> dict:
    """Answer a query as a PLAN, across the lanes the plan names.

    `index` and `frame_index` may be passed in when the caller already
    has them; otherwise the shipped ones are loaded for this project.
    `use_visual` turns the frame lane off, for a caller that only wants
    the measured text and sound lanes.  `coverage` and `sources` may be
    passed in when the caller already read them, and are read from the
    project when they are not.  `filters` are deterministic facets
    (clip_id, person, kinds, framing, ...) applied to the text lane
    BEFORE its embedding ranking - the same mapping `ren search`
    takes, so an equivalent request from the CLI names the same facets
    and gets the same candidates.

    Which lanes run is the retrieval contract's decision
    (`contract.route_plan`), not an inline `if` per lane: the text lane
    runs as `primary` when the plan asks for words and as `backstop`
    when it does not but a requirement no other lane answered still
    could be.  A query that is fundamentally visual or acoustic pays for
    no text embedding pass once its own lanes answered - and a skipped
    lane says so in `lanes`, with the reason, rather than reading as a
    lane that ran and found nothing.

    Every hit carries the contract's evidence envelope: its media
    interval, its uncertainty and its provenance, whatever lane
    produced it.

    The name is `search_footage` and not `search` because this MODULE is
    called `search`: a package attribute called `search` that held a
    function would shadow the module, and `from library.tools.retrieval
    import search` would then hand a caller the function where they
    asked for the module.
    """
    from library.tools.analysis import footage_query, footage_segments

    plan = decompose(query)
    decision = retrieval_contract.route_plan(plan, use_visual=use_visual)
    if index is None:
        index = footage_query.FootageIndex(project_folder)

    lanes: dict[str, dict] = {}
    rows: list[dict] = []
    lanes_ran: list[str] = []

    text_report: dict = {}
    if decision.text_policy == retrieval_contract.TEXT_PRIMARY:
        text_report, lanes[LANE_TEXT] = _run_text_lane(
            index, query, top_k=top_k, mode=mode, floor=floor,
            filters=filters, policy=retrieval_contract.TEXT_PRIMARY)
        lanes_ran.append(LANE_TEXT)
        rows.extend(_text_rows(text_report))

    if LANE_OCR in decision.lanes_to_run:
        ocr_report = index.ocr_search(query, top_k=top_k)
        lanes[LANE_OCR] = {
            "lane": LANE_OCR,
            "ran": not ocr_report.get("error"),
            "returned": len(ocr_report.get("hits") or ()),
            "error": ocr_report.get("error"),
            "available_texts": ocr_report.get("available_texts", []),
            "abstention": ocr_report.get("abstention"),
        }
        if not ocr_report.get("error"):
            lanes_ran.append(LANE_OCR)
        rows.extend(_ocr_rows(ocr_report))

    if LANE_SOUND in decision.lanes_to_run:
        sound_report = index.sound_search(query, top_k=top_k)
        lanes[LANE_SOUND] = {
            "lane": LANE_SOUND,
            "ran": not sound_report.get("error"),
            "returned": len(sound_report.get("hits") or ()),
            "error": sound_report.get("error"),
            "available_labels": sound_report.get("available_labels", []),
            "abstention": sound_report.get("abstention"),
        }
        if not sound_report.get("error"):
            lanes_ran.append(LANE_SOUND)
        rows.extend(_sound_rows(sound_report))

    if LANE_VISUAL in decision.lanes_to_run:
        visual_report, visual_error, visual_refused = _visual_report(
            project_folder, query, top_k, frame_index)
        lanes[LANE_VISUAL] = {
            "lane": LANE_VISUAL,
            # A REFUSED lane did not run: it says the question is not one
            # it can answer.  Reporting it as a lane that ran and found
            # nothing is a false observation (VI-005).
            "ran": visual_error is None and not visual_refused,
            "returned": len(visual_report.get("ranges") or ()),
            "error": visual_error,
            "refused": visual_refused,
            "abstention": visual_report.get("abstention"),
        }
        if visual_error is None and not visual_refused:
            lanes_ran.append(LANE_VISUAL)
        rows.extend(_visual_rows(visual_report))

    if LANE_TIME in decision.lanes_to_run:
        lanes[LANE_TIME] = {"lane": LANE_TIME, "ran": True, "returned": 0,
                            "error": None}
        lanes_ran.append(LANE_TIME)

    if LANE_RELATIONAL in decision.skipped:
        lanes[LANE_RELATIONAL] = {
            "lane": LANE_RELATIONAL,
            "ran": False,
            "returned": 0,
            "error": ("the project graph answers named questions "
                      "(reaction_shots, broll_matches, coverage), not free "
                      "text; issue the named query for this relation"),
        }

    if decision.text_policy == retrieval_contract.TEXT_BACKSTOP:
        needed, backstop_for = retrieval_contract.text_backstop_needed(
            plan, lanes_ran)
        if needed:
            text_report, lanes[LANE_TEXT] = _run_text_lane(
                index, query, top_k=top_k, mode=mode, floor=floor,
                filters=filters, policy=retrieval_contract.TEXT_BACKSTOP,
                backstop_for=backstop_for)
            lanes_ran.append(LANE_TEXT)
            rows.extend(_text_rows(text_report))
        else:
            lanes[LANE_TEXT] = {
                "lane": LANE_TEXT,
                "ran": False,
                "returned": 0,
                "error": None,
                "policy": retrieval_contract.TEXT_SKIP,
                "reason": retrieval_contract.SKIP_NO_REQUIREMENT,
            }

    for skipped_lane in (LANE_OCR, LANE_SOUND, LANE_VISUAL):
        if skipped_lane in decision.skipped and skipped_lane not in lanes:
            lanes[skipped_lane] = {
                "lane": skipped_lane,
                "ran": False,
                "returned": 0,
                "error": None,
                "reason": decision.skipped[skipped_lane],
            }

    deduped, dedup_report = dedupe_windows(rows, merge_gap_s=merge_gap_s)

    if sources is None:
        sources = footage_query.clip_sources(project_folder)
    frames = _frames(project_folder, frame_index)
    hits = attach_support(deduped, sources=sources, frames=frames)

    backends = {LANE_TEXT: text_report.get("backend_answered")}
    hits = [retrieval_contract.evidence_from_row(
        hit, lane=_KIND_LANE.get(hit.get("kind"), LANE_TEXT),
        backend=backends.get(_KIND_LANE.get(hit.get("kind"))),
        abstention=(lanes.get(_KIND_LANE.get(hit.get("kind"))) or {}
                    ).get("abstention"))
        for hit in hits]

    if coverage is None:
        coverage = footage_segments.coverage_report(project_folder)
    absence = absence_verdict(plan, found=bool(hits), coverage=coverage,
                               lanes_ran=lanes_ran)

    per_requirement = []
    for requirement in plan.requirements:
        kinds = LANE_KINDS.get(requirement.lane, ())
        lane_hits = sum(1 for row in hits if row.get("kind") in kinds)
        lane = lanes.get(requirement.lane) or {}
        per_requirement.append({
            **requirement.to_dict(),
            "hits": lane_hits,
            "status": _requirement_status(requirement, coverage,
                                          requirement.lane in lanes_ran, lane_hits,
                                          bool(lane.get("refused"))),
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
    """The frame lane's report, and why it could not answer.

    The frame lane REFUSES some questions by design - an action query
    is one a still frame cannot resolve - and it says so in the report
    it returns.  The refusal is carried out of here, because a lane that
    refused has not observed anything, and an answer that cannot say so
    reports a refusal as an empty observation.
    """
    from library.tools.analysis.footage_frames import FrameIndex
    from library.tools.ren_refusal import RenRefusal
    try:
        index = frame_index if frame_index is not None else FrameIndex(project_folder)
        report = index.search_ranges(query, top_ranges=top_k, temporal=True)
    except RenRefusal as refused:
        return {}, refused.render(), refused.render()
    except (OSError, KeyError, ValueError) as error:
        return {}, f"the frame lane could not run: {error}", None
    refusal = report.get("refused")
    return report, None, (refusal or None)


def _frames(project_folder, frame_index) -> list:
    """The cached stills, when a frame index exists."""
    from library.tools.analysis.footage_frames import FrameIndex
    from library.tools.ren_refusal import RenRefusal
    try:
        index = frame_index if frame_index is not None else FrameIndex(project_folder)
        return index.frames
    except (RenRefusal, OSError, KeyError, ValueError):
        return []
