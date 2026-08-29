"""Word timings are for the code that cuts on them, not for the prompt.

WhisperX gives every speech region its text AND every word in it with a
start and an end.  `temporal_index.*.speech_regions` put the whole of
that in two prompts: on project 001 it is 110 regions, 1,439 word records
and 93,246 bytes of `creative_direction`'s 113,053-byte context - 82.5%
of the call - to say 7,184 bytes of English.  No creative model is asked
anything a word boundary answers.

Every reader of those timings is Python, and every one of them reads them
somewhere other than the prompt: `speech_sequence`'s post-bridge opens the
per-clip index files off disk, and `spine_contract`, `plan_subtitles` and
the other post-bridges receive the UNPROJECTED inputs.  So the two facts
this file has to hold together are:

  * the prompt carries the transcript and no word timings, and
  * the code still gets every word.

The second is demonstrated by running the real post-bridge, not asserted.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import CONTEXT_VIEWS, build_view
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

# The two steps the view replaced the word-level regions in.  Both are
# still checked for word timings; only 2.01 still reads the VIEW.
TRANSCRIPT_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "speech_sequence": "step_2_02_speech_sequence",
}

# 2.02's own pre-bridge builds `transcripts_toon` off the per-clip index
# files, with the same content and a deliberate `clip_id,start,end,text`
# header, and its handoff tells the model to read that table by name.
# Declaring the view as well put all 110 lines in the prompt twice, in two
# different column orders - 19,844 characters, a quarter of the context.
# So the view is the route for the step that has no bridge.
# `tests/test_context_ships_it_once.py` holds the pair of them together.
#
# 3.03 is the third case and the reason is its own: Check 5 of its frozen
# handoff tells it to reconstruct what the viewer ACTUALLY hears "from
# actual temporal index data, not from the speech_sequence's intended
# text", and the projection was deleting the only input that could answer
# it.  Every other input the step is routed is a decision some upstream
# step made, so without this the reviewer was handed nothing but the plan
# it was reviewing. See docs/RULE_EVIDENCE.md, "the review answered
# and nobody read it".
VIEW_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "review_rough_cut": "step_3_03_review_rough_cut",
}

# The two steps handed their whole input set on a standing decision
# (tests/test_llm_context_routing.py NO_PROJECTION).  They carried word
# timings too, by a different route: `assembly_manifest.subtitles[*].words`
# from `plan_subtitles`.  6,953 bytes of 001's render call.
QA_STEPS = {
    "render": "step_6_01_render",
    "validate": "step_6_02_validate_output",
}

CONTRACTION = "okay, we're here, we're here."

TEMPORAL_INDEX = [
    {
        "clip_id": "clip_006",
        "source_file": "/nowhere/clip_006.mov",
        "duration": 20.0,
        "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
        "speech_regions": [
            {
                "start": 14.68, "end": 16.085, "text": CONTRACTION,
                "confidence": -0.086, "method": "whisperx-wav2vec2-large-v3",
                "words": [
                    {"word": "okay,", "start": 14.68, "end": 15.202},
                    {"word": "we're", "start": 15.222, "end": 15.443},
                    {"word": "here,", "start": 15.463, "end": 15.643},
                    {"word": "we're", "start": 15.684, "end": 15.864},
                    {"word": "here.", "start": 15.884, "end": 16.085},
                ],
            },
        ],
        "energy_curve": {"sample_rate_hz": 5, "values": [0.1] * 100},
    },
    {
        "clip_id": "clip_009",
        "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
        "speech_regions": [],
    },
]


def manifest(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text())


def declared_inputs(m: dict) -> set:
    return {i["name"] for i in m.get("interface", {}).get("inputs", [])}


def find_key(obj, name: str) -> bool:
    if isinstance(obj, dict):
        return name in obj or any(find_key(v, name) for v in obj.values())
    if isinstance(obj, list):
        return any(find_key(v, name) for v in obj)
    return False


# ── What the prompt carries ───────────────────────────────────────────

@pytest.mark.parametrize("node_id", sorted(TRANSCRIPT_STEPS))
def test_no_word_timing_reaches_the_prompt(node_id):
    cf = manifest(TRANSCRIPT_STEPS[node_id])["context_fields"]
    projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
    context = json_to_toon(projected)

    assert not find_key(projected, "words"), (
        f"'{node_id}' still sends per-word timings to the model"
    )
    assert '"word":' not in context, (
        f"'{node_id}'s context still contains word records:\n{context}"
    )


@pytest.mark.parametrize("node_id", sorted(VIEW_STEPS))
def test_what_was_said_survives(node_id):
    """A saving bought by blinding the step is not a saving."""
    cf = manifest(VIEW_STEPS[node_id])["context_fields"]
    projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)

    assert projected["transcript"] == [{
        "clip_id": "clip_006", "start": 14.68, "end": 16.085,
        "text": CONTRACTION,
    }]
    # And it reaches the prompt spelled the way the captain said it.
    assert CONTRACTION in json_to_toon(projected)


@pytest.mark.parametrize("node_id", sorted(VIEW_STEPS))
def test_the_view_declares_the_input_it_reads(node_id):
    """A view is not routing. The step still has to be sent the input."""
    m = manifest(VIEW_STEPS[node_id])
    assert "view:transcript" in m["context_fields"]
    assert "temporal_index" in declared_inputs(m), (
        f"'{node_id}' asks for the transcript view and no longer declares "
        f"temporal_index, so there is nothing for the view to read"
    )


@pytest.mark.parametrize("node_id", sorted(QA_STEPS))
def test_no_word_timing_reaches_the_qa_prompts(node_id):
    """The other route in: `subtitles[*].words` off the assembly manifest.

    These two steps are deliberately unprojected, so the removal is a
    drop-only declaration - everything they were handed, minus this one
    field - rather than an allow-list, which would quietly become the
    decision about what the QA calls should ask for.
    """
    inputs = {
        "assembly_manifest": {
            "subtitles": [
                {"id": "sub_001", "text": "i can feel the",
                 "timeline_start": 0.0, "timeline_end": 0.636,
                 "emphasis_words": ["feel"],
                 "words": [{"word": "i", "start": 0.0, "end": 0.036}]},
            ],
            "tracks": {"v1": [{"clip_id": "clip_001"}]},
        },
        "rendered_output": {"output_path": "/nowhere/out.mp4"},
    }
    cf = manifest(QA_STEPS[node_id])["context_fields"]
    projected = project_fields(inputs, cf)

    assert not find_key(projected, "words")
    # Everything else it was handed is still there: this is a drop, not
    # an allow-list.
    sub = projected["assembly_manifest"]["subtitles"][0]
    assert sub["text"] == "i can feel the"
    assert sub["emphasis_words"] == ["feel"]
    assert projected["assembly_manifest"]["tracks"]["v1"]
    assert projected["rendered_output"]["output_path"] == "/nowhere/out.mp4"


# ── The view enumeration ──────────────────────────────────────────────

def test_an_unknown_view_raises():
    with pytest.raises(ValueError, match="Unknown context view"):
        build_view("moments_but_not_yet", {"temporal_index": TEMPORAL_INDEX})


def test_a_view_whose_source_is_not_routed_contributes_nothing():
    assert build_view("transcript", {"clip_catalog": []}) == {}


@pytest.mark.parametrize("name", sorted(CONTEXT_VIEWS))
@pytest.mark.parametrize("node_id", sorted(VIEW_STEPS))
def test_projecting_an_already_projected_tree_keeps_the_view(node_id, name):
    """An `llm_only` step is projected TWICE on every run.

    `gather_step_inputs` projects it and `present_llm_step` projects the
    result again.  The second pass sees a tree the first one already took
    `speech_regions` out of, so a view that rebuilt itself from scratch
    would delete the section it had just built - which is exactly what
    happened the first time this was wired.
    """
    cf = manifest(VIEW_STEPS[node_id])["context_fields"]
    once = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
    twice = project_fields(once, cf)
    # `.get`, because a view whose source this fixture does not route
    # builds nothing on either pass - and "absent both times" is the same
    # guarantee as "identical both times". The one this fixture does
    # build is asserted on above.
    assert twice.get(name) == once.get(name)


# ── And the code still gets every word ────────────────────────────────

def test_the_post_bridge_still_reads_every_word_timing(tmp_path):
    """Run the real post-bridge, over a real per-clip index, on disk.

    `speech_sequence`'s post-bridge is where a passage picks up its
    `word_timestamps`, and it reads them out of
    `pipeline_output/steps/1_04_temporal_index/<clip_id>.json` - never
    out of the prompt.  `run_hybrid_step` hands it `dict(inputs)`, the
    unprojected set, so nothing the projection does reaches it.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(str(project))
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX)
    (index_dir / "clip_006.json").write_text(
        json.dumps(TEMPORAL_INDEX[0]), encoding="utf-8")

    payload = {
        "project_folder": str(project),
        "temporal_index": {"index_dir": str(index_dir)},
        "speech_sequence": {
            "hook_segment": {
                "clip_id": "clip_006", "text": CONTRACTION,
                "source_start": 14.68, "source_end": 16.085,
            },
            "body_sequence": [],
        },
    }

    proc = subprocess.run(
        [sys.executable,
         str(STEPS / "step_2_02_speech_sequence" / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    hook = json.loads(proc.stdout)["speech_sequence"]["hook_segment"]

    words = [w["word"] for w in hook["word_timestamps"]]
    assert words == ["okay,", "we're", "here,", "we're", "here."], (
        f"the post-bridge lost the word timings the prompt no longer "
        f"carries: {hook.get('word_timestamps')}"
    )
    assert hook["word_timestamps"][0]["source_start"] == 14.68


# ── End to end: what actually lands in a recorded request ─────────────

def test_a_recorded_request_carries_the_transcript_and_no_word_timings(tmp_path):
    """Driven through `present_llm_step`, so the file on disk is the proof."""
    import threading
    import time

    from library.processes.edit_video.run_pipeline import present_llm_step

    project = tmp_path / "project"
    project.mkdir()
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Decide the direction.\n", encoding="utf-8")

    req = project / "pipeline_output" / "llm_requests" / "creative_direction.json"
    res = project / "pipeline_output" / "llm_responses" / "creative_direction.json"

    reply = {"creative_direction": {"narrative_theme": "a theme",
                                    "target_mood": "calm"}}

    def answer():
        # Answer for as long as the call runs: an empty answer fails QA
        # and the step asks again, so a responder that fires once leaves
        # the retry waiting out its timeout.
        deadline = time.time() + 25
        while time.time() < deadline:
            if req.exists() and not res.exists():
                res.parent.mkdir(parents=True, exist_ok=True)
                res.write_text(json.dumps(reply), encoding="utf-8")
            time.sleep(0.05)

    threading.Thread(target=answer, daemon=True).start()

    present_llm_step(
        str(prompt_path),
        {"project_folder": str(project), "temporal_index": TEMPORAL_INDEX},
        "creative_direction",
        manifest={
            "context_fields": manifest(
                VIEW_STEPS["creative_direction"])["context_fields"],
            "interface": {"outputs": [{"name": "creative_direction"}]},
        },
        full_auto="agy", llm_timeout=30,
    )

    context = json.loads(req.read_text(encoding="utf-8"))["context"]
    assert CONTRACTION in context, context
    assert '"word":' not in context, context
    assert "''" not in context, context
