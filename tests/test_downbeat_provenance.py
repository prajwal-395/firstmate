"""Rung-1 finding 3: guessed downbeats must never travel as downbeats.

Ren's downbeat grid was every 4th librosa beat from beat 0 and nothing
said so - 4.04 snapped SFX to it, and on Sickick - Infected
(Instrumental) it sat one beat off the bar (downbeat F 0.065 against
beat_this, bass 177.7 on Ren's bar position against 567.7 one beat
away). The producer now carries `downbeat_source` ("detected" vs
"estimated"), the estimate is labelled everywhere it travels, and these
tests fail if a grid ever ships without its provenance again.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.music_pipeline import _finalize_tempo


def test_finalize_labels_the_grid_detected_or_estimated():
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    downbeats = beats[::4]
    got = _finalize_tempo("beat-this-final0", beats, downbeats, "detected")
    assert got["downbeat_source"] == "detected"
    assert got["beats"] == beats
    assert got["downbeats"] == downbeats
    # Grid-derived BPM from the median interval, not the tracker's own
    # estimate (librosa's octave-errors).
    assert got["bpm"] == round(60.0 / 0.68, 1)

    # The every-4th-beat guess is labelled estimated.
    downbeats = [beats[i] for i in range(0, len(beats), 4)]
    got = _finalize_tempo("librosa-beat-track", beats, downbeats,
                          "estimated", "every 4th beat")
    assert got["downbeat_source"] == "estimated"
    assert "every 4th" in got["note"]


def _rhythm_with(source):
    import library.tools.analysis.music_pipeline as mp
    import library.tools.music_measurement as mm

    real_tempo, real_key = mp.analyze_tempo_beats, mp.analyze_key
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    mp.analyze_tempo_beats = lambda _path: {
        "method": "librosa-beat-track", "bpm": 88.2,
        "beats": beats, "downbeats": beats[::4],
        "downbeat_source": source, "tempo_stable": True, "note": "",
    }
    mp.analyze_key = lambda _path: {"method": None, "key": None,
                                    "scale": None,
                                    "note": "essentia not installed"}
    try:
        return mm.measure_rhythm_track("does-not-need-to-exist.wav")
    finally:
        mp.analyze_tempo_beats, mp.analyze_key = real_tempo, real_key


def test_the_estimate_reaches_the_model_labelled():
    """`measure_rhythm_track` surfaces the source and warns in
    `tempo_note` - the column the model reads beside the count - and a
    detected grid carries no such warning."""
    got = _rhythm_with("estimated")
    assert got["tempo_downbeat_source"] == "estimated"
    assert got["tempo_downbeat_count"] == 4
    assert "estimated" in got["tempo_note"], (
        "an estimated grid reaches the model with a bare count and no "
        "warning in tempo_note")
    got = _rhythm_with("detected")
    assert got["tempo_downbeat_source"] == "detected"
    assert got["tempo_note"] == ""


# ─────────────────────────────────────────────────────────
# `ren doctor` reports the beat_this checkpoint
# ─────────────────────────────────────────────────────────

def _beat_this_check(monkeypatch, present: bool):
    """The doctor's beat_this line with the checkpoint present/absent."""
    from pathlib import Path

    from ren import doctor

    if present:
        return [c for c in doctor.model_checks() if "beat_this" in c.name]
    monkeypatch.setattr(doctor, "torch_checkpoints",
                        lambda: Path("/nonexistent-torch-hub"))
    try:
        return [c for c in doctor.model_checks() if "beat_this" in c.name]
    finally:
        monkeypatch.undo()


def test_doctor_names_the_fetch_when_the_checkpoint_is_missing(monkeypatch):
    """Without the beat_this line a machine silently runs estimated
    downbeats; missing, the line degrades music.analyse and names the
    fetch, without failing the doctor."""
    found = _beat_this_check(monkeypatch, present=False)
    assert len(found) == 1
    check = found[0]
    from ren import doctor
    assert not check.ok and doctor.required_failures([check]) == [], (
        "a missing checkpoint must not FAIL the doctor - the run still "
        "completes on the labelled estimate - it only degrades music.analyse")
    assert "model.beat_this" in doctor.capability_report(
        [check])["music.analyse"][2]
    assert "estimate" in check.detail
    assert "load_model" in check.fix and "final0" in check.fix, (
        "the fix must say how to fetch the checkpoint, not just that it "
        "is missing")
