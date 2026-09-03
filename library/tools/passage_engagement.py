"""Passage engagement: the model JUDGES it, and nothing else invents one.

`speech_sequence` (step 2.02) used to attach an `engagement` dict to every
passage - `{hook, flow, value, composite, rationale}` - produced by
`library/tools/engagement_scorer.py`.  On project 001's 2026-08-26 run nine
of eleven passages scored an identical composite of 49 and the `hook`
component was 30 for ten of eleven, so the one ranking signal the edit's
ordering rested on was a constant.

The scorers are withdrawn rather than repaired, because none of the three
was reading a measurement this pipeline produces.  `WITHDRAWN_SCORERS`
records why for each, so nobody re-derives it.  What survived them is the
READER half, and `NO_ENGAGEMENT_BASIS` is the sentence a reader prints
when nothing carries a judgement at all - which is still the right answer
for a sequence that carries none, and is why the arithmetic never has to
come back.

**A missing measurement must never resolve to a value that reads as a real
one.**  That is the whole rule this module exists to hold.
`engagement_rank` returns None - not 0, and not last - and every reader
must say it has no basis rather than compare Nones coerced to a number.

**THE ORDERING IS THE WHOLE JUDGEMENT, AND THERE IS NO SCORE.**  The
captain ruled on 2026-09-02 that the 0-100 composite goes with the closed
role vocabulary: only the rank ordering was ever consumed, and the number
beside it was noise that read like magnitude.  So step 2.02's handoff asks
the model that already reads every passage to place it in ONE ordering
against the others and to say plainly when it cannot, the shape that lands
is `{"rank": 1..N, "basis": "one sentence"}`, and this module is the whole
reading of it - `engagement_rank` for the ordering and `unjudged_summary`
for the line a reader prints about the passages that were NOT judged.
`engagement_of`, which read the composite off whatever landed, is GONE
with the number it read; do not reintroduce a magnitude reader without a
step that measures one.

**Compare RANKS, and only near the top.**  `MEASURED_SPREAD` is the
measurement that settles it: three answers to the identical prompt, at one
revision, against project 001's frozen snapshot.  The two strongest
passages came back in the same order all three times; ranks in the middle
of the list moved by up to two places, and the composite on those same
passages - before it was withdrawn - moved by up to twenty points.  So
"which passage is strongest" is a real signal and "is passage five better
than passage seven" is not.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**Passage engagement is a JUDGEMENT the model writes, it is an ORDERING, and there is NO SCORE.**
One enumeration, `library/tools/passage_engagement.py` - `engagement_rank`, `engagement_basis`, `unjudged_summary`. Step 2.02's handoff asks for `engagement` on every passage it selects: `{rank, basis}`, ranked against that sequence and nothing else.
- **The 0-100 composite is WITHDRAWN** (captain, 2026-09-02), along with `engagement_of`, the reader that read it: only the ordering was ever consumed, and the number beside it read as magnitude it did not have. `MEASURED_SPREAD` keeps the measurement that settled it - between answers to the identical prompt, ranks in the middle moved two places and the composite moved twenty points. Do not reintroduce a magnitude reader without a step that MEASURES one.
- **Compare ranks, and only near the top.** [why](docs/RULE_EVIDENCE.md#every-line-scored-the-same)
- **A rank is comparable only inside ONE speech_sequence.** It is the model's ordering over the passages it chose, not a scale.
- **A passage the model declined to judge reads as UNJUDGED, never as a low score**, and its reason is stated. `engagement_rank` returns **None**; never coerce it to 0 or to last. `WITHDRAWN_SCORERS` records why each of the three arithmetic scorers that came before was not a measurement.
- **Step 2.02 names no roles and no opener.** The closed `opening|development|climax|resolution` vocabulary, the separately mandated `hook_segment` and its 1-3 second target all went on the same ruling: the model proposes the structure the footage wants. What survived is one ORDERED `body_sequence`, and every consumer works from that ordering - `mesh_spine` addresses a passage by its `position` (never by a role name), and 5.03's engagement observation compares the play order against the rank order.

`tests/test_passage_engagement.py`.
"""

