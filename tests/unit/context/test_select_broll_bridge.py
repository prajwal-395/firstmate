"""The B-roll candidate table must describe the footage.

It used to be 12 alphabetically-ordered rows per slot, each carrying 180
characters of a single-frame caption, repeated for every slot.  The model
had nothing to choose on and picked straight down the list.  It also
returned zero rows - and failed the step - for any project analysed by
the current vision pipeline, because no v3 document yielded a
description.
"""

import json
import os
import subprocess
import sys

import pytest

from library.steps.step_3_02_select_broll.post_bridge import (
    find_best_segment,
    resolve_broll,
)
from tests.unit.context.test_vision_schema_adapter import V3_PROFILE

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
BRIDGE = os.path.join(
    REPO_ROOT, "library", "steps", "step_3_02_select_broll", "bridge.py")

CATALOG = [
    {"clip_id": "clip_009", "path": "/footage/IMG_1814.MOV",
     "duration_seconds": 45.943},
    {"clip_id": "clip_001", "path": "/footage/IMG_1806.MOV",
     "duration_seconds": 3.567},
]

A_ROLL = [
    {"segment_id": "block_1", "spine_block_position": 1,
     "video_segments": [{"clip_id": "clip_009"}]},
    {"segment_id": "block_2", "spine_block_position": 2,
     "video_segments": [{"clip_id": "clip_009"}]},
]


def _second_clip_doc():
    """A second v3 document, so the catalog has two described clips."""
    doc = json.loads(json.dumps(V3_PROFILE))
    doc["clip_id"] = "IMG_1806"
    doc["file_path"] = "/footage/IMG_1806.MOV"
    doc["camera"] = [{"start": 0, "end": 3.567, "mode": "handheld",
                      "framing": "wide", "stability": "stable",
                      "movement": "stationary"}]
    doc["assessment"] = dict(V3_PROFILE["assessment"],
                             content_type="scenery",
                             camera_stability="stable",
                             usable_ranges=[[0, 3.567]])
    return doc


def run_bridge(payload: dict, project_folder=None):
    """Invoke the bridge the way the orchestrator does: JSON on stdin.

    `project_folder` is broadcast by the runner on every run and the
    bridge now writes the vision analysis into it (see
    `library/tools/footage_reference.py`), so a test that omits it is
    testing a run that cannot happen. It is always a `tmp_path`: no test
    reaches a real project (AGENTS.md 8).
    """
    payload = dict(payload)
    if project_folder is not None:
        payload["project_folder"] = str(project_folder)
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, BRIDGE],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return proc


def parse_table(toon: str):
    """Split a TOON table into (header_fields, [row dicts])."""
    lines = [line for line in toon.splitlines() if line.strip()]
    header = lines[0]
    fields = header[header.index("{") + 1:header.index("}")].split(",")
    rows = [dict(zip(fields, line.split("\t"))) for line in lines[1:]]
    return fields, rows


def test_v3_documents_produce_a_candidate_table(tmp_path):
    """This exact input used to exit 1 with "no usable description"; and
    it is one row per clip - two slots used to mean two copies."""
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE, _second_clip_doc()],
    }, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["broll_candidates_toon"])
    assert {r["clip_id"] for r in rows} == {"clip_001", "clip_009"}
    assert len(rows) == len(CATALOG)


def test_no_describable_clip_still_fails_the_step(tmp_path):
    """An empty table means the model can only produce filler."""
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [],
    }, tmp_path)
    assert proc.returncode == 1
    assert "usable semantic description" in json.loads(proc.stdout)["error"]


# ── The post-bridge must seek to the moment it matched, not to a guess ──

# Blocks as the v3 adapter derives them: each action window carries the
# time it was actually observed at.
V3_BLOCKS = [
    {"label": "kitchen", "visual": "pouring coffee", "start": 30.0, "end": 34.0},
    {"label": "kitchen", "visual": "slicing bread", "start": 8.0, "end": 12.0},
]



def test_scene_segment_is_scored_against_the_block_covering_it():
    """Strategy 1 knows each segment's real bounds - it must use them.

    The clip cuts at 20s.  The moment described sits at 8s, inside the
    first segment; mapping segment index onto block index by proportion
    would score it against the second block and pick the wrong scene.
    """
    temporal = {"scene_boundaries": [{"time": 0.0}, {"time": 20.0}]}
    video_in, _ = find_best_segment(
        "slicing bread", {"blocks": V3_BLOCKS}, temporal, 40.0, 3.0)
    assert video_in < 20.0


# ── resolve_broll: the post-bridge's own half ─────────────────────────
#
# `tests/test_bridges.py` claimed to cover this by importing a
# `post_bridge` function that has never existed, so the assertions below
# are the first this half has had.  They are the invariants the manifest
# validator and `_assert_timeline_fully_covered` go on to enforce, caught
# where they are decided instead of where they blow up.

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_009",
     "timeline_start": 0.0, "timeline_end": 4.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_009",
     "timeline_start": 4.0, "timeline_end": 8.0},
]}

RESOLVE_CATALOG = [
    dict(CATALOG[0], width=1920, height=1080, rotation=0),
    dict(CATALOG[1], width=1080, height=1920, rotation=0),
]

RESOLVE_DOCS = [V3_PROFILE, _second_clip_doc()]


def _resolve(creative, interjections=(), spine=None, catalog=None):
    # The delivery frame, stated by the caller: vertical, the same
    # numbers the removed default carried. Production resolves it via
    # `resolve_delivery_format`; tests state it.
    return resolve_broll(
        list(creative), list(interjections),
        catalog if catalog is not None else RESOLVE_CATALOG,
        RESOLVE_DOCS, [], spine if spine is not None else SPINE,
        target_resolution=(1080, 1920),
    )


