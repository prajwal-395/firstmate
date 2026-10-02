from library.tools.manifest_validator import validate_manifest
import pytest
from library.steps.step_5_04_compile_manifest.step import (
    _apply_manifest_qa_checks,
    _transition_cut_frame,
    _v1_index_ending_at,
)
from library.tools.spine_contract import validate_spine_blocks, SpineContractError
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
import os
from library.tools import layer_coherence


# Dummy valid manifest generator
def get_valid_manifest():
    return {
        "project": {
            "name": "Test",
            "resolution": [1080, 1920],
            "frame_rate": 30.0,
            "duration_seconds": 10.0
        },
        "tracks": {
            "V1": {
                "label": "A-Roll",
                "clips": [
                    {
                        "source_file": __file__,  # Use current file as a guaranteed existing file
                        "source_in": 17.666,
                        "source_out": 22.348,
                        "timeline_in": 0.0,
                        "timeline_out": 4.682,
                        "timeline_in_frame": 0,
                        "timeline_out_frame": 140,
                        "label": "clip_1"
                    }
                ]
            }
        },
        "subtitles": []
    }

def _missing_source(m):
    m["tracks"]["V1"]["clips"][0]["source_file"] = "/does/not/exist.mov"


def _invalid_cdl(m):
    m["color_grade"] = {"per_clip_adjustments": [{
        "clip_id": "clip_1",
        "cdl_values": {"slope_r": -0.5, "power_g": 0, "offset_b": 2.0}}]}


def _subtitle_past_the_end(m):
    # The shape compile_manifest writes: subtitle_overlay.segments. A
    # fabricated tracks["subtitle_overlay"]["clips"] once passed green
    # while validating a structure the compiler never emits.
    m["subtitle_overlay"] = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 12.0,
         "overlay_path": "/tmp/sub_block_1.mov"}]}


def _subtitle_overlap(m):
    m["subtitle_overlay"] = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 3.0},
        {"timeline_start": 2.0, "timeline_end": 4.0}]}


def _zero_duration_clip(m):
    # out == in used to pass: the old check only rejected out < in.
    m["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0, "source_out": 0,
         "timeline_in": 0, "timeline_out": 0, "label": "broll_1"}]}


def _v2_overlap(m):
    # Overlap detection used to run on V1 only.
    m["tracks"]["V2"] = {"label": "B-Roll", "clips": [
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 0.0, "timeline_out": 2.0, "label": "broll_1"},
        {"source_file": __file__, "source_in": 0.0, "source_out": 2.0,
         "timeline_in": 1.0, "timeline_out": 3.0, "label": "broll_2"}]}


def _repeated_source_audio(m):
    # Consecutive V1 clips from one source with overlapping source
    # ranges repeat audio (AGENTS.md 6).
    m["tracks"]["V1"]["clips"].append(
        {"source_file": __file__, "source_in": 20.0, "source_out": 25.0,
         "timeline_in": 4.682, "timeline_out": 9.682, "label": "clip_2"})


DEFECTS = [
    (_missing_source, ["not found"], 1),
    (_invalid_cdl, ["slope_r", "power_g", "offset_b"], 3),
    (_subtitle_past_the_end, ["exceeds project duration"], None),
    (_subtitle_overlap, ["before the previous one ends"], None),
    (_zero_duration_clip, ["zero-length"], None),
    (_v2_overlap, ["overlaps the previous clip"], None),
    (_repeated_source_audio, ["repeats"], None),
]


def test_each_manifest_defect_is_rejected_by_name():
    """The valid manifest passes; each planted defect is named."""
    assert validate_manifest(get_valid_manifest()) == []
    for plant, expected, count in DEFECTS:
        manifest = get_valid_manifest()
        plant(manifest)
        errors = validate_manifest(manifest)
        for word in expected:
            assert any(word in e for e in errors), (plant.__name__, errors)
        if count is not None:
            assert len(errors) == count, (plant.__name__, errors)


