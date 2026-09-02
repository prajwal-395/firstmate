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


# ── The MACHINE-READABLE half of the request ────────────────────────
#
# The agy request file carries the schema twice: as prose inside
# `prompt`, and as JSON in `expected_schema`.  An answering agent that
# reads the JSON one - the obvious shortcut, since it is the one meant
# for a machine - saw a schema with no `could_not_determine` in it,
# because `expected_schema` was built from `llm_outputs` before the
# field was appended.  Every declaring step then recorded a NON-ANSWER,
# which is precisely the reading the three states exist to keep
# meaningful: the signal was not lost, it was filled with false silence,
# in the one mode the pipeline actually runs in.

def _answer_n_times(req: Path, res: Path, payload: dict, seen: list, times: int):
    def run():
        deadline = time.time() + 40
        answered = 0
        while time.time() < deadline and answered < times:
            if req.exists() and not res.exists():
                seen.append(json.loads(req.read_text(encoding="utf-8")))
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(payload), encoding="utf-8")
                answered += 1
                time.sleep(0.3)
            time.sleep(0.05)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _agy_request(tmp_path, step_id, payload, inputs=None, times=1, outputs=None):
    """Drive the REAL agy path and hand back the request files it wrote."""
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Do the work.\n", encoding="utf-8")
    req = project / "pipeline_output" / "llm_requests" / f"{step_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{step_id}.json"
    seen = []
    _answer_n_times(req, res, payload, seen, times)
    step_inputs = {"project_folder": str(project)}
    step_inputs.update(inputs or {})
    try:
        present_llm_step(
            str(prompt_path), step_inputs, step_id,
            manifest={"interface": {"outputs": outputs or [
                {"name": "a_verdict", "type": "string", "required": True,
                 "description": "the verdict"}]}},
            full_auto="agy", llm_timeout=35,
        )
    except Exception:
        # A step whose answer never validates still issued its requests,
        # and the requests are what this is reading.
        pass
    assert seen, "the step never issued a request"
    return seen


def test_the_agy_request_asks_for_the_field_in_expected_schema(tmp_path):
    undetermined.reset()
    seen = _agy_request(tmp_path, "plan_transitions",
                        {"a_verdict": "fine", undetermined.FIELD: []})
    schema = json.loads(seen[0]["expected_schema"])
    names = [entry["name"] for entry in schema]
    assert undetermined.FIELD in names, (
        "the field reached the agent in `prompt` and not in "
        "`expected_schema`; an agent reading the machine-readable half "
        "never emits it and the step records a false non-answer"
    )
    entry = next(e for e in schema if e["name"] == undetermined.FIELD)
    assert entry["required"] is False, (
        "a model must be able to leave it empty without being told it "
        "failed - the emptiness is the answer"
    )


def test_expected_schema_and_the_prompt_describe_the_same_schema(tmp_path):
    """Nothing else may fall through the gap `could_not_determine` fell
    through.  `expected_schema` is built from the SAME list the prompt's
    schema block is rendered from, so this holds for whatever is
    appended next - here checked with `note_acknowledgements`, which is
    appended on a different condition again."""
    undetermined.reset()
    seen = _agy_request(
        tmp_path, "plan_transitions", {"a_verdict": "fine"},
        inputs={"timeline_notes": {"notes": [{"id": "n1", "text": "tighter"}]}},
        times=3)
    from library.tools import direction_contradiction
    assert direction_contradiction.flags("plan_transitions"), (
        "this step carries BOTH appended fields, which is what makes it "
        "the case where one appender could clobber the other")
    for request in seen:
        names = [e["name"] for e in json.loads(request["expected_schema"])]
        assert names == ["a_verdict", "note_acknowledgements",
                         undetermined.FIELD,
                         direction_contradiction.FIELD], names
        for name in names:
            assert f'"{name}"' in request["prompt"], (
                f"{name} is promised in expected_schema and is not in the "
                f"prompt the same request carries")


