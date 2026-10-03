"""A field a manifest does not declare cannot reach the prompt.

`context_fields` is enforcement only where `project_step_context` reads it
(the manifest's top level). History (3.04's misplaced declaration, 817,317
characters of raw transcript): docs/evidence/context_projection.md.
"""
import json
import sys
from pathlib import Path
import pytest
import ast
import threading
import time


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    get_step_implementation, project_step_context,
)

STEPS = REPO / "library" / "steps"
DAG = json.loads(
    (REPO / "library/processes/edit_video/dag.json").read_text(encoding="utf-8"))

# A sentinel no manifest declares and no restore-by-name knows about, so
# its survival means one thing only: the allow-list did not bind.
UNDECLARED = "a_field_no_manifest_declares"

# The two steps handed their whole input set on a standing captain
# decision (AGENTS.md 10.1). Both express it as a `-`-only declaration,
# which means "everything, minus these" - so an undeclared key reaching
# them is the declared behaviour, not a leak. Enumerated rather than
# detected so that a THIRD step written that way has to be argued for.
UNPROJECTED = {"render", "validate"}

# An LLM step that is never actually asked anything. `semantic_analysis`
# has a handoff.md but every key it declares is produced by its own
# step.py, so the schema handed to the model is `[]` and the call is
# skipped - see test_llm_context_routing.
NO_CALL = {"semantic_analysis": "asks the model for nothing - the call "
                                "is skipped, so there is no prompt"}


def step_dir(node_id: str) -> Path:
    for node in DAG["nodes"]:
        if node["id"] == node_id:
            return REPO / "library" / node["step_ref"]
    raise KeyError(node_id)


def manifest(node_id: str) -> dict:
    return json.loads(
        (step_dir(node_id) / "manifest.json").read_text(encoding="utf-8"))


def declared_anywhere(m: dict):
    """The allow-list, from EITHER location a manifest has used.

    Deliberately not `declared_context_fields`: these tests have to be
    able to run against a tree where the declaration is still in the
    wrong place, or they cannot prove they would have caught it.
    """
    top = m.get("context_fields")
    if top is not None:
        return top
    return (m.get("interface") or {}).get("context_fields")


def llm_nodes() -> list:
    """Every DAG node whose implementation reaches a model.

    DERIVED, from the same `get_step_implementation` the runner uses.
    A hand-written list is what let `select_reels` go unexamined.
    """
    out = []
    for node in DAG["nodes"]:
        impl = get_step_implementation(REPO / "library" / node["step_ref"])
        if impl["type"] in ("llm_only", "hybrid", "deterministic_with_llm"):
            out.append(node["id"])
    return sorted(out)


def projecting_nodes() -> list:
    """LLM nodes whose declaration should narrow the prompt."""
    return [n for n in llm_nodes()
            if n not in UNPROJECTED and n not in NO_CALL
            and declared_anywhere(manifest(n))]


ALL_STEP_MANIFESTS = sorted(
    p.parent.name for p in STEPS.glob("*/manifest.json"))


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def test_a_declaration_is_where_the_projection_reads_it():
    """One location, and a second one is refused rather than ignored.

    Reading both would be two spellings of the same rule, free to
    disagree; ignoring the second is what shipped. The manifest's top
    level is the location, enforced by `declared_context_fields` - the
    dead `library/schema/manifest.schema.json` once described it too,
    but nothing ever loaded that file, so it was deleted.
    """
    misplaced = [
        step for step in ALL_STEP_MANIFESTS
        if "context_fields" in (json.loads(
            (STEPS / step / "manifest.json").read_text(encoding="utf-8")
        ).get("interface") or {})
    ]
    assert not misplaced, (
        f"{misplaced} declare context_fields under `interface`, where nothing "
        f"reads it: the projection would never run and the model would be "
        f"handed every byte the step was routed. Move it to the "
        f"manifest's top level."
    )


# ---------------------------------------------------------------------------
# The consequence
# ---------------------------------------------------------------------------

