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


def test_validate_was_considered_and_stays_out_for_a_stated_reason():
    """vep-validate-in-flagging-steps: does `validate` belong in the
    contradicts-direction channel, or only in could-not-determine.

    The answer is no, on the direction half of the membership rule: 6.02
    reaches a model and holds measurements (its own bridge runs render_qa
    over the finished render), but it is handed no inherited creative
    direction to hold them against.  Its manifest declares no
    `creative_direction` input and no DAG edge routes one to it; its only
    deterministic-routed input is the declined consolidation
    `assembly_manifest`.  The exclusion is recorded in the module rather
    than left as a derivation side-effect, so the next edit here meets
    the question deliberately instead of re-answering it by accident.
    """
    assert "validate" in dc.CONSIDERED_AND_EXCLUDED, (
        "validate's exclusion from the flagging set must be a recorded "
        "decision, not a derivation side-effect nobody stated")
    assert dc.CONSIDERED_AND_EXCLUDED["validate"].strip(), (
        "a recorded exclusion with no reason is omission with a comment")
    assert not dc.flags("validate")
    assert dc.evidence_sources("validate") == {}
    # The reason's claims, re-derived from the expressions the module
    # uses rather than copied literals: the manifest the derivation
    # reads, and the DAG edges it derives the routed half from.
    manifest = dc._MANIFESTS["validate"]
    input_names = {i.get("name")
                   for i in manifest.get("interface", {}).get("inputs", [])}
    assert "creative_direction" not in input_names, (
        f"validate now declares a direction input {sorted(input_names)} - "
        "its recorded exclusion is stale; delete the row and let the "
        "derivation pick it up")
    assert undetermined.declares("validate"), (
        "validate stopped reaching a model - the recorded exclusion "
        "answers a question that no longer arises")


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
        full_auto="agent", llm_timeout=30,
    )
    return seen, result


@pytest.mark.heavy
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


@pytest.mark.heavy
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
        # Which model call this was.  A step whose answer fails QA is
        # asked again, so one step can produce several flags in a run;
        # unnumbered they are indistinguishable rows that make a reader
        # counting steps count model calls.  Sibling of
        # `undetermined.Declaration.attempt`, same reason.
        "attempt": 1,
    }]
    assert json.loads(json.dumps(records)) == records
    dc.reset()


def test_a_flag_built_positionally_still_binds_its_entries():
    """`Flag` is constructed positionally here and in the module, so a
    field added anywhere above `entries` silently rebinds every such
    call - `entries` lands in the new field and the real entries in the
    next one along, with no error until something compares them.  That
    is exactly what adding `attempt` above `entries` did.  Any future
    field goes at the END."""
    flag = dc.Flag("speech_sequence", dc.CONTRADICTED,
                   [PROSODY_CONTRADICTION])
    assert flag.entries == [PROSODY_CONTRADICTION]
    assert flag.attempt == 1
    import dataclasses
    names = [f.name for f in dataclasses.fields(dc.Flag)]
    assert names[:3] == ["step_id", "reading", "entries"], names
    assert names[-1] == "attempt", names


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


def test_steps_without_vision_inputs_carry_no_vision_caveat():
    """The other side of the same gate: marking where nothing is mixed
    is a warning that teaches the model to ignore real measurements."""
    for step in sorted(dc.FLAGGING_STEPS):
        if dc.VISION_INPUT_NAMES & set(dc.evidence_sources(step)):
            continue
        block = dc.prompt_block(step)
        assert "MIXED" not in block, (
            f"{step} holds no vision document but its prompt carries the "
            "vision-prose caveat")
        assert "analysis.scene" not in block


def test_a_real_measurement_still_counts_after_the_marking():
    """The gate that must NOT fail correct output: the prosody
    contradiction this channel was built for still reads CONTRADICTED
    with the caveat in the prompt."""
    block = dc.prompt_block("speech_sequence")
    assert "prosody_analysis" in block
    assert "parselmouth" in block
    _, flag = dc.take("speech_sequence",
                      {"speech_sequence": {}, dc.FIELD: [PROSODY_CONTRADICTION]})
    assert flag.reading == dc.CONTRADICTED
    assert flag.entries[0]["measured_in"] == "prosody_analysis"


