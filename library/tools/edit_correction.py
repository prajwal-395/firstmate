"""edit_correction.py - close the loop on dead sections that reached the render.

`pacing.find_dead_sections` measures the stretches of the assembled
sequence where no word is sounding, the picture is static and there is
no audio activity, against thresholds derived from the sequence's own
distribution.  `pacing.propose_trims_for_dead_sections` turns each into
a trim proposal measured in the dead duration itself.  At plan time that
is where it stops: the trims are proposals the planner takes or
rejects, and a dead section the plan declines - or one the analysis
never ran on - ships in the render.

This module closes that loop on the as-built sequence.  Each iteration
measures the working sequence with the SAME `find_dead_sections` the
plan ran, applies the proposals it can apply, ripples the timeline, and
re-measures, until no dead section remains or the loop names why one
cannot be removed.  The re-measure is the production analysis on the
restructured sequence - the same code that found the section confirming
it is gone, not arithmetic agreeing with itself.

Two boundaries keep the loop honest.  Only head and tail trims are
applied: removing dead air at a block's start or end is a linear shift
of that block's anchors, while a mid-block pause can only close by
splitting the block - an editorial act the loop does not do, recorded
as refused with its reason.  And a trim that would eat a sounding word
is refused rather than applied: the cut point must fall in silence,
checked against the block's own word timestamps.

The loop's product is the VERIFIED restructure proposal, carried on the
loop record the validate bridge appends to the run's QA results - the
same findings channel the run log and the reviewer read.  Unlike a
placement or a level, a post-render trim is not written onto the live
manifest: nothing downstream reads it there.  The next compile
re-derives dead sections deterministically from the structure, so the
verified trim set reaches the next planning pass as findings, where the
planner takes it verbatim instead of re-proving it.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from library.tools.correction_loop import CorrectionLoop


def _is_finite(value: Any) -> bool:
    import math
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _block_by_position(blocks: List[dict], position: Any) -> Optional[dict]:
    for block in blocks:
        if isinstance(block, dict) and block.get("position") == position:
            return block
    return None


def _word_straddles_cut(block: dict, cut_source: float) -> bool:
    """Whether a sounding word spans the source-domain cut point."""
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["source_start"])
            end = float(entry["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < cut_source < end:
            return True
    return False


def _drop_words_outside(block: dict) -> None:
    """Drop words that fell outside the block's trimmed source span."""
    try:
        source_start = float(block.get("source_start", 0.0))
        source_end = float(block.get("source_end", 0.0))
    except (TypeError, ValueError):
        return
    kept = []
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        try:
            start = float(entry["source_start"])
            end = float(entry["source_end"])
        except (KeyError, TypeError, ValueError):
            kept.append(entry)
            continue
        if end <= source_start or start >= source_end:
            continue
        kept.append(entry)
    block["word_timestamps"] = kept


