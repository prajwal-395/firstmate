"""Regression tests for three artifact honesty defects (issue #224).

1. Speech fields asserting ``speech_present: false`` when the vision model
   cannot hear audio.
2. Music analysis ``key``/``chords`` recording no reason for failure.
3. Collision-avoidance duplicate files (``__2``) polluting step output.
"""


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

