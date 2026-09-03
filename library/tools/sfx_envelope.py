"""What a sound's measured ENVELOPE is, and what the engine does with it.

One enumeration, because the same four words decide two things in two
files and nothing said so in either.

The library profiles every playable sound and records an
``technical.energy_profile.envelope_shape`` - ``punchy``, ``swelling``,
``fading``, ``sustained``, or nothing where the profiler measured none.
Step 4.04's post-bridge KEYS ITS PLACEMENT on that word: a punchy sound
is snapped onto the nearest transient, and **a swelling sound is anchored
by its END so that its climax lands on the moment** rather than its
attack.  That second one is a build, and the engine already does it.

Nobody told the planner.  The catalogue map prints an ``envelope`` column
per sound and never says what the column means or that anything reads it,
so on project 001 the answer was two camera shutters - 0.46 s and 0.34 s,
one of them nominally ``swelling`` and far too short to read as one -
placed at two blocks, layering nothing and building nothing.  The
planner's own ``could_not_determine`` recorded the reasoning: it judged
the library *"built for a different kind of edit"*.

**That was a judgement made without the two facts that would have
informed it**, and both are measurements rather than opinions:

1. the placement mechanism above, which is a fact about the code; and
2. what the library actually holds - measured on the captain's library on
   2026-09-03: 78 sounds, of which 42 are ``swelling`` and 31 of those run
   2 s or longer, the longest 76 s.  A catalogue ordered by folder reads
   like whooshes and impacts; the envelope column says otherwise, and
   nothing had ever totalled it.

**This states no preference.**  It does not say a moment should carry a
build, or that the plan should layer.  Whether a sound is right for a
moment is the supervising sound editor's, and `library/tools/craft_role.py`
is where that role is stated.  This module is the mechanism written down -
the `MEASUREMENT_LEGEND` route, which defines what a key IS and never what
to conclude (AGENTS.md 10.5).

`tests/test_sfx_envelope.py`.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

- **What a sound's measured ENVELOPE buys is written down, in ONE place.** `library/tools/sfx_envelope.py`. [why](docs/RULE_EVIDENCE.md#the-catalogue-column-nobody-said-was-read) The library profiles `punchy`/`swelling`/`fading`/`sustained` per sound and step 4.04's post-bridge KEYS ITS PLACEMENT on that word - **a `swelling` sound is anchored by its END so its climax lands on the moment, which is what a build IS and the engine already does it**. Nothing said so: the catalogue map printed an `envelope` column and never named a reader, and 001 shipped two 0.3-0.5s camera shutters, layering nothing, on a planner that judged the library *"built for a different kind of edit"*. `post_bridge._describe_placement` and the prompt's `sfx_envelope_legend` are rendered from the SAME table, so they cannot drift.
- **What the library HOLDS is counted, not asserted.** `sfx_envelope.library_shape` measures per envelope on every run - on the captain's library 2026-09-03: 78 sounds, 42 `swelling`, 31 of those 2s or longer, longest 76s. A catalogue ordered by folder reads like its folder names. **It counts and recommends nothing**; whether a moment earns a sound is the sound editor's, and `craft_role` is where that role is stated (§3).
- `tests/test_sfx_envelope.py`, `tests/test_sfx_layering_and_build.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: What a sound with no measured envelope reads as.  Not one of the four:
#: "the profiler measured no shape" and "the shape is flat" are different
#: facts, and the placement pass already treats them differently.
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class Envelope:
    """One measured shape, and what the placement pass does with it.

    Attributes:
        shape: the word the library's profiler writes.
        what_it_is: the shape in time, as a listener would describe it.
        engine_does: what step 4.04's post-bridge does with a sound
            carrying this shape.  A statement about the CODE - keep it
            in step with `post_bridge._place_sfx_by_signal`.
        anchored_by: which end of the sound is pinned to the moment.
    """

    shape: str
    what_it_is: str
    engine_does: str
    anchored_by: str


#: The four the profiler measures.  A shape outside this table reads as
#: `UNMEASURED`, which is what the placement pass already falls back to.
ENVELOPES: Tuple[Envelope, ...] = (
    Envelope(
        shape="punchy",
        what_it_is=(
            "a single sharp attack at the head, decaying away - an impact, "
            "a click, a shutter."),
        engine_does=(
            "snapped onto the nearest measured audio transient within "
            "200 ms, then onto an energy peak within 500 ms if there was "
            "no transient, then quantised onto the music's beat grid if "
            "one is within 50 ms."),
        anchored_by="its start",
    ),
    Envelope(
        shape="swelling",
        what_it_is=(
            "energy that RISES through the sound and arrives at the end - "
            "a riser, a build, a swell, a rev."),
        engine_does=(
            "anchored by its END: the start is placed so that "
            "start + duration lands on the nearest measured energy peak "
            "within 2 s. The sound therefore rises INTO the moment "
            "instead of landing on it as one hit."),
        anchored_by="its end",
    ),
    Envelope(
        shape="fading",
        what_it_is=(
            "energy that falls away from a soft head - a tail, a wash, a "
            "decay with no hard attack."),
        engine_does=(
            "aligned to the nearest scene boundary or block edge within "
            "500 ms, then tightened onto a transient within 100 ms."),
        anchored_by="its start",
    ),
    Envelope(
        shape="sustained",
        what_it_is=(
            "level energy across its whole length - a bed, a tone, a room, "
            "a drone."),
        engine_does=(
            "aligned the same way as `fading`: the nearest scene boundary "
            "or block edge within 500 ms, then tightened onto a transient "
            "within 100 ms."),
        anchored_by="its start",
    ),
)

ENVELOPES_BY_SHAPE: Dict[str, Envelope] = {e.shape: e for e in ENVELOPES}

#: What the placement pass does with a sound nothing measured.  Kept here
#: with the four so `_describe_placement` has ONE table to read.
UNMEASURED_PLACEMENT = (
    "kept at the position the block gives it, snapped onto a measured "
    "transient only if one is already within 100 ms.")


def placement_of(envelope: str) -> str:
    """What the engine does with a sound of this shape, in one line.

    `post_bridge._describe_placement` reads this, so the sentence in the
    manifest and the sentence in the prompt are the same sentence.
    """
    known = ENVELOPES_BY_SHAPE.get((envelope or "").strip())
    return known.engine_does if known else UNMEASURED_PLACEMENT


def envelope_legend() -> Dict[str, str]:
    """The four shapes and what each buys, as DATA beside the context.

    Rendered from `ENVELOPES`, so the legend cannot drift from the table
    the placement code reads.
    """
    legend = {
        "what_an_envelope_is": (
            "the shape a sound's energy takes over its own length, "
            "MEASURED by the library's profiler for every sound it holds "
            "and printed in the `envelope` column of the catalogue map. It "
            "is not a word you type: you name an `sfx_id`, and that "
            "sound's measured shape decides how the engine anchors it to "
            "the picture."),
    }
    for envelope in ENVELOPES:
        legend[envelope.shape] = (
            f"{envelope.what_it_is} Anchored by {envelope.anchored_by}: "
            f"{envelope.engine_does}")
    legend[UNMEASURED] = (
        f"the profiler measured no shape for this sound. It is an admitted "
        f"absence and never a claim that the sound is flat. Such a sound "
        f"is {UNMEASURED_PLACEMENT}")
    return legend


def _duration(entry: Any) -> Optional[float]:
    if not isinstance(entry, dict):
        return None
    value = entry.get("duration_seconds")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def library_shape(catalog: Sequence[Any],
                  long_enough_seconds: float = 2.0) -> dict:
    """What this library actually HOLDS, per envelope, measured.

    Args:
        catalog: `sfx_library.load_sfx_catalog()`'s rows.
        long_enough_seconds: the length above which a count is reported
            separately.  It is a REPORTING boundary and not a rule about
            what may be chosen - every sound of every length is in the
            catalogue and reachable.  Two seconds because that is the
            order of a cut in a sixty-second piece, so it is the length
            at which a sound spans one rather than marking it.

    Returns:
        A record per measured shape, plus a one-line summary. Counts and
        length ranges only: nothing here ranks, shortlists or recommends,
        because whatever picks a shortlist becomes the chooser
        (AGENTS.md 10.5).
    """
    buckets: Dict[str, List[float]] = {}
    unmeasured_length = 0
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        shape = (entry.get("envelope") or "").strip() or UNMEASURED
        duration = _duration(entry)
        if duration is None:
            unmeasured_length += 1
            buckets.setdefault(shape, [])
            continue
        buckets.setdefault(shape, []).append(duration)

    per_shape = {}
    for shape in list(ENVELOPES_BY_SHAPE) + [UNMEASURED]:
        if shape not in buckets:
            continue
        lengths = sorted(buckets[shape])
        per_shape[shape] = {
            "sounds": len(lengths),
            "shortest_seconds": round(lengths[0], 2) if lengths else None,
            "longest_seconds": round(lengths[-1], 2) if lengths else None,
            f"at_least_{long_enough_seconds:g}s": sum(
                1 for d in lengths if d >= long_enough_seconds),
        }

    total = sum(1 for e in (catalog or []) if isinstance(e, dict))
    summary = "; ".join(
        f"{shape}: {row['sounds']} sound(s), "
        f"{row['shortest_seconds']}-{row['longest_seconds']}s, "
        f"{row[f'at_least_{long_enough_seconds:g}s']} of them "
        f"{long_enough_seconds:g}s or longer"
        for shape, row in per_shape.items()
    ) or "the catalogue is empty"

    record = {
        "total_sounds": total,
        "by_envelope": per_shape,
        "measured_on_this_run": summary,
        "what_this_is": (
            "a count of what the library holds, measured on this run from "
            "the same catalogue the map points at. It is here because a "
            "catalogue ordered by folder reads like whatever the folder "
            "names are, and the folder a file was filed under says nothing "
            "about what it can do in a cut. It recommends nothing."),
    }
    if unmeasured_length:
        record["length_unmeasured"] = (
            f"{unmeasured_length} sound(s) carry no measured length and are "
            f"counted in their envelope's `sounds` total but in no length "
            f"range - an admitted absence, not a length of zero.")
    return record
