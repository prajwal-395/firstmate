"""Every music candidate reaches the choice carrying rhythm and harmony.

Step 2.04 chose the bed on loudness alone: bpm and key are in the model's
output schema, but step 2.06 measures them DOWNSTREAM of the choice, so on
the 29 Aug run the model correctly answered null for both.  A track was
chosen with no access to its rhythm or its harmony.

Captain's decision 2026-09-07, option (a): compute tempo, key and
beat-grid PER CANDIDATE in the 2.04 bridge, by the SAME code 2.06 uses
(`analyze_tempo_beats` / `analyze_key`), so every candidate carries them
AT CHOICE TIME.  Step 2.06 itself does not move.

STRICT SCOPE ON TASTE: these tests also assert the new columns are
MEASUREMENTS, not preferences - no threshold, no BPM range, no key and no
ranking on the new fields.  Taste belongs to the model, never to a
hardcoded value.
"""
import json
import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import music_measurement as mm  # noqa: E402
from library.tools.context_projector import project_fields  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# The rhythm columns a measured candidate must carry.  Scalars reach the
# model; beat_grid is stored for code and dropped from the prompt (the
# manifest's `-` path, AGENTS.md 10.1).
RHYTHM_SCALARS = {
    "tempo_bpm", "tempo_method", "tempo_beat_count",
    "tempo_downbeat_count", "tempo_stable", "tempo_note",
    "musical_key", "key_method", "key_strength", "key_note",
}

# Words that would mean the bridge had started choosing instead of
# measuring.  A candidate carrying any of these is a ranking, and the
# captain ruled taste out of this change.
TASTE_KEYS = {
    "rank", "score", "preferred", "preferred_bpm", "recommendation",
    "verdict", "preferred_key", "tempo_ok", "key_ok", "suitable",
    "bpm_match", "fits_brief",
}


