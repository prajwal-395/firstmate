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
from tests.unit.context.test_vision_pipeline import V3_PROFILE
from pathlib import Path


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


# --------------------------------------------------------------------------
# From test_speech_sequence_bridge.py
#
# Step 2.02's pre-bridge builds the transcript the prompt reads.
#
# This is the file that replaces four tests in `tests/test_bridges.py`
# which imported a `pre_bridge` function that has never existed.  The
# bridge is a SUBPROCESS - `run_pipeline.run_subprocess` hands it JSON on
# stdin and reads JSON off stdout - so that is what these drive, the same
# way `tests/unit/context/test_bridges.py` drives 3.02's.
#
# What is asserted here is what the step promises and what has gone wrong
# before:
#
# * the transcript comes off the per-clip index FILES, not off the routed
#   `temporal_index` value, because the manifest projection never carried
#   the per-word records (AGENTS.md 10.1, `view:transcript`);
# * the columns are `clip_id,start,end,text` in that order - alphabetising
#   them put `end` before `start` in two prompts;
# * the rows are sorted, because `os.listdir` order is the filesystem's and
#   the same project would otherwise present its clips differently on every
#   run;
# * the output is CONTEXT ONLY.  It used to also emit a `speech_sequence`
#   built from every transcript region, which is the raw transcript wearing
#   the output's name, and in `--auto` that became the step's answer.

REPO_ROOT_2 = Path(__file__).resolve().parents[3]
if str(REPO_ROOT_2) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT_2))

from library.tools.project_layout import Area, ProjectLayout  # noqa: E402

BRIDGE_2 = REPO_ROOT_2 / "library" / "steps" / "step_2_02_speech_sequence" / "bridge.py"

# Two clips, written to disk in the order that would come out WRONG if
# the bridge trusted `os.listdir`: clip_002 sorts after clip_001, and
# within clip_001 the later region is written first.
CLIP_INDICES = {
    "clip_002": {
        "speech_regions": [
            {"start": 0.5, "end": 2.25, "text": "and that is the whole trick"},
        ],
    },
    "clip_001": {
        "speech_regions": [
            {"start": 12.0, "end": 14.5, "text": "second thing I said"},
            {"start": 1.0, "end": 3.5, "text": "first thing\nI said"},
        ],
    },
}

SEMANTIC_DOCS = [
    {"clip_id": "clip_001", "assessment": {"keywords": ["coffee", "kitchen"]}},
    {"clip_id": "clip_002", "assessment": {"keywords": ["street"]}},
]


def _project(tmp_path: Path, *, record_index_dir: bool = True) -> Path:
    """A project carrying per-clip temporal-index files, and nothing else.

    The area is NAMED, never composed: a test builds a project path no
    more freely than a step does (AGENTS.md 8).
    """
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(project)
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX, step="temporal_index")
    for clip_id, index in CLIP_INDICES.items():
        (index_dir / f"{clip_id}.json").write_text(
            json.dumps(index), encoding="utf-8")

    state = {"step_outputs": {"temporal_index": {}}}
    if record_index_dir:
        state["step_outputs"]["temporal_index"]["index_dir"] = str(index_dir)
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")
    return project


def run_bridge_2(payload: dict) -> subprocess.CompletedProcess:
    """Invoke the bridge the way the orchestrator does: JSON on stdin."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT_2) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(BRIDGE_2)],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO_ROOT_2), env=env,
    )


def _payload(project: Path) -> dict:
    return {
        "project_folder": str(project),
        # Routed but deliberately hollow: the real transcript is on disk.
        "temporal_index": {"temporal_event_indices": []},
        "semantic_analysis_documents": SEMANTIC_DOCS,
    }


# ── The transcript table ──────────────────────────────────────────────

def test_the_transcript_table_is_read_off_the_index_files_ordered_and_flat(
        tmp_path):
    """Off the per-clip index FILES (the routed value is hollow); columns
    `clip_id,start,end,text` in that order (alphabetising put `end` before
    `start`); rows sorted by clip then start (`os.listdir` order is the
    filesystem's); and a newline in a region does not break its row."""
    proc = run_bridge_2(_payload(_project(tmp_path)))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    table = json.loads(proc.stdout)["transcripts_toon"]
    fields, rows = parse_table(table)
    assert fields == ["clip_id", "start", "end", "text"]
    assert [(r["clip_id"], r["start"]) for r in rows] == [
        ("clip_001", "1.00"), ("clip_001", "12.00"), ("clip_002", "0.50"),
    ]
    assert rows[0]["text"] == "first thing I said"
    # One header line plus one line per region, and no more.
    assert len([line for line in table.splitlines() if line.strip()]) == 4


def test_the_index_dir_is_found_without_a_recorded_index_dir(tmp_path):
    """A run predating the recorded `index_dir` still has the files."""
    project = _project(tmp_path, record_index_dir=False)
    proc = run_bridge_2(_payload(project))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["transcripts_toon"])
    assert len(rows) == 3


