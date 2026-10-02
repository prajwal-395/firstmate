"""A cutaway plays muted, so its audio may not choose which seconds play.

`step_3_02_select_broll`'s post-bridge chose the window from
``temporal_index.energy_curve`` - per-second RMS of the clip's own audio -
on clips `compile_manifest` places ``video_only: True``.  On project 001's
run of record that decided **7 of 7 windows**, 31.0% of the finished
picture, five of them centred within 0.000 s of the nearest audio peak.

These tests hold the replacement to three things: the audio is
structurally out of reach, the picture really decides, and the candidate
rows stay in a shape a model could be handed (route 2).
"""
import ast
import os
from library.tools.cutaway_window import (
    AUDIO_SIGNALS,
    BASES,
    candidate_windows,
    choose_window,
)
import importlib.util
from pathlib import Path
from library.tools.broll_coverage import (
    VIDEO_ONLY_AUDIO_READING,
    coverage_by_block,
    covering_assignment,
    picture_clip_id,
)
import sys
import pytest
import json
import subprocess


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WINDOW_SOURCES = (
    os.path.join(REPO_ROOT, "library", "tools", "cutaway_window.py"),
    os.path.join(REPO_ROOT, "library", "steps", "step_3_02_select_broll",
                 "post_bridge.py"),
)


# ── Fixtures: one clip, described, with a loud muted soundtrack ───────

# The vision pass observed two different things in this clip. The moment
# the model asked for is in the SECOND window.
BLOCKS = [
    {"label": "parking lot", "visual": "wide view of parked cars",
     "start": 0.0, "end": 10.0},
    {"label": "brick wall", "visual": "close detail of a brick wall and "
     "a window with a metal frame", "start": 10.0, "end": 20.0},
]

# A soundtrack that screams at 3s and is silent everywhere else. Nothing
# may read it: the clip is placed video_only.
LOUD_AT_THREE = {
    "sample_rate_hz": 1,
    "values": [0.0, 0.0, 0.0, 1.0] + [0.0] * 16,
    "peak_times": [3.0],
}

INDEX = {
    "clip_id": "clip_001",
    "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
    "energy_curve": LOUD_AT_THREE,
    "audio_events": [{"time": 3.0}],
    "onset_times": [3.0],
    "speech_regions": [{"start": 2.5, "end": 3.5}],
    "motion_energy": {"sample_rate_hz": 1, "peak_mean_abs_diff": 0.2,
                      "values": [0.1] * 20},
    "optical_flow_direction": {"sample_rate_hz": 1, "dominant_motion": "static",
                               "values": [{"dx": 0.0, "dy": 0.0,
                                           "magnitude": 0.1}] * 20},
    "face_presence": {"sample_rate_hz": 1, "values": [0.0] * 20,
                      "face_center_x": [None] * 20},
    "color_curves": {"sample_rate_hz": 1, "brightness_values": [120] * 20,
                     "saturation_values": [60] * 20, "hue_values": [30] * 20},
}


def _analysis(blocks=None, assessment=None):
    doc = {"blocks": list(BLOCKS if blocks is None else blocks)}
    if assessment is not None:
        doc["assessment"] = assessment
    return doc


# ── The audio cannot reach the decision ───────────────────────────────