def test_vision_prose_paths_name_real_reader_columns():
    """A stale inventory reads as coverage: every marked path must still
    be addressed by at least one step's prompt declaration or by a
    reader that renders vision prose, or the row is describing prose
    nobody reads.

    Two routes, because the pipeline has two: `context_fields` dotted
    paths (the `analysis.scene` half) and the view/bridge/reference
    readers that never appear in a manifest (the `blocks[].visual` and
    `objects[].readable_text` half - `context_views`,
    `footage_reference`).  An instrument that checked only manifests
    reported the second half as unread; it was calibrated against
    `readable_text` in `footage_reference.py` before being believed."""
    repo = Path(__file__).resolve().parents[1]
    manifested = set()
    for path in sorted((repo / "library" / "steps").glob("*/manifest.json")):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for declared in (manifest.get("context_fields") or ()):
            manifested.add(str(declared).split(".")[-1].lstrip("-"))
    readers = [
        "library/tools/vision_schema_adapter.py",
        "library/tools/semantic_index.py",
        "library/tools/context_views.py",
        "library/tools/footage_reference.py",
        "library/tools/analysis/vision_pipeline_v3.py",
        "library/steps/step_3_02_select_broll/bridge.py",
        "library/steps/step_4_02_plan_transitions/bridge.py",
        "library/steps/step_4_03_plan_vfx/bridge.py",
        "library/steps/step_4_04_plan_sfx/bridge.py",
    ]
    rendered = set()
    for rel in readers:
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for path in dc.VISION_PROSE_PATHS:
            leaf = path.split(".")[-1].rstrip("[]")
            if f'"{leaf}"' in text or f"'{leaf}'" in text:
                rendered.add(path)
    missing = [path for path in dc.VISION_PROSE_PATHS
               if path.split(".")[-1].rstrip("[]") not in manifested
               and path not in rendered]
    assert not missing, (
        f"marked as prose but read nowhere: {missing} - the table has "
        "drifted from what prompts actually address")


def test_the_inventory_s_existing_markings_still_hold():
    """The adjudicated half of the inventory cannot rot silently: 4.02's
    prompt still says whose judgement a verdict is, the music
    justification is still dropped where the inventory says it is, and
    the direction is still prose-by-construction with a closed key set.

    The verdict columns were MARKED by `CUT_VERDICT_LEGEND` travelling
    beside the table while 4.02's `handoff.md` was frozen. The freeze
    was lifted 2026-09-09 and the definition moved into the prompt, so
    the marking is asserted where it now lives - the prompt the model
    actually reads.
    """
    handoff = (Path(__file__).resolve().parents[1] / "library" / "steps"
               / "step_4_02_plan_transitions" / "handoff.md").read_text(
        encoding="utf-8")
    assert "`narrative_verdict`" in handoff
    assert "`verdict_note`" in handoff
    assert "rough-cut review's judgement" in handoff.lower(), (
        "the attribution is gone: a downstream decision would rest on "
        "prose whose author the prompt no longer names")

    # The inventory's CORRECTLY EXCLUDED row: 2.05 drops the
    # justification's forbidden-register reasoning out of the
    # top-level `music_selection` it routes. (3.03's old
    # `-audio_spine.music_selection.*` drops went with the nested
    # copy itself when the audit trail moved to its own file -
    # captain's ruling, 2026-09-16 - so only the 2.05 marking is
    # asserted here.)
    manifest = json.loads(
        (Path(__file__).resolve().parents[1] / "library" / "steps" /
         "step_2_05_mesh_spine" / "manifest.json").read_text(
            encoding="utf-8"))
    assert ("-music_selection.direction_justification.why_not_forbidden"
            in (manifest.get("context_fields") or ())), (
        "step_2_05_mesh_spine no longer drops the music justification "
        "- the inventory's CORRECTLY EXCLUDED row is stale")

    assert len(DIRECTION_KEYS) == 8, (
        f"step 2.01 is asked for {DIRECTION_KEYS} - if the schema grew, "
        "the inventory's SELF-MARKING row must say so")


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


def test_the_alignment_citation_does_not_leak_to_a_step_that_cannot_see_it():
    """mesh_spine is routed speech_sequence too, but drops the report by
    name with no view replacing it - its model never sees the numbers,
    so inviting it to cite them would invite fabrication."""
    assert "speech_sequence" not in dc.evidence_sources("mesh_spine")