# ── The topics table ──────────────────────────────────────────────────

def test_the_bridge_emits_only_its_two_context_tables(tmp_path):
    """Topics come from the assessment keywords, and the output is CONTEXT
    ONLY: a `speech_sequence` built from every region was the raw
    transcript wearing the output's name, and `--auto` took it as the
    step's answer."""
    proc = run_bridge_2(_payload(_project(tmp_path)))
    _, rows = parse_table(json.loads(proc.stdout)["topics_toon"])
    assert {r["clip_id"]: r["topics"] for r in rows} == {
        "clip_001": "coffee, kitchen",
        "clip_002": "street",
    }

    assert set(json.loads(proc.stdout)) == {"transcripts_toon", "topics_toon"}


# ── No speech in the footage ────────────────────────────────────────
#
# A valid index with zero speech regions is the footage saying nothing
# (music, montage, no dialogue), and the bridge answers it
# deterministically with an empty body - the run proceeds speechless
# with no model call and no human. A missing or unreadable index is a
# defect upstream and still refuses.

def _empty_index_project(tmp_path: Path) -> Path:
    """A project whose temporal index exists but says nothing."""
    project = tmp_path / "empty"
    project.mkdir()
    layout = ProjectLayout(project)
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX, step="temporal_index")
    (index_dir / "clip_001.json").write_text(
        json.dumps({"speech_regions": []}), encoding="utf-8")
    layout.pipeline_data_path.write_text(json.dumps(
        {"step_outputs": {"temporal_index": {"index_dir": str(index_dir)}}}),
        encoding="utf-8")
    return project


def test_zero_speech_regions_yield_an_empty_sequence_not_a_refusal(
        tmp_path):
    """Speechless footage gets an empty body the run plans around."""
    proc = run_bridge_2(_payload(_empty_index_project(tmp_path)))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert out["speech_sequence"]["body_sequence"] == []
    assert out["speech_sequence"]["excluded_passages"]
    _, rows = parse_table(out["transcripts_toon"])
    assert rows == []


def test_a_missing_index_still_refuses(tmp_path):
    """No index on disk is a broken run, not a quiet one."""
    project = tmp_path / "missing"
    project.mkdir()
    proc = run_bridge_2({
        "project_folder": str(project),
        "temporal_index": {},
        "semantic_analysis_documents": SEMANTIC_DOCS,
    })
    assert proc.returncode == 1
    assert "nothing to build a speech sequence from" in (
        json.loads(proc.stdout)["error"])


# --------------------------------------------------------------------------
# From test_vfx_bridge.py
#
# The VFX bridge originates no creative value.
#
# The bridge used to unconditionally inject `{"effect_type": "color_wash",
# "intensity": 0.5}` on the first segment of every project as
# `enhancement_spec`, and to hardcode `vfx_suggested` to the literal
# string "No" on every row. Both are a bridge voting on a creative
# decision nobody made.
#
# These tests drive the real bridge with representative input and assert:
#
#   * `enhancement_spec` is not emitted at all (the post-bridge writes it).
#   * No effect_type, intensity or creative value appears in the output.
#   * `vfx_suggested` carries a measurement, not a hardcoded verdict.
#   * `text` is populated from the spine's content, not empty.
#   * The table has one row per spine block, keyed by position.

REPO = Path(__file__).resolve().parents[3]
VFX = REPO / "library" / "steps" / "step_4_03_plan_vfx"


def _run_bridge(payload: dict):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(VFX / "bridge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )


def _spine(n_blocks=3) -> dict:
    return {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": f"clip_{i + 1:03d}",
                "source_start": i * 5.0,
                "source_end": i * 5.0 + 5.0,
                "timeline_start": i * 5.0,
                "timeline_end": i * 5.0 + 5.0,
                "content": {"text": f"This is sentence number {i + 1}"},
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(n_blocks)
        ]
    }


