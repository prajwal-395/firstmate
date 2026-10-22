"""Task-dependent editorial precision (F02).

A question about footage is answered at ONE temporal precision, and the
precision used to be the caller's accident rather than the caller's
choice: a rough-logging browse and a timeline cut both received the
same rows, and a frame-approximate observation (a 10 s vision window,
an 8 s chunk, a widened utterance passage) could land on the timeline
as though it were an exact event boundary.

This module owns the vocabulary that stops that:

* **The precisions are named.** ``browse`` is a ranking for rough
  logging and browsing; ``concept`` is a shot-level region for
  conceptual content searches; ``transient`` is a tighter check for
  transient actions and speech boundaries; ``cut`` is an exact
  source-clock interval for a timeline cut. The precision is an
  EXPLICIT query parameter (``FootageIndex.search`` /
  ``search_report``, ``edit_candidates.candidates_from_retrieval``),
  never a by-product of which tool was called.
* **Every answer says which precision it carries.** A hit is
  ``frame_exact`` only when its boundary is a measured source-clock
  reading (matching words with their own timings); every other row is
  ``frame_approximate`` and says so, with the basis. A frame-approximate
  row is never presented as an exact event boundary.
* **The edit path refuses an approximate boundary for a cut.**
  ``partition_for_cut`` / ``check_boundary_for_cut`` drop (with the
  reason REPORTED, never silently) what ``cut`` must not place.

What this module does NOT do: it does not measure anything, decode
anything, or hold an index. The lanes that measure keep their own
code; this module only says which precision a question needs, which
precision an answer carries, and whether the two meet.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping

PRECISION_BROWSE = "browse"
PRECISION_CONCEPT = "concept"
PRECISION_TRANSIENT = "transient"
PRECISION_CUT = "cut"

PRECISIONS: tuple[str, ...] = (
    PRECISION_BROWSE,
    PRECISION_CONCEPT,
    PRECISION_TRANSIENT,
    PRECISION_CUT,
)
"""The task-dependent precisions, coarsest first.

