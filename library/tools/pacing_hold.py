"""EDIT-022: the moments where the cut should SLOW DOWN, not shorten.

The pipeline measured one direction of pacing well.  `library/tools/
pacing.py` finds DEAD SECTIONS - stretches where no word is sounding, the
picture is static and there is no audio activity - and proposes trims,
with the trim amount being the measured dead duration and nothing
invented.  That is a cut that wants to be shorter.

Nothing measured the other direction: the stretch that should be HELD.
The long pause in the middle of a passage, the sentence the speaker
delivered slowly, the beat before the answer.  Every module that saw
those seconds treated them as a gap to be closed.  A gap that is a beat
is not a gap.

This module finds the moments that measure as deliberate, and says so
with the same discipline `library/tools/alignment_findings.py` uses: it
REPORTS, it never decides, and it never fires a threshold on its own.
The evidence it carries is:

- **the speaker slowed here** - the block's delivery rate is below the
  material's own median block rate.  The median is the material's, so a
  speaker who is slow throughout is not read as slowing at every
  moment.
- **the delivery carries emphasis** - the prosody pass measured an
  emphasized word inside this block, and the emphasis is the speaker's
  own marking.
- **the direction asked for it** - the intent model's pacing curve
  declares a low energy for the beat this block serves.
- **the block holds a pause** - there is a measured silence inside the
  block, between two of its words.  A block whose words are separated by
  a long gap is holding something.

**A hold candidate always has words sounding.**  A stretch with no words
is DEAD, and dead air is `library/tools/pacing.py`'s business; proposing
to hold it would be proposing to keep nothing.  That test is the reason
this module and that one cannot be confused for each other.

**The length of the hold is the project's to say.**  A project declares
`pacing.target_asl_seconds` (the average shot length its cut aims for)
and `pacing.hold_minimum_seconds` (the shortest a deliberately held
moment may be).  Neither is declared here, and an undeclared slot is
read as undeclared: the proposal then says the moment is already as long
as it should be, and asks for no extra time at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.silence_reduction import emphasis_spans

#: The evidence a hold candidate may rest on.  One is enough; the
#: material decides how strong each one is.
SLOWED_HERE = "slowed_here"
EMPHASIS_INSIDE = "emphasis_inside"
DIRECTED_LOW_ENERGY = "directed_low_energy"
HOLDS_A_PAUSE = "holds_a_pause"

EVIDENCE_TEXT = {
    SLOWED_HERE:
        "this block delivers slower than the material's own median block",
    EMPHASIS_INSIDE:
        "the prosody pass measured an emphasized word inside this block",
    DIRECTED_LOW_ENERGY:
        "the direction's pacing curve declares a low energy for the beat "
        "this block serves",
    HOLDS_A_PAUSE:
        "there is a measured silence inside this block, between two of its "
        "words",
}


class PacingHoldError(ValueError):
    """A hold request could not be read."""


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def block_rate(block: Mapping[str, Any]) -> float | None:
    """How fast this block delivers its words, per second of its own span."""
    words = list(block.get("word_timestamps") or [])
    if not words:
        return None
    duration = _number(block.get("timeline_end")) - _number(block.get("timeline_start"))
    if duration <= 0:
        return None
    return len(words) / duration


def _internal_gap(block: Mapping[str, Any]) -> float:
    """The longest silence between two of this block's words."""
    words = sorted(
        (row for row in (block.get("word_timestamps") or [])
         if isinstance(row, Mapping)),
        key=lambda row: _number(row.get("source_start")))
    longest = 0.0
    for left, right in zip(words, words[1:]):
        gap = (_number(right.get("source_start"))
               - _number(left.get("source_end")))
        longest = max(longest, gap)
    return round(longest, 3)


def _declared_energy(block: Mapping[str, Any],
                     intent_model: Mapping[str, Any] | None) -> tuple[float | None, str]:
    """The energy the direction declared for the beat this block serves."""
    if not isinstance(intent_model, Mapping):
        return None, ""
    serves = block.get("serves_beats") or []
    if isinstance(serves, str):
        serves = [serves]
    for entry in intent_model.get("pacing_curve") or []:
        if not isinstance(entry, Mapping):
            continue
        beat_id = entry.get("beat_id")
        if beat_id in serves and isinstance(entry.get("energy"), (int, float)):
            return float(entry["energy"]), str(beat_id)
    return None, ""