# --------------------------------------------------------------------------
# From test_manifest_frame_boundaries.py
#
# A frame-aligned join must not fail from sub-frame seconds drift.

def _manifest(first_out_frame, second_in_frame):
    return {
        "tracks": {
            "V1": {
                "clips": [
                    {
                        "label": "outgoing",
                        "timeline_out": 5.382,
                        "timeline_out_frame": first_out_frame,
                    },
                    {
                        "label": "incoming",
                        "timeline_in": 5.367,
                        "timeline_in_frame": second_in_frame,
                        "timeline_out": 15.4,
                        "timeline_out_frame": 462,
                    },
                ],
            },
        },
    }


def test_manifest_overlap_check_compares_frames_not_drifting_seconds():
    """A 0.015s display drift is not an overlap when both clips join at
    frame 161; a real one-frame overlap of the Resolve spans still is."""
    _apply_manifest_qa_checks(_manifest(161, 161))
    with pytest.raises(ValueError, match="overlap: 1 frame\\(s\\)"):
        _apply_manifest_qa_checks(_manifest(162, 161))


def test_exact_anchor_does_not_attach_to_a_nearby_v1_cut():
    """A stated frame is exact even when another cut is within 0.25
    seconds, and an unreadable stated frame refuses rather than falling
    back to seconds."""
    clips = [{
        "timeline_out": 14.267,
        "timeline_out_frame": 462,
    }]
    assert _v1_index_ending_at(
        clips, 14.267, cut_frame=428) is None
    assert _v1_index_ending_at(
        clips, 14.267, cut_frame=462) == 0
    # Legacy, unanchored entries retain their seconds-based tolerance.
    assert _v1_index_ending_at(clips, 14.267) == 0
    with pytest.raises(ValueError, match="unreadable exact cut frame"):
        _transition_cut_frame({"transition_id": "t1",
                                "cut_point_frame": None})


# --------------------------------------------------------------------------
# From test_spine_contract.py
#
# Tests for the spine contract.

def test_validate_spine_rejects_out_of_bounds_duration():
    blocks = [
        {
            "position": 1,
            "block_type": "speech",
            "clip_id": "c1",
            "source_start": 0.0,
            "source_end": 10.0,
            "timeline_start": 0.0,
            "timeline_end": 10.0,
            "word_timestamps": [{"word": "hello", "source_start": 0.0, "source_end": 1.0}],
            "alignment_method": "whisperx",
        }
    ]

    # B1 fold: the deleted `test_validate_spine_accepts_valid_duration`
    # lives on as the baseline here - the same shape at a valid duration
    # passes, so the zone has an inside and not just an outside.
    valid = [dict(blocks[0], source_end=60.0, timeline_end=60.0)]
    validate_spine_blocks(valid, total_duration=60.0,
                          target_duration_zone=(54.0, 60.0, 66.0))

    # Target zone [54.0, 66.0]. Total duration 10.0 is way below.
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks(blocks, total_duration=10.0, target_duration_zone=(54.0, 60.0, 66.0))
    assert "outside the target duration zone" in str(exc.value)

def test_validate_spine_rejects_leading_silence():
    blocks = [
        {
            "position": 1,
            "block_type": "silence",
            "clip_id": None,
            "source_start": None,
            "source_end": None,
            "timeline_start": 0.0,
            "timeline_end": 5.0,
            "word_timestamps": [],
            "alignment_method": None,
        },
        {
            "position": 2,
            "block_type": "speech",
            "clip_id": "c1",
            "source_start": 0.0,
            "source_end": 60.0,
            "timeline_start": 5.0,
            "timeline_end": 65.0,
            "word_timestamps": [{"word": "hello", "source_start": 0.0, "source_end": 1.0}],
            "alignment_method": "whisperx",
        }
    ]
    
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks(blocks, total_duration=65.0, target_duration_zone=(54.0, 60.0, 66.0))
    assert "leading silence block at the head of the timeline is not allowed" in str(exc.value)


