"""Regression tests for three artifact honesty defects (issue #224).

1. Speech fields asserting ``speech_present: false`` when the vision model
   cannot hear audio.
2. Music analysis ``key``/``chords`` recording no reason for failure.
3. Collision-avoidance duplicate files (``__2``) polluting step output.
"""

import json
import os
import sys

import pytest

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

    def test_speech_present_is_none_without_temporal_index(self):
        result = compute_deterministic_assessment(
            temporal_index=None, transcript="", duration=120
        )
        assert result["speech_present"] is None, (
            "speech_present must be None when temporal_index is unavailable, "
            "not False"
        )

    def test_speech_coverage_is_none_without_temporal_index(self):
        result = compute_deterministic_assessment(
            temporal_index=None, transcript="", duration=120
        )
        assert result["speech_coverage"] is None, (
            "speech_coverage must be None when temporal_index is unavailable, "
            "not 0.0"
        )

    def test_speech_coverage_method_is_unmeasured(self):
        result = compute_deterministic_assessment(
            temporal_index=None, transcript="", duration=120
        )
        assert result["speech_coverage_method"] == "unmeasured"

    def test_speech_fields_are_measured_with_temporal_index(self):
        ti = {
            "duration_s": 60,
            "speech_regions": [
                {"start": 0, "end": 30},
                {"start": 40, "end": 50},
            ],
        }
        result = compute_deterministic_assessment(
            temporal_index=ti, transcript="hello world", duration=60
        )
        assert result["speech_present"] is True
        assert result["speech_coverage"] == pytest.approx(0.67, abs=0.01)
        assert result["speech_coverage_method"] == "temporal_index"

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

    def test_chords_records_note_on_import_error(self, monkeypatch):
        """Simulate essentia not being installed."""
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "essentia.standard" or name == "essentia":
                raise ImportError("No module named 'essentia'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        result = analyze_chord_progression("/nonexistent/track.wav")
        assert result["method"] is None
        assert "note" in result or "error" in result, (
            "chord analysis must record a reason when it produces nothing"
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

    def test_collision_suffix_is_detected(self):
        assert _is_collision_duplicate("clip_profile_IMG_1806_v3__2.json")
        assert _is_collision_duplicate("clip_profile_IMG_1806_v3__3.json")
        assert _is_collision_duplicate("clip_profile_IMG_1816_v3__2.json")
        assert _is_collision_duplicate("vision_index_v3__2.json")

    def test_normal_files_are_not_flagged(self):
        assert not _is_collision_duplicate("clip_profile_IMG_1806_v3.json")
        assert not _is_collision_duplicate("clip_profile_IMG_1806.json")
        assert not _is_collision_duplicate("vision_index_v3.json")

    def test_profile_stems_skips_collision_duplicates(self, tmp_path):
        """Only the original should count, not the ``__2`` copy."""
        for name in [
            "clip_profile_IMG_1806_v3.json",
            "clip_profile_IMG_1806_v3__2.json",
            "clip_profile_IMG_1812_v3.json",
            "clip_profile_IMG_1812_v3__2.json",
        ]:
            (tmp_path / name).write_text("{}")
        stems = _profile_stems(str(tmp_path))
        assert stems == {"IMG_1806", "IMG_1812"}

    def test_video_only_with_collision_suffix_also_skipped(self, tmp_path):
        """Double-check that _video_only + __2 is filtered."""
        (tmp_path / "clip_profile_IMG_1806_video_only__2.json").write_text("{}")
        stems = _profile_stems(str(tmp_path))
        assert stems == set()