def _clicks(path: Path, bpm: float, seconds: float) -> Path:
    """A metronome: a decaying 880 Hz click every beat, written stdlib-only.

    librosa's beat tracker locks onto onsets, so the fixture needs real
    ones - a steady sine has none and correctly measures as no grid.
    """
    rate = 48000
    beat = 60.0 / bpm
    total = int(rate * seconds)
    frames = bytearray()
    for i in range(total):
        t = i / rate
        frac = (t / beat) % 1.0
        env = math.exp(-frac * 40.0)
        value = math.sin(2 * math.pi * 880.0 * t) * env * 0.5
        frames += struct.pack(
            "<h", int(max(-1.0, min(1.0, value)) * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def test_rhythm_uses_the_same_code_2_06_runs(tmp_path):
    """The measurement is 2.06's, called per candidate - not a duplicate."""
    import library.tools.analysis.music_pipeline as pipeline

    assert callable(pipeline.analyze_tempo_beats)
    assert callable(pipeline.analyze_key)

    track = _clicks(tmp_path / "clicks.wav", 120.0, 60.0)
    rhythm = mm.measure_rhythm_track(str(track))

    assert rhythm["tempo_method"] == "librosa-beat-track"
    assert rhythm["tempo_bpm"] == pytest.approx(120.0, abs=6.0)
    assert rhythm["tempo_beat_count"] > 8
    assert len(rhythm["beat_grid"]["beats"]) == rhythm["tempo_beat_count"]
    assert len(rhythm["beat_grid"]["downbeats"]) == \
        rhythm["tempo_downbeat_count"]


def test_a_track_with_no_onsets_states_no_tempo_rather_than_zero(tmp_path):
    """AGENTS.md 10.3: an absent measurement is stated, never defaulted."""
    track = tmp_path / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=220:duration=30",
         "-ar", "48000", "-ac", "2", str(track)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    rhythm = mm.measure_rhythm_track(str(track))

    assert rhythm["tempo_bpm"] is None
    assert "no usable grid" in rhythm["tempo_note"]
    assert rhythm["musical_key"] is None
    assert rhythm["key_note"] != ""


def test_the_bridge_carries_tempo_key_and_grid_on_every_candidate(
        tmp_path, monkeypatch):
    """The regression test: drive the real 2.04 bridge over real audio.

    Two metronomes at different tempi must come out DISTINGUISHABLE in
    rhythm - that is the whole point: the choice can now hear the
    difference between 120 and 90 without listening.
    """
    library = tmp_path / "shared_music"
    library.mkdir()
    _clicks(library / "fast bed.wav", 120.0, 70.0)

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    _clicks(project / "music" / "slow bed.wav", 90.0, 70.0)
    (project / "project.yaml").write_text(
        "name: fixture\ntarget_duration_seconds: 60\n", encoding="utf-8")

    monkeypatch.setenv("PIPELINE_MUSIC_LIBRARY", str(library))

    bridge = REPO / "library/steps/step_2_04_music_selection/bridge.py"
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({"project_folder": str(project)}),
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        # check=False: the returncode is asserted below with the bridge's
        # own stderr as the message, which reads better than CalledProcessError.
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]

    for key in RHYTHM_SCALARS | {"beat_grid"}:
        assert key in catalogue["measurement_legend"], key

    by_title = {c["title"]: c for c in catalogue["candidates"]}
    fast, slow = by_title["fast bed"], by_title["slow bed"]

    for candidate in (fast, slow):
        assert candidate["measured"] is True
        for key in RHYTHM_SCALARS:
            assert key in candidate, key
        assert candidate["tempo_bpm"] is not None
        assert candidate["tempo_beat_count"] > 8
        assert len(candidate["beat_grid"]["beats"]) > 8
        # Key travels as a value where essentia answers and as a stated
        # absence where it is not installed - never as a missing column.
        assert "musical_key" in candidate
        if candidate["musical_key"] is None:
            assert candidate["key_note"] != ""

    assert fast["tempo_bpm"] == pytest.approx(120.0, abs=6.0)
    assert slow["tempo_bpm"] == pytest.approx(90.0, abs=6.0)
    assert abs(fast["tempo_bpm"] - slow["tempo_bpm"]) > 15.0


def test_rhythm_adds_measurements_not_preferences(tmp_path, monkeypatch):
    """No threshold, no BPM range, no key and no ranking on the new fields."""
    library = tmp_path / "shared_music"
    library.mkdir()
    _clicks(library / "fast bed.wav", 120.0, 70.0)

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    _clicks(project / "music" / "slow bed.wav", 90.0, 70.0)
    (project / "project.yaml").write_text(
        "name: fixture\ntarget_duration_seconds: 60\n", encoding="utf-8")

    monkeypatch.setenv("PIPELINE_MUSIC_LIBRARY", str(library))

    bridge = REPO / "library/steps/step_2_04_music_selection/bridge.py"
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({"project_folder": str(project)}),
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        # check=False: the returncode is asserted below with the bridge's
        # own stderr as the message, which reads better than CalledProcessError.
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]

    titles = sorted(c["title"] for c in catalogue["candidates"])
    assert titles == ["fast bed", "slow bed"]
    for candidate in catalogue["candidates"]:
        assert not (set(candidate) & TASTE_KEYS), set(candidate) & TASTE_KEYS
        # Both tempi survive side by side: nothing filtered the 90.
        assert candidate["measured"] is True


def test_a_candidate_out_on_duration_carries_no_rhythm_keys():
    """The rhythm pass costs seconds a track: unmeasured stays unmeasured."""
    out = mm.measure_rhythm_candidates([{
        "title": "compilation", "audio_path": "/nonexistent/huge.wav",
        "duration_seconds": 11386.88, "duration_ok": False,
        "duration_note": "TOO LONG",
        "measured": False,
        "measurement_note": "not measured: this candidate is already out",
    }])

    assert out[0]["measured"] is False
    assert not (set(out[0]) & RHYTHM_SCALARS), set(out[0]) & RHYTHM_SCALARS
    assert "beat_grid" not in out[0]


def test_the_full_grid_never_reaches_the_prompt():
    """AGENTS.md 10.1: no raw value list reaches a prompt.

    The candidate carries the grid for code; this step's own manifest
    drops it before the model sees the table, and the tempo_* scalars -
    the choice-time reading of rhythm - survive the drop.
    """
    manifest = json.loads(
        (REPO / "library/steps/step_2_04_music_selection"
         / "manifest.json").read_text(encoding="utf-8"))
    fields = manifest["context_fields"]
    assert "-music_candidates.candidates.*.beat_grid" in fields

    catalogue = {"music_candidates": {"candidates": [{
        "title": "fast bed", "tempo_bpm": 117.5,
        "beat_grid": {"beats": [1.0, 1.5, 2.0], "downbeats": [1.0]},
    }]}}
    projected = project_fields(catalogue, fields)
    candidate = projected["music_candidates"]["candidates"][0]

    assert "beat_grid" not in candidate
    assert candidate["tempo_bpm"] == 117.5