# --------------------------------------------------------------------------
# From test_validate_output.py

POST_BRIDGE = Path("library/steps/step_6_02_validate_output/post_bridge.py")


def test_a_missing_or_undetermined_half_is_never_distribution_ready():
    """A step reporting success while measuring nothing is the defect
    class this repo exists to prevent: a missing deterministic or LLM
    half reads as `fail`, an undetermined verdict stays `undetermined`,
    and none is distribution-ready."""
    cases = [
        ({"validation_result": {"status": "pass", "summary": "LLM fine"}},
         "fail", "Deterministic validation failed"),
        ({"deterministic_validation": {"status": "pass", "summary": "Det"}},
         "fail", None),
        ({"deterministic_validation": {"status": "pass", "summary": "Det"},
          "validation_result": {"status": "undetermined", "summary": "?"}},
         "undetermined", None),
    ]
    for input_data, status, summary in cases:
        result = subprocess.run(
            [sys.executable, str(POST_BRIDGE)], input=json.dumps(input_data),
            capture_output=True, encoding="utf-8", check=True)
        v = json.loads(result.stdout)["validation_result"]
        assert v["status"] == status, input_data
        assert v["distribution_ready"] is False, input_data
        if summary:
            assert summary in v["summary"]


def test_a_qa_pass_that_crashed_fails_validation_loudly(tmp_path):
    """A QA toolkit (render or subtitle) that did not run must not read
    as a passing one.

    Every individual `render_qa` measurement fails closed on its own
    error, so the aggregate does the same: when `run_full_render_qa`
    raises, the step reports fail with the crash named, instead of
    reporting pass with zero measurements (the file-exists check alone
    would still pass, which is exactly how an unvalidated render would
    ship as `distribution_ready: true`).
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                            "duration_seconds": 10.0},
                "tracks": {}, "subtitles": []}

    def _boom(*args, **kwargs):
        raise RuntimeError("probe exploded")

    with patch.object(validate, "run_full_render_qa", side_effect=_boom):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["status"] == "fail"
    assert result["distribution_ready"] is False
    assert result["critical_checks_passed"] is False
    assert result["checks"]["technical"]["pass"] is False
    assert any("did not run" in i and "probe exploded" in i
               for i in result["all_issues"])
    report = result["qa_report"]
    assert any(r["metric"] == "render_qa" and r["passed"] is False
               for r in report)

    # The subtitle pass fails closed the same way.
    manifest["subtitles"] = [{"timeline_start": 0.0, "timeline_end": 1.0,
                              "text": "hello"}]

    def _aligner_boom(*args, **kwargs):
        raise RuntimeError("aligner exploded")

    with patch.object(validate, "run_full_render_qa", return_value=[]), \
         patch.object(validate, "verify_subtitle_timing",
                      side_effect=_aligner_boom):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False
    assert result["checks"]["technical"]["pass"] is False
    assert any("did not run" in i and "aligner exploded" in i
               for i in result["all_issues"])
    assert any(r["metric"] == "subtitle_qa" and r["passed"] is False
               for r in result["qa_report"])


def test_declared_audio_delivery_targets_reach_export_measurement(tmp_path):
    """The export gate measures against the plan's exact delivery targets."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {
        "project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                    "duration_seconds": 10.0},
        "tracks": {}, "subtitles": [],
        "audio_mix": {"delivery_lufs_target": -16.0,
                       "delivery_true_peak_ceiling_dbtp": -1.5},
    }

    with patch.object(validate, "run_full_render_qa", return_value=[]) as qa:
        validate.validate_output({"output_path": str(video)}, manifest)

    assert qa.call_args.kwargs["target_lufs"] == -16.0
    assert qa.call_args.kwargs["true_peak_ceiling"] == -1.5