def _keys_read_from_state(path):
    """Every string this module indexes or `.get()`s out of a mapping.

    Reads the code, not the prose: the enumeration below and the step's
    own input docstring both NAME the audio keys, and a source scan that
    could not tell a name from an access would have to be written so
    loosely it stopped catching anything.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice,
                                                          ast.Constant):
            if isinstance(node.slice.value, str):
                found.add(node.slice.value)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


def test_no_window_code_reads_an_audio_signal():
    """The regression that shipped 001: strategy 2 read `energy_curve`."""
    for path in WINDOW_SOURCES:
        read = _keys_read_from_state(path)
        offending = sorted(read & set(AUDIO_SIGNALS))
        assert not offending, (
            f"{os.path.basename(path)} reads {offending} to decide a cutaway "
            f"window; a cutaway is placed video_only and that audio is never "
            f"heard. Reasons: "
            + "; ".join(f"{k}: {AUDIO_SIGNALS[k]}" for k in offending)
        )


def test_a_muted_clips_audio_peak_does_not_select_the_window():
    """The behavioural half of the same rule.

    The soundtrack peaks at 3.0 s and the model asked for the brick wall,
    which the vision pass observed at 10.0-20.0 s.  The old chooser
    centred a 2 s window on 3.0 s; the picture puts it at 10.0 s.
    """
    choice = choose_window(
        "close detail of a brick wall", _analysis(), INDEX, 20.0, 2.0)
    assert choice.video_in == 10.0
    assert choice.video_out == 12.0
    assert choice.basis == "moment_match"


def test_moving_the_audio_peak_does_not_move_the_window():
    """Strongest form: the answer is invariant under the whole curve."""
    quiet = dict(INDEX, energy_curve={"sample_rate_hz": 1,
                                      "values": [0.0] * 20, "peak_times": []},
                 audio_events=[], onset_times=[], speech_regions=[])
    loud_late = dict(INDEX, energy_curve={
        "sample_rate_hz": 1, "values": [0.0] * 17 + [1.0, 1.0, 1.0],
        "peak_times": [18.0]})
    baseline = choose_window("brick wall", _analysis(), INDEX, 20.0, 2.0)
    for variant in (quiet, loud_late):
        assert choose_window(
            "brick wall", _analysis(), variant, 20.0, 2.0
        ).as_tuple() == baseline.as_tuple()


# ── The picture decides, and says which part of it did ────────────────


def test_one_span_is_recorded_as_no_choice_at_all():
    """`moment_match` on a clip offering one span reads as a decision.

    Four of project 001's seven cutaways are this case, so the difference
    is not hypothetical.
    """
    one_block = [{"label": "parking lot", "visual": "wide view of parked "
                  "cars", "start": 0.0, "end": 20.0}]
    choice = choose_window(
        "wide view of parked cars", _analysis(one_block), INDEX, 20.0, 2.0)
    assert choice.basis == "single_span"
    assert choice.video_in == 0.0


def test_nothing_matching_is_undiscriminated_not_a_preference():
    """No motion, brightness or face rule breaks the tie - §10.5."""
    choice = choose_window(
        "zzz nothing here", _analysis(), INDEX, 20.0, 2.0)
    assert choice.basis == "undiscriminated"
    assert choice.video_in == 0.0
    assert "none matching" in choice.basis_detail


# ── Where the candidate spans come from ───────────────────────────────

def test_block_bounds_are_span_points_not_only_scene_boundaries():
    """The 2-boundary requirement is what let the audio strategy run.

    `INDEX` carries ONE scene boundary, which is what 15 of project 001's
    17 clips carry.  The vision pass's own action windows supply the rest.
    """
    rows = candidate_windows("", _analysis(), INDEX, 20.0, 2.0)
    assert [(r["span_start"], r["span_end"]) for r in rows] == [
        (0.0, 10.0), (10.0, 20.0)]


def test_span_points_closer_than_the_index_can_resolve_are_one_point():
    """001's documents end a block 3 ms before the catalog ends the clip.

    The finest curve in `INDEX` samples at 1 Hz, so 19.999 and 20.0 are
    the same moment and must not become a candidate span describing
    nothing.
    """
    ragged = [{"label": "x", "visual": "y", "start": 0.0, "end": 19.999}]
    rows = candidate_windows("", _analysis(ragged), INDEX, 20.0, 2.0)
    assert len(rows) == 1


# ── usable_ranges: the method decides, not the ranges ─────────────────

# ── The rows stay usable by a model (route 2 must stay open) ──────────


def test_the_post_bridge_records_what_chose_the_window():
    from library.steps.step_3_02_select_broll.post_bridge import resolve_broll

    catalog = [
        {"clip_id": "clip_001", "path": "/f/a.MOV", "duration_seconds": 20.0,
         "width": 1080, "height": 1920, "rotation": 0},
        {"clip_id": "clip_002", "path": "/f/b.MOV", "duration_seconds": 20.0,
         "width": 1080, "height": 1920, "rotation": 0},
    ]
    spine = {"structure": [
        {"position": 1, "block_type": "transition_slot", "clip_id": "clip_002",
         "timeline_start": 0.0, "timeline_end": 2.0},
    ]}
    out = resolve_broll(
        [{"clip_id": "clip_001", "spine_block_position": 1,
          "preferred_moment": "close detail of a brick wall"}],
        [], catalog, [], [dict(INDEX, clip_id="clip_001")], spine,
        target_resolution=(1080, 1920),
    )
    # No semantic document, so nothing describes the clip: the honest
    # answer is that nothing discriminated the window.
    assignment = out["b_roll_assignments"][0]
    assert assignment["window_basis"] in BASES
    assert assignment["window_basis_detail"]


# --------------------------------------------------------------------------
# From test_broll_coverage_reaches_the_tables.py
#
# The B-roll rows of two candidate tables said nothing was measurable.
#
# A transition_slot's covering cutaway is measured in 4.03's table and admitted
# as unheard (video_only) in 4.04's - never `not measured (no source clip)`.
# History: `docs/evidence/broll_coverage.md`.

ROOT = Path(__file__).resolve().parents[3]


def _bridge(step: str):
    spec = importlib.util.spec_from_file_location(
        f"{step}_bridge", ROOT / "library" / "steps" / step / "bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# 001's own shapes, trimmed.
SPINE = {"structure": [
    {"block_type": "hook", "position": "hook", "clip_id": "clip_011",
     "source_start": 0.836, "source_end": 3.234,
     "timeline_start": 0.0, "timeline_end": 2.398,
     "content": {"text": "i can feel the silent judgment"}},
    {"block_type": "transition_slot", "position": 1, "clip_id": None,
     "source_start": None, "source_end": None,
     "timeline_start": 2.398, "timeline_end": 5.398,
     "visual_note": "B-roll: the parking lot"},
]}

BROLL = [{
    "spine_block_position": 1, "block_type": "transition_slot",
    "clip_id": "clip_001", "source_file": "/raw/IMG_1806.MOV",
    "video_in": 0.0, "video_out": 3.0, "duration_seconds": 3.0,
    "timeline_start": 2.398, "timeline_end": 5.398,
}]

CATALOG = [
    {"clip_id": "clip_011", "path": "/raw/IMG_1816.MOV"},
    {"clip_id": "clip_001", "path": "/raw/IMG_1806.MOV"},
]

DOCS = [
    {"clip_id": "IMG_1816_v3", "file_path": "/raw/IMG_1816.MOV",
     "camera": [{"start": 0, "end": 90, "movement": "tilting_up",
                 "stability": "shaky"}],
     "assessment": {}},
    {"clip_id": "IMG_1806_v3", "file_path": "/raw/IMG_1806.MOV",
     "camera": [{"start": 0, "end": 4, "movement": "stationary",
                 "stability": "stable"}],
     "assessment": {}},
]


def test_coverage_by_block_keys_on_the_identifier_the_answer_names():
    coverage = coverage_by_block(BROLL)
    assert set(coverage) == {1}
    assert covering_assignment(SPINE["structure"][1], coverage)["clip_id"] \
        == "clip_001"
    assert picture_clip_id(SPINE["structure"][1], coverage) == "clip_001"
    # A speech block plays its own clip and is unaffected.
    assert picture_clip_id(SPINE["structure"][0], coverage) == "clip_011"


def test_the_vfx_table_measures_the_cutaway_it_will_show():
    rows = _bridge("step_4_03_plan_vfx").build_vfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "clip_catalog": CATALOG, "semantic_analysis_documents": DOCS,
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert "not measured" not in broll_row["vfx_suggested"]
    assert "stationary" in broll_row["vfx_suggested"]
    assert "stable" in broll_row["vfx_suggested"]
    assert "B-roll cutaway clip_001" in broll_row["vfx_suggested"]


def test_the_sfx_table_names_the_cutaway_and_counts_no_unheard_transients():
    """The cutaway's own audio never plays, so its transients are not it."""
    rows = _bridge("step_4_04_plan_sfx").build_sfx_candidates({
        "timed_spine": SPINE, "b_roll_assignments": BROLL,
        "temporal_event_indices": [
            {"clip_id": "clip_001",
             "energy_curve": {"peak_times": [0.4, 1.1, 2.2]}}],
    })
    broll_row = next(r for r in rows if r["segment_id"] == 1)
    assert broll_row["action_sfx_suggested"] == (
        f"covered by clip_001, {VIDEO_ONLY_AUDIO_READING}")
    assert "transient" not in broll_row["action_sfx_suggested"]


