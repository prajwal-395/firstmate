"""A step's measurements disagree with the direction, and it says so and complies.

The captain's ruling of 2026-09-01: a step MAY FLAG and MAY NOT ACT. The
flag never reaches the step's output, and the four readings stay four.
History: docs/evidence/direction_contradiction.md.
"""
from library.tools import direction_contradiction as dc
from library.tools import undetermined
from library.tools.creative_direction import DIRECTION_KEYS


# A real prosody contradiction: step 2.02 is routed step 1.05's
# deterministic measurements AND step 2.01's inherited affect words.
# The direction calls the piece high-energy; parselmouth measured a slow,
# flat delivery.  This is the case the channel was built for.
PROSODY_CONTRADICTION = {
    "direction_field": "target_energy",
    "direction_said": "high - urgent, propulsive, the viewer never settles",
    "measurement": (
        "clip_003 speaking_rate 2.9 syllables/sec and pitch_std 18.4 Hz "
        "over 41s of speech; clip_007 3.1 syl/sec, pitch_std 21.0 Hz"
    ),
    "measured_in": "prosody_analysis",
    "why_they_disagree": (
        "a measured pace under 3.2 syllables/sec with a pitch spread "
        "under 25 Hz is a level, unhurried delivery, not an urgent one"
    ),
    "complied_by": (
        "selected the passages the direction's energy arc asks for and "
        "ordered them for the propulsive build it names"
    ),
}


# ── The four readings ───────────────────────────────────────────────

def test_the_four_readings_stay_four():
    """Empty is not absent, a measured disagreement keeps its evidence, and
    polite prose disagreement with no measurement is UNEVIDENCED - neither
    a contradiction nor a clean bill of health."""
    _, empty = dc.take("speech_sequence", {"a": 1, dc.FIELD: []})
    _, absent = dc.take("speech_sequence", {"a": 1})
    assert empty.reading == dc.NOTHING_CONTRADICTED
    assert absent.reading == dc.NOT_DECLARED
    assert empty.reading != absent.reading, (
        "'nothing contradicted' and 'the model did not consider it' "
        "reading the same is the whole defect this field exists not to "
        "have"
    )

    _, flag = dc.take("speech_sequence",
                      {"speech_sequence": {}, dc.FIELD: [PROSODY_CONTRADICTION]})
    assert flag.reading == dc.CONTRADICTED
    assert flag.contradicted
    assert flag.unevidenced == []
    entry = flag.entries[0]
    assert entry["measured_in"] == "prosody_analysis"
    assert "2.9 syllables/sec" in entry["measurement"], (
        "the measurement must travel with the claim, or the flag is prose"
    )

    _, flag = dc.take("speech_sequence", {dc.FIELD: [{
        "direction_field": "target_mood",
        "direction_said": "warm and intimate",
        "why_they_disagree": "the footage feels colder than that to me",
    }]})
    assert flag.reading == dc.UNEVIDENCED
    assert flag.reading != dc.CONTRADICTED
    assert flag.reading != dc.NOTHING_CONTRADICTED, (
        "an unevidenced disagreement is not a clean bill of health either"
    )
    assert flag.entries == []
    assert flag.unevidenced[0]["direction_said"] == "warm and intimate", (
        "kept verbatim: dropping it would hide that the field is being "
        "filled with prose"
    )


def test_an_unrouted_measurement_or_unasked_direction_field_is_unevidenced():
    """`music_analysis` is real, and 2.02 is not routed it; `energy_level`
    is one of the withdrawn reads in creative_direction."""
    for override in ({"measured_in": "music_analysis"},
                     {"direction_field": "energy_level"}):
        entry = dict(PROSODY_CONTRADICTION, **override)
        _, flag = dc.take("speech_sequence", {dc.FIELD: [entry]})
        assert flag.reading == dc.UNEVIDENCED, override
    assert "music_analysis" not in dc.evidence_sources("speech_sequence")
    assert "energy_level" not in DIRECTION_KEYS


# ── It never reaches the step's output ──────────────────────────────

def test_the_field_is_taken_out_of_the_answer():
    """This is what makes compliance structural rather than promised.

    `validate_step_output` refuses an unexpected extra key and a
    post-bridge is handed the model's answer as its own input, so the
    output that leaves `take` is the output the step would have produced
    with no field at all.  A step that flags cannot deviate.
    """
    answer = {"speech_sequence": {"body_sequence": [1, 2]},
              dc.FIELD: [PROSODY_CONTRADICTION]}
    remainder, flag = dc.take("speech_sequence", answer)
    assert remainder == {"speech_sequence": {"body_sequence": [1, 2]}}
    assert dc.FIELD not in remainder
    assert flag.contradicted


# ── The set of steps, argued rather than assumed ────────────────────