def _semantic_docs():
    """Per-clip profiles keyed by file stem, with camera segments."""
    return [
        {
            "clip_id": "IMG_1806",
            "file_path": "/raw/IMG_1806.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 15.0,
                    "mode": "selfie",
                    "framing": "close-up",
                    "stability": "steady",
                    "movement": "stationary",
                }
            ],
            "assessment": {
                "camera_stability": "stable",
                "content_type": "person_talking_to_camera",
            },
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 15, "location": "studio"}],
        },
        {
            "clip_id": "IMG_1807",
            "file_path": "/raw/IMG_1807.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "mode": "handheld",
                    "framing": "medium",
                    "stability": "shaky",
                    "movement": "panning_left",
                }
            ],
            "assessment": {
                "camera_stability": "handheld",
                "content_type": "b_roll",
            },
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 10, "location": "outdoor"}],
        },
        {
            "clip_id": "IMG_1808",
            "file_path": "/raw/IMG_1808.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "mode": "selfie",
                    "framing": "close-up",
                    "stability": "steady",
                    "movement": "stationary",
                }
            ],
            "assessment": {"camera_stability": "stable"},
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 10, "location": "studio"}],
        },
    ]


def _aroll():
    """a_roll_assignments carrying clip_id + source_file for the join."""
    return [
        {
            "spine_block_position": 1,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_001", "source_file": "/raw/IMG_1806.MOV"}
            ],
        },
        {
            "spine_block_position": 2,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_002", "source_file": "/raw/IMG_1807.MOV"}
            ],
        },
        {
            "spine_block_position": 3,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_003", "source_file": "/raw/IMG_1808.MOV"}
            ],
        },
    ]


def _payload_2() -> dict:
    return {
        "timed_spine": _spine(),
        "semantic_analysis_documents": _semantic_docs(),
        "a_roll_assignments": _aroll(),
        "b_roll_assignments": [],
        "creative_direction": {},
        "rough_cut_review": {"passed": True},
    }


class TestVfxBridgeOriginatesNoCreativeValue:
    """Regression: the colour wash cannot come back."""

    def test_no_enhancement_spec_and_no_effect_value_in_output(self):
        """The post-bridge writes `enhancement_spec` after the model
        answers; the pre-bridge names no effect, intensity or wash."""
        proc = _run_bridge(_payload_2())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        assert "enhancement_spec" not in output
        stdout_text = proc.stdout.lower()
        assert "color_wash" not in stdout_text
        assert "effect_type" not in stdout_text
        assert "\"intensity\": 0.5" not in proc.stdout

    def test_text_column_is_populated(self):
        proc = _run_bridge(_payload_2())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        lines = [l for l in toon.strip().split("\n") if l and not l.startswith("[")]
        assert len(lines) > 0
        populated = 0
        for line in lines:
            fields = line.split("\t")
            assert len(fields) >= 2
            text = fields[1]
            if text.strip():
                populated += 1
        assert populated == len(lines), (
            f"Only {populated} of {len(lines)} rows have text"
        )


    def test_vfx_suggested_carries_camera_data(self):
        proc = _run_bridge(_payload_2())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        lines = [l for l in toon.strip().split("\n") if l and not l.startswith("[")]
        # Never the old hardcoded verdict, on any row.
        assert all(line.split("\t")[2] != "No" for line in lines), lines
        # First block: stationary + steady camera on clip_001
        fields = lines[0].split("\t")
        vfx_suggested = fields[2]
        assert "stationary" in vfx_suggested or "steady" in vfx_suggested, (
            f"First block should show stationary/steady camera: {vfx_suggested}"
        )
        # Second block: panning_left + shaky camera on clip_002
        fields = lines[1].split("\t")
        vfx_suggested = fields[2]
        assert "panning_left" in vfx_suggested or "shaky" in vfx_suggested, (
            f"Second block should show panning/shaky camera: {vfx_suggested}"
        )


class TestVfxBridgeNoSourceClip:
    """Non-speech blocks without a source clip report 'not measured'."""

    def test_non_speech_block_reports_not_measured(self):
        spine = {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "clip_id": None,
                    "source_start": None,
                    "source_end": None,
                    "timeline_start": 0.0,
                    "timeline_end": 2.0,
                    "word_timestamps": [],
                    "alignment_method": None,
                },
            ]
        }
        payload = {
            "timed_spine": spine,
            "semantic_analysis_documents": [],
            "a_roll_assignments": [],
        }
        proc = _run_bridge(payload)
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        assert "not measured" in toon


class TestVfxBridgeEmptyInput:
    """Empty spine produces an empty table, not a crash."""

    def test_empty_spine(self):
        payload = {
            "timed_spine": {"structure": []},
            "semantic_analysis_documents": [],
            "a_roll_assignments": [],
        }
        proc = _run_bridge(payload)
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        assert toon.startswith("[0]")
        assert "enhancement_spec" not in output
