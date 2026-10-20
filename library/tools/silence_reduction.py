"""EDIT-016: silence and filler reduction that does not damage the rhythm.

The pipeline could MEASURE silence and it could MEASURE fillers, and it
could trim dead air - `library/tools/pacing.py` has done that since the
first cut.  What it could not do was say what a removal would COST.

That is the whole of this item.  "Remove the pauses" and "remove the
filler" are different edits with different prices, and nothing in the
repository priced either one:

- **The silence.**  A pause carries the speaker's beat.  Cutting it
  collapses two phrases into one and raises the passage's own delivery
  rate, which is what makes a tightened cut sound rushed.
- **The filler.**  "um" is a word with a duration.  Dropping the word
  and keeping the pause is a different cut from dropping the pause too,
  and the second is the one that damages rhythm.

So this module does two things and refuses a third.

**It measures the rhythm.**  `rhythm_profile` reads the passage's own
words and records its pauses, its phrase boundaries, its delivery rate
and, when the prosody pass measured any, the emphasis of each word.
Every number is a measurement of that passage, not a verdict.

**It prices a removal.**  `rhythm_cost` says what a given trim would
take: how much silence, how many phrase boundaries, what the delivery
rate becomes, and whether the trim lands inside a word.

**It refuses the two removals that damage rhythm, always:**

- a trim whose edge lands strictly inside a word clips a syllable;
- a trim that spans a pause LONGER THAN THE PASSAGE'S OWN MEDIAN PAUSE
  and that sits next to a word the prosody measured as emphasized removes
  a beat the speaker marked.  The median is the passage's own, so a
  speaker who pauses a lot is not read as pausing too much.

Everything else is REPORTED with its measured cost.  A long pause can be
dead air or a dramatic beat, and `library/tools/alignment_findings.py`
already refuses to decide which; this module keeps that line.  What it
adds is the PRICE, so the decision has something to be made against.

**The thresholds come from the project or from the material.**  A project
declares `silence.max_gap_seconds` and `silence.remove_fillers` in its
editorial policy (`library/tools/editorial_policy.py`).  A project that
declares neither gets the material's own distribution: the gap that
outlives nine of every ten gaps in the same material, the same
derivation `library/tools/pacing.py` uses for a dead section.  Nothing
here has a house-style default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.phrase_coherence import phrase_spans

#: The percentile of the material's own gap distribution that a silence
#: must exceed to be a candidate.  The derivation is the one
#: `library/tools/pacing.py` already uses for a dead section's minimum
#: duration: a gap that nine of ten gaps in the same material beat is
#: the material's own outlier, not a constant this module invented.
MATERIAL_GAP_PERCENTILE = 0.9

#: The emphasis above which a measured word counts as marked.  This is
#: the bound `library/tools/prosody_edit_candidates.py` already reads;
#: it is cited here, not re-derived, so the two modules cannot disagree
#: about which words are emphasized.
EMPHASIS_THRESHOLD = 0.5

#: The pause above which a phrase ends.  `library/tools/phrase_coherence.py`
#: reads the same bound as a parameter; it is stated here so a profile
#: says which bound it used.
PHRASE_PAUSE_SECONDS = 0.6

PRESERVED = "preserved"
DAMAGED = "damaged"
REFUSED = "refused"

#: Why a trim was refused.  Both are measurements, not tastes.
CLIPS_A_WORD = "clips_a_word"
REMOVES_A_BEAT_PAUSE = "removes_a_beat_pause"


class SilenceReductionError(ValueError):
    """A reduction request could not be read."""


def _ordered(words: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in words or ():
        if not isinstance(row, Mapping):
            continue
        try:
            start = float(row["source_start"])
            end = float(row["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        rows.append({
            "word": str(row.get("word") or ""),
            "source_start": start,
            "source_end": end,
        })
    rows.sort(key=lambda row: row["source_start"])
    return rows


def gaps_between(words: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every positive silence between two words, with its place.

    Abutting words produce no gap: a zero-length span is not an interval,
    and widening one would invent evidence.  The same rule
    `library/tools/speech_take.py` states for its own pauses.
    """
    ordered = _ordered(words)
    gaps = []
    for left, right in zip(ordered, ordered[1:]):
        start = left["source_end"]
        end = right["source_start"]
        if end > start:
            gaps.append({
                "start_seconds": start,
                "end_seconds": end,
                "seconds": round(end - start, 3),
                "before_word": left["word"],
                "after_word": right["word"],
            })
    return gaps


