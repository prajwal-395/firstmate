"""A creative step says what it could not determine, and empty is not absent.

Across all nine model responses in the 29 Aug 2026 run of 001 there is
exactly ONE hedge, and it is `select_broll` reasoning about a softness
measurement - not an "I cannot see".  Nothing invited the models to say
what they were short of, so "where are the bottlenecks" had no demand
signal to read.

The failure mode this guards is the opposite one: a field filled with
polite noise on every call is worse than no field.  So the test that
matters most here is not that the field exists - it is that the THREE
readings stay three.  An empty declaration means "nothing was missing";
an absent one means the model did not answer; and they are recorded
differently.  Same line the repository already draws for `usable_ranges`
`[]`/`unmeasured`, for `primary_subject_visible` None/`[]`, and for
`speech_present` True-or-None-never-False (AGENTS.md 10.3).
"""
import json
import threading
import time
from pathlib import Path

import pytest

from library.processes.edit_video.run_pipeline import present_llm_step
from library.tools import undetermined


# ── The three readings ──────────────────────────────────────────────

def test_an_empty_declaration_is_not_an_absent_one():
    _, empty = undetermined.take("plan_vfx", {"a": 1, undetermined.FIELD: []})
    _, absent = undetermined.take("plan_vfx", {"a": 1})
    assert empty.reading == undetermined.NOTHING_MISSING
    assert absent.reading == undetermined.NOT_DECLARED
    assert empty.reading != absent.reading, (
        "an empty declaration and a missing one read the same, which is "
        "the whole defect this field exists not to have"
    )


def test_a_named_gap_reads_as_declared_and_keeps_its_words():
    answer = {
        "visual_effects": [],
        undetermined.FIELD: [{
            "what": "whether clip_004 is soft or the subject is moving",
            "why_it_mattered": "I left it undecorated",
            "what_would_have_helped": "a per-window sharpness measurement",
        }],
    }
    remainder, decl = undetermined.take("plan_vfx", answer)
    assert decl.reading == undetermined.DECLARED
    assert decl.entries[0]["what"].startswith("whether clip_004")
    assert decl.entries[0]["what_would_have_helped"] == (
        "a per-window sharpness measurement")


def test_the_field_is_taken_out_of_the_answer():
    """It is a demand signal, not one of the step's outputs.

    `validate_step_output` refuses an unexpected extra key and a
    post-bridge is handed the model's answer as its own input, so a field
    left in would be visible to two places with no business seeing it.
    """
    remainder, _ = undetermined.take("mesh_spine",
                                     {"structure": [1], undetermined.FIELD: []})
    assert remainder == {"structure": [1]}
    assert undetermined.FIELD not in remainder


def test_a_step_that_does_not_declare_is_left_alone():
    answer = {"catalog": [], undetermined.FIELD: ["noise"]}
    remainder, decl = undetermined.take("catalog", answer)
    assert remainder is answer, "a non-declaring step's answer is untouched"
    assert decl.reading == undetermined.NOT_DECLARED


def test_a_malformed_declaration_is_not_read_as_nothing_missing():
    _, decl = undetermined.take("plan_sfx", {undetermined.FIELD: None})
    assert decl.reading == undetermined.NOT_DECLARED
    assert decl.malformed, "a null field must say it was present and unusable"


def test_the_summary_tells_the_three_apart():
    lines = "\n".join(undetermined.summary_lines([
        undetermined.Declaration("plan_vfx", undetermined.DECLARED,
                                 [{"what": "the sharpness of clip_004"}]),
        undetermined.Declaration("plan_sfx", undetermined.NOTHING_MISSING),
        undetermined.Declaration("mesh_spine", undetermined.NOT_DECLARED),
    ]))
    assert "the sharpness of clip_004" in lines
    assert "declared nothing missing: plan_sfx" in lines
    assert "mesh_spine" in lines.split("did not answer")[1]


# ── Nine steps, argued rather than assumed ──────────────────────────

def test_every_step_that_reaches_a_model_declares():
    """Choosing a subset would answer, from outside and in advance, the
    question this field exists to collect data for."""
    assert undetermined.DECLARING_STEPS == frozenset({
        "creative_direction", "speech_sequence", "music_selection",
        "mesh_spine", "select_broll", "review_rough_cut",
        "plan_transitions", "plan_vfx", "plan_sfx",
    })
    assert not undetermined.declares("semantic_analysis"), (
        "its schema is empty and its call is skipped"
    )


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


@pytest.mark.parametrize("answer,expected", [
    ({"a_verdict": "fine", undetermined.FIELD: []},
     undetermined.NOTHING_MISSING),
    ({"a_verdict": "fine", undetermined.FIELD: [
        {"what": "which clip the third passage came from"}]},
     undetermined.DECLARED),
    ({"a_verdict": "fine"}, undetermined.NOT_DECLARED),
])
def test_the_question_reaches_the_prompt_and_the_answer_is_recorded(
        tmp_path, answer, expected):
    undetermined.reset()
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the effects.\n", encoding="utf-8")

    req = project / "pipeline_output" / "llm_requests" / "plan_vfx.json"
    res = project / "pipeline_output" / "llm_responses" / "plan_vfx.json"
    seen = []
    _answer_once(req, res, answer, seen)

    result = present_llm_step(
        str(prompt_path), {"project_folder": str(project)}, "plan_vfx",
        manifest={"interface": {"outputs": [{"name": "a_verdict"}]}},
        full_auto="agy", llm_timeout=30,
    )

    assert seen, "the step never issued a request"
    prompt = seen[0]["prompt"]
    assert undetermined.FIELD in prompt
    assert "Return `[]` when the material was sufficient" in prompt
    assert "read as a non-answer" in prompt, (
        "the prompt must say that omitting the field is not the same as "
        "declaring nothing, or the three readings collapse to two"
    )

    assert result == {"a_verdict": "fine"}, (
        "the declaration must not be carried into the step's output")
    recorded = undetermined.collected()
    assert [d.reading for d in recorded] == [expected]


def test_a_non_declaring_step_is_not_asked(tmp_path):
    undetermined.reset()
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Do the work.\n", encoding="utf-8")
    req = project / "pipeline_output" / "llm_requests" / "validate.json"
    res = project / "pipeline_output" / "llm_responses" / "validate.json"
    seen = []
    _answer_once(req, res, {"a_verdict": "fine"}, seen)
    present_llm_step(
        str(prompt_path), {"project_folder": str(project)}, "validate",
        manifest={"interface": {"outputs": [{"name": "a_verdict"}]}},
        full_auto="agy", llm_timeout=30,
    )
    assert seen and undetermined.FIELD not in seen[0]["prompt"]
    assert undetermined.collected() == []