def test_an_undeclared_field_cannot_reach_a_prompt():
    """Driven through the real `project_step_context`.

    The manifest is read whole and passed as the runner passes it, so
    this asks the question the runner asks: given what this step really
    declares, does a key it does not name survive?
    """
    nodes = projecting_nodes()
    assert nodes
    leaking = [
        node_id for node_id in nodes
        if UNDECLARED in project_step_context(
            {UNDECLARED: "x" * 1000, "project_folder": "/tmp/p"},
            manifest(node_id),
        )
    ]
    assert not leaking, (
        f"{leaking} hand the model a field its manifest does not "
        f"declare. Either the declaration is somewhere the projection "
        f"does not read, or the step is not projected at all."
    )


KEPT = "a_declared_leaf"


def _shaped_for(path: str):
    """A tree whose only content sits exactly where `path` points.

    `*` becomes a one-item list, so `clip_catalog.*.clip_id` builds
    `{"clip_catalog": [{"clip_id": KEPT, <UNDECLARED>: ...}]}`. The
    sibling is what proves the projection narrowed rather than passed
    the tree through.
    """
    parts = path.split(".")

    def build(rest):
        if not rest:
            return KEPT
        head, tail = rest[0], rest[1:]
        if head == "*":
            return [build(tail)]
        inner = build(tail)
        if isinstance(inner, dict) or inner is KEPT:
            return {head: inner, UNDECLARED: "x"}
        return {head: inner}

    return {parts[0]: build(parts[1:])} if parts[1:] else {parts[0]: KEPT}


def _leaf_at(tree, path: str):
    for part in path.split("."):
        if part == "*":
            if not isinstance(tree, list) or not tree:
                return None
            tree = tree[0]
            continue
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


def test_a_declared_field_still_reaches_the_prompt():
    """The other direction: the gate must not pass by deleting everything.

    A projection that dropped its own declared paths would satisfy every
    assertion above. The first path each step declares is built as a
    tree with an undeclared sibling; the declared leaf must arrive and
    the sibling must not.
    """
    for node_id in projecting_nodes():
        fields = declared_anywhere(manifest(node_id))
        keeps = [f for f in fields if not f.startswith(("-", "view:"))]
        assert keeps, (
            f"'{node_id}' declares no positive path, so this direction "
            f"cannot be checked here - narrow the test, do not drop it"
        )
        path = keeps[0]
        inputs = _shaped_for(path)
        inputs["project_folder"] = "/tmp/p"
        projected = project_step_context(inputs, manifest(node_id))

        assert _leaf_at(projected, path) == KEPT, (
            f"'{node_id}' lost {path!r}, which its own manifest declares"
        )
        assert UNDECLARED not in json.dumps(projected), (
            f"'{node_id}' kept an undeclared sibling of {path!r}"
        )


# ---------------------------------------------------------------------------
# The enumeration that missed it
# ---------------------------------------------------------------------------

def test_every_llm_step_declares_an_allow_list():
    """Derived from the DAG, so a new LLM step cannot escape it."""
    missing = [n for n in llm_nodes()
               if n not in NO_CALL and not declared_anywhere(manifest(n))]
    assert missing == [], missing


# ---------------------------------------------------------------------------
# What 3.04 declares now
# ---------------------------------------------------------------------------

def test_select_reels_does_not_carry_the_transcript_document():
    """`timeline_transcript.turns` did not exist, and `segments` is huge.

    The document carries `segments`, not `turns`; the `turns` the
    handoff tells the model to read is the PRE-BRIDGE's own table, built
    from those segments and restored by name after the projection. The
    old declaration named a path that resolves to nothing - which the
    projector would have WARNED about on every run, had the projection
    ever run.

    What is declared now is the document's one-sentence `measurement`,
    which says what the times mean and how the speech was obtained. The
    940 segments and the 8,509 per-word timings under them are not.
    """
    document = {
        "measurement": "Speech transcribed by WhisperX. Times are "
                       "timeline time.",
        "derived_from": {"fps": 23.976, "picture_holes": [[1.0, 2.0]]},
        "segments": [{
            "speaker": "SpeakerTwo", "text": "hello", "timeline_start": 0.0,
            "timeline_end": 1.0, "source_file": "/m/a.MXF",
            "resolve_item_id": "u",
            "words": [{"word": "hello", "start": 0.0, "end": 1.0,
                       "timed": True}],
        }],
    }
    projected = project_step_context(
        {"timeline_transcript": document, "project_folder": "/tmp/p"},
        manifest("select_reels"),
    )
    carried = projected.get("timeline_transcript") or {}
    assert carried.get("measurement"), (
        "the model no longer knows what the times it is choosing MEAN"
    )
    assert "segments" not in carried, (
        "the transcript document is back in select_reels' prompt: "
        "817,317 characters, 8,509 per-word timings"
    )
    # The per-word array is forbidden by its KEY, not by the letters
    # `words` appearing anywhere in the blob. The view's own prose says
    # what the transcriber recorded about the WORDS on a line, and a
    # transcript line may contain the English word too - matching those
    # is the test firing on its own explanation.
    assert '"words"' not in json.dumps(projected)

    bridge = (step_dir("select_reels") / "bridge.py").read_text(
        encoding="utf-8")
    assert '"turns"' in bridge, (
        "the pre-bridge no longer builds `turns`, so dropping the "
        "transcript document from the prompt now blinds the step"
    )