# --------------------------------------------------------------------------
# From test_broll_slip.py
#
# Rung 7 (K1, CT3.3): a b-roll source slip is a plan value.
#
# "Slip the second b-roll shot 1s later in its source, keep its
# position and duration on the timeline" had no plan spelling: the
# window chooser owned the source range outright. `slip_seconds` (the
# request stating seconds) / `slip_frames` (stating frames, E3) shift
# the chosen window at the same timeline position and duration. Both
# stated must agree; a slip past the file's ends refuses with the
# bounds rather than clamping onto unplayed media.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_3_02_select_broll.post_bridge import (
    resolve_broll,
)

SPINE_2 = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_a",
     "timeline_start": 0.0, "timeline_end": 4.0},
], "frame_rate": 30.0}

CATALOG_2 = [
    {"clip_id": "clip_a", "path": "/footage/a.MOV",
     "duration_seconds": 45.0, "width": 1920, "height": 1080,
     "rotation": 0},
    {"clip_id": "clip_b", "path": "/footage/b.MOV",
     "duration_seconds": 45.0, "width": 1920, "height": 1080,
     "rotation": 0},
]


def _resolve(creative):
    return resolve_broll(
        list(creative), [], CATALOG_2, [], [], SPINE_2,
        target_resolution=(1080, 1920))


