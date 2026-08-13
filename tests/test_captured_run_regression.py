"""Regression tests built from a real broken run, not from empty fixtures.

`tests/fixtures/captured_run/` holds the manifest and step outputs from the
run that shipped a hollow timeline: no B-roll, one transition repeated ten
times, five SFX stacked at 0.000s, subtitle overlays overlapping by ~1s,
non-monotonic ducking, and a back half cut to invented timestamps.

The old suite passed on that data because the B-roll path was exercised
with `"b_roll_assignments": []` and the subtitle-overlay check validated a
shape the compiler never emits.  Each test here asserts that the defect IS
detected in the captured data, and that the corresponding fixed code path
no longer produces it.
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.manifest_validator import (
    validate_manifest_semantics,
    _check_broll_differs_from_aroll,
    _check_distinct_cut_points,
    _check_ducking_monotonic,
    _check_no_fabricated_source_ranges,
    _check_no_zero_duration_clips,
    _check_overlay_segments_do_not_overlap,
    _check_sfx_distributed,
    _check_vfx_distinct,
)
from library.tools.spine_contract import (
    SpineContractError,
    validate_spine_blocks,
)
from library.tools.audio_ducker import compute_ducking_curves

FIXTURES = Path(__file__).parent / "fixtures" / "captured_run"


@pytest.fixture(scope="module")
def broken_manifest():
    with open(FIXTURES / "broken_assembly_manifest.json") as f:
        return json.load(f)["assembly_manifest"]


@pytest.fixture(scope="module")
def broken_steps():
    with open(FIXTURES / "broken_step_outputs.json") as f:
        return json.load(f)


# ─── The validator must reject the captured manifest ─────────

def test_captured_manifest_is_rejected(broken_manifest):
    """The whole point: this manifest used to produce ONE error."""
    errors = validate_manifest_semantics(broken_manifest)
    assert len(errors) > 20, (
        "the captured broken manifest must trip many semantic assertions, "
        f"got {len(errors)}: {errors}"
    )


def test_broll_collapsed_to_one_zero_length_clip(broken_manifest):
    """Nine assignments + one interjection became a single invisible clip."""
    v2 = broken_manifest["tracks"]["V2"]["clips"]
    assert len(v2) == 1
    errors = _check_no_zero_duration_clips(broken_manifest)
    assert any("broll" in e for e in errors)


def test_all_transitions_share_one_cut_point(broken_manifest):
    errors = _check_distinct_cut_points(broken_manifest)
    assert len(errors) == 9, errors
    assert all("2.682" in e for e in errors)


def test_sfx_stacked_at_zero(broken_manifest):
    """Five whooshes were planned; A3 kept one, at 0.0."""
    planned = broken_manifest.get("sfx", [])
    a3 = broken_manifest["tracks"]["A3"]["clips"]
    assert planned == [] and len(a3) == 1, (
        "the captured manifest carried SFX in two places, one of them empty"
    )
    # Reconstruct the pre-dedup state to prove the check catches stacking.
    stacked = json.loads(json.dumps(broken_manifest))
    stacked["tracks"]["A3"]["clips"] = [
        {"label": f"sfx_{i}", "timeline_in": 0.0, "timeline_out": 0.3}
        for i in range(5)
    ]
    assert _check_sfx_distributed(stacked)


def test_identical_vfx_ranges(broken_manifest):
    errors = _check_vfx_distinct(broken_manifest)
    assert len(errors) == 4, errors
    assert all("slow_zoom_in" in e for e in errors)


def test_subtitle_overlays_overlap(broken_manifest):
    errors = _check_overlay_segments_do_not_overlap(broken_manifest)
    assert len(errors) == 13, errors


def test_ducking_curve_runs_backwards(broken_manifest):
    errors = _check_ducking_monotonic(broken_manifest)
    assert len(errors) == 13, errors


def test_back_half_uses_fabricated_source_ranges(broken_manifest):
    errors = _check_no_fabricated_source_ranges(broken_manifest)
    labels = {e.split()[2] for e in errors}
    assert labels == {
        "speech_9_seg0", "speech_10_seg0", "speech_11_seg0",
        "speech_12_seg0", "speech_13_seg0",
    }, errors


def test_broll_over_its_own_aroll_is_detected(broken_manifest):
    """The captured B-roll for blocks 1-2 reused the A-roll clip.

    In the manifest it is invisible (zero-length), so give it the extent
    it was meant to have and confirm the assertion fires.
    """
    manifest = json.loads(json.dumps(broken_manifest))
    aroll = manifest["tracks"]["V1"]["clips"][1]
    manifest["tracks"]["V2"]["clips"] = [{
        "label": "broll_1",
        "source_file": aroll["source_file"],
        "source_in": 1.0,
        "source_out": 2.0,
        "timeline_in": aroll["timeline_in"],
        "timeline_out": aroll["timeline_out"],
    }]
    errors = _check_broll_differs_from_aroll(manifest)
    assert len(errors) == 1
    assert "not a cutaway" in errors[0]


# ─── The spine contract must reject the captured spine ───────

def test_captured_spine_violates_the_contract(broken_steps):
    """13 of 14 blocks had empty word_timestamps and no top-level clip_id."""
    blocks = broken_steps["mesh_spine"]["timed_spine"]["structure"]
    with pytest.raises(SpineContractError) as excinfo:
        validate_spine_blocks(blocks)
    message = str(excinfo.value)
    assert "missing required keys" in message
    assert "clip_id" in message


def test_captured_spine_blocks_have_no_top_level_clip_id(broken_steps):
    blocks = broken_steps["mesh_spine"]["timed_spine"]["structure"]
    assert all("clip_id" not in b for b in blocks)
    # ...which is exactly the key plan_transitions read to decide whether
    # to run word-end detection, so the whole path was unreachable.
    assert all(b.get("clip_id", "") == "" for b in blocks)


def test_captured_body_passages_have_no_word_timings(broken_steps):
    body = broken_steps["speech_sequence"]["speech_sequence"]["body_sequence"]
    assert len(body) == 13
    assert all(not p["word_timestamps"] for p in body)
    assert all(p["start_time"] is None for p in body)


def test_hook_and_a_body_passage_share_one_source_range(broken_steps):
    seq = broken_steps["speech_sequence"]["speech_sequence"]
    hook = seq["hook_segment"]
    duplicates = [
        p for p in seq["body_sequence"]
        if p["clip_id"] == hook["clip_id"]
        and abs(p["source_start"] - hook["source_start"]) < 0.001
        and abs(p["source_end"] - hook["source_end"]) < 0.001
    ]
    assert len(duplicates) == 1
    assert duplicates[0]["position"] == 5


# ─── The fixed code paths must not reproduce the defects ─────

def test_word_end_cutting_is_reachable_on_a_conformant_spine():
    """The guard that gated beat-snapping now opens."""
    sys.path.insert(0, str(REPO_ROOT / "library" / "steps" / "step_4_02_plan_transitions"))
    from library.steps.step_4_02_plan_transitions.post_bridge import resolve_cut_point

    outgoing = {
        "position": 1,
        "block_type": "speech",
        "clip_id": "clip_006",
        "source_start": 14.68,
        "source_end": 16.065,
        "timeline_start": 2.682,
        "timeline_end": 4.067,
        "alignment_method": "whisperx_word_alignment",
        "word_timestamps": [
            {"word": "okay", "source_start": 14.68, "source_end": 14.9},
            {"word": "here", "source_start": 15.6, "source_end": 16.02},
        ],
    }
    incoming = dict(outgoing, position=2, timeline_start=4.067,
                    timeline_end=6.0)

    result = resolve_cut_point(incoming, outgoing, beat_grid=[])
    assert result["method"].startswith("word-end"), result
    # The cut lands on the last word's end, not the block boundary.
    assert result["cut_time"] == pytest.approx(4.022, abs=0.01)


def test_ducking_curves_are_monotonic_for_back_to_back_speech():
    """Contiguous blocks are what used to make the curve run backwards."""
    blocks = [
        {"start_time": 0.0, "end_time": 2.682},
        {"start_time": 2.682, "end_time": 4.067},
        {"start_time": 4.067, "end_time": 6.896},
        {"start_time": 20.0, "end_time": 22.0},
    ]
    curves = compute_ducking_curves(blocks, 43.0)
    times = [k["time_ms"] for k in curves]
    assert times == sorted(times), curves
    assert _check_ducking_monotonic({"music_ducking": {"ducking_curves": curves}}) == []


def test_subtitle_overlay_segments_no_longer_overlap():
    """Placement uses content bounds; the render buffer is trimmed."""
    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
        SUBTITLE_RENDER_BUFFER_S,
        generate_subtitle_props_per_block,
    )

    subtitle_data = {"subtitle_entries": [
        {"spine_block_position": 1, "timeline_start": 0.0,
         "timeline_end": 2.682, "text": "first", "words": []},
        {"spine_block_position": 2, "timeline_start": 2.682,
         "timeline_end": 4.067, "text": "second", "words": []},
        {"spine_block_position": 3, "timeline_start": 4.067,
         "timeline_end": 6.896, "text": "third", "words": []},
    ]}
    props = generate_subtitle_props_per_block(subtitle_data, fps=30)
    segments = [
        {
            "timeline_start": p["_timeline_start"],
            "timeline_end": p["_timeline_end"],
        }
        for p in props
    ]
    assert _check_overlay_segments_do_not_overlap(
        {"subtitle_overlay": {"segments": segments}}) == []
    # The handles still exist in the rendered clip - they are trimmed at
    # placement time, not removed from the render.
    padded = props[1]
    assert padded["durationInFrames"] > (
        padded["_source_out_frame"] - padded["_source_in_frame"])
    assert padded["_source_in_frame"] == round(SUBTITLE_RENDER_BUFFER_S * 30)


# ─── Steps that lied about their own success ─────────────────

def _check_output_is_real():
    """Load the runner's honesty check without executing the module twice."""
    import importlib.util
    path = (REPO_ROOT / "library" / "processes" / "edit_video"
            / "run_pipeline.py")
    spec = importlib.util.spec_from_file_location("run_pipeline_hon", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_output_is_real


def test_prosody_reporting_available_with_zero_profiles_is_a_failure(
        broken_steps):
    """`available: true, profiles: {}, total_clips: 0` used to pass."""
    check = _check_output_is_real()
    problems = check("prosody_analysis", broken_steps["prosody_analysis"])
    assert problems, "a step with an empty payload must not count as success"
    assert any("available=true" in p and "empty" in p for p in problems), problems


def test_music_analysis_reporting_unavailable_is_a_failure(broken_steps):
    """The run continued past `available: false` around a traceback."""
    check = _check_output_is_real()
    problems = check("music_analysis", broken_steps["music_analysis"])
    assert problems
    assert any("available=false" in p for p in problems), problems
    # The reason survives into the failure message instead of being dropped.
    assert any("Traceback" in p or "Error" in p for p in problems), problems


def test_a_real_step_output_is_not_flagged(broken_steps):
    """The check must not fire on output that actually carries data."""
    check = _check_output_is_real()
    healthy = {
        "prosody_analysis": {
            "available": True,
            "profiles": {"clip_001": {"pitch_mean": 142.0}},
            "total_clips": 1,
        }
    }
    assert check("prosody_analysis", healthy) == []
    assert check("select_broll", broken_steps["select_broll"]) == []


# ─── A passage anchored onto its predecessor's tail ──────────
#
# The shipped export cut IMG_1816 63.135-66.635 as block 4 and then
# IMG_1816 65.894-70.977 as block 5, so 0.741s of the same source audio
# plays twice across the cut ("...to post" | "to post..."). Block 5's
# alignment began on block 4's trailing words while its own opening words
# ("i'm going") went unmatched. The drift was 0.76s, well under
# MAX_HINT_DRIFT, which is why the threshold guard let it through.

# The transcript around that cut, as WhisperX timed it. "to post" occurs
# twice, seconds apart - that repetition is what the aligner latched onto.
_REPEATED_PHRASE_WORDS = [
    ("if", 63.135, 63.300), ("you", 63.300, 63.500),
    ("really", 63.500, 64.900), ("want", 65.770, 65.890),
    ("to", 65.890, 65.990), ("post", 65.990, 66.220),
    ("every", 66.220, 66.390), ("single", 66.390, 66.590),
    ("day.", 66.590, 66.635),
    ("I'm", 67.200, 67.450), ("going", 67.450, 67.700),
    ("to", 67.700, 67.850), ("post", 67.850, 68.200),
    ("a", 68.660, 68.750), ("video,", 68.750, 69.200),
    ("at", 69.300, 69.450), ("least", 69.450, 69.700),
    ("one,", 69.700, 70.000), ("every", 70.100, 70.350),
    ("single", 70.350, 70.600), ("day.", 70.600, 70.977),
]

PASSAGE_4_TEXT = "If you really want to post every single day."
PASSAGE_5_TEXT = "I'm going to post a video, at least one, every single day."


@pytest.fixture
def repeated_phrase_index(tmp_path):
    """A temporal index for IMG_1816 covering the block 4/5 cut."""
    index_dir = tmp_path / "temporal_index"
    index_dir.mkdir()
    with open(index_dir / "clip_016.json", "w") as f:
        json.dump({"speech_regions": [{
            "start": 63.135,
            "end": 70.977,
            "words": [
                {"word": w, "start": s, "end": e}
                for w, s, e in _REPEATED_PHRASE_WORDS
            ],
        }]}, f)
    return str(index_dir)


def _two_passage_sequence(second_text=PASSAGE_5_TEXT):
    """The LLM's output for the two passages, hints and all."""
    return {
        "hook_segment": None,
        "body_sequence": [
            {"position": 1, "clip_id": "clip_016", "text": PASSAGE_4_TEXT,
             "source_start": 63.1, "source_end": 66.6},
            {"position": 2, "clip_id": "clip_016", "text": second_text,
             "source_start": 66.9, "source_end": 71.0},
        ],
    }


def test_alignment_would_anchor_on_the_previous_passages_tail(
        repeated_phrase_index):
    """Reproduce the mis-anchor: it is invisible to the drift guard."""
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        MAX_HINT_DRIFT,
        collect_words_in_range,
    )

    with open(os.path.join(repeated_phrase_index, "clip_016.json")) as f:
        regions = json.load(f)["speech_regions"]

    unguarded = collect_words_in_range(
        66.9, 71.0, regions, passage_text=PASSAGE_5_TEXT)
    first_words = [w["word"] for w in unguarded["word_timestamps"][:3]]
    assert first_words == ["to", "post", "a"], first_words
    assert unguarded["start_time"] == pytest.approx(65.890)
    # ...which lands inside passage 4 (63.135-66.635): 0.745s replayed.
    drift = max(abs(unguarded["start_time"] - 66.9),
                abs(unguarded["end_time"] - 71.0))
    assert drift < MAX_HINT_DRIFT, (
        "the threshold guard cannot see this mis-anchor - that is the point")


