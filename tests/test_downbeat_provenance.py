"""Rung-1 finding 3: guessed downbeats must never travel as downbeats.

Ren's downbeat grid was every 4th librosa beat from beat 0 and nothing
said so - 4.04 snapped SFX to it, and on Sickick - Infected
(Instrumental) it sat one beat off the bar (downbeat F 0.065 against
beat_this, bass 177.7 on Ren's bar position against 567.7 one beat
away). The producer now carries `downbeat_source` ("detected" vs
"estimated"), the estimate is labelled everywhere it travels, and these
tests fail if a grid ever ships without its provenance again.
"""
import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.music_pipeline import _finalize_tempo


MUSIC_PIPELINE = os.path.join(PROJECT_ROOT, "library", "tools", "analysis",
                              "music_pipeline.py")


def _dict_keys(src):
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    out.add(key.value)
    return out


def test_the_producer_emits_downbeat_provenance():
    """`downbeat_source` is built everywhere a tempo dict is built."""
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        src = f.read()
    assert "downbeat_source" in _dict_keys(src), (
        "music_pipeline builds tempo dicts with no downbeat_source key - "
        "an estimated grid would again travel as bare downbeats")


def test_every_fourth_beat_guess_only_exists_beside_estimated():
    """The `range(0, len(beats), 4)` guess is the estimate - it must sit
    next to the word that labels it, not bare."""
    with open(MUSIC_PIPELINE, encoding="utf-8") as f:
        lines = f.read().splitlines()
    guess = [i for i, l in enumerate(lines) if "range(0, len(beats), 4)" in l]
    assert guess, "the every-4th-beat fallback moved - re-derive this test"
    for i in guess:
        window = "\n".join(lines[max(0, i - 6):i + 7])
        assert "estimated" in window, (
            f"music_pipeline.py:{i + 1} guesses downbeats as every 4th "
            f"beat with no 'estimated' label beside it")


def test_finalize_marks_detected_grids_detected():
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    downbeats = beats[::4]
    got = _finalize_tempo("beat-this-final0", beats, downbeats, "detected")
    assert got["downbeat_source"] == "detected"
    assert got["beats"] == beats
    assert got["downbeats"] == downbeats
    # Grid-derived BPM from the median interval, not the tracker's own
    # estimate (librosa's octave-errors).
    assert got["bpm"] == round(60.0 / 0.68, 1)


def test_finalize_marks_guessed_grids_estimated():
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    downbeats = [beats[i] for i in range(0, len(beats), 4)]
    got = _finalize_tempo("librosa-beat-track", beats, downbeats,
                          "estimated", "every 4th beat")
    assert got["downbeat_source"] == "estimated"
    assert "every 4th" in got["note"]


def test_the_estimate_reaches_the_model_labelled():
    """`measure_rhythm_track` surfaces the source and warns in
    `tempo_note` - the column the model reads beside the count."""
    import library.tools.analysis.music_pipeline as mp
    import library.tools.music_measurement as mm

    real_tempo, real_key = mp.analyze_tempo_beats, mp.analyze_key
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    mp.analyze_tempo_beats = lambda _path: {
        "method": "librosa-beat-track", "bpm": 88.2,
        "beats": beats, "downbeats": beats[::4],
        "downbeat_source": "estimated", "tempo_stable": True, "note": "",
    }
    mp.analyze_key = lambda _path: {"method": None, "key": None,
                                    "scale": None,
                                    "note": "essentia not installed"}
    try:
        got = mm.measure_rhythm_track("does-not-need-to-exist.wav")
    finally:
        mp.analyze_tempo_beats, mp.analyze_key = real_tempo, real_key
    assert got["tempo_downbeat_source"] == "estimated"
    assert got["tempo_downbeat_count"] == 4
    assert "estimated" in got["tempo_note"], (
        "an estimated grid reaches the model with a bare count and no "
        "warning in tempo_note")


def test_a_detected_grid_carries_no_estimate_warning():
    import library.tools.analysis.music_pipeline as mp
    import library.tools.music_measurement as mm

    real_tempo, real_key = mp.analyze_tempo_beats, mp.analyze_key
    beats = [round(0.5 + i * 0.68, 3) for i in range(16)]
    mp.analyze_tempo_beats = lambda _path: {
        "method": "beat-this-final0", "bpm": 88.2,
        "beats": beats, "downbeats": beats[::4],
        "downbeat_source": "detected", "tempo_stable": True, "note": "",
    }
    mp.analyze_key = lambda _path: {"method": None, "key": None,
                                    "scale": None,
                                    "note": "essentia not installed"}
    try:
        got = mm.measure_rhythm_track("does-not-need-to-exist.wav")
    finally:
        mp.analyze_tempo_beats, mp.analyze_key = real_tempo, real_key
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


def test_doctor_reports_the_beat_this_checkpoint():
    """Without this line a machine silently runs estimated downbeats -
    the same silent degradation finding 3 removed from the grid."""
    from ren import doctor

    names = [c.name for c in doctor.model_checks()]
    assert any("beat_this" in n for n in names), (
        "ren doctor reports no beat_this checkpoint line - its absence "
        "is invisible")


def test_doctor_names_the_fetch_when_the_checkpoint_is_missing(monkeypatch):
    found = _beat_this_check(monkeypatch, present=False)
    assert len(found) == 1
    check = found[0]
    assert check.ok, (
        "a missing checkpoint must not FAIL the doctor - the run still "
        "completes on the labelled estimate (MFA precedent)")
    assert "estimate" in check.detail
    assert "load_model" in check.fix and "final0" in check.fix, (
        "the fix must say how to fetch the checkpoint, not just that it "
        "is missing")