def apply_verified_trims(spine_blocks: List[dict],
                         trim_proposals: List[dict]) -> tuple:
    """Apply head/tail trims to a copy of the sequence and ripple it.

    Returns `(new_blocks, applied, refused)`.  Each applied trim shortens
    its block from the named edge by the measured dead duration and
    shifts every block starting at or after the trimmed block's original
    end earlier by the removed time.  Words are source-anchored, so they
    ride the ripple untouched; words left outside a trimmed source span
    are dropped, and a trim whose cut point falls inside a sounding word
    is refused rather than eating speech.  Mid-block pauses are refused:
    closing one needs a split, which the loop does not do.  A trim that
    would delete its block, names no block, or carries no number is
    refused with its reason - never applied, never silent.
    """
    blocks = [dict(b) if isinstance(b, dict) else b
              for b in (spine_blocks or [])]
    for block in blocks:
        if isinstance(block, dict) and isinstance(
                block.get("word_timestamps"), list):
            block["word_timestamps"] = list(block["word_timestamps"])

    ordered = sorted(
        (p for p in (trim_proposals or []) if isinstance(p, dict)),
        key=lambda p: (float(p.get("timeline_start", 0.0))
                       if _is_finite(p.get("timeline_start")) else 0.0))

    applied: List[dict] = []
    refused: List[dict] = []
    for proposal in ordered:
        try:
            trim = float(proposal.get("trim_seconds", 0.0))
        except (TypeError, ValueError):
            trim = 0.0
        position = proposal.get("block_position")
        edge = proposal.get("trim_edge")
        if not (trim > 0):
            refused.append({
                "proposal": proposal,
                "reason": "the trim carries no positive duration",
            })
            continue
        block = _block_by_position(blocks, position)
        if block is None:
            refused.append({
                "proposal": proposal,
                "reason": (f"no block at position {position!r} - the "
                           f"sequence moved under the proposal"),
            })
            continue
        try:
            tl_start = float(block.get("timeline_start", 0.0))
            tl_end = float(block.get("timeline_end", 0.0))
            source_start = float(block.get("source_start", 0.0))
            source_end = float(block.get("source_end", 0.0))
        except (TypeError, ValueError):
            refused.append({
                "proposal": proposal,
                "reason": "the block's anchors are not numbers",
            })
            continue
        block_span = tl_end - tl_start
        if trim >= block_span:
            refused.append({
                "proposal": proposal,
                "reason": (f"the trim ({trim:.3f}s) would delete the "
                           f"{block_span:.3f}s block"),
            })
            continue
        if edge == "start":
            cut_source = source_start + trim
            if _word_straddles_cut(block, cut_source):
                refused.append({
                    "proposal": proposal,
                    "reason": ("a sounding word spans the cut point - "
                               "the trim would eat speech"),
                })
                continue
            block["timeline_start"] = tl_start + trim
            block["source_start"] = cut_source
            removed, anchor_end = trim, tl_end
        elif edge == "end":
            cut_source = source_end - trim
            if _word_straddles_cut(block, cut_source):
                refused.append({
                    "proposal": proposal,
                    "reason": ("a sounding word spans the cut point - "
                               "the trim would eat speech"),
                })
                continue
            block["timeline_end"] = tl_end - trim
            block["source_end"] = cut_source
            removed, anchor_end = trim, tl_end
        else:
            refused.append({
                "proposal": proposal,
                "reason": ("a mid-block pause closes only by splitting "
                           "the block - an editorial act the loop does "
                           "not do"),
            })
            continue
        _drop_words_outside(block)
        for later in blocks:
            if not isinstance(later, dict) or later is block:
                continue
            try:
                later_start = float(later.get("timeline_start", 0.0))
            except (TypeError, ValueError):
                continue
            if later_start >= anchor_end:
                later["timeline_start"] = later_start - removed
                try:
                    later["timeline_end"] = float(
                        later.get("timeline_end", 0.0)) - removed
                except (TypeError, ValueError):
                    pass
        applied.append({
            "block_position": position,
            "trim_edge": edge,
            "trim_seconds": trim,
            "dead_span": [float(proposal.get("timeline_start", 0.0)),
                          float(proposal.get("timeline_end", 0.0))],
            "block_span_before": [tl_start, tl_end],
            "block_span_after": [float(block.get("timeline_start", 0.0)),
                                 float(block.get("timeline_end", 0.0))],
        })
    if applied:
        _pack_contiguous(blocks, applied)
    return blocks, applied, refused


def _pack_contiguous(blocks: List[dict], applied: List[dict]) -> None:
    """Close the gaps trimming leaves between blocks.

    A head trim shortens its block without moving it, so the block
    would start later than the previous block ends - removed time the
    ripple never reclaimed, and at the timeline head a void no block
    covers (and no analysis sees).  Walk the blocks in timeline order:
    the first keeps its start, or its pre-trim start when the trim took
    its head; every later block starts where the previous ends, keeping
    its duration.  Words are source-anchored, so they follow their
    block; source anchors are untouched, only timeline positions move.
    """
    by_position = {}
    for entry in applied:
        by_position.setdefault(entry.get("block_position"), entry)
    ordered = sorted(
        (b for b in blocks if isinstance(b, dict)),
        key=lambda b: float(b.get("timeline_start", 0.0))
        if _is_finite(b.get("timeline_start")) else 0.0)
    prev_end = None
    for block in ordered:
        try:
            tl_start = float(block.get("timeline_start", 0.0))
            tl_end = float(block.get("timeline_end", 0.0))
        except (TypeError, ValueError):
            continue
        duration = tl_end - tl_start
        entry = by_position.get(block.get("position"))
        if prev_end is None:
            if entry is not None and entry.get("trim_edge") == "start":
                before = entry.get("block_span_before") or [tl_start, tl_end]
                try:
                    head = float(before[0])
                except (TypeError, ValueError, IndexError):
                    head = tl_start
                block["timeline_start"] = head
                block["timeline_end"] = head + duration
                prev_end = head + duration
            else:
                prev_end = tl_end
            continue
        block["timeline_start"] = prev_end
        block["timeline_end"] = prev_end + duration
        prev_end = prev_end + duration