def test_passage_is_reanchored_past_the_previous_one(repeated_phrase_index):
    """Block 5 now starts on its own words, not block 4's tail."""
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        enrich_speech_sequence,
    )

    result = enrich_speech_sequence(
        _two_passage_sequence(), repeated_phrase_index)
    first, second = result["body_sequence"]

    assert first["source_end"] == pytest.approx(66.635)
    assert second["source_start"] == pytest.approx(67.200)
    assert second["source_start"] >= first["source_end"]
    assert [w["word"] for w in second["word_timestamps"][:2]] == \
        ["I'm", "going"]


def test_unresolvable_overlap_fails_the_passage(repeated_phrase_index):
    """If it cannot be re-anchored it must fail loudly, not ship."""
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        PassageAlignmentError,
        enrich_speech_sequence,
    )

    # This text only exists inside passage 1's range, so there is nothing
    # to re-anchor to after it.
    sequence = _two_passage_sequence(second_text="If you really want")
    with pytest.raises(PassageAlignmentError) as excinfo:
        enrich_speech_sequence(sequence, repeated_phrase_index)
    assert "overlaps the previous passage" in str(excinfo.value)


def test_non_overlapping_passages_on_one_clip_still_align(
        repeated_phrase_index):
    """The check must not disturb a clip cut into consecutive passages."""
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        enrich_speech_sequence,
    )

    sequence = _two_passage_sequence(
        second_text="At least one, every single day.")
    result = enrich_speech_sequence(sequence, repeated_phrase_index)
    first, second = result["body_sequence"]
    assert (first["source_start"], first["source_end"]) == \
        pytest.approx((63.135, 66.635))
    assert second["source_start"] == pytest.approx(69.300)
    assert all(p["alignment_method"] == "whisperx_word_alignment"
               for p in result["body_sequence"])