def test_a_stated_slip_shifts_source_at_the_same_timeline():
    """CT3.3's shape: 1 s later in source, timeline untouched."""
    plain = _resolve([{"clip_id": "clip_b", "spine_block_position": 1}])
    slipped = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                         "slip_seconds": 1.0}])
    (base,) = plain["b_roll_assignments"]
    (entry,) = slipped["b_roll_assignments"]
    assert entry["video_in"] == pytest.approx(base["video_in"] + 1.0)
    assert entry["video_out"] == pytest.approx(base["video_out"] + 1.0)
    assert entry["timeline_start"] == base["timeline_start"]
    assert entry["timeline_end"] == base["timeline_end"]
    assert entry["slip_seconds"] == pytest.approx(1.0)
    assert base["slip_seconds"] == 0.0


def test_slip_frames_match_slip_seconds_and_disagreement_refuses():
    seconds = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                         "slip_seconds": 1.0}])
    frames = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                        "slip_frames": 30}])
    assert frames["b_roll_assignments"][0]["video_in"] == pytest.approx(
        seconds["b_roll_assignments"][0]["video_in"])
    with pytest.raises(ValueError, match="ambiguous spec"):
        _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                   "slip_seconds": 1.0, "slip_frames": 60}])


# --------------------------------------------------------------------------
# From test_broll_usability_gate.py
#
# The measured usable picture reaches the cutaway choice.
#
# The captain marked 001 at 00:00:42:06: *"why does this part of the clip
# keep getting recommended as broll? like its a zoomed up bit of the dash of
# my car and doesn't show anything actually."*
#
# Every candidate window was its span's HEAD, and `usable_overlap` was
# collapsed to `> 0.0`.  The marked window scored 0.0909 and was selected
# anyway, on a clip that is 83% usable.
#
# The other half of that defect - `usable_ranges` reading empty on B-roll -
# is in `test_usable_ranges.py::TestRule2IsAROllOnly`, beside the rule it
# gates.  Shapes are 001's.  No test reaches a real project.

def _doc(usable, blocks, method="deterministic_v1"):
    return {"assessment": {"usable_ranges": usable,
                           "usable_ranges_method": method},
            "blocks": [{"start": s, "end": e, "visual": v}
                       for s, e, v in blocks]}