def test_failing_subtitles_reach_the_verdict_without_gating_it(tmp_path):
    """Subtitle quality is measured AND read: the six subtitle metrics
    land in checks["subtitles"], all_issues and qa_report.

    Reporting-only, deliberately: whether a subtitle metric should FAIL
    a build, and at what threshold, is the captain's call, so a reel
    failing every subtitle metric still validates - but the verdict now
    SAYS so instead of carrying no subtitle key at all.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                            "duration_seconds": 30.0},
                "tracks": {},
                "subtitles": [
                    {"timeline_start": 0.0, "timeline_end": 0.2,
                     "text": "Hi"},
                    {"timeline_start": 0.1, "timeline_end": 8.0,
                     "text": "x" * 400},
                    {"timeline_start": 20.0, "timeline_end": 21.0,
                     "text": "ok"},
                ]}

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    sub = result["checks"]["subtitles"]
    assert sub["pass"] is False
    assert len(sub["issues"]) == 5
    # Visible in the verdict three ways: the check, the issue list, the
    # report. (subtitle_overflow is the sixth metric; the bridge never
    # passes total_duration, so that row cannot exist here.)
    assert any(i.startswith("[subtitles]") for i in result["all_issues"])
    metrics = {r["metric"] for r in result["qa_report"]}
    assert {"subtitle_overlap", "subtitle_too_short", "subtitle_too_long",
            "subtitle_gaps", "subtitle_read_speed"} <= metrics
    # ...but no threshold was chosen here, so nothing new gates.
    assert result["status"] == "pass"
    assert result["distribution_ready"] is True


def _validate(validate, video, manifest):
    with patch.object(validate, "run_full_render_qa", return_value=[]):
        return validate.validate_output({"output_path": str(video)}, manifest)


def test_a_drawn_transition_must_sit_on_its_cut(tmp_path):
    """A planned transition floating off its cut point fails the gate -
    the arithmetic the bridge holds so the prompt no longer carries the
    rows. CUT types draw nothing (`transition_vocabulary.CUT_TYPES`), so
    a hard-cut row far from any edge gates nothing: the 001 snapshots
    carry one 0.3s off its edge on renders that are correct."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    clips = [_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.0, 5.0)]
    result = _validate(validate, video, _geometry_manifest(
        clips=clips,
        transitions=[
            {"transition_id": "t1", "transition_type": "hard_cut",
             "cut_point_timeline": 2.0},
            {"transition_id": "t2", "transition_type": "glow",
             "cut_point_timeline": 3.0, "duration_frames": 12},
        ],
    ))
    check = result["checks"]["transitions_at_seams"]
    assert check["pass"] is False
    assert any("t2" in i and "3.000" in i for i in check["issues"])
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False

    result = _validate(validate, video, _geometry_manifest(
        clips=clips,
        transitions=[{"transition_id": "t1", "transition_type": "hard_cut",
                      "cut_point_timeline": 3.5}],
    ))
    assert result["checks"]["transitions_at_seams"]["pass"] is True
    assert result["status"] == "pass"


