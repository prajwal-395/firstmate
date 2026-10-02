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

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import CONTEXT_VIEWS
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

# The two steps the view replaced the word-level regions in.  Both are
# still checked for word timings; only 2.01 still reads the VIEW.
TRANSCRIPT_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "speech_sequence": "step_2_02_speech_sequence",
}

# The steps that read the VIEW: 2.02 builds its own `transcripts_toon`
# (`tests/contracts/test_context_ships_it_once.py`), and 3.03 needs what the viewer
# actually hears (docs/RULE_EVIDENCE.md, "the review answered and nobody
# read it").
VIEW_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "review_rough_cut": "step_3_03_review_rough_cut",
}

# The two steps handed their whole input set on a standing decision
# (tests/contracts/test_llm_context_routing.py NO_PROJECTION).  They carried word
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

def test_no_word_timing_reaches_the_prompt():
    for node_id, step_dir in sorted(TRANSCRIPT_STEPS.items()):
        cf = manifest(step_dir)["context_fields"]
        projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
        context = json_to_toon(projected)

        assert not find_key(projected, "words"), (
            f"'{node_id}' still sends per-word timings to the model"
        )
        assert '"word":' not in context, (
            f"'{node_id}'s context still contains word records:\n{context}"
        )


def test_what_was_said_survives():
    """A saving bought by blinding the step is not a saving."""
    for node_id, step_dir in sorted(VIEW_STEPS.items()):
        cf = manifest(step_dir)["context_fields"]
        projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)

        assert projected["transcript"] == [{
            "clip_id": "clip_006", "start": 14.68, "end": 16.085,
            "text": CONTRACTION,
        }], node_id
        # And it reaches the prompt spelled the way the captain said it.
        assert CONTRACTION in json_to_toon(projected), node_id


def test_no_word_timing_reaches_the_render_qa_prompt():
    """The other route in: `subtitles[*].words` off the assembly manifest.

    `render` is deliberately unprojected, so the removal is a drop-only
    declaration - everything it was handed, minus this one field -
    rather than an allow-list, which would quietly become the decision
    about what the QA calls should ask for. `validate` carries no
    manifest at all (pinned below).
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
    cf = manifest(QA_STEPS["render"])["context_fields"]
    projected = project_fields(inputs, cf)

    assert not find_key(projected, "words")
    # Everything else it was handed is still there: this is a drop,
    # not an allow-list.
    sub = projected["assembly_manifest"]["subtitles"][0]
    assert sub["text"] == "i can feel the"
    assert sub["emphasis_words"] == ["feel"]
    assert projected["assembly_manifest"]["tracks"]["v1"]
    assert projected["rendered_output"]["output_path"] == "/nowhere/out.mp4"


def test_validate_prompt_carries_no_manifest_at_all():
    """#1282 dropped `assembly_manifest` whole from 6.02's prompt: the
    transition seating and V1 tiling the model used to re-derive are
    held by the bridge now, so the prompt carries the render record and
    the measurements, not the plan. No word timing reaches it because no
    manifest does."""
    inputs = {
        "assembly_manifest": {
            "subtitles": [
                {"id": "sub_001", "text": "i can feel the",
                 "words": [{"word": "i", "start": 0.0, "end": 0.036}]},
            ],
        },
        "rendered_output": {"output_path": "/nowhere/out.mp4"},
    }
    projected = project_fields(
        inputs, manifest(QA_STEPS["validate"])["context_fields"])
    assert "assembly_manifest" not in projected
    assert projected["rendered_output"]["output_path"] == "/nowhere/out.mp4"


def test_projecting_an_already_projected_tree_keeps_every_view():
    """An `llm_only` step is projected TWICE on every run.

    `gather_step_inputs` projects it and `present_llm_step` projects the
    result again.  The second pass sees a tree the first one already took
    `speech_regions` out of, so a view that rebuilt itself from scratch
    would delete the section it had just built - which is exactly what
    happened the first time this was wired.
    """
    for step_dir in VIEW_STEPS.values():
        cf = manifest(step_dir)["context_fields"]
        once = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
        twice = project_fields(once, cf)
        assert once["transcript"], step_dir
        # `.get`: a view whose source this fixture does not route builds
        # nothing on either pass, the same guarantee as "identical".
        for name in sorted(CONTEXT_VIEWS):
            assert twice.get(name) == once.get(name), (step_dir, name)


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
            "body_sequence": [{
                "clip_id": "clip_006", "text": CONTRACTION,
                "source_start": 14.68, "source_end": 16.085,
            }],
        },
    }

    proc = subprocess.run(
        [sys.executable,
         str(STEPS / "step_2_02_speech_sequence" / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    hook = json.loads(proc.stdout)["speech_sequence"]["body_sequence"][0]

    words = [w["word"] for w in hook["word_timestamps"]]
    assert words == ["okay,", "we're", "here,", "we're", "here."], (
        f"the post-bridge lost the word timings the prompt no longer "
        f"carries: {hook.get('word_timestamps')}"
    )
    assert hook["word_timestamps"][0]["source_start"] == 14.68