# ─── ...and the manifest assertion that would have caught it ──

def _overlapping_pair_manifest(broken_manifest):
    """The captured manifest, with the shipped block 4/5 overlap restored.

    speech_6 already carries IMG_1816 63.135-66.635; moving speech_7's
    source_in back to 65.894 reproduces the pair exactly as it shipped.
    """
    manifest = json.loads(json.dumps(broken_manifest))
    clips = manifest["tracks"]["V1"]["clips"]
    assert (clips[6]["source_in"], clips[6]["source_out"]) == (63.135, 66.635)
    clips[7]["source_in"] = 65.894
    return manifest


def test_repeated_source_audio_is_rejected(broken_manifest):
    from library.tools.manifest_validator import (
        _check_no_repeated_source_audio,
    )

    errors = _check_no_repeated_source_audio(
        _overlapping_pair_manifest(broken_manifest))
    assert len(errors) == 1, errors
    assert "speech_7_seg0" in errors[0] and "0.741s" in errors[0]
    assert "IMG_1816.MOV" in errors[0]


def test_repeated_source_audio_reaches_the_semantic_pass(broken_manifest):
    """It belongs with the other semantic assertions, not off to the side."""
    overlapping = _overlapping_pair_manifest(broken_manifest)
    assert any("plays that audio twice in a row" in e
               for e in validate_manifest_semantics(overlapping))


def test_same_clip_blocks_that_do_not_overlap_pass(broken_manifest):
    """Consecutive cuts from one clip are normal; only the overlap is not."""
    from library.tools.manifest_validator import (
        _check_no_repeated_source_audio,
    )

    # As captured: speech_6 ends at 66.635 and speech_7 starts at 66.655,
    # both IMG_1816, plus several other same-clip runs.
    assert _check_no_repeated_source_audio(broken_manifest) == []

    # Blocks from different clips are unaffected even when their source
    # ranges happen to coincide.
    manifest = json.loads(json.dumps(broken_manifest))
    clips = manifest["tracks"]["V1"]["clips"]
    clips[7]["source_in"] = 65.894
    clips[7]["source_file"] = clips[7]["source_file"].replace(
        "IMG_1816", "IMG_1899")
    assert _check_no_repeated_source_audio(manifest) == []