# --------------------------------------------------------------------------
# From test_context_ships_it_once.py
#
# Nothing reaches a prompt twice, and no raw array reaches one at all.
#
# These are properties of the step manifests' `context_fields`, asserted on
# the manifests (AGENTS.md 10.1). History: docs/evidence/context_projection.md.

sys.path.insert(0, str(REPO))


ALL_MANIFESTS = sorted(STEPS.glob("*/manifest.json"))


def context_fields(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8")).get("context_fields") or []


# `vision_schema_adapter.scene_prose` renders `scene[]`; `camera_prose`
# renders `camera[]`.  Either is a legitimate thing to send.  Both is not.
DERIVED_FROM = {
    "semantic_analysis_documents.*.analysis.scene":
        "semantic_analysis_documents.*.scene",
    "semantic_analysis_documents.*.analysis.motion":
        "semantic_analysis_documents.*.camera",
}


def test_no_step_gets_a_summary_and_its_own_source():
    assert ALL_MANIFESTS
    offenders = [
        f"{path.parent.name}: {prose!r} + {raw!r}"
        for path in ALL_MANIFESTS
        for prose, raw in DERIVED_FROM.items()
        if {prose, raw} <= set(context_fields(path))
    ]
    assert not offenders, (
        "a step declares a vision prose field AND the structure it was "
        f"rendered from - send one of them: {offenders}"
    )


RAW_MUSIC_ARRAYS = (
    "music_analysis.tempo.beats",
    "music_analysis.tempo.downbeats",
    "music_analysis.energy_dynamics.energy_curve_1hz",
)


def test_the_raw_beat_grid_never_reaches_a_prompt():
    """A step routed the whole analysis must drop the three value lists
    with `-` paths (the grid is read in code through `beat_grid.py`)."""
    offenders = [
        f"{path.parent.name}: {array}"
        for path in ALL_MANIFESTS
        if "music_analysis" in context_fields(path)
        for array in RAW_MUSIC_ARRAYS
        if f"-{array}" not in context_fields(path)
    ]
    assert not offenders, (
        f"whole `music_analysis` routed without dropping raw arrays: {offenders}"
    )


def test_the_transcript_reaches_speech_sequence_exactly_once():
    """2.02's pre-bridge builds `transcripts_toon`, so it must not also
    declare the view; 2.01 has no pre-bridge, so the view is its route."""
    step = STEPS / "step_2_02_speech_sequence"
    declared = context_fields(step / "manifest.json")
    assert "view:transcript" not in declared
    assert '"transcripts_toon"' in (step / "bridge.py").read_text(encoding="utf-8")
    assert "view:transcript" in context_fields(
        STEPS / "step_2_01_creative_direction" / "manifest.json")


SPEECH_TIMING_PATHS = (
    "speech_sequence.body_sequence.*.word_timestamps",
    "speech_sequence.hook_segment.word_timestamps",
)


def test_the_rough_cut_review_gets_no_word_timings():
    declared = context_fields(
        STEPS / "step_3_03_review_rough_cut" / "manifest.json")
    for timing in SPEECH_TIMING_PATHS:
        assert f"-{timing}" in declared, (
            f"3.03 routes the whole `speech_sequence` and does not drop "
            f"{timing!r}. Word timings do not reach a prompt (AGENTS.md 10.1)."
        )


def test_validate_is_not_sent_the_qa_report_and_its_own_summary():
    declared = context_fields(
        STEPS / "step_6_02_validate_output" / "manifest.json")
    assert "-deterministic_validation.qa_report" in declared, (
        "6.02 is handed its own QA report's raw rows beside the summary "
        "rendered from them. Send one of them."
    )


# --------------------------------------------------------------------------
# From test_llm_context_routing.py
#
# What each LLM step is handed, and what it is deliberately not handed.
#
# A step that declares no `context_fields` is handed its whole input set.
# `mesh_spine` and `review_rough_cut` declared none, so each was handed
# `temporal_index` in full - a 5 Hz per-frame numeric stream that neither
# step's own code reads, and 98% of the tokens in the two calls that decide
# the spine of the edit and review the rough cut.
#
# The assertions here are about ROUTING, not about token counts: what the
# prompt carries, and what it must not.

sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (
    llm_output_declarations,
    present_llm_step,
)
from library.tools.context_projector import project_fields

DAG_2 = json.loads((REPO / "library/processes/edit_video/dag.json").read_text())

# Steps whose implementation reaches a model, by DAG node id, with the
# step directory each node runs. DERIVED from the DAG through the same
# `get_step_implementation` the runner uses, so a new LLM step joins the
# coverage automatically (a hand-written list drifted twice).
def _llm_steps_from_dag() -> dict:
    out = {}
    for node in DAG_2["nodes"]:
        impl = get_step_implementation(REPO / "library" / node["step_ref"])
        if impl["type"] in ("llm_only", "hybrid", "deterministic_with_llm"):
            out[node["id"]] = Path(node["step_ref"]).name
    return out


LLM_STEPS = _llm_steps_from_dag()

# The four steps that receive the spine as a planning input.
SPINE_PLANNERS = ("select_broll", "plan_transitions", "plan_vfx", "plan_sfx")


def manifest_2(node_id: str) -> dict:
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
        m = manifest_2(node_id)
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
               for e in DAG_2["edges"]), "no vision edge reaches mesh_spine"
    m = manifest_2("mesh_spine")
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
               for e in DAG_2["edges"])
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
                                   manifest_2(node_id)["context_fields"])

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
    m = manifest_2(node_id)
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


