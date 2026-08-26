"""What each LLM step is handed, and what it is deliberately not handed.

A step that declares no `context_fields` is handed its whole input set.
`mesh_spine` and `review_rough_cut` declared none, so each was handed
`temporal_index` in full - a 5 Hz per-frame numeric stream that neither
step's own code reads, and 98% of the tokens in the two calls that decide
the spine of the edit and review the rough cut.

The assertions here are about ROUTING, not about token counts: what the
prompt carries, and what it must not.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import present_llm_step
from library.tools.context_projector import project_fields

DAG = json.loads((REPO / "library/processes/edit_video/dag.json").read_text())
STEPS = REPO / "library" / "steps"

# Steps whose implementation reaches `present_llm_step`, by DAG node id.
LLM_STEPS = {
    "semantic_analysis": "step_1_03_semantic_analysis",
    "creative_direction": "step_2_01_creative_direction",
    "speech_sequence": "step_2_02_speech_sequence",
    "music_selection": "step_2_04_music_selection",
    "mesh_spine": "step_2_05_mesh_spine",
    "select_broll": "step_3_02_select_broll",
    "review_rough_cut": "step_3_03_review_rough_cut",
    "plan_transitions": "step_4_02_plan_transitions",
    "plan_vfx": "step_4_03_plan_vfx",
    "plan_sfx": "step_4_04_plan_sfx",
    "render": "step_6_01_render",
    "validate": "step_6_02_validate_output",
}

# A step reaches an LLM without a projection only for a stated reason.
NO_PROJECTION = {
    "semantic_analysis": "asks the model for nothing - the call is skipped",
    "render": "what the QA calls should ask for is an open decision",
    "validate": "same open decision as render",
}

# The four steps that receive the spine as a planning input.
SPINE_PLANNERS = ("select_broll", "plan_transitions", "plan_vfx", "plan_sfx")


def manifest(node_id: str) -> dict:
    return json.loads((STEPS / LLM_STEPS[node_id] / "manifest.json").read_text())


def declared_inputs(m: dict) -> set:
    return {i["name"] for i in m.get("interface", {}).get("inputs", [])}


def find_key(obj, name: str) -> bool:
    """Is `name` a key anywhere in this tree?"""
    if isinstance(obj, dict):
        return name in obj or any(find_key(v, name) for v in obj.values())
    if isinstance(obj, list):
        return any(find_key(v, name) for v in obj)
    return False


@pytest.mark.parametrize("node_id", sorted(set(LLM_STEPS) - set(NO_PROJECTION)))
def test_every_llm_step_projects_its_context(node_id):
    assert manifest(node_id).get("context_fields"), (
        f"'{node_id}' declares no context_fields, so the model is handed "
        f"every byte of its inputs. Project them, or add '{node_id}' to "
        f"NO_PROJECTION with the reason."
    )


@pytest.mark.parametrize("node_id", ["mesh_spine", "review_rough_cut"])
def test_temporal_index_stays_out_of_the_prompt(node_id):
    """It is still an input; it is no longer prompt text.

    The deterministic half of each step keeps receiving it unprojected -
    `run_hybrid_step` hands the post-bridge the raw inputs, and
    `deterministic_with_llm` runs step.py before the projection - so
    dropping it from the prompt takes nothing away from any code.
    """
    m = manifest(node_id)
    assert "temporal_index" in declared_inputs(m), (
        "the input declaration was removed; this test no longer proves "
        "the prompt-side drop is safe"
    )
    assert not any("temporal_index" in p for p in m["context_fields"]), (
        f"temporal_index is back in '{node_id}'s prompt"
    )
    step_code = "".join(
        p.read_text() for p in (STEPS / LLM_STEPS[node_id]).glob("*.py"))
    assert "temporal_index" not in step_code, (
        f"'{node_id}' now reads temporal_index in code, so the prompt-side "
        f"drop needs re-examining rather than this assertion relaxing"
    )


def test_mesh_spine_can_see_the_footage():
    """The spine commits to N seconds of non-speech picture.

    It did that knowing only what was SAID: no vision edge reached it, so
    a `transition_slot` was sized before anything had checked that N
    seconds of usable non-speech picture exist.
    """
    assert any(e["from"] == "semantic_analysis" and e["to"] == "mesh_spine"
               for e in DAG["edges"]), "no vision edge reaches mesh_spine"
    m = manifest("mesh_spine")
    assert "semantic_analysis_documents" in declared_inputs(m)
    card = [p for p in m["context_fields"]
            if p.startswith("semantic_analysis_documents")]
    # What the shot is, how long it runs, what is in it, what is usable.
    for field in ("clip_id", "duration_s", "analysis.scene",
                  "assessment.content_type", "assessment.keywords",
                  "assessment.usable_ranges"):
        assert any(p.endswith(field) for p in card), f"the card lost {field}"

    # A card nothing can be joined to is not delivered. The vision
    # documents are keyed by FILE STEM and every other section of this
    # prompt is keyed clip_XXX (AGENTS.md 10.1), so the catalog has to
    # come with them or the model cannot tell which card belongs to the
    # clip a passage was cut from.
    assert any(e["from"] == "catalog" and e["to"] == "mesh_spine"
               for e in DAG["edges"])
    assert "clip_catalog" in declared_inputs(m)
    for field in ("clip_catalog.*.clip_id", "clip_catalog.*.filename"):
        assert field in m["context_fields"]


@pytest.mark.parametrize("node_id", SPINE_PLANNERS)
def test_word_timestamps_do_not_reach_a_planning_prompt(node_id):
    """No planning model reads them; every reader of them is Python.

    `spine_contract`, `bookends`, `plan_subtitles` and three post-bridges
    read `word_timestamps`, and all of them receive the unprojected
    inputs.  Sending them costs about 5,000 tokens a step and answers
    nothing the model is asked.
    """
    spine = {"structure": [{
        "position": 1, "block_type": "speech", "duration_seconds": 2.0,
        "clip_id": "clip_001", "source_start": 0.0, "source_end": 2.0,
        "timeline_start": 0.0, "timeline_end": 2.0,
        "alignment_method": "whisperx_word_alignment",
        "word_timestamps": [{"word": "hi", "source_start": 0.0,
                             "source_end": 0.2}],
        "content": {"passage_ref": 1, "text": "hi",
                    "word_timestamps": [{"word": "hi", "source_start": 0.0,
                                         "source_end": 0.2}]},
    }]}
    projected = project_fields({"timed_spine": spine},
                               manifest(node_id)["context_fields"])

    assert not find_key(projected, "word_timestamps"), (
        f"'{node_id}' still sends per-word timings to the model"
    )
    # The rest of the spine must survive, or this is a saving bought by
    # blinding the step.
    block = projected["timed_spine"]["structure"][0]
    for key in ("position", "block_type", "duration_seconds", "clip_id",
                "source_start", "source_end", "timeline_start", "timeline_end"):
        assert key in block, f"'{node_id}' lost {key} from the spine"
    assert block["content"]["text"] == "hi"


def test_a_step_with_nothing_to_ask_does_not_call_the_model(tmp_path):
    """`semantic_analysis`'s standing case, as a property of the schema.

    Every key the step declares was already produced by its own step.py,
    so the schema handed to the model is `[]` and its answer was the
    three bytes `{}` - after 33,000 tokens of vision documents. A call
    with nothing to ask is not made.
    """
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("This step is deterministic.\n", encoding="utf-8")

    result = present_llm_step(
        str(prompt_path),
        {"project_folder": str(project),
         "semantic_analysis_documents": [{"clip_id": "clip_001"}],
         "total_clips_analyzed": 1},
        "semantic_analysis",
        manifest={"interface": {"outputs": [
            {"name": "semantic_analysis_documents"},
            {"name": "total_clips_analyzed"}]}},
        # A real call in this mode blocks on a response file; reaching the
        # timeout instead of returning is itself the failure.
        full_auto="agy", llm_timeout=5,
    )

    assert result == {}
    assert not (project / "pipeline_output" / "llm_requests").exists(), (
        "a request was written for a step with nothing to ask"
    )


@pytest.mark.parametrize("node_id,produced_by_step_py", [
    ("render", {"render_output"}),
    ("validate", {"validation_result"}),
])
def test_the_qa_calls_still_have_something_to_ask(node_id, produced_by_step_py):
    """The skip must not silently take render or validate with it.

    Both are the same classification accident as semantic_analysis - a
    handoff.md beside a step.py - but both ask the model for a key their
    step.py does not produce, so both still call. What they SHOULD ask
    for is an open decision, and it is not this change's to make.
    """
    outputs = {o["name"] for o in
               manifest(node_id)["interface"]["outputs"]}
    assert outputs - produced_by_step_py, (
        f"'{node_id}' now has nothing left to ask and its call would be "
        f"skipped. That is a decision about the QA calls, not a "
        f"consequence to absorb here."
    )


def test_a_step_with_something_to_ask_still_calls_the_model(tmp_path):
    """The counterpart: the skip is about the schema, not about a name."""
    import threading
    import time

    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Do the work.\n", encoding="utf-8")

    req = project / "pipeline_output" / "llm_requests" / "semantic_analysis.json"
    res = project / "pipeline_output" / "llm_responses" / "semantic_analysis.json"

    def answer():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps({"a_verdict": "fine"}), encoding="utf-8")
                return
            time.sleep(0.05)

    threading.Thread(target=answer, daemon=True).start()

    result = present_llm_step(
        str(prompt_path), {"project_folder": str(project)}, "semantic_analysis",
        manifest={"interface": {"outputs": [{"name": "a_verdict"}]}},
        full_auto="agy", llm_timeout=30,
    )
    assert result == {"a_verdict": "fine"}


# ---------------------------------------------------------------------------
# A pre-bridge's own table must survive the projection.
# ---------------------------------------------------------------------------
#
# Every hybrid step here computes ONE summarised table and its handoff.md
# tells the model, by name, to read it.  `context_fields` is an allow-list,
# so a table nobody thought to add to it is deleted between the bridge that
# built it and the prompt that asks for it.  Four steps shipped that way -
# only `select_broll` happened to name its table - and the symptom is
# invisible from inside the step: the bridge logs success, the prompt still
# says "use `cuts_toon`", and there is no `cuts_toon`.

BRIDGE_TABLES = {
    "speech_sequence": ("transcripts_toon", "topics_toon"),
    "select_broll": ("broll_candidates_toon",),
    "plan_transitions": ("cuts_toon",),
    "plan_vfx": ("vfx_candidates_toon",),
    "plan_sfx": ("sfx_candidates_toon",),
}


@pytest.mark.parametrize("node_id", sorted(BRIDGE_TABLES))
def test_the_handoff_asks_for_a_table_the_bridge_really_builds(node_id):
    """The prompt and the pre-bridge agree on the name.

    Independent of the projection: a handoff naming a table no bridge
    emits is the same key-name mismatch one step earlier.
    """
    step_dir = STEPS / LLM_STEPS[node_id]
    bridge = (step_dir / "bridge.py").read_text(encoding="utf-8")
    handoff = (step_dir / "handoff.md").read_text(encoding="utf-8")
    for table in BRIDGE_TABLES[node_id]:
        assert f'"{table}"' in bridge, (
            f"'{node_id}' bridge.py no longer emits {table!r}; "
            f"update BRIDGE_TABLES or the handoff that asks for it"
        )
        # The STEM, not the key: `speech_sequence`'s handoff describes its
        # two tables as "transcripts" and "topics" rather than by their
        # `_toon` key names, and which spelling a prompt uses is the
        # captain's call. What must hold is that the prompt refers to the
        # table at all - a bridge computing something no prompt mentions
        # is a table nothing reads.
        stem = table.removesuffix("_toon")
        assert stem in handoff, (
            f"'{node_id}' builds {table!r} and its handoff.md never "
            f"mentions {stem!r} - either the prompt lost the instruction "
            f"or the bridge is computing a table nothing reads"
        )


@pytest.mark.parametrize("node_id", sorted(BRIDGE_TABLES))
def test_a_bridge_table_reaches_the_prompt(node_id, tmp_path):
    """The regression this pins: built, then projected away.

    Driven through `present_llm_step` rather than `project_fields`,
    because the allow-list really does drop these names - what keeps them
    is the restore that runs after it, and only the full call exercises
    that.
    """
    import threading
    import time

    tables = BRIDGE_TABLES[node_id]
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Use the table.\n", encoding="utf-8")

    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"

    def answer():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps({"a_verdict": "fine"}),
                               encoding="utf-8")
                return
            time.sleep(0.05)

    threading.Thread(target=answer, daemon=True).start()

    inputs = {"project_folder": str(project),
              "a_key_no_manifest_names": {"dropped": True}}
    for table in tables:
        inputs[table] = f"[1]{{col}}\n{node_id}-row"

    m = manifest(node_id)
    present_llm_step(
        str(prompt_path), inputs, node_id,
        manifest={"context_fields": m["context_fields"],
                  "interface": {"outputs": [{"name": "a_verdict"}]}},
        full_auto="agy", llm_timeout=30,
        bridge_supplied=set(tables),
    )

    written = json.loads(req.read_text(encoding="utf-8"))
    for table in tables:
        assert f"{node_id}-row" in written["context"], (
            f"'{node_id}' built {table!r} and the prompt does not carry "
            f"it - the projection deleted the one table the handoff tells "
            f"the model to read"
        )
    # The allow-list still governs everything the bridge did NOT build.
    assert "a_key_no_manifest_names" not in written["context"], (
        "the bridge restore is passing through un-named inputs too, "
        "which would undo the projection entirely"
    )