class EditCorrectionLoop(CorrectionLoop):
    """Iterate a sequence until no dead section remains in it.

    Holds the working blocks and their temporal indices, and runs the
    generic loop: measure the dead sections with the production
    analysis, propose the trims, apply what is applicable, re-measure
    the restructured sequence.  The production caller supplies
    `measure`/`apply` over the live manifest; the ripple and the
    iteration discipline are the same, and are what the fixtures here
    pin.
    """

    def __init__(self, spine_blocks: List[dict],
                 temporal_indices: Dict[str, dict], *,
                 max_iterations: int = 3):
        from library.tools import pacing as _pacing

        self._blocks = spine_blocks
        self._indices = temporal_indices
        self._pacing = _pacing
        self.applied: List[dict] = []
        self.refused: List[dict] = []
        super().__init__(
            measure=self._measure,
            correct=self._correct,
            apply=self._apply,
            max_iterations=max_iterations,
            name="dead_sections",
        )

    def _measure(self) -> dict:
        dead = self._pacing.find_dead_sections(
            self._blocks, self._indices)
        return {
            "passed": not dead,
            "dead_sections": dead,
            "dead_count": len(dead),
        }

    def _correct(self, measurement: dict) -> Optional[dict]:
        dead = measurement.get("dead_sections") or []
        if not dead:
            return None
        proposals = self._pacing.propose_trims_for_dead_sections(
            dead, self._blocks)
        # One dead span is reported once per clip it touches, and each
        # record proposes on every block the span overlaps - so the same
        # trim arrives once per reporting clip. Deduplicate on the full
        # key: two proposals from the same dead span are the same trim,
        # while two spans trimming one block are genuinely two trims.
        unique = []
        seen_keys = set()
        for proposal in proposals:
            if not isinstance(proposal, dict):
                continue
            key = (proposal.get("block_position"),
                   proposal.get("trim_edge"),
                   proposal.get("trim_seconds"),
                   proposal.get("timeline_start"),
                   proposal.get("timeline_end"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique.append(proposal)
        applicable = [p for p in unique
                      if p.get("trim_edge") in ("start", "end")]
        if not applicable:
            measurement["cannot_correct_reason"] = (
                f"{len(dead)} dead section(s), all mid-block pauses - "
                f"each closes only by splitting its block, which the "
                f"loop does not do")
            return None
        return {"trims": applicable}

    def _apply(self, correction: dict) -> None:
        self._blocks, applied, refused = apply_verified_trims(
            self._blocks, correction["trims"])
        self.applied.extend(applied)
        self.refused.extend(refused)

    @property
    def blocks(self) -> List[dict]:
        """The working sequence as restructured so far."""
        return self._blocks


def correct_pacing(
        spine_blocks: List[dict],
        temporal_indices: Dict[str, dict],
        *,
        max_iterations: int = 3) -> dict:
    """Close the loop on the dead sections in this sequence.

    Runs the `EditCorrectionLoop`: measure with the production
    dead-section analysis, apply the head/tail trims, ripple, and
    re-measure the restructured sequence until it is clean or the loop
    names why a section cannot go.  Returns the loop record carrying the
    verdict, the full iteration history, one entry per dead section the
    loop ever saw with its outcome, and the verified trim set - the
    restructure proposal the next planning pass takes verbatim.
    """
    loop = EditCorrectionLoop(
        [dict(b) if isinstance(b, dict) else b
         for b in (spine_blocks or [])],
        temporal_indices or {}, max_iterations=max_iterations)
    result = loop.run()

    from library.tools import pacing as _pacing

    iterations = result.as_dict()["iterations"]
    opening = ((iterations[0].get("measurement") or {}).get(
        "dead_sections") or [] if iterations else [])
    remeasured = _pacing.find_dead_sections(
        loop.blocks, temporal_indices or {})

    def span_of(dead: dict) -> tuple:
        return (round(float(dead.get("timeline_start", 0.0)), 3),
                round(float(dead.get("timeline_end", 0.0)), 3))

    sections = []
    for dead in opening:
        if not isinstance(dead, dict):
            continue
        key = span_of(dead)
        trimmed = any(
            (round(float(a.get("dead_span", [0, 0])[0]), 3),
             round(float(a.get("dead_span", [0, 0])[1]), 3)) == key
            for a in loop.applied)
        refusal = None
        for refused in loop.refused:
            proposal = refused.get("proposal") or {}
            if (round(float(proposal.get("timeline_start", 0.0)), 3),
                    round(float(proposal.get("timeline_end", 0.0)), 3)) == key:
                refusal = refused.get("reason")
                break
        sections.append({
            "span": list(key),
            "duration_seconds": dead.get("duration_seconds"),
            "reasons": dead.get("reasons"),
            "trim_applied": trimmed,
            "refusal": refusal,
        })

    return {
        "verdict": result.verdict,
        "passed": result.passed,
        "detail": result.detail,
        "iterations": iterations,
        "sections": sections,
        "remaining": [
            {"span": [round(float(d.get("timeline_start", 0.0)), 3),
                       round(float(d.get("timeline_end", 0.0)), 3)],
             "duration_seconds": d.get("duration_seconds"),
             "reasons": d.get("reasons")}
            for d in remeasured if isinstance(d, dict)],
        "applied_trims": loop.applied,
        "refused_trims": loop.refused,
        "restructured_span_count": len(loop.blocks),
    }
