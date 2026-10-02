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