# --------------------------------------------------------------------------
# From test_entry_step_inputs.py
#
# An entry node can only get what its own manifest asks for.
#
# `validate_sfx_library` is an entry node: no incoming edges, so no
# `data_mapping` can route anything to it. Its `step.py` reads
# `data["sfx_library"]` and exits 1 without it, and its manifest declared
# `interface.inputs: []`. The step was therefore unrunnable on any project
# whose `pipeline_data.json` did not already carry the key from some earlier
# era - which is every new project, and project 001 the moment its state was
# cleared.
#
# The runner now injects a PROCESS_LEVEL_INPUT only into steps that declare
# it, so the manifest is the request. These tests hold the two ends
# together: the step must declare what its code requires, and the runner must
# honour the declaration.

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    PROCESS_LEVEL_INPUTS,
    gather_step_inputs,
)

STEPS_ROOT = REPO_ROOT / "library" / "steps"
DAG_3 = json.loads(
    (REPO_ROOT / "library" / "processes" / "edit_video" / "dag.json").read_text()
)


def _step_dir(node_id: str) -> Path:
    for node in DAG_3["nodes"]:
        if node["id"] == node_id:
            return REPO_ROOT / "library" / node["step_ref"]
    raise AssertionError(f"{node_id} is not in the DAG")


def _keys_read_from_stdin_payload(step_py: Path) -> set:
    """Every `data.get("x")` / `data["x"]` in the step's main().

    Reads the producer's own source rather than a fixture, which is the
    only way this catches a rename.
    """
    tree = ast.parse(step_py.read_text())
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "data"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "data" and isinstance(node.slice, ast.Constant):
                keys.add(node.slice.value)
    return keys


def test_entry_steps_declare_the_process_inputs_their_code_reads():
    entry_nodes = DAG_3["entry_nodes"]
    assert entry_nodes, "the DAG has no entry nodes"

    for node_id in entry_nodes:
        step_dir = _step_dir(node_id)
        step_py = step_dir / "step.py"
        manifest_path = step_dir / "manifest.json"
        if not step_py.exists() or not manifest_path.exists():
            continue

        declared = {
            inp.get("name")
            for inp in json.loads(manifest_path.read_text())
            .get("interface", {})
            .get("inputs", [])
        }
        needed = _keys_read_from_stdin_payload(step_py) & set(PROCESS_LEVEL_INPUTS)
        missing = needed - declared
        assert not missing, (
            f"Entry step '{node_id}' reads {sorted(missing)} from its stdin "
            f"payload but does not declare it in manifest.json. An entry node "
            f"has no incoming edges, so a declaration is the only way the "
            f"value can reach it."
        )


