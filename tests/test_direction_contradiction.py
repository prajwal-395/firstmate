"""A step's measurements disagree with the direction, and it says so and complies.

On the 29 Aug 2026 run of 001, "emotion" and "energy" appear ZERO times
in the semantic documents and FOUR times each in the `creative_direction`
block at station 2 and station 3.  Step 2.01 read the footage once and
every creative step after it inherited that reading whole, with no way to
say "what I measured disagrees with what I was told".

The captain's ruling of 2026-09-01 is what these tests pin: a step MAY
FLAG and MAY NOT ACT.  So the two properties that matter most here are
not that the field exists.  They are that the flag NEVER reaches the
step's output - which is what makes compliance structural rather than
promised - and that the FOUR readings stay four.  An unevidenced
disagreement is not a contradiction, an empty answer is not an absent
one, and none of them is a failure.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from library.processes.edit_video.run_pipeline import present_llm_step
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

def test_an_empty_flag_is_not_an_absent_one():
    _, empty = dc.take("speech_sequence", {"a": 1, dc.FIELD: []})
    _, absent = dc.take("speech_sequence", {"a": 1})
    assert empty.reading == dc.NOTHING_CONTRADICTED
    assert absent.reading == dc.NOT_DECLARED
    assert empty.reading != absent.reading, (
        "'nothing contradicted' and 'the model did not consider it' "
        "reading the same is the whole defect this field exists not to "
        "have"
    )


def test_a_measured_disagreement_reads_as_contradicted_and_keeps_its_evidence():
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


def test_a_disagreement_with_no_measurement_is_not_a_contradiction():
    """The failure mode this design is against: polite prose disagreement.

    A model can produce an opinion that the brief is wrong for nothing.
    It cannot produce a measurement it was not routed.
    """
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


def test_a_measurement_the_step_was_not_routed_is_unevidenced():
    """`music_analysis` is real, and 2.02 is not routed it."""
    entry = dict(PROSODY_CONTRADICTION, measured_in="music_analysis")
    _, flag = dc.take("speech_sequence", {dc.FIELD: [entry]})
    assert flag.reading == dc.UNEVIDENCED
    assert "music_analysis" not in dc.evidence_sources("speech_sequence")


def test_a_direction_field_2_01_is_not_asked_for_is_unevidenced():
    """`energy_level` is one of the withdrawn reads in creative_direction."""
    entry = dict(PROSODY_CONTRADICTION, direction_field="energy_level")
    _, flag = dc.take("speech_sequence", {dc.FIELD: [entry]})
    assert flag.reading == dc.UNEVIDENCED
    assert "energy_level" not in DIRECTION_KEYS


def test_a_malformed_flag_is_not_read_as_nothing_contradicted():
    _, flag = dc.take("plan_sfx", {dc.FIELD: None})
    assert flag.reading == dc.NOT_DECLARED
    assert flag.malformed


def test_evidenced_and_unevidenced_entries_in_one_answer_stay_apart():
    _, flag = dc.take("speech_sequence", {dc.FIELD: [
        PROSODY_CONTRADICTION,
        {"direction_field": "target_mood", "why_they_disagree": "a feeling"},
    ]})
    assert flag.reading == dc.CONTRADICTED
    assert len(flag.entries) == 1 and len(flag.unevidenced) == 1


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


def test_a_step_that_does_not_flag_is_left_alone():
    answer = {"catalog": [], dc.FIELD: ["noise"]}
    remainder, flag = dc.take("catalog", answer)
    assert remainder is answer
    assert flag.reading == dc.NOT_DECLARED


def test_nothing_here_gates_or_ranks():
    """The module reads; it decides nothing.

    Whatever picked which contradictions mattered would become the
    director, and the captain's ruling is that no step does.
    """
    source = Path("library/tools/direction_contradiction.py").read_text(
        encoding="utf-8")
    for forbidden in ("raise ValueError", "sys.exit", "assert "):
        assert forbidden not in source, (
            f"{forbidden!r} in a module that must only report")


# ── The set of steps, argued rather than assumed ────────────────────

def test_the_flagging_steps_are_every_model_step_but_the_author():
    """A contradiction needs a prompt, an inherited direction and a
    measurement, all in one step."""
    assert dc.FLAGGING_STEPS == frozenset(
        undetermined.DECLARING_STEPS - {"creative_direction"})
    assert not dc.flags("creative_direction"), (
        "2.01 authors the direction; it has nothing inherited to "
        "contradict"
    )
    for deterministic in ("color_grade", "creative_cohesion",
                          "render_motion_graphics"):
        assert not dc.flags(deterministic), (
            f"{deterministic} declares creative_direction but reaches no "
            "model, so it has nothing to say it in"
        )


def test_every_flagging_step_has_at_least_one_routed_measurement():
    for step_id in dc.FLAGGING_STEPS:
        assert dc.evidence_sources(step_id), step_id


def test_prosody_reaches_the_step_that_can_use_it():
    """#417 wired 1.05 to 2.01 and 2.02. 2.01 authors, so 2.02 is the one
    step that can hold prosody against an inherited affect reading."""
    assert dc.evidence_sources("speech_sequence")["prosody_analysis"].startswith(
        "1.05")


def test_every_deterministic_output_is_accounted_for():
    """The import-time check is the real guard; this states it.

    A new deterministic output has to say whether it is a measurement of
    the material or not, before it can go quiet.
    """
    overlap = set(dc.MEASURED_OUTPUTS) & set(dc.DECLINED_OUTPUTS)
    assert not overlap, overlap
    import importlib
    importlib.reload(dc)  # raises if anything is unaccounted for


# ── The summary tells them apart ────────────────────────────────────

def test_the_summary_tells_the_four_apart():
    lines = "\n".join(dc.summary_lines([
        dc.Flag("speech_sequence", dc.CONTRADICTED, [PROSODY_CONTRADICTION]),
        dc.Flag("plan_sfx", dc.NOTHING_CONTRADICTED),
        dc.Flag("select_broll", dc.UNEVIDENCED,
                unevidenced=[{"direction_said": "a feeling"}]),
        dc.Flag("mesh_spine", dc.NOT_DECLARED),
    ]))
    assert "2.9 syllables/sec" in lines
    assert "prosody_analysis" in lines
    assert "FLAGGED and COMPLIED" in lines
    assert "measured nothing that contradicted: plan_sfx" in lines
    assert "no routed measurement" in lines.split("select_broll")[1]
    assert "mesh_spine" in lines.split("did not answer")[1]


# ── Through the real prompt assembly ────────────────────────────────

def _answer_once(req: Path, res: Path, payload: dict, seen: list):
    def run():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists() and not res.exists():
                seen.append(json.loads(req.read_text(encoding="utf-8")))
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(payload), encoding="utf-8")
                return
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _drive(tmp_path, node_id, answer, outputs=("speech_sequence",)):
    dc.reset()
    undetermined.reset()
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Order the speech.\n", encoding="utf-8")
    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"
    seen = []
    _answer_once(req, res, answer, seen)
    result = present_llm_step(
        str(prompt_path), {"project_folder": str(project)}, node_id,
        manifest={"interface": {"outputs": [{"name": o} for o in outputs]}},
        full_auto="agy", llm_timeout=30,
    )
    return seen, result


@pytest.mark.parametrize("answer,expected", [
    ({"speech_sequence": {"body_sequence": []}, dc.FIELD: []},
     dc.NOTHING_CONTRADICTED),
    ({"speech_sequence": {"body_sequence": []},
      dc.FIELD: [PROSODY_CONTRADICTION]}, dc.CONTRADICTED),
    ({"speech_sequence": {"body_sequence": []},
      dc.FIELD: [{"direction_field": "target_mood",
                  "why_they_disagree": "it just feels wrong"}]},
     dc.UNEVIDENCED),
    ({"speech_sequence": {"body_sequence": []}}, dc.NOT_DECLARED),
])
def test_the_question_reaches_the_prompt_and_the_answer_is_recorded(
        tmp_path, answer, expected):
    seen, result = _drive(tmp_path, "speech_sequence", answer)

    assert seen, "the step never issued a request"
    prompt = seen[0]["prompt"]
    assert dc.FIELD in prompt
    assert "prosody_analysis" in prompt, (
        "the step must be told which measurements can carry a flag")
    assert "Flagging does not change what you do" in prompt, (
        "the captain ruled the step flags and complies; the prompt has "
        "to say so or the field invites deviation")
    assert "read as a non-answer" in prompt, (
        "omitting the field must not read as 'nothing contradicted', or "
        "the readings collapse")

    assert result == {"speech_sequence": {"body_sequence": []}}, (
        "the flag must not be carried into the step's output - that is "
        "what compliance means here")
    assert [f.reading for f in dc.collected()] == [expected]


def test_a_step_that_does_not_flag_is_not_asked(tmp_path):
    seen, _ = _drive(tmp_path, "validate", {"a_verdict": "fine"},
                     outputs=("a_verdict",))
    assert seen and dc.FIELD not in seen[0]["prompt"]
    assert dc.collected() == []


def test_the_run_records_it_on_the_state_shape():
    """What lands on `pipeline_data.json` and in the run summary."""
    dc.reset()
    dc.record(dc.Flag("speech_sequence", dc.CONTRADICTED,
                      [PROSODY_CONTRADICTION]))
    records = dc.as_records()
    assert records == [{
        "step_id": "speech_sequence",
        "reading": dc.CONTRADICTED,
        "entries": [PROSODY_CONTRADICTION],
    }]
    assert json.loads(json.dumps(records)) == records
    dc.reset()