# Why each scorer is withdrawn, in the terms of what it actually read.
# Recorded here because a withdrawal without its reason gets re-added.
WITHDRAWN_SCORERS = {
    "score_hook": (
        "Read `prosody_data.get(\"energy_rms\", 0)` and subtracted 20 "
        "points whenever it came back under 0.3.  Three independent "
        "reasons it could never be a measurement: `energy_rms` is emitted "
        "by nothing in this repository - `analyze_prosody` produces "
        "pitch_stats, pitch_contour_10ms, voice_quality, speaking_rate "
        "and intensity_contour_50ms, and no key of any of them is called "
        "energy_rms; the object it was read off is step 1.05's whole "
        "output, `{available, profiles}`, which is one record per RUN and "
        "so could only ever give every passage the same number; and "
        "parselmouth was not installed, so the default fired for every "
        "line the captain has ever said.  A missing dependency read as "
        "'this speech has no energy'."
    ),
    "score_flow": (
        "Carried the comment `# Dummy flow scoring logic for scaffolding` "
        "and was exactly that: 60, plus 20 for the substrings \"first\", "
        "\"then\" or \"finally\", minus 30 under five words.  It measured "
        "no narrative structure and read no speech_sequence."
    ),
    "score_value": (
        "60, plus 20 over twenty words, plus 15 for the literal "
        "substrings \"important\", \"key\" or \"insight\".  Length and "
        "three English words are not the substance of a passage."
    ),
    "compute_engagement": (
        "Weighted the three above into a composite and recorded a "
        "`rationale` of \"Hook:30, Flow:60, Value:60\" - the numbers "
        "restated, not a reason.  An audit that cannot tell a measurement "
        "from a declaration is not an audit (AGENTS.md 8)."
    ),
}

# What three answers to the identical prompt did on 001's frozen
# snapshot, at one revision.  Recorded here because the shape of the
# reader - rank near the top, composite reported only - is derived from
# it, and a later widening should be argued against a new measurement
# rather than against nothing.  Method: replay_bench reconstructs the
# 2.02 prompt, the answers come from one model, and the passages are
# matched across runs by their normalised text.
MEASURED_SPREAD = {
    "snapshot": "001-2026-08-26T1058Z",
    "answers": 3,
    "passages_present_in_all_three": 7,
    "passages_selected_per_answer": [9, 9, 10],
    "rank_1_identical_across_all_three": True,
    "rank_2_identical_across_all_three": True,
    "rank_spread": {"min": 0, "median": 2, "max": 2},
    # Recorded as history: the composite is WITHDRAWN (captain,
    # 2026-09-02).  This is the spread that made it noise.
    "composite_spread": {"min": 1, "median": 7, "max": 20},
    "note": (
        "The selection itself moves between answers at ONE revision - "
        "nine, nine and ten passages, seven of them common - so a "
        "between-revision difference in what 2.02 selects means nothing "
        "until it exceeds this."
    ),
}

# What a reader says instead of comparing judgements that are not there.
NO_ENGAGEMENT_BASIS = (
    "no passage carries an engagement rank, and the pipeline measures "
    "none: see library/tools/passage_engagement.py"
)


def engagement_rank(passage) -> int:
    """This passage's place in its sequence's own ordering, or None.

    1 is the strongest.  The ordering is the model's, over the passages
    it selected and nothing else, so a rank is only ever comparable
    inside one speech_sequence - never across projects or across runs.

    None means "not judged", and a reader must say so rather than sort
    the passage to the bottom.
    """
    if not isinstance(passage, dict):
        return None
    value = passage.get("engagement")
    if not isinstance(value, dict):
        return None
    rank = value.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int):
        return None
    return rank if rank >= 1 else None


def engagement_basis(passage) -> str:
    """The one sentence the judgement gave for itself, or None.

    On a judged passage it says what makes it strong or weak.  On an
    UNJUDGED one it says why the model could not judge it, which is the
    half that makes an absence reportable rather than blank.
    """
    if not isinstance(passage, dict):
        return None
    value = passage.get("engagement")
    if not isinstance(value, dict):
        return None
    basis = value.get("basis")
    if isinstance(basis, str) and basis.strip():
        return basis.strip()
    return None


def is_unjudged(passage) -> bool:
    """Did this passage carry a judgement that declined to judge it?

    Distinct from carrying nothing at all.  A passage the model looked at
    and could not place is a stated absence with a reason attached; a
    passage with no `engagement` key was never asked about.  Both read as
    absent to `engagement_rank`, and only this one has something to
    report.
    """
    if not isinstance(passage, dict):
        return False
    if not isinstance(passage.get("engagement"), dict):
        return False
    return engagement_rank(passage) is None


def unjudged_summary(passages) -> str:
    """ONE line naming how many passages were not judged, and why.

    The same shape `view:prosody` uses for a clip nothing measured: state
    the absence, never hide it and never let it read as a low score.
    Returns None when every passage was judged - there is nothing to say.
    """
    if not isinstance(passages, (list, tuple)):
        return None
    unjudged = [p for p in passages if is_unjudged(p)]
    if not unjudged:
        return None
    reasons = sorted({engagement_basis(p) or "no reason given"
                      for p in unjudged})
    return (
        f"{len(unjudged)} of {len(passages)} passage(s) were not judged for "
        f"engagement: " + "; ".join(reasons[:3])
        + ("; ..." if len(reasons) > 3 else "")
    )