def test_broll_audio_is_never_linked():
    """A cutaway that carries its own sound talks over the narration."""
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 1}])
    assert out["b_roll_assignments"][0]["video_only"] is True


def test_a_short_cutaway_over_speech_is_shortened_and_declared():
    """A-roll plays underneath a speech block, so returning to it early
    is safe - but the plan must SAY so. The shortening used to be
    announced on stderr only, which nothing downstream can read, so
    compile_manifest's undeclared-black check was the first thing that
    noticed. `coverage_shortfall_seconds` carries it in the plan."""
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 1}])
    entry = out["b_roll_assignments"][0]
    played = round(entry["video_out"] - entry["video_in"], 3)
    claimed = round(entry["timeline_end"] - entry["timeline_start"], 3)
    # clip_001 is 3.567s of source: claiming more leaves V2 a hole.
    assert claimed <= played + 0.001, (
        f"claimed {claimed}s of timeline from {played}s of source")
    assert claimed < 4.0
    assert entry["coverage_shortfall_seconds"] > 0
    assert entry["coverage_shortfall_seconds"] == pytest.approx(
        round(4.0 - claimed, 3), abs=0.002)


@pytest.mark.parametrize("block_type", ["transition_slot"])
def test_a_short_cutaway_with_nothing_underneath_refuses(block_type):
    """The line is V1 membership, not a duration threshold: a
    transition_slot, intro or outro block puts no clip on V1, so a
    cutaway that cannot fill the slot would leave black no A-roll
    fills. That REFUSES here - naming the clip and the shortfall, so
    the post-bridge rejection reaches the model that chose it -
    instead of failing a whole stage later at compile_manifest with
    the evidence about WHY gone."""
    spine = {"structure": [{"position": 1, "block_type": block_type,
                             "clip_id": None,
                             "timeline_start": 0.0, "timeline_end": 4.0}]}
    with pytest.raises(ValueError, match="clip_001"):
        _resolve([{"clip_id": "clip_001", "spine_block_position": 1}],
                 spine=spine)


def test_broll_matching_its_own_aroll_is_skipped_not_substituted(capsys):
    """Cutting to the clip already on screen reads as a glitch, not a cut.

    Which picture replaces it is a creative outcome: settling it by
    catalogue order decided what the viewer sees by alphabet (AGENTS.md
    10.5). Nothing is substituted for a clip nothing chose - the
    selection is dropped with the reason recorded, and the A-roll
    picture plays.
    """
    out = _resolve([{"clip_id": "clip_009", "spine_block_position": 1}])
    assert out["b_roll_assignments"] == []
    err = capsys.readouterr().err
    assert "same clip as its A-roll" in err
    assert "no alternative clip is substituted" in err


def test_only_one_cutaway_reaches_a_block():
    """Two selections on one block claim the same stretch of V2."""
    out = _resolve([
        {"clip_id": "clip_001", "spine_block_position": 1},
        {"clip_id": "clip_001", "spine_block_position": 1},
    ])
    assert len(out["b_roll_assignments"]) == 1


def test_an_uncovered_music_block_is_refused_by_position():
    payload = {
        "clip_catalog": [{
            "clip_id": "clip_001", "source_file": "/tmp/a.mov",
            "duration_seconds": 30.0, "width": 1080, "height": 1920,
        }],
        "semantic_analysis_documents": [],
        "temporal_event_indices": [],
        "timed_spine": {"structure": [{
            "position": 1, "block_type": "music", "clip_id": None,
            "timeline_start": 0.0, "timeline_end": 4.0,
        }]},
        "broll_creative": [],
        "b_roll_interjections": [],
    }
    script = os.path.join(
        REPO_ROOT, "library", "steps", "step_3_02_select_broll",
        "post_bridge.py",
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, script], input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", cwd=REPO_ROOT,
        env=env,
    )
    assert proc.returncode != 0, "an uncovered music block passed the post-bridge"
    assert "1" in (proc.stdout + proc.stderr), "the refusal names no block position"


def test_a_needs_conform_from_the_model_is_refused():
    """The handoff tells the model not to answer `needs_conform` - the
    bridge recomputes it from the catalog on every assignment. A model
    that answers it anyway is refused, naming the key and the known
    keys, so it re-plans instead of its wrong value being silently
    overwritten (or worse, carried)."""
    from library.tools.plan_keys import UnreadPlanKey
    with pytest.raises(UnreadPlanKey, match="needs_conform"):
        _resolve([{"clip_id": "clip_009", "spine_block_position": 1,
                   "needs_conform": False}],
                 spine={"structure": [dict(SPINE["structure"][0],
                                           clip_id="clip_001")]})


def test_an_interjection_is_trimmed_around_broll_already_on_v2():
    """Two clips cannot share frames of a track.

    The interjection asks for 0-4s, which the block-1 assignment already
    holds; it must be trimmed into what is left rather than displacing it.
    """
    out = _resolve(
        [{"clip_id": "clip_001", "spine_block_position": 1}],
        [{"clip_id": "clip_001", "over_spine_block_position": 2,
          "timeline_start": 0.0, "timeline_end": 6.0}],
    )
    assignment = out["b_roll_assignments"][0]
    interjection = out["b_roll_interjections"][0]
    assert interjection["timeline_start"] >= assignment["timeline_end"]
    assert interjection["timeline_end"] <= 6.0