``browse`` is for rough logging and browsing; ``concept`` for
conceptual content searches (a shot-level region is the answer);
``transient`` for transient actions and speech boundaries (tighter
verification before anything is placed); ``cut`` for timeline cuts
(exact source-clock intervals only).
"""

FRAME_EXACT = "frame_exact"
FRAME_APPROXIMATE = "frame_approximate"

_REQUIREMENTS: dict[str, dict[str, Any]] = {
    PRECISION_BROWSE: {
        "description": ("rough logging and browsing: any measured row "
                        "answers, at whatever precision it carries"),
        "allows_approximate": True,
        "cut_allowed": False,
        "verification": ("none: the answer is a place to look, and each "
                         "row still names the precision it carries"),
    },
    PRECISION_CONCEPT: {
        "description": ("conceptual content searches: a shot-level region "
                        "is the answer, and a broader window is not a "
                        "defect"),
        "allows_approximate": True,
        "cut_allowed": False,
        "verification": ("none beyond the lane's own floor: the region "
                         "answers the question as asked"),
    },
    PRECISION_TRANSIENT: {
        "description": ("transient actions and speech boundaries: a "
                        "tighter check before anything is placed - an "
                        "approximate boundary carries what would verify "
                        "it, and a cut still waits for the exact one"),
        "allows_approximate": True,
        "cut_allowed": False,
        "verification": ("an approximate boundary is placed only with "
                         "needs_verification set and the verifying "
                         "evidence named; a cut refuses it"),
    },
    PRECISION_CUT: {
        "description": ("timeline cuts: exact source-clock intervals "
                        "only - a frame-approximate observation is "
                        "refused, never placed as an exact boundary"),
        "allows_approximate": False,
        "cut_allowed": True,
        "verification": ("frame_exact only: matching words with their "
                         "own source-clock timings, a frame-exact "
                         "refinement, or an exact measured boundary"),
    },
}


def precisions() -> tuple[str, ...]:
    """The precision names, coarsest first."""
    return PRECISIONS


def requirement_for(precision: str) -> dict[str, Any]:
    """The requirement a task precision carries.

    Raises KeyError naming an unknown precision: a rename fails loudly
    (AGENTS.md 10.1), it never returns a default.
    """
    try:
        requirement = _REQUIREMENTS[precision]
    except KeyError:
        raise KeyError(
            f"unknown editorial precision {precision!r}; one of "
            f"{list(PRECISIONS)}") from None
    return {"precision": precision, **{k: v for k, v in requirement.items()}}


def classify_hit(hit: Mapping[str, Any]) -> dict[str, str]:
    """The temporal precision one result row carries.

    ``frame_exact`` only when the row's boundary is a measured
    source-clock reading: a speech hit whose matching words carry
    their own timings, a row already classified upstream as
    ``frame_exact``, or a row naming an exact measured boundary. Every
    other row - a vision window, a chunk, a widened passage, a sound
    or OCR span, a scene or camera row - is ``frame_approximate``: its
    span is WHERE to look, not an event boundary.
    """
    carried = hit.get("temporal_precision")
    if carried in (FRAME_EXACT, FRAME_APPROXIMATE):
        return {"temporal_precision": carried,
                "basis": "classified upstream; carried, not re-decided"}
    word_hits = hit.get("word_hits")
    if isinstance(word_hits, list) and word_hits:
        return {"temporal_precision": FRAME_EXACT,
                "basis": ("the matching words carry their own "
                          "source-clock timings")}
    if hit.get("frame_index") is not None and hit.get("on_grid"):
        return {"temporal_precision": FRAME_EXACT,
                "basis": "a frame-exact refinement named this frame"}
    status = hit.get("boundary_status")
    if status == "exact":
        return {"temporal_precision": FRAME_EXACT,
                "basis": "an exact measured boundary"}
    kind = str(hit.get("kind") or "row")
    return {"temporal_precision": FRAME_APPROXIMATE,
            "basis": (f"a {kind} row's span is where to look, not a "
                      f"measured event boundary")}


def cut_allowed(hit: Mapping[str, Any]) -> bool:
    """Whether a result row may bound a timeline cut."""
    return classify_hit(hit)["temporal_precision"] == FRAME_EXACT


def annotate_hit(hit: Mapping[str, Any], requested: str) -> dict[str, Any]:
    """A copy of a result row carrying its precision block.

    The block names the precision the question asked for, the precision
    the row carries, whether the row may bound a cut, and - at
    ``transient`` - what would verify an approximate boundary.
    Raises KeyError on an unknown requested precision.
    """
    requirement_for(requested)
    classified = classify_hit(hit)
    out = dict(hit)
    block: dict[str, Any] = {
        "requested": requested,
        "temporal_precision": classified["temporal_precision"],
        "basis": classified["basis"],
        "cut_allowed": classified["temporal_precision"] == FRAME_EXACT,
    }
    if requested == PRECISION_TRANSIENT and block["temporal_precision"] == FRAME_APPROXIMATE:
        block["needs_verification"] = True
        block["verify_with"] = ("a frame-exact refinement "
                                "(library.tools.frame_exact.refine_boundary) "
                                "or word timings covering the boundary")
    if requested == PRECISION_CONCEPT:
        block["region"] = True
    out["precision"] = block
    return out


def partition_for_cut(
        hits: list[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split rows into what a cut may place and what it must refuse.

    Returns ``(placed, refused)``. A refused row carries its ``reason``:
    a frame-approximate observation is never placed on the timeline as
    though it were an exact event boundary, and the refusal is
    REPORTED - a dropped row that names no reason reads as a search
    that found nothing.
    """
    placed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for hit in hits:
        classified = classify_hit(hit)
        if classified["temporal_precision"] == FRAME_EXACT:
            placed.append(dict(hit))
        else:
            refused.append({"hit": dict(hit),
                            "reason": (f"frame-approximate boundary refused "
                                       f"for a cut: {classified['basis']}")})
    return placed, refused


def check_boundary_for_cut(
        *, temporal_precision: str | None = None,
        boundary_status: str | None = None,
        boundary_precision_seconds: float | None = None,
        tolerance_seconds: float | None = None) -> dict[str, Any]:
    """The verdict on one boundary a cut wants to sit on.

    Returns ``{"status": "placed"|"refused", "reasons": [...]}``. A
    frame-approximate observation, an estimated or open boundary, or a
    precision coarser than the tolerance is REFUSED with each reason
    listed; anything else is placed. A boundary that says nothing
    about itself is refused: an unmeasured precision is not an exact
    one.
    """
    reasons: list[str] = []
    if temporal_precision is None and boundary_status is None \
            and boundary_precision_seconds is None:
        reasons.append("the boundary carries no measured precision")
    if temporal_precision is not None \
            and temporal_precision != FRAME_EXACT:
        reasons.append(f"frame-approximate observation refused for a cut: "
                       f"{temporal_precision}")
    if boundary_status is not None and boundary_status != "exact":
        reasons.append(f"boundary status {boundary_status!r} is not exact")
    if boundary_precision_seconds is not None:
        try:
            precision = float(boundary_precision_seconds)
        except (TypeError, ValueError):
            reasons.append("boundary_precision_seconds is not a number")
            precision = None
        if precision is not None and tolerance_seconds is not None \
                and precision > float(tolerance_seconds):
            reasons.append(f"boundary precision {precision}s exceeds "
                           f"tolerance {tolerance_seconds}s")
    if reasons:
        return {"status": "refused", "reasons": reasons}
    return {"status": "placed", "reasons": []}
