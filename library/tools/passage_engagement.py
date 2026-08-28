"""Passage engagement: the pipeline measures none, and may not invent one.

`speech_sequence` (step 2.02) used to attach an `engagement` dict to every
passage - `{hook, flow, value, composite, rationale}` - produced by
`library/tools/engagement_scorer.py`.  On project 001's 2026-08-26 run nine
of eleven passages scored an identical composite of 49 and the `hook`
component was 30 for ten of eleven, so the one ranking signal the edit's
ordering rested on was a constant.

The scorers are withdrawn rather than repaired, because none of the three
was reading a measurement this pipeline produces.  `WITHDRAWN_SCORERS`
records why for each, so nobody re-derives it.  What is left here is the
READER half: `engagement_of` answers "does this passage carry a score",
and `NO_ENGAGEMENT_BASIS` is the sentence a reader prints when nothing
does.

**A missing measurement must never resolve to a value that reads as a real
one.**  That is the whole rule this module exists to hold.  `engagement_of`
returns None - not 0, and not a plausible-looking middle - and every reader
must say it has no basis rather than compare Nones coerced to zero.

Restoring a real signal means a step that MEASURES or JUDGES one and writes
it onto the passage.  `engagement_of` reads the composite off whatever
lands there, so a future scored judgement needs no change here.
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

# What a reader says instead of comparing scores that are not there.
NO_ENGAGEMENT_BASIS = (
    "no passage carries an engagement score, and the pipeline measures "
    "none: see library/tools/passage_engagement.py"
)


def engagement_of(passage) -> float:
    """The composite engagement of one passage, or None if it has none.

    None means "not measured".  Callers must report that, never coerce it
    to 0 - a hook that scored nothing is not a hook that scored zero, and
    the gate in step 5.03 read exactly that way for months.

    Both shapes are accepted: the `{..., "composite": n}` dict a scored
    judgement would write, and a bare number.  Nothing emits either today.
    """
    if not isinstance(passage, dict):
        return None
    value = passage.get("engagement")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        composite = value.get("composite")
        if isinstance(composite, (int, float)) and not isinstance(composite, bool):
            return float(composite)
    return None