def test_a_v1_gap_fails_unless_declared_or_covered(tmp_path):
    """A V1 track that does not tile fails the gate - unless the spine
    validly declared the gap a black beat (compile_manifest lets it
    through, so the render gate must too), or a B-roll cutaway covers it
    end to end (measured on round3: four V1 gaps, each exactly a V2
    clip, a correct render). Partially covered does not count."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    clips = [_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.5, 5.0)]

    result = _validate(validate, video,
                       _geometry_manifest(clips=clips, transitions=[]))
    check = result["checks"]["v1_tiling"]
    assert check["pass"] is False
    assert any("0.500" in i for i in check["issues"])
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False

    result = _validate(validate, video, _geometry_manifest(
        clips=clips, transitions=[],
        spine_blocks=[{
            "position": 1, "block_type": "transition_slot",
            "timeline_start": 2.0, "timeline_end": 2.5,
            "intentional_black_beat": True,
            "black_beat_reason": "deliberate breath before the reveal",
        }],
    ))
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"

    manifest = _geometry_manifest(clips=clips, transitions=[],
                                  v2_clips=[_v1_clip("cover", 2.0, 2.5)])
    result = _validate(validate, video, manifest)
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"

    manifest["tracks"]["V2"]["clips"] = [_v1_clip("short", 2.1, 2.4)]
    result = _validate(validate, video, manifest)
    assert result["checks"]["v1_tiling"]["pass"] is False
    assert result["status"] == "fail"


def _v1_clip(label, timeline_in, timeline_out):
    return {
        "label": label,
        "source_file": "/nonexistent/source.mp4",
        "source_in": 0.0,
        "source_out": timeline_out - timeline_in,
        "timeline_in": timeline_in,
        "timeline_out": timeline_out,
    }


def _geometry_manifest(clips, transitions, spine_blocks=(), v2_clips=()):
    manifest = {
        "project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                    "duration_seconds": 10.0},
        "tracks": {"V1": {"clips": clips}},
        "transitions": transitions,
        "subtitles": [],
        "_spine_blocks": list(spine_blocks),
    }
    if v2_clips:
        manifest["tracks"]["V2"] = {"clips": list(v2_clips)}
    return manifest


# --------------------------------------------------------------------------
# From test_validate_sfx_library.py
#
# Step 0.01 must validate the library the pipeline actually reads.
#
# It used to assert a layout no reader wants: `library_analysis.json` and
# `library_semantic.json` inside `profiles/`. The only reader,
# `library.tools.sfx_library.load_sfx_index`, reads `<library>/sfx_index.json`
# first and lists those two filenames in `_NON_ENTRY_FILES` - it SKIPS them.
#
# The consequence was backwards in both directions. The shipped library at
# PIPELINE_SFX_LIBRARY - 78 entries, every file on disk - failed the gate.
# A project-local `mock_sfx/` holding profiles and no audio at all passed it,
# so every run under it placed SFX that could never make a sound and the gate
# said the library was fine.

REPO_ROOT = Path(__file__).resolve().parents[3]
STEP = REPO_ROOT / "library" / "steps" / "step_0_01_validate_sfx_library" / "step.py"


def _run(payload: dict):
    proc = subprocess.run(
        [sys.executable, str(STEP)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    body = json.loads(proc.stdout)["sfx_library_status"]
    return proc.returncode, body


def _library(tmp_path: Path, *, with_audio: bool) -> Path:
    lib = tmp_path / "sfx library"
    (lib / "Accents").mkdir(parents=True)
    wav = lib / "Accents" / "whoosh_1.wav"
    if with_audio:
        wav.write_bytes(b"RIFF....WAVEfmt ")
    (lib / "sfx_index.json").write_text(json.dumps([
        {
            "file": "whoosh_1.wav",
            "path": str(wav),
            "folder_category": "Accents",
            "description": "a whoosh transition swish",
            "technical": {"basic": {"duration": 0.6}},
        }
    ]))
    return lib


def test_a_library_whose_files_exist_is_valid(tmp_path):
    """The regression proper: no `profiles/library_*.json` (metadata the
    reader skips), entries that load, files on disk - valid."""
    lib = _library(tmp_path, with_audio=True)
    assert not (lib / "profiles").exists()
    code, body = _run({"sfx_library": str(lib)})
    assert code == 0, body
    assert body["valid"] is True
    assert body["playable_entries"] == 1
    # The catalogue is what step 4.04 offers the model, so it is what
    # "valid" means. There is no `available_types` any more: nothing maps
    # a type name to a file.
    assert body["catalog_entries"] == 1
    assert "available_types" not in body


def test_profiles_without_audio_fail(tmp_path):
    """The mock_sfx shape: an index, and not one file behind it."""
    code, body = _run({"sfx_library": str(_library(tmp_path, with_audio=False))})
    assert code == 1
    assert body["valid"] is False
    assert body["playable_entries"] == 0
    assert "silent" in body["error"]


def test_no_path_supplied_is_a_failure_with_a_fix(tmp_path):
    code, body = _run({})
    assert code == 1
    assert "PIPELINE_SFX_LIBRARY" in body["fix"]


# --------------------------------------------------------------------------
# From test_artifact_honesty.py
#
# Regression tests for three artifact honesty defects (issue #224).
#
# 1. Speech fields asserting ``speech_present: false`` when the vision model
#    cannot hear audio.
# 2. Music analysis ``key``/``chords`` recording no reason for failure.
# 3. Collision-avoidance duplicate files (``__2``) polluting step output.

# ── 1. Speech fields ────────────────────────────────────────────────────

try:
    from library.tools.analysis.vision_pipeline_v3 import (
        compute_deterministic_assessment,
    )
except ImportError:
    compute_deterministic_assessment = None


@pytest.mark.skipif(
    compute_deterministic_assessment is None,
    reason='could not import "mlx_vlm" - mlx is a macOS-only dependency',
)
class TestSpeechFieldsWithoutTemporalIndex:
    """When temporal_index is absent, speech fields must say so, not assert
    ``False`` / ``0.0``.
    """


    def test_transcript_present_but_temporal_index_absent_is_still_unmeasured(self):
        """Even with a transcript string, we cannot measure coverage without
        the temporal index - so the fields remain unmeasured rather than
        fabricating values."""
        result = compute_deterministic_assessment(
            temporal_index=None, transcript="I am speaking right now", duration=120
        )
        assert result["speech_present"] is None
        assert result["speech_coverage"] is None
        assert result["speech_coverage_method"] == "unmeasured"


# ── 2. Music analysis failure reasons ───────────────────────────────────

try:
    from library.tools.analysis.music_pipeline import (
        analyze_key,
        analyze_chord_progression,
    )
except ImportError:
    analyze_key = None
    analyze_chord_progression = None


class TestMusicAnalysisFailureRecording:
    """When an analyser produces nothing, it must say WHY - matching the
    pattern ``stems`` already uses with ``"note": "demucs not installed"``.
    """

    def test_key_records_note_on_import_error(self, monkeypatch):
        """Simulate essentia not being installed."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "essentia.standard" or name == "essentia":
                raise ImportError("No module named 'essentia'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = analyze_key("/nonexistent/track.wav")
        assert result["method"] is None
        assert "note" in result or "error" in result, (
            "key analysis must record a reason when it produces nothing"
        )
        reason = result.get("note") or result.get("error", "")
        assert "essentia" in reason.lower() or "not installed" in reason.lower()


# ── 3. Collision-avoidance duplicate filtering ──────────────────────────

from library.steps.step_1_03_semantic_analysis.step import (
    _is_collision_duplicate,
    _profile_stems,
)


class TestCollisionDuplicateFiltering:
    """The migration tool's ``_unique()`` appends ``__2``, ``__3``, ...
    to avoid overwriting.  Step 1.03 must filter these out.
    """

    def test_profile_stems_skips_collision_duplicates(self, tmp_path):
        """Only the original should count, not the ``__2`` copy."""
        assert _is_collision_duplicate("clip_profile_IMG_1806_v3__3.json")
        assert _is_collision_duplicate("vision_index_v3__2.json")
        for name in [
            "clip_profile_IMG_1806_v3.json",
            "clip_profile_IMG_1806_v3__2.json",
            "clip_profile_IMG_1812_v3.json",
            "clip_profile_IMG_1812_v3__2.json",
        ]:
            (tmp_path / name).write_text("{}")
        stems = _profile_stems(str(tmp_path))
        assert stems == {"IMG_1806", "IMG_1812"}


# --------------------------------------------------------------------------
# From test_layer_coherence.py
#
# Layer coherence: each layer agrees with the one it derives from.
#
# Read-only by construction: the check never writes to the project, so
# running it over a fixture project must create no files, and running
# it over the captain's project changes nothing. Fixtures live under
# `tmp_path` (AGENTS.md 8).

def _write(path, document):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)