def test_the_flagging_steps_are_every_model_step_but_the_author():
    """A contradiction needs a prompt, an inherited direction and a
    measurement, all in one step."""
    assert dc.FLAGGING_STEPS == frozenset(
        undetermined.DECLARING_STEPS
        - {"creative_direction", "validate", "judge_reels"})
    assert not dc.flags("judge_reels"), (
        "3.05 is deliberately routed no creative_direction. Its reader is "
        "given the reel's words and nothing else - the direction is this "
        "episode's statement of what it is trying to be, and a reader "
        "holding it would be reading the intention rather than the reel. "
        "See library/tools/reel_quality_bar.FORBIDDEN_IN_THE_ASK."
    )
    assert not dc.flags("creative_direction"), (
        "2.01 authors the direction; it has nothing inherited to "
        "contradict"
    )
    # `render_motion_graphics` used to be in this list, and `color_grade`
    # with it. Each stopped being deterministic and grew a handoff - 4.06
    # on 2026-09-02, 5.01 on 2026-09-03 - so each now has a prompt to say
    # it in, and the derived set above picked both up with no edit to
    # either module. `creative_cohesion` is the one left: it declares the
    # direction, reaches no model, and is excluded with a reason rather
    # than by omission.
    assert not dc.flags("creative_cohesion"), (
        "creative_cohesion declares creative_direction but reaches no "
        "model, so it has nothing to say it in"
    )
    assert dc.flags("color_grade"), (
        "5.01 became hybrid and is routed the vision documents; it is the "
        "clearest case the channel has - a direction that calls the piece "
        "vibrant, held against a clip measuring 53 luma"
    )
    assert dc.flags("render_motion_graphics"), (
        "4.06 reaches a model now; a step that starts reaching one and is "
        "left out of the derivation is exactly the staleness the "
        "derivation exists to prevent"
    )


# ── One step's prose is marked where another step reads it as evidence ─
#
# The 2026-09-07 inventory: `semantic_analysis_documents` is listed in
# MEASURED_OUTPUTS but carries VLM prose beside its measurements, and
# every flagging step routed the document was told the whole of it is
# something it MEASURED.  These tests pin the marking in both
# directions: the gate FAILS when the marking is removed, and it does
# NOT fail output that was correct before it.

def _vision_routed_steps():
    return [step for step in sorted(dc.FLAGGING_STEPS)
            if dc.VISION_INPUT_NAMES & set(dc.evidence_sources(step))]


def test_every_vision_routed_step_is_told_which_paths_are_prose():
    """The gate that CAN fail: remove the caveat from `prompt_block` and
    this goes red.  A consumer that cannot tell a measurement from a
    summary will mistake one for the other, which is the defect."""
    steps = _vision_routed_steps()
    assert len(steps) >= 5, (
        f"expected most flagging steps to hold vision docs, found {steps} - "
        "if the DAG really stopped routing them, update VISION_PROSE_PATHS "
        "rather than this number")
    for step in steps:
        block = dc.prompt_block(step)
        for path in sorted(dc.VISION_PROSE_PATHS):
            assert path in block, (
                f"{step}: prompt_block names no `{path}` as prose - a "
                "contradiction citing it would read as measured")


# ── 3.03 can cite the alignment it runs on ──────────────────────────

ALIGNMENT_CONTRADICTION = {
    "direction_field": "target_energy",
    "direction_said": "high - urgent, propulsive, the viewer never settles",
    "measurement": (
        "body[0] largest internal gap 1.169s in a 2.982s block, "
        "voiced_fraction 0.474, leading gap 0.312s, 3 anchors considered"
    ),
    "measured_in": "speech_sequence",
    "why_they_disagree": (
        "a block that is half silence cannot carry an urgent propulsion"
    ),
    "complied_by": (
        "kept the passage where the sequence put it and judged the cut "
        "on the surrounding continuity"
    ),
}


def test_review_rough_cut_can_cite_the_alignment_it_runs_on():
    """Item 8: 3.03's richest routed measurement is view:alignment -
    leading gaps, largest internal gap, voiced fraction, anchors
    considered, per passage - and it was on neither citable list, so any
    disagreement grounded in the numbers the step is built around read
    as UNEVIDENCED. The step IS routed speech_sequence and IS shown the
    numbers as view:alignment, so citing them must read as contradicted.
    """
    sources = dc.evidence_sources("review_rough_cut")
    assert "speech_sequence" in sources, (
        f"3.03 cannot cite its own evidence: {sorted(sources)}"
    )
    _, flag = dc.take("review_rough_cut",
                      {"review": {}, dc.FIELD: [ALIGNMENT_CONTRADICTION]})
    assert flag.reading == dc.CONTRADICTED
    assert flag.unevidenced == []
    assert flag.entries[0]["measured_in"] == "speech_sequence"