def emphasis_spans(prosody_profile: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The words the prosody pass measured as emphasized.

    The measurement is step 1.05's `word_prosody` rows, read exactly as
    `library/tools/prosody_edit_candidates.py` reads them.  A profile
    that measured no emphasis yields no spans, and a profile that was
    never run yields the same - absence, not zero emphasis.
    """
    if not isinstance(prosody_profile, Mapping):
        return []
    prosody = prosody_profile.get("prosody")
    if not isinstance(prosody, Mapping):
        prosody = prosody_profile
    rows = prosody.get("word_prosody")
    if not isinstance(rows, list):
        return []
    spans = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        emphasis = row.get("emphasis")
        if isinstance(emphasis, bool) or not isinstance(emphasis, (int, float)):
            continue
        if float(emphasis) < EMPHASIS_THRESHOLD:
            continue
        try:
            start = float(row["start"])
            end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        spans.append({
            "word": str(row.get("word") or ""),
            "start_seconds": start,
            "end_seconds": end,
            "emphasis": float(emphasis),
        })
    spans.sort(key=lambda span: span["start_seconds"])
    return spans


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def _percentile(values: Sequence[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def rhythm_profile(words: Sequence[Mapping[str, Any]],
                   *, prosody_profile: Mapping[str, Any] | None = None,
                   max_pause_seconds: float = PHRASE_PAUSE_SECONDS) -> dict[str, Any]:
    """What this passage's rhythm IS, measured from its own words.

    The profile is a MEASUREMENT.  It records the passage's pauses, which
    of them end a phrase, how fast the passage delivers its words, and
    which words the prosody pass marked.  It says nothing about whether
    any of that should change.
    """
    ordered = _ordered(words)
    gaps = gaps_between(ordered)
    spans = phrase_spans(ordered, max_pause_seconds=max_pause_seconds)
    spoken = sum(row["source_end"] - row["source_start"] for row in ordered)
    duration = 0.0
    if ordered:
        duration = ordered[-1]["source_end"] - ordered[0]["source_start"]
    gap_values = [gap["seconds"] for gap in gaps]
    median_gap = _median(gap_values)
    emphasis = emphasis_spans(prosody_profile)
    for gap in gaps:
        gap["longer_than_median"] = median_gap > 0 and gap["seconds"] > median_gap
        gap["beat_pause"] = bool(gap["longer_than_median"]
                                 and _adjacent_to_emphasis(gap, emphasis))
    return {
        "word_count": len(ordered),
        "duration_seconds": round(duration, 3),
        "spoken_seconds": round(spoken, 3),
        "delivery_rate_words_per_second": (
            round(len(ordered) / duration, 3) if duration > 0 else None),
        "speech_rate_words_per_second": (
            round(len(ordered) / spoken, 3) if spoken > 0 else None),
        "gap_count": len(gaps),
        "gap_total_seconds": round(sum(gap_values), 3),
        "median_gap_seconds": round(median_gap, 3),
        "largest_gap_seconds": round(max(gap_values), 3) if gap_values else None,
        "phrase_count": len(spans),
        "gaps": gaps,
        "emphasis": emphasis,
        "emphasis_measured": bool(emphasis),
        "max_pause_seconds": max_pause_seconds,
    }


def _adjacent_to_emphasis(gap: Mapping[str, Any],
                          emphasis: Sequence[Mapping[str, Any]]) -> bool:
    """Does a measured emphasis sit against this gap?

    "Adjacent" means within one word of it: the emphasized word ends where
    the pause begins, or the pause ends where the emphasized word begins.
    """
    for span in emphasis:
        if abs(float(span["end_seconds"]) - float(gap["start_seconds"])) <= 0.001:
            return True
        if abs(float(gap["end_seconds"]) - float(span["start_seconds"])) <= 0.001:
            return True
    return False


def rhythm_cost(profile: Mapping[str, Any], trim: Mapping[str, Any],
                words: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """What this trim would take from the passage's rhythm.

    The cost is a measurement and the verdict is one of three readings:

    - `preserved` - the trim takes silence only, and takes no beat;
    - `damaged` - the trim takes a beat pause, and the numbers say so;
    - `refused` - the trim lands inside a word, which clips a syllable.

    A `damaged` trim is reported, not proposed: whether a beat the
    speaker marked is worth keeping is a judgement, and the engine's job
    is to put the evidence in front of it.
    """
    try:
        start = float(trim["start_seconds"])
        end = float(trim["end_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SilenceReductionError(
            "a trim needs start_seconds and end_seconds") from exc
    if end <= start:
        raise SilenceReductionError("a trim must cover a positive span")

    ordered = _ordered(words)
    gaps = list(profile.get("gaps") or [])
    word_count = int(profile.get("word_count") or 0)
    duration = float(profile.get("duration_seconds") or 0.0)
    spoken = float(profile.get("spoken_seconds") or 0.0)

    clipped = None
    removed_words = []
    removed_word_seconds = 0.0
    for row in ordered:
        word_start = row["source_start"]
        word_end = row["source_end"]
        if word_start < start < word_end or word_start < end < word_end:
            clipped = row
            break
        if word_start >= start and word_end <= end:
            removed_words.append(row)
            removed_word_seconds += word_end - word_start

    covered = [gap for gap in gaps
               if gap["end_seconds"] > start and gap["start_seconds"] < end]
    removed_gap_seconds = sum(
        min(gap["end_seconds"], end) - max(gap["start_seconds"], start)
        for gap in covered)
    boundaries_removed = sum(1 for gap in covered if gap.get("longer_than_median"))
    beat_pauses = [gap for gap in covered if gap.get("beat_pause")]

    words_after = word_count - len(removed_words)
    duration_after = max(0.0, duration - (end - start))
    spoken_after = max(0.0, spoken - removed_word_seconds)
    rate_before = (word_count / duration) if duration > 0 else None
    rate_after = (words_after / duration_after) if duration_after > 0 else None

    cost = {
        "start_seconds": start,
        "end_seconds": end,
        "removed_seconds": round(end - start, 3),
        "removed_gap_seconds": round(removed_gap_seconds, 3),
        "removed_words": [row["word"] for row in removed_words],
        "removed_word_count": len(removed_words),
        "phrase_boundaries_removed": boundaries_removed,
        "phrase_count_after": max(0, int(profile.get("phrase_count") or 0)
                                  - boundaries_removed),
        "delivery_rate_before": (round(rate_before, 3)
                                 if rate_before is not None else None),
        "delivery_rate_after": (round(rate_after, 3)
                                if rate_after is not None else None),
        "speech_rate_after": (round(words_after / spoken_after, 3)
                              if spoken_after > 0 else None),
        "clips_word": clipped["word"] if clipped else None,
        "beat_pauses_removed": [
            {"start_seconds": gap["start_seconds"],
             "end_seconds": gap["end_seconds"],
             "seconds": gap["seconds"]}
            for gap in beat_pauses],
    }
    if clipped is not None:
        cost["verdict"] = REFUSED
        cost["reason"] = CLIPS_A_WORD
        cost["detail"] = (
            f"the trim lands inside the word {clipped['word']!r}, which "
            f"clips it")
        return cost
    if beat_pauses:
        cost["verdict"] = DAMAGED
        cost["reason"] = REMOVES_A_BEAT_PAUSE
        cost["detail"] = (
            "the trim spans "
            + ", ".join(f"{gap['seconds']}s" for gap in beat_pauses)
            + " of pause that sits beside a measured emphasis and is "
              "longer than this passage's own median pause")
        return cost
    cost["verdict"] = PRESERVED
    cost["reason"] = ""
    cost["detail"] = (
        f"the trim removes {cost['removed_gap_seconds']}s of silence, "
        f"{boundaries_removed} phrase boundary(ies) and no word")
    return cost


def material_gap_threshold(words: Sequence[Mapping[str, Any]],
                           *, percentile: float = MATERIAL_GAP_PERCENTILE) -> float:
    """The gap this MATERIAL treats as long, derived from its own gaps.

    A project that declares no `silence.max_gap_seconds` gets this, not
    a constant: the gap that nine of ten gaps in the same material fail
    to reach.  A material where every pause is short proposes nothing.
    """
    values = [gap["seconds"] for gap in gaps_between(words)]
    if not values:
        return 0.0
    return round(_percentile(values, percentile), 3)


def silence_candidates(words: Sequence[Mapping[str, Any]], *,
                       policy: Any = None,
                       prosody_profile: Mapping[str, Any] | None = None,
                       max_pause_seconds: float = PHRASE_PAUSE_SECONDS
                       ) -> dict[str, Any]:
    """Every silence this passage offers for reduction, and what each costs.

    The threshold is the project's declared `silence.max_gap_seconds`
    when there is one, and the material's own distribution when there is
    not.  Every candidate carries its measured rhythm cost, so a caller
    can see the price before it decides.
    """
    profile = rhythm_profile(words, prosody_profile=prosody_profile,
                             max_pause_seconds=max_pause_seconds)
    declared = None
    if policy is not None:
        try:
            declared = policy.slot("silence.max_gap_seconds")
        except Exception:  # noqa: BLE001 - a policy without the slot reads undeclared
            declared = None
    threshold = (float(declared) if declared is not None
                 else material_gap_threshold(words))
    candidates = []
    for gap in profile["gaps"]:
        if gap["seconds"] <= threshold:
            continue
        cost = rhythm_cost(profile, {
            "start_seconds": gap["start_seconds"],
            "end_seconds": gap["end_seconds"],
        }, words)
        candidates.append({
            "kind": "silence",
            "start_seconds": gap["start_seconds"],
            "end_seconds": gap["end_seconds"],
            "seconds": gap["seconds"],
            "before_word": gap["before_word"],
            "after_word": gap["after_word"],
            "threshold_seconds": threshold,
            "threshold_source": "policy" if declared is not None else "material",
            "rhythm_cost": cost,
        })
    return {
        "profile": profile,
        "threshold_seconds": threshold,
        "threshold_source": "policy" if declared is not None else "material",
        "candidates": candidates,
    }


def filler_candidates(words: Sequence[Mapping[str, Any]], *,
                      policy: Any = None,
                      prosody_profile: Mapping[str, Any] | None = None,
                      filler_vocabulary: Sequence[str] = (),
                      max_pause_seconds: float = PHRASE_PAUSE_SECONDS
                      ) -> dict[str, Any]:
    """Every filler this passage offers, and the two ways it could go.

    A filler is a WORD with a duration, and there are two edits hiding
    inside "remove the filler":

    - **keep the pause** - drop the word, leave the silence where it was.
      The pause structure is untouched and the delivery rate barely
      moves.
    - **close the pause** - drop the word and the silence around it, so
      the speech runs on.  This is the one that damages rhythm, and
      `rhythm_cost` says so when the pause beside it is a beat.

    Both are priced.  Which one to take is a judgement, and the numbers
    are here so the judgement has something to be made against.
    """
    from library.tools.retake_scan import FILLERS

    vocabulary = {str(token).lower() for token in (filler_vocabulary or FILLERS)}
    profile = rhythm_profile(words, prosody_profile=prosody_profile,
                             max_pause_seconds=max_pause_seconds)
    ordered = _ordered(words)
    permitted = False
    if policy is not None:
        try:
            permitted = policy.slot("silence.remove_fillers") is True
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            permitted = False
    candidates = []
    for index, row in enumerate(ordered):
        token = row["word"].lower().strip(".,!?;:")
        if token not in vocabulary:
            continue
        previous = ordered[index - 1] if index else None
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        keep_pause = {
            "start_seconds": row["source_start"],
            "end_seconds": row["source_end"],
        }
        close_pause = {
            "start_seconds": previous["source_end"] if previous else row["source_start"],
            "end_seconds": following["source_start"] if following else row["source_end"],
        }
        candidates.append({
            "kind": "filler",
            "word": row["word"],
            "start_seconds": row["source_start"],
            "end_seconds": row["source_end"],
            "seconds": round(row["source_end"] - row["source_start"], 3),
            "permitted": permitted,
            "options": [
                {"treatment": "keep_the_pause",
                 "rhythm_cost": rhythm_cost(profile, keep_pause, ordered)},
                {"treatment": "close_the_pause",
                 "rhythm_cost": rhythm_cost(profile, close_pause, ordered)},
            ],
        })
    return {
        "profile": profile,
        "permitted": permitted,
        "permission_source": "policy" if policy is not None else "undeclared",
        "candidates": candidates,
    }


def propose_reductions(words: Sequence[Mapping[str, Any]], *,
                       policy: Any = None,
                       prosody_profile: Mapping[str, Any] | None = None,
                       filler_vocabulary: Sequence[str] = (),
                       max_pause_seconds: float = PHRASE_PAUSE_SECONDS
                       ) -> dict[str, Any]:
    """The reductions this passage can take, and the ones it cannot.

    A reduction is proposed only when it costs the passage no word, no
    beat and no syllable.  Everything else is reported with the reason
    it was not proposed, because a silence the engine will not cut is
    still a silence the editor needs to see.
    """
    silence = silence_candidates(words, policy=policy,
                                 prosody_profile=prosody_profile,
                                 max_pause_seconds=max_pause_seconds)
    fillers = filler_candidates(words, policy=policy,
                                prosody_profile=prosody_profile,
                                filler_vocabulary=filler_vocabulary,
                                max_pause_seconds=max_pause_seconds)
    proposals = []
    reported = []

    for candidate in silence["candidates"]:
        cost = candidate["rhythm_cost"]
        if cost["verdict"] == PRESERVED:
            proposals.append({
                "kind": "silence",
                "start_seconds": candidate["start_seconds"],
                "end_seconds": candidate["end_seconds"],
                "seconds": candidate["seconds"],
                "removed_seconds": cost["removed_seconds"],
                "treatment": "close_the_pause",
                "why": (
                    f"a {candidate['seconds']}s silence, above this "
                    f"material's own {candidate['threshold_seconds']}s "
                    f"threshold, and it carries no beat"),
            })
        else:
            reported.append({
                "kind": "silence",
                "start_seconds": candidate["start_seconds"],
                "end_seconds": candidate["end_seconds"],
                "seconds": candidate["seconds"],
                "rhythm_cost": cost,
                "why_not": cost["detail"],
            })

    for candidate in fillers["candidates"]:
        if not candidate["permitted"]:
            reported.append({
                "kind": "filler",
                "word": candidate["word"],
                "start_seconds": candidate["start_seconds"],
                "end_seconds": candidate["end_seconds"],
                "seconds": candidate["seconds"],
                "why_not": (
                    "the project has not permitted filler removal; the "
                    "filler is reported, not proposed"),
            })
            continue
        for option in candidate["options"]:
            cost = option["rhythm_cost"]
            if cost["verdict"] == PRESERVED:
                proposals.append({
                    "kind": "filler",
                    "word": candidate["word"],
                    "start_seconds": cost["start_seconds"],
                    "end_seconds": cost["end_seconds"],
                    "seconds": candidate["seconds"],
                    "removed_seconds": cost["removed_seconds"],
                    "treatment": option["treatment"],
                    "why": (
                        f"the filler {candidate['word']!r} carries no beat "
                        f"and the treatment takes no word with it"),
                })
            else:
                reported.append({
                    "kind": "filler",
                    "word": candidate["word"],
                    "start_seconds": candidate["start_seconds"],
                    "end_seconds": candidate["end_seconds"],
                    "seconds": candidate["seconds"],
                    "treatment": option["treatment"],
                    "rhythm_cost": cost,
                    "why_not": cost["detail"],
                })

    return {
        "rhythm": silence["profile"],
        "silence_threshold_seconds": silence["threshold_seconds"],
        "silence_threshold_source": silence["threshold_source"],
        "filler_removal_permitted": fillers["permitted"],
        "proposals": proposals,
        "reported": reported,
    }