def test_gather_step_inputs_supplies_only_the_declared_process_inputs():
    """Declaring is asking. A step that did not ask does not receive."""
    state = {
        "project_folder": "/tmp/project",
        "sfx_library": "/tmp/sfx",
        "music_library": "/tmp/music",
        "step_outputs": {},
    }
    manifest = {"interface": {"inputs": [{"name": "sfx_library"}]}}
    inputs = gather_step_inputs("validate_sfx_library", DAG_3, state, manifest)
    assert inputs["sfx_library"] == "/tmp/sfx"
    assert "music_library" not in inputs


# --------------------------------------------------------------------------
# From test_brand_constraints_reach_the_prompt.py
#
# The brand's constraints must reach the steps that plan the look.
#
# Every assertion starts from a fact of the pipeline (node ids in `dag.json`,
# the synthetic project copies), never from the identifier the loader wants.
# History: docs/RULE_EVIDENCE.md (brand constraints keyed on manifest ids).

sys.path.insert(0, str(REPO))

from library.tools.template_loader import TemplateLoader  # noqa: E402
from tests.brand_fixtures import write_brand_json, write_templates_dir  # noqa: E402

DAG_4 = json.loads((REPO / "library/processes/edit_video/dag.json").read_text())


def _dag_node_id(step_dirname: str) -> str:
    """The DAG's id for a step, read off the DAG rather than assumed."""
    for node in DAG_4["nodes"]:
        if Path(node["step_ref"]).name == step_dirname:
            return node["id"]
    raise AssertionError(f"no DAG node runs {step_dirname}")


def test_exactly_the_three_brand_slots_answer_the_runners_identifier(tmp_path):
    """Ask the way the runner asks, and a brand must answer - for each of
    the three steps with a brand slot, and every other step gets "".  The
    id is read out of `dag.json`, so renaming a node without teaching the
    loader fails here."""
    templates = write_templates_dir(tmp_path / "templates")
    loader = TemplateLoader(str(tmp_path), templates)
    for step_dirname in ("step_2_01_creative_direction",
                         "step_4_02_plan_transitions", "step_4_03_plan_vfx"):
        node_id = _dag_node_id(step_dirname)
        constraints = loader.get_brand_constraints("synthetic_default", node_id)
        assert constraints.strip(), (
            f"synthetic_default contributes nothing to '{node_id}'. The "
            f"runner passes exactly this identifier.")
        assert "Brand Constraints:" in constraints

    templates = write_templates_dir(tmp_path / "templates")
    loader = TemplateLoader(str(tmp_path), templates)
    for node_id in ("mesh_spine", "select_broll", "plan_sfx", "render"):
        assert loader.get_brand_constraints("synthetic_default", node_id) == ""


