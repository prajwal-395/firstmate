"""Transient classification: cuts snap to strong transients.

The spec names "snap cuts to transients" as a reasoning the audio
intelligence track enables. Before this, SFX placement for "punchy"
envelope would snap to any onset, not preferring strong transients
(drum hits, hard attacks) over weak ones (note attacks, soft
percussive events).

The defect: a punchy SFX placed at a weak onset sounds misplaced
because the ear attends to strong transients, not weak ones. The fix:
classify onsets by strength and prefer strong transients for punchy
placement.

Second defect (PR 1831 review 2): classify_onsets re-decoded the
whole clip with librosa to recompute the onset envelope, doubling the
indexing cost per audio clip, and answered unreadable strengths with
a silent [] that reads as "no transients here". Strengths now come
from the detect pass itself, and unreadable strengths return None so
the index records why.
"""
import sys

import pytest

from library.steps.step_1_04_temporal_index.step import (
    classify_onsets,
    detect_onsets_with_strength,
)


class TestOnsetClassification:
    """Onset classification distinguishes strong from weak transients."""

    def test_classify_onsets_marks_strong_and_weak(self):
        """Onsets are classified by the data's own 75th percentile.

        The defect this catches: without classification, all onsets are
        treated equally, so a punchy SFX can snap to a weak onset when
        a strong one is nearby.
        """
        onsets = [0.5, 2.0, 3.5, 5.0]
        strengths = [10.0, 0.5, 0.3, 0.4]
        result = classify_onsets(onsets, strengths)

        assert len(result) == 4
        kinds = {r["time"]: r["kind"] for r in result}
        assert kinds[0.5] == "strong"
        assert kinds[2.0] == "weak"
        assert kinds[3.5] == "weak"
        assert kinds[5.0] == "weak"

    def test_classify_onsets_takes_strengths_without_a_path(self):
        """Classification reads the strengths the detect pass measured.

        The defect this catches: classify_onsets used to take the
        audio path and load the clip with librosa a second time per
        audio clip to recompute the onset envelope. Strengths arrive
        from detect_onsets_with_strength, so the classifier has no
        audio argument at all - a second decode is impossible.
        """
        result = classify_onsets([1.0, 2.0], [9.0, 1.0])
        assert [r["kind"] for r in result] == ["strong", "weak"]

    def test_classify_onsets_empty_when_no_onsets(self):
        """No onsets means no classification, not a default."""
        result = classify_onsets([], [])
        assert result == []

    def test_classify_onsets_none_when_strengths_unreadable(self):
        """Unreadable strengths return None, never a silent [].

        The defect this catches: a silent except->[] when the envelope
        could not be read writes an empty classification that reads
        as "no transients here" - a default reported as measured.
        None forces the caller to record the reason on the index.
        """
        assert classify_onsets([1.0, 2.0], []) is None
        assert classify_onsets([1.0, 2.0], [1.0]) is None


class TestDetectOnsetsWithStrength:
    """The detect pass returns times and strengths in one decode."""

    def test_times_and_strengths_are_parallel(self, monkeypatch):
        """Each onset carries the envelope value at its own frame.

        The defect this catches: strengths recomputed in a second
        pass could disagree with the times - a different decode, a
        different envelope - and an onset frame beyond the envelope
        would be dropped from the classification alone.
        """
        import numpy as np
        import types

        fake_librosa = types.ModuleType("librosa")
        fake_onset = types.ModuleType("librosa.onset")

        def fake_load(path, sr=None):
            return np.zeros(1), sr or 22050

        def fake_onset_detect(y=None, sr=None):
            return np.array([21, 86, 150, 215])

        def fake_onset_strength(y=None, sr=None):
            env = np.zeros(500)
            env[21] = 10.0
            env[86] = 0.5
            env[150] = 0.3
            env[215] = 0.4
            return env

        fake_librosa.load = fake_load
        fake_onset.onset_detect = fake_onset_detect
        fake_onset.onset_strength = fake_onset_strength
        fake_librosa.onset = fake_onset
        fake_librosa.frames_to_time = (
            lambda frames, sr=None: frames * 512 / (sr or 22050))

        monkeypatch.setitem(sys.modules, "librosa", fake_librosa)
        monkeypatch.setitem(sys.modules, "librosa.onset", fake_onset)

        times, strengths = detect_onsets_with_strength("/fake/path.wav")

        assert len(times) == len(strengths) == 4
        assert strengths == [10.0, 0.5, 0.3, 0.4]