def test_window_moves_off_an_unusable_span_head():
    """IMG_1811's shape: 83% usable, and the unusable part is the head."""
    usable = [[2.0, 2.8], [3.6, 7.2], [8.2, 22.87]]
    doc = _doc(usable, [(0.0, 10.0, "a street with a traffic light")])

    choice = choose_window("the street with traffic", doc, {}, 22.87, 2.2)

    assert choice.video_in != 0.0, (
        "the span head is 91% outside the measured usable ranges - this is "
        "the window the captain marked"
    )
    assert any(s <= choice.video_in and choice.video_out <= e
               for s, e in usable)


def test_an_unmeasured_clip_is_not_moved():
    """`measured_usable_ranges` returns None for any method that is not
    `deterministic_v1`, so this path is reachable - and moving a window on a
    measurement that does not exist is the taste fabrication AGENTS.md
    forbids."""
    doc = _doc(None, [(0.0, 10.0, "a street")], method="unmeasured")

    choice = choose_window("a street", doc, {}, 20.0, 2.0)

    assert choice.video_in == 0.0
    assert all(row["placed_by"] == "span_head" for row in choice.candidates)


def test_usability_breaks_a_tie_the_words_cannot():
    """Two spans, one description, different measured usability."""
    doc = _doc([[5.0, 10.0]], [(0.0, 5.0, "a parked car"),
                               (5.0, 10.0, "a parked car")])

    choice = choose_window("a parked car", doc, {}, 10.0, 2.0)

    assert choice.video_in >= 5.0, (
        "where the words cannot discriminate, the span with more measured "
        "usable picture must win over the earliest one"
    )


def test_the_words_still_outrank_usability():
    """`moment_match` is first. Usability only breaks its ties."""
    # The matching span is the LESS usable one. The words still win.
    doc = _doc([[0.0, 1.0], [5.0, 10.0]],
               [(0.0, 5.0, "a red bicycle by a wall"),
                (5.0, 10.0, "an empty pavement")])

    choice = choose_window("a red bicycle", doc, {}, 10.0, 2.0)

    assert choice.video_in < 5.0
    assert choice.basis.startswith("moment_match")


# --------------------------------------------------------------------------
# From test_select_broll_scene_prose_not_structure.py
#
# select_broll keeps the scene prose inline and the scene structure at a path.
#
# Issue #679 / #339 on the ASSEMBLED prompt (real bridge + projection +
# serializer): the prose is inline, no scene structure is, and the structure
# is one followed path away. History: `docs/evidence/select_broll_context.md`.

BROLL_STEP = REPO / "library" / "steps" / "step_3_02_select_broll"

sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    project_step_context,
)
from library.tools.brief_reference import reference_path  # noqa: E402
try:  # Absent before #339 moved the structure to a reference document.
    from library.tools.footage_reference import DOCUMENT_NAME  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - pre-fix trees only
    DOCUMENT_NAME = "footage_analysis.md"
from library.tools.toon_serializer import json_to_toon  # noqa: E402


def _document(n: int) -> dict:
    stem = f"IMG_{1800 + n}"
    return {
        "clip_id": f"{stem}_v3",
        "file_path": f"/footage/{stem}.MOV",
        "duration_s": 45.0,
        "vision_schema_version": "3.0",
        "scene": [{
            "start": 0.0, "end": 18.9,
            "location": f"Outdoor urban area {n} with a parking lot",
            "type": "outdoor", "lighting": "Overcast daylight",
            "notable_features": ["Parked cars", "Concrete sidewalk"],
        }],
        "camera": [{
            "start": 0, "end": 45, "mode": "handheld", "framing": "wide",
            "stability": "stable", "movement": "stationary",
        }],
        "actions": [{
            "window": [w * 9.0, (w + 1) * 9.0],
            "actions": [{
                "start": w * 9.0, "end": (w + 1) * 9.0,
                "action": f"Clip {n} window {w}: the speaker walks past parked cars.",
                "speech_cue": None, "body_language": None,
            }],
        } for w in range(5)],
        "blocks": [{
            "timestamp_range": f"0:{w * 9:02d}-0:{(w + 1) * 9:02d}",
            "start": w * 9.0, "end": (w + 1) * 9.0,
            "label": f"Outdoor urban area {n}",
            "visual": f"Clip {n} window {w}: the speaker walks past parked cars.",
            "body_language": "", "speech_cue": None,
        } for w in range(5)],
        "objects": [{
            "label": f"object {o} in clip {n}",
            "appearances": [[0.0, 44.5]],
            "role": "background", "category": "structure",
            "readable_text": None,
        } for o in range(3)],
        "assessment": {
            "content_type": "scenery",
            "clip_type": "b_roll",
            "keywords": ["outdoor"],
            "interest_score": 0.5,
            "camera_stability": "unknown",
            "usable_ranges": [[0, 45.0]],
            "usable_ranges_method": "unmeasured",
            "primary_subject_visible": [],
        },
    }