def _fixture_project(root, heard="lucy", correct="Lucie"):
    _write(os.path.join(root, "learned_context", "learnings.json"), [
        {"id": "lc-0001", "kind": "correction", "status": "active",
         "statement": "spelling", "read_by": ["*"],
         "source": {"correction_type": "transcript_spelling",
                     "heard": heard, "correct": correct},
         "detail": "vetting"}])
    words = [{"word": "say", "start": 1.0, "end": 1.2, "timed": True},
             {"word": correct, "start": 1.3, "end": 1.7,
              "timed": True}]
    _write(os.path.join(
        root, "pipeline_output", "scratch", "timeline_transcript",
        "transcript.json"),
        {"segments": [{"text": f"say {correct}", "words": words}],
         "transcript_corrections_applied": [
             {"id": "lc-0001", "heard": heard, "correct": correct,
              "replacements": 1}]})
    return root


def test_heard_form_in_display_is_named_with_both_values(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    row = report["wording"][0]
    assert row["found"] == "lucy" and row["should_be"] == "Lucie"
    assert "a_subtitles.json" in row["layer_file"]
    assert "say lucy" in row["context"]


def test_check_creates_no_files(tmp_path):
    root = _fixture_project(str(tmp_path))
    before = set()
    for dirpath, _, filenames in os.walk(root):
        before.update(os.path.join(dirpath, f) for f in filenames)
    layer_coherence.check_project(root)
    after = set()
    for dirpath, _, filenames in os.walk(root):
        after.update(os.path.join(dirpath, f) for f in filenames)
    assert after == before


def _file_like_build(root, report):
    """Store a report the way a reels build stores it, and return bytes.

    `reel_build.rebuild_reels_in_project` files the full report at the
    project root (`layer_coherence.write_coherence_report`, outside
    every scanned root) and keeps only counts in `pipeline_data.json`
    (`layer_coherence.summarize_coherence`). The state writer rewrites
    the whole of `pipeline_data.json` after every step.
    """
    layer_coherence.write_coherence_report(root, report)
    path = os.path.join(root, "pipeline_data.json")
    document = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    document.setdefault("step_outputs", {}).setdefault(
        "build_reels", {}).setdefault(
            "reel_build", {})["coherence_summary"] = (
                layer_coherence.summarize_coherence(report))
    _write(path, document)
    return os.path.getsize(path)


def test_no_pruning_filter_remains(tmp_path):
    """The loop is cut at the cause, not hidden behind a filter.

    A helper that lifts stored rows out of the document before the
    scan reads it leaves the feedback in place and hides it - which is
    how one project's state file reached 12.2 GB unnoticed. Findings
    live outside the scan now, so there is nothing to prune and no
    pruner may exist.
    """
    assert not hasattr(layer_coherence, "_without_own_report"), (
        "the stored-rows filter is back: cut the loop structurally "
        "instead (findings outside the scan, counts in state)")
    assert not hasattr(layer_coherence, "OWN_REPORT_ROUTE"), (
        "the stored-report route is back: nothing about this report "
        "may live where the scan reads")


def test_the_scan_does_not_find_its_own_filed_findings(tmp_path):
    """The feedback loop that grew one project's state file to 12.2 GB.

    `check_wording` scans `pipeline_data.json` (regenerated step
    outputs are a display) and a reels build used to store this report
    back into that same file. Each run then re-found the previous
    run's rows - quoted inside their own `found` and `context` fields -
    and the file doubled on every build. Measured on the captain's
    `geo-podcast` 2026-09-16: 31,982 of 32,011 rows were self-inflicted.

    The cut is structural: the full report is filed outside the scan
    and only counts reach the state file. Two runs through the REAL
    build storage path is enough to show it: run two must find nothing
    new, and the state file must not grow from having been scanned.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})

    first = layer_coherence.check_project(root)["wording"]
    assert len(first) == 1, "the real divergence is the one in the plan"
    first_bytes = _file_like_build(root, layer_coherence.check_project(root))

    second = layer_coherence.check_project(root)["wording"]
    second_bytes = _file_like_build(
        root, layer_coherence.check_project(root))

    assert len(second) == len(first), (
        f"run two found {len(second)} row(s) where run one found "
        f"{len(first)}: the scan is reading its own filed output")
    assert second_bytes == first_bytes, (
        f"the state file grew {first_bytes} -> {second_bytes} bytes "
        f"across two runs that changed nothing")

    third = layer_coherence.check_project(root)["wording"]
    assert len(third) == len(first)
    # The row that survives is the real one, by identity - not merely
    # a count that happens to match.
    assert third[0]["found"] == "lucy"
    assert "a_subtitles.json" in third[0]["layer_file"]


def test_the_sidecar_lives_where_the_scan_never_reads(tmp_path):
    """The filed full rows must be invisible to the scan by location.

    Rows quote the heard form by construction, so any copy under a
    scanned root re-arms the loop no matter what the state file
    carries. The sidecar's path must sit outside every scanned root
    and must not be the state file itself.
    """
    root = _fixture_project(str(tmp_path))
    path = layer_coherence.coherence_report_path(root)
    for sub in layer_coherence.SCAN_SUBDIRS:
        assert not path.startswith(os.path.join(root, sub) + os.sep), (
            f"the sidecar {path} sits under scanned root {sub}")
    assert os.path.basename(path) != "pipeline_data.json"

    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    before = layer_coherence.check_project(root)["wording"]
    layer_coherence.write_coherence_report(
        root, layer_coherence.check_project(root))
    after = layer_coherence.check_project(root)["wording"]
    assert len(after) == len(before) == 1, (
        "filing the full report changed the next scan: the sidecar "
        "is being read")


def test_the_state_summary_cannot_self_match(tmp_path):
    """Counts in the state file quote nothing, so they match nothing.

    The summary is what reaches `pipeline_data.json` (which the scan
    reads), so it must carry no `found`, no `context` and no
    `should_be` - only per-class counts. Stored summaries then add
    rows to no future scan, whatever the heard form is.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    summary = layer_coherence.summarize_coherence(report)
    assert summary["wording"] == 1
    blob = json.dumps(summary).lower()
    assert "lucy" not in blob, "the summary quotes the heard form"
    assert "found" not in summary and "context" not in summary, (
        "the summary carries row text under a new name")

    _write(os.path.join(root, "pipeline_data.json"),
           {"step_outputs": {"build_reels": {"reel_build": {
               "coherence_summary": summary}}}})
    assert len(layer_coherence.check_project(root)["wording"]) == 1


def test_real_findings_are_still_produced_and_readable(tmp_path):
    """The scan keeps reading the state file; the filed report keeps
    the rows.

    Dropping `pipeline_data.json` from the scan would have been the
    easy cut, but real divergences live in other step outputs there -
    so a real row is planted in the state file outside any report
    route, and the test demands it back. Whatever consumes the
    findings reads the filed sidecar (full rows) and the state
    summary (counts) - both are asserted here, not inspected.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    _write(os.path.join(root, "pipeline_data.json"),
           {"step_outputs": {"subtitle_plan": {
               "cards": [{"text": "say lucy again"}]}}})

    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 2, (
        "the scan must still read the state file for real rows in "
        "other step outputs")
    assert {row["found"] for row in report["wording"]} == {"lucy"}

    path = layer_coherence.write_coherence_report(root, report)
    filed = layer_coherence.read_coherence_report(root)
    assert filed is not None, f"nothing filed at {path}"
    assert len(filed["wording"]) == 2
    assert any(row["should_be"] == "Lucie"
               for row in filed["wording"]), (
        "the filed report must keep both values of each finding")
    summary = layer_coherence.summarize_coherence(report)
    assert summary["status"] == "ok" and summary["wording"] == 2
