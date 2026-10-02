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

from library.processes.edit_video.run_pipeline import (
    get_step_implementation,
    llm_output_declarations,
    present_llm_step,
)
from library.tools.context_projector import project_fields

DAG = json.loads((REPO / "library/processes/edit_video/dag.json").read_text())
STEPS = REPO / "library" / "steps"

# Steps whose implementation reaches a model, by DAG node id, with the
# step directory each node runs. DERIVED from the DAG through the same
# `get_step_implementation` the runner uses, so a new LLM step joins the
# coverage automatically (a hand-written list drifted twice).
def _llm_steps_from_dag() -> dict:
    out = {}
    for node in DAG["nodes"]:
        impl = get_step_implementation(REPO / "library" / node["step_ref"])
        if impl["type"] in ("llm_only", "hybrid", "deterministic_with_llm"):
            out[node["id"]] = Path(node["step_ref"]).name
    return out


LLM_STEPS = _llm_steps_from_dag()

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


def test_temporal_index_stays_out_of_the_prompt():
    """It is still an input; it is no longer prompt text.

    The deterministic half of each step keeps receiving it unprojected -
    `run_hybrid_step` hands the post-bridge the raw inputs, and
    `deterministic_with_llm` runs step.py before the projection - so
    dropping it from the prompt takes nothing away from any code.
    """
    for node_id in ("mesh_spine", "review_rough_cut"):
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


def test_word_timestamps_do_not_reach_a_planning_prompt():
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
    for node_id in SPINE_PLANNERS:
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
        full_auto="agent", llm_timeout=5,
    )

    assert result == {}
    assert not (project / "pipeline_output" / "llm_requests").exists(), (
        "a request was written for a step with nothing to ask"
    )


@pytest.mark.parametrize("node_id,already_in_hand,still_asked", [
    ("validate", {"deterministic_validation"}, "validation_result"),
])
def test_the_qa_calls_still_have_something_to_ask(node_id, already_in_hand,
                                                  still_asked):
    """The skip must not silently take the QA call with it.

    6.02 is `bridge.py` + `handoff.md` + `post_bridge.py` and has NO
    step.py: the pre-bridge emits `deterministic_validation`, the model
    writes `validation_result`, and the post-bridge merges the two into
    one verdict with the deterministic half decisive.

    Asked through the shipped `llm_output_declarations` rather than by
    re-implementing its subtraction here. The earlier version subtracted
    a hand-written `{"validation_result"}` as "produced by step.py" -
    which nothing produces before the model - so what kept the call
    alive in that reading was `final_qa_decision`, an output declared by
    the manifest and produced by nobody. Deleting the phantom (see
    library/tools/output_contract.py) does NOT skip the call: the model
    is still asked for the one key it actually writes.
    """
    m = manifest(node_id)
    have = declared_inputs(m) | set(already_in_hand)
    asked = [o["name"] for o in llm_output_declarations(m, have)]
    assert asked, (
        f"'{node_id}' now has nothing left to ask and its call would be "
        f"skipped. That is a decision about the QA calls, not a "
        f"consequence to absorb here."
    )
    assert still_asked in asked, asked


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
    "mesh_spine": ("duration_zone",),
    "select_broll": ("broll_candidates_toon",),
    "plan_transitions": ("cuts_toon",),
    "plan_vfx": ("vfx_candidates_toon",),
    "plan_sfx": ("sfx_candidates_toon",),
    # 5.01 became hybrid on 2026-09-03. Its tables carry no `_toon`
    # suffix because they are lists the serialiser renders, not strings
    # the bridge formats - the stem check below is the same either way.
    # Rung 4c added the picture and the match: the stills the colourist
    # opens (`shot_stills`, a formatted string), what the still router
    # saw in them (`still_colour_notes`), and the measured camera-match
    # proposal (`camera_match`).
    "color_grade": ("clip_exposure", "cut_adjacency", "shot_stills",
                    "still_colour_notes", "camera_match"),
    # 3.04's two tables. The step reads `timeline_transcript` in its
    # bridge and the model reads `turns`, which is the summary the
    # bridge renders from it - so the projection dropping the raw
    # document is only safe while this survives.
    "select_reels": ("turns", "reel_candidates"),
}


def test_the_handoff_asks_for_a_table_the_bridge_really_builds():
    """The prompt and the pre-bridge agree on the name.

    Independent of the projection: a handoff naming a table no bridge
    emits is the same key-name mismatch one step earlier.
    """
    for node_id in sorted(BRIDGE_TABLES):
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


# ---------------------------------------------------------------------------
# The projection itself (`context_projector.project_fields`).
# ---------------------------------------------------------------------------

# (data, paths, expected) - a positive path selects; a `-` path removes what
# the paths before it selected, so a projection can say "the whole spine
# without the per-word timings" instead of enumerating the other keys.
PROJECTION_CASES = [
    ({"a": 1, "b": 2}, ["a"], {"a": 1}),
    ({"a": {"b": {"c": 1, "d": 2}}, "e": 3}, ["a.b.c"], {"a": {"b": {"c": 1}}}),
    ({"clips": [{"id": 1, "dur": 5}, {"id": 2, "dur": 10}]}, ["clips.*.dur"],
     {"clips": [{"dur": 5}, {"dur": 10}]}),
    ({"clips": [{"id": 1, "dur": 5}, {"id": 2, "dur": 10}],
      "meta": {"name": "test", "date": "today"}},
     ["clips.*.id", "meta.name"],
     {"clips": [{"id": 1}, {"id": 2}], "meta": {"name": "test"}}),
    ({"spine": {"blocks": [{"text": "a", "words": [1, 2]},
                           {"text": "b", "words": [3]}], "duration": 9}},
     ["spine", "-spine.blocks.*.words"],
     {"spine": {"blocks": [{"text": "a"}, {"text": "b"}], "duration": 9}}),
    ({"spine": {"blocks": [{"content": {"text": "a", "words": [1]}}]}},
     ["spine", "-spine.blocks.*.content.words"],
     {"spine": {"blocks": [{"content": {"text": "a"}}]}}),
    ({"music": {"bpm": 90, "curve": [1, 2, 3]}}, ["music", "-music.curve"],
     {"music": {"bpm": 90}}),
]


def test_project_fields_selects_and_excludes():
    for data, paths, expected in PROJECTION_CASES:
        assert project_fields(data, paths) == expected, paths