CLIPS = 3
DOCUMENTS = [_document(n) for n in range(CLIPS)]
CATALOG_3 = [{"clip_id": f"clip_{n:03d}", "filename": f"IMG_{1800 + n}.MOV",
            "path": f"/footage/IMG_{1800 + n}.MOV",
            "duration_seconds": 45.0, "width": 1920, "height": 1080,
            "rotation": 0, "frame_rate": 30.0}
           for n in range(CLIPS)]


def _routed_inputs(project: Path) -> dict:
    return {
        "project_folder": str(project),
        "clip_catalog": CATALOG_3,
        "a_roll_assignments": [{"segment_id": "block_1",
                                "spine_block_position": 1,
                                "video_segments": [{"clip_id": "clip_000"}]}],
        "semantic_analysis_documents": DOCUMENTS,
        "temporal_event_indices": [{"clip_id": c["clip_id"],
                                    "scene_boundaries": [0.0, 22.5]}
                                   for c in CATALOG_3],
        "timed_spine": {"structure": [
            {"position": p, "block_type": "speech", "clip_id": "clip_000",
             "content": f"Spoken line {p} of the edit.",
             "timeline_start": p * 5.0, "timeline_end": (p + 1) * 5.0,
             "visual_note": "the speaker, mid-sentence"}
            for p in range(3)]},
        "creative_direction": {"target_mood": "reflective",
                               "energy_arc": "building"},
    }


def _assembled_prompt(project: Path) -> tuple:
    """(projected dict, prompt text) via the real bridge and projection."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, str(BROLL_STEP / "bridge.py")],
        input=json.dumps(_routed_inputs(project)),
        capture_output=True, text=True, encoding="utf-8",
        cwd=str(REPO), env=env, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pre = json.loads(proc.stdout)
    with open(BROLL_STEP / "manifest.json", encoding="utf-8") as fh:
        manifest = json.load(fh)
    inputs = dict(_routed_inputs(project))
    inputs.update(pre)
    projected = project_step_context(inputs, manifest, set(pre))
    return projected, json_to_toon(projected)


# Keys that only exist in the scene/camera/object STRUCTURE - the field
# names of `scene[]`, `objects[]` and the assessment method. The prose
# rendering (`scene_prose`) carries the VALUES (locations, features) as
# running text, never these keys.
STRUCTURE_MARKERS = (
    "notable_features",
    "seen [",
    "usable_ranges_method",
)


def test_the_structure_is_at_the_reference_path_not_in_the_prompt(tmp_path):
    """#339's half: every byte moved, reached by the map, not inline."""
    projected, context = _assembled_prompt(tmp_path)
    assert "footage_analysis_reference" in projected, (
        "the bridge emits no reference to the vision pass's own analysis")
    path = reference_path(projected["footage_analysis_reference"])
    assert path, projected["footage_analysis_reference"][:400]
    assert Path(path).name == DOCUMENT_NAME
    document = Path(path).read_text(encoding="utf-8")
    assert "Where it is, per scene segment:" in document
    for marker in STRUCTURE_MARKERS:
        assert marker in document, f"the document lost {marker!r}"
        assert marker not in context, (
            f"{marker!r} is in the prompt AND at the path")