def hold_candidates(blocks: Sequence[Mapping[str, Any]], *,
                    policy: Any = None,
                    prosody_profiles: Mapping[str, Mapping[str, Any]] | None = None,
                    intent_model: Mapping[str, Any] | None = None
                    ) -> dict[str, Any]:
    """The blocks that measure as deliberate, and the evidence each rests on.

    The list is ORDERED by the strength of the evidence: a block with
    three of the four signals comes before a block with one.  The order
    is over the signals, not over a number - there is no score, and a
    block with one signal is not "half as deliberate" as a block with
    two.
    """
    rows = [block for block in blocks or () if isinstance(block, Mapping)]
    rates = [rate for rate in (block_rate(block) for block in rows)
             if rate is not None]
    baseline = _median(rates)

    target = None
    hold_minimum = None
    if policy is not None:
        try:
            target = policy.slot("pacing.target_asl_seconds")
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            target = None
        try:
            hold_minimum = policy.slot("pacing.hold_minimum_seconds")
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            hold_minimum = None

    profiles = prosody_profiles or {}
    candidates = []
    for block in rows:
        words = list(block.get("word_timestamps") or [])
        if not words:
            # No words sounding is DEAD AIR, not a slow moment.  It is
            # library/tools/pacing.py's finding and this module says so
            # rather than proposing to hold nothing.
            continue
        rate = block_rate(block)
        evidence = []
        if rate is not None and baseline > 0 and rate < baseline:
            evidence.append(SLOWED_HERE)
        clip_id = str(block.get("clip_id") or "")
        profile = profiles.get(clip_id)
        if profile:
            spans = emphasis_spans(profile)
            block_start = _number(block.get("source_start"))
            block_end = _number(block.get("source_end"))
            inside = [span for span in spans
                      if span["end_seconds"] >= block_start
                      and span["start_seconds"] <= block_end]
            if inside:
                evidence.append(EMPHASIS_INSIDE)
        energy, beat_id = _declared_energy(block, intent_model)
        if energy is not None and energy <= 0.5:
            evidence.append(DIRECTED_LOW_ENERGY)
        gap = _internal_gap(block)
        if gap > 0:
            evidence.append(HOLDS_A_PAUSE)

        duration = _number(block.get("timeline_end")) - _number(block.get("timeline_start"))
        proposed = duration
        if hold_minimum is not None:
            proposed = max(duration, float(hold_minimum))
        elif target is not None:
            proposed = max(duration, float(target))
        candidates.append({
            "position": block.get("position"),
            "clip_id": clip_id,
            "timeline_start": block.get("timeline_start"),
            "timeline_end": block.get("timeline_end"),
            "duration_seconds": round(duration, 3),
            "delivery_rate_words_per_second": (round(rate, 3)
                                               if rate is not None else None),
            "baseline_rate_words_per_second": round(baseline, 3),
            "internal_gap_seconds": gap,
            "declared_energy": energy,
            "declared_beat": beat_id,
            "evidence": evidence,
            "why": "; ".join(EVIDENCE_TEXT.get(name, name) for name in evidence),
            "proposed_hold_seconds": round(proposed, 3),
            "hold_extension_seconds": round(max(0.0, proposed - duration), 3),
        })

    candidates.sort(key=lambda row: (-len(row["evidence"]),
                                     row["delivery_rate_words_per_second"]
                                     if row["delivery_rate_words_per_second"]
                                     is not None else 0.0,
                                     str(row["position"])))
    measured_asl = None
    if rows:
        timeline_seconds = 0.0
        for block in rows:
            timeline_seconds += (_number(block.get("timeline_end"))
                                  - _number(block.get("timeline_start")))
        if timeline_seconds > 0:
            measured_asl = round(len(rows) / timeline_seconds, 3)
    return {
        "baseline_rate_words_per_second": round(baseline, 3),
        "measured_average_shot_seconds": measured_asl,
        "target_average_shot_seconds": target,
        "hold_minimum_seconds": hold_minimum,
        "candidates": candidates,
    }