def test_a_step_that_does_not_declare_gets_no_such_schema_entry(tmp_path):
    undetermined.reset()
    seen = _agy_request(tmp_path, "validate", {"a_verdict": "fine"})
    assert undetermined.FIELD not in seen[0]["expected_schema"]


# ── One row per step, and the attempts kept ─────────────────────────

def test_each_attempt_is_numbered_and_the_summary_counts_steps(tmp_path):
    """A step whose answer fails QA is asked again, so one step can
    produce three declarations.  Before they were numbered, the summary
    read `mesh_spine, mesh_spine, mesh_spine` and a reader counting
    names counted model calls.  Every attempt is still KEPT, because a
    step failing the same way three times running is the evidence
    `post_bridge_retry` accumulates one module over."""
    undetermined.reset()
    seen = _agy_request(tmp_path, "mesh_spine", {"the_wrong_key": 1}, times=3)
    assert len(seen) == 3, "the step should have made three model calls"

    rows = undetermined.collected()
    assert [d.attempt for d in rows] == [1, 2, 3]
    assert {d.step_id for d in rows} == {"mesh_spine"}
    assert [r["attempt"] for r in undetermined.as_records()] == [1, 2, 3]

    final = undetermined.final_by_step()
    assert len(final) == 1, "one row per STEP, not per attempt"
    assert final[0].attempt == 3, "the last attempt is the answer it returned"

    lines = undetermined.summary_lines()
    assert sum(line.count("mesh_spine") for line in lines) == 2, (
        "once in the reading, once saying it answered more than once - "
        "never once per model call"
    )
    assert any("3 attempts" in line for line in lines), (
        "a step asked three times must SAY so rather than have it "
        "inferred from three identical rows"
    )


def test_one_attempt_says_nothing_about_attempts():
    undetermined.reset()
    undetermined.record(undetermined.Declaration(
        step_id="plan_vfx", reading=undetermined.NOTHING_MISSING))
    lines = undetermined.summary_lines()
    assert not any("attempts" in line for line in lines)
    assert undetermined.as_records()[0]["attempt"] == 1


# ── Surviving a --rerun ─────────────────────────────────────────────

_PREVIOUS = [
    {"step_id": "creative_direction", "reading": undetermined.DECLARED,
     "entries": [{"what": "the palette"}], "attempt": 1},
    {"step_id": "music_selection", "reading": undetermined.NOTHING_MISSING,
     "entries": [], "attempt": 1},
]


def test_a_rerun_of_one_step_does_not_erase_the_others():
    """`state["undetermined_declarations"]` was REPLACED, so a
    `--rerun music_selection` deleted the other eight steps'
    declarations: the narrowest possible run destroying the signal."""
    merged = undetermined.merge_records(_PREVIOUS, [
        {"step_id": "music_selection", "reading": undetermined.DECLARED,
         "entries": [{"what": "which section"}], "attempt": 1}])
    assert sorted(r["step_id"] for r in merged) == [
        "creative_direction", "music_selection"]


def test_a_carried_row_is_never_read_as_fresh():
    """A step this run did not reach describes material that may since
    have moved.  Carrying it silently would be the confident wrong
    reading this whole field exists to avoid."""
    merged = undetermined.merge_records(_PREVIOUS, [
        {"step_id": "music_selection", "reading": undetermined.DECLARED,
         "entries": [], "attempt": 1}])
    carried = next(r for r in merged if r["step_id"] == "creative_direction")
    assert carried["from_a_previous_run"] is True
    fresh = next(r for r in merged if r["step_id"] == "music_selection")
    assert "from_a_previous_run" not in fresh
    # And the mark, once set, stays set across a further run.
    again = undetermined.merge_records(merged, [])
    assert all(r.get("from_a_previous_run") for r in again)


def test_a_rerun_replaces_that_step_rather_than_appending_to_it():
    merged = undetermined.merge_records(_PREVIOUS, [
        {"step_id": "music_selection", "reading": undetermined.DECLARED,
         "entries": [{"what": "which section"}], "attempt": 1}])
    rows = [r for r in merged if r["step_id"] == "music_selection"]
    assert len(rows) == 1
    assert rows[0]["reading"] == undetermined.DECLARED, (
        "the material changed, so the old answer is not an answer about "
        "this run"
    )