def _answer_when_asked(project: Path, node_id: str, answer: dict):
    """Stand in for the agent on the other end of the agent file handshake."""
    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"

    def run():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(answer), encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_the_brand_reaches_the_text_handed_to_the_model(tmp_path):
    """End to end, through the runner, in the mode this pipeline runs in.

    `agent` writes the request to a file and an agent answers it, so the
    request file IS the prompt.  It used to carry `prompt` alone while
    the constraints were concatenated only into the API path's
    `full_prompt` - which meant that even a working
    `get_brand_constraints` would have reached nobody here.
    """
    project = tmp_path / "project"
    project.mkdir()
    # The project SELECTS a brand and carries its copy.  It used to
    # declare none and still get a fallback file's constraints, which is
    # the thing that changed: a template-less project now contributes no
    # brand text at all, so a test that asserts the brand reaches the
    # prompt has to carry one.
    (project / "project.yaml").write_text(
        "name: t\nslug: t\npipeline:\n  brand_template: synthetic_default\n",
        encoding="utf-8")
    write_brand_json(project, "synthetic_default")
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the transitions.\n", encoding="utf-8")

    node_id = _dag_node_id("step_4_02_plan_transitions")
    _answer_when_asked(project, node_id,
                       {"transition_creative": [{"cut_point_position": 1,
                                                 "transition_type": "hard_cut"}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "timed_spine": {"structure": []}},
        node_id,
        manifest={"interface": {"outputs": [{"name": "transition_creative"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / f"{node_id}.json").read_text()
    )
    expected = TemplateLoader(str(project)).get_brand_constraints(
        "synthetic_default", node_id)
    assert expected.strip()
    assert request["constraints"] == expected, (
        "the request file does not record what the brand contributed"
    )
    assert expected.strip() in request["prompt"], (
        "the brand constraints are missing from the prompt the agent is given"
    )
    # The vocabulary itself, not just the header.
    assert "hard_cut" in request["prompt"]


def test_a_project_that_selected_no_brand_contributes_no_brand_text(tmp_path):
    """The other half, and the point of the change.

    A project declaring no `pipeline.brand_template` used to be handed
    `default_brand.yaml`'s constraints: step 2.01 was told the series runs
    at "high" energy and step 4.03 was told to plan VFX at intensity 0.5.
    On project 001 the model overruled the energy in writing and called
    those values "a default nobody chose for this project".  Absence now
    declares nothing - see ABSENT_SLOT_READINGS in
    library/tools/brand_registry.py.
    """
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: t\nslug: t\n", encoding="utf-8")
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the transitions.\n", encoding="utf-8")

    node_id = _dag_node_id("step_4_02_plan_transitions")
    _answer_when_asked(project, node_id,
                       {"transition_creative": [{"cut_point_position": 1,
                                                 "transition_type": "hard_cut"}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "timed_spine": {"structure": []}},
        node_id,
        manifest={"interface": {"outputs": [{"name": "transition_creative"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / f"{node_id}.json").read_text()
    )
    assert request["constraints"] == ""
    assert "Brand Constraints" not in request["prompt"]
    assert "Transition Duration MS" not in request["prompt"]


# --------------------------------------------------------------------------
# From test_creative_brief_reaches_prompt.py
#
# The captain's creative brief has to arrive in the prompt, not just exist.
#
# Asserted by FOLLOWING the reference the model is handed and requiring the
# declared brief's words at the end of it. History: docs/evidence/brief_reference.md
# ("The brief that never reached a prompt").

sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    load_pipeline_state,
)


BRIEF_TEXT = (
    "# Through the 4th Wall\n\n"
    "Minimal cuts. Let moments breathe.\n"
    "SENTINEL_BRIEF_MARKER_9f3a\n"
)


def _manifest(step_dir: Path) -> dict:
    return json.loads((step_dir / "manifest.json").read_text())


def _declared_inputs(step_dir: Path) -> set:
    iface = _manifest(step_dir).get("interface", {})
    return {i.get("name") for i in iface.get("inputs", []) if isinstance(i, dict)}


def _steps_documenting_the_brief():
    """Every step whose handoff.md tells the model to read a brief."""
    found = []
    for handoff in sorted(STEPS_ROOT.glob("*/handoff.md")):
        if "creative_brief" in handoff.read_text():
            found.append(handoff.parent)
    return found


def test_every_step_that_documents_the_brief_declares_it():
    """Documenting it is not asking for it: the runner injects a
    process-level input only into steps whose manifest declares it."""
    documenting = _steps_documenting_the_brief()
    assert documenting, "no handoff names the brief; the scan is broken"
    silent = [d.name for d in documenting
              if "creative_brief" not in _declared_inputs(d)]
    assert not silent, (
        f"handoff.md tells the LLM to read the creative brief but the "
        f"manifest does not declare the input: {silent}")


# An edgeless DAG: these tests are about the process-level input
# mechanism, not about data_mapping routing, and a real node would demand
# its whole upstream be present in state first.
EDGELESS_DAG = {"nodes": [], "edges": []}


def _gather(node_id, state, manifest):
    return gather_step_inputs(node_id, EDGELESS_DAG, state, manifest=manifest)


def _brief_the_model_can_reach(inputs) -> str:
    """Everything the model can get to, following what it was handed.

    The reference itself plus, when it names one, the file at the end of
    the path - opened here exactly as a shell-capable harness would open
    it. A reference naming an unreachable or relative path fails here.
    """
    from library.tools.brief_reference import reference_path
    text = inputs["creative_brief"]
    path = reference_path(text)
    if not path:
        return text
    assert Path(path).is_absolute(), (
        f"the reference hands the model {path!r}, which is relative: it "
        f"runs from wherever the harness put it, not from the project")
    return text + Path(path).read_text(encoding="utf-8")


DECLARING_MANIFEST = {
    "interface": {"inputs": [{"name": "creative_brief", "required": False}]}
}
SILENT_MANIFEST = {"interface": {"inputs": [{"name": "clip_catalog"}]}}


def test_a_relative_brief_resolves_against_the_project(tmp_path):
    (tmp_path / "brief.md").write_text(BRIEF_TEXT, encoding="utf-8")
    state = {"project_folder": str(tmp_path), "creative_brief": "brief.md"}

    inputs = _gather("plan_vfx", state, DECLARING_MANIFEST)

    assert BRIEF_TEXT in _brief_the_model_can_reach(inputs)


def test_a_missing_brief_raises_rather_than_passing_the_path(tmp_path):
    """The regression that made the old code look like it worked."""
    state = {
        "project_folder": str(tmp_path),
        "creative_brief": str(tmp_path / "does_not_exist.md"),
    }

    with pytest.raises(RuntimeError, match="cannot be read"):
        _gather("plan_vfx", state, DECLARING_MANIFEST)


def test_a_project_declaring_no_brief_gets_none(tmp_path):
    """A project without a brief must still run."""
    inputs = _gather("plan_vfx", {"project_folder": str(tmp_path)},
                     DECLARING_MANIFEST)
    assert not inputs.get("creative_brief")

    project = _project_declaring(tmp_path, 'name: "T"\nslug: "t"\n')

    state = load_pipeline_state(str(project))

    assert not state.get("creative_brief")


def _answer_when_asked_2(project: Path, node_id: str, answer: dict):
    """Stand in for the agent on the other end of the agent file handshake.

    The response cannot simply be pre-placed: `present_llm_step` deletes
    any existing response file before it writes the request, precisely so
    a stale answer cannot be mistaken for a fresh one.
    """
    req = project / "pipeline_output" / "llm_requests" / f"{node_id}.json"
    res = project / "pipeline_output" / "llm_responses" / f"{node_id}.json"

    def run():
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(answer), encoding="utf-8")
                return
            time.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_context_field_projection_does_not_drop_the_brief(tmp_path):
    """`context_fields` deletes every key it does not name.

    The brief is not a context field - it is restored around the
    projection - so a step declaring narrow `context_fields` must still
    get it. This is the mechanism that would silently undo the fix.
    """
    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Plan the sound effects.\n", encoding="utf-8")

    _answer_when_asked_2(project, "plan_sfx",
                       {"sfx_plan": [{"timeline_in": 0.0,
                                      "timeline_out": 0.5}]})

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "creative_brief": BRIEF_TEXT,
         "dropped_key": "should not survive", "timed_spine": {}},
        "plan_sfx",
        manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                "outputs": [{"name": "sfx_plan"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30,
    )

    request = json.loads(
        (project / "pipeline_output" / "llm_requests" / "plan_sfx.json").read_text()
    )
    assert "SENTINEL_BRIEF_MARKER_9f3a" in request["context"]
    assert "should not survive" not in request["context"]


# ── The declaration half ──────────────────────────────────────────────
#
# Everything above starts from `state["creative_brief"]` already being
# set. What sets it is `load_pipeline_state`, off the project's own
# `project.yaml`, and nothing tested that - which is how a channel that
# works end to end can still deliver nothing: no project points at a
# document.


def _project_declaring(tmp_path, declaration: str) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text(declaration, encoding="utf-8")
    return project


def test_a_brief_in_a_read_only_planning_tree_reaches_the_request(tmp_path, request):
    """The whole path, from the declaration to the file the model is given.

    This is the shape a real project uses: the planning tree lives
    outside the project and is never copied in, so the declaration is an
    absolute path into somebody else's directory. Read-only here, because
    a run must not need to write to the captain's planning tree.
    """
    planning = tmp_path / "planning" / "1_through_the_4th_wall"
    planning.mkdir(parents=True)
    brief = planning / "branding_creative_direction.md"
    brief.write_text(BRIEF_TEXT, encoding="utf-8")
    brief.chmod(0o444)
    planning.chmod(0o555)
    request.addfinalizer(lambda: planning.chmod(0o755))

    project = _project_declaring(
        tmp_path, f'name: "T"\nslug: "t"\ncreative_brief: "{brief}"\n')

    state = load_pipeline_state(str(project))
    inputs = gather_step_inputs(
        "plan_vfx", EDGELESS_DAG, state, manifest=DECLARING_MANIFEST)
    assert BRIEF_TEXT in _brief_the_model_can_reach(inputs)

    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Do the creative work.\n", encoding="utf-8")
    _answer_when_asked_2(project, "plan_vfx",
                       {"vfx_plan": [{"clip_id": "clip_001",
                                      "effect": "punch_in"}]})
    present_llm_step(
        str(prompt_path), {**inputs, "project_folder": str(project)},
        "plan_vfx",
        manifest={"interface": {"inputs": [{"name": "creative_brief"}],
                                "outputs": [{"name": "vfx_plan"}]},
                  "context_fields": ["timed_spine"]},
        full_auto="agent", llm_timeout=30)

    request = json.loads(
        (project / "pipeline_output" / "llm_requests"
         / "plan_vfx.json").read_text())
    assert "SENTINEL_BRIEF_MARKER_9f3a" in request["context"], (
        "the declaration reached state and the words did not reach the "
        "request")


# --------------------------------------------------------------------------
# From test_craft_role.py
#
# A step that makes a craft judgement is told what craft it is.
#
# Measured 2026-08-25: not one of the pipeline's handoffs told the model
# what job it was doing. `library/tools/craft_role.py` answers that, and
# these hold the three things that make it trustworthy - that a role really
# reaches the assembled prompt, that a step which starts reaching a model
# cannot go quiet about not having one, and that the bench mirrors it.

sys.path.insert(0, str(REPO))

from library.tools import craft_role  # noqa: E402
from library.tools.undetermined import DECLARING_STEPS  # noqa: E402
from library.tools import model_task  # noqa: E402


def test_the_membership_guard_can_actually_fire(monkeypatch):
    """A gate that cannot fail reads as coverage (AGENTS.md 10.4)."""
    monkeypatch.setattr(craft_role, "_REACHES_A_MODEL",
                        frozenset(DECLARING_STEPS) | {"a_new_step"})
    with pytest.raises(RuntimeError, match="a_new_step"):
        craft_role._assert_roles_account_for_every_model_reaching_step()


def test_the_role_is_prepended_to_the_prompt_the_model_reads(tmp_path,
                                                             monkeypatch):
    """The regression this pins: a role module nothing calls.

    Asserted on the ARCHIVED request file rather than on the module,
    because that file is what an answering agent reads.
    """
    from library.processes.edit_video import run_pipeline
    from library.tools.project_layout import Area, layout_for

    node_id = "plan_sfx"
    project = tmp_path / "proj"
    layout = layout_for(str(project))
    layout.ensure()

    handoff = tmp_path / "handoff.md"
    handoff.write_text("# Step\n\nSENTINEL_HANDOFF_BODY\n", encoding="utf-8")

    responses = layout.write_dir(Area.LLM_RESPONSES)
    original_sleep = model_task._agent_sleep

    def _sleep(seconds):
        (responses / f"{node_id}.json").write_text(
            json.dumps({"sfx_creative": []}))
        return original_sleep(0)

    monkeypatch.setattr(model_task, "_agent_sleep", _sleep)

    run_pipeline.present_llm_step(
        str(handoff),
        {"project_folder": str(project)},
        node_id,
        {"interface": {"outputs": [{"name": "sfx_creative", "type": "list"}]}},
        full_auto="agent", llm_timeout=5)

    prompt = json.loads(
        (layout.read_dir(Area.LLM_REQUESTS)
         / f"{node_id}.json").read_text())["prompt"]

    role = craft_role.ROLES[node_id]
    assert role.addressed_as in prompt
    # The correction to the frozen handoff travels with the role, which is
    # the only route it has - the handoff is not ours to edit.
    for correction in role.corrects:
        assert correction in prompt
    # PREPENDED: the frame comes before the document it frames.
    assert prompt.index(role.addressed_as) < prompt.index(
        "SENTINEL_HANDOFF_BODY")