def test_a_rerun_replaces_every_attempt_of_that_step():
    previous = [
        {"step_id": "mesh_spine", "reading": undetermined.NOT_DECLARED,
         "entries": [], "attempt": n} for n in (1, 2, 3)]
    merged = undetermined.merge_records(previous, [
        {"step_id": "mesh_spine", "reading": undetermined.NOTHING_MISSING,
         "entries": [], "attempt": 1}])
    assert merged == [
        {"step_id": "mesh_spine", "reading": undetermined.NOTHING_MISSING,
         "entries": [], "attempt": 1}]


# ── Two appenders, one list ─────────────────────────────────────────

def test_both_appended_fields_reach_expected_schema(tmp_path):
    """`could_not_determine` (#426) and `contradicts_direction` (#428) are
    appended to the same list, one after the other.  `expected_schema` is
    rendered from that list BELOW both, so neither appender can be the one
    that built it and left the other out."""
    from library.tools import direction_contradiction
    undetermined.reset()
    direction_contradiction.reset()
    seen = _agy_request(tmp_path, "plan_transitions", {"a_verdict": "fine"},
                        times=3)
    names = [e["name"] for e in json.loads(seen[0]["expected_schema"])]
    assert undetermined.FIELD in names
    assert direction_contradiction.FIELD in names
    for name in (undetermined.FIELD, direction_contradiction.FIELD):
        assert f'"{name}"' in seen[0]["prompt"], (
            f"{name} is in expected_schema and not in the prompt")


def test_a_step_gets_exactly_the_fields_its_two_modules_ask_for(tmp_path):
    """The membership of each field is the module's own predicate and
    nothing else - `creative_direction` declares and does not flag,
    because it AUTHORS the direction."""
    from library.tools import direction_contradiction as dc
    for step in sorted(undetermined.DECLARING_STEPS | dc.FLAGGING_STEPS):
        undetermined.reset()
        dc.reset()
        root = tmp_path / step
        root.mkdir()
        seen = _agy_request(root, step, {"a_verdict": "fine"})
        names = [e["name"] for e in json.loads(seen[0]["expected_schema"])]
        assert (undetermined.FIELD in names) is undetermined.declares(step), step
        assert (dc.FIELD in names) is dc.flags(step), step


def test_the_twin_channel_counts_steps_and_survives_a_rerun(tmp_path):
    """`direction_contradiction` is `undetermined`'s deliberate twin and
    shares its collector shape, so it shared both counting defects.  The
    two print into the SAME run summary and land beside each other on the
    state file: fixing one and leaving the other would report the two
    channels on different bases."""
    from library.tools import direction_contradiction as dc
    undetermined.reset()
    dc.reset()
    _agy_request(tmp_path, "plan_transitions", {"the_wrong_key": 1}, times=3)

    assert [f.attempt for f in dc.collected()] == [1, 2, 3]
    assert len(dc.final_by_step()) == 1
    assert dc.final_by_step()[0].attempt == 3
    lines = dc.summary_lines()
    assert sum(line.count("plan_transitions") for line in lines) == 2
    assert any("3 attempts" in line for line in lines)

    merged = dc.merge_records(
        [{"step_id": "plan_vfx", "reading": dc.NOT_DECLARED,
          "entries": [], "attempt": 1},
         {"step_id": "select_broll", "reading": dc.NOT_DECLARED,
          "entries": [], "attempt": 1}],
        [{"step_id": "select_broll", "reading": dc.NOTHING_CONTRADICTED,
          "entries": [], "attempt": 1}])
    assert sorted(r["step_id"] for r in merged) == ["plan_vfx", "select_broll"]
    assert next(r for r in merged
                if r["step_id"] == "plan_vfx")["from_a_previous_run"] is True
    assert "from_a_previous_run" not in next(
        r for r in merged if r["step_id"] == "select_broll")
