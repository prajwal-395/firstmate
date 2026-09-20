"""Every music candidate reaches the choice carrying song-structure labels.

Step 2.04 names which section of the track plays (`section.source_in`,
`splices`), and until now it named it with no notion of where the chorus
is: step 2.06 labels the CHOSEN track's bridge/chorus/verse downstream of
the choice. The 2.06 output cannot reach this prompt - 2.04 runs BEFORE
it (DAG: creative_direction -> music_selection -> music_analysis), and an
edge back would be a cycle - so the measurement moves to choice time the
way tempo and key did (captain's decision 2026-09-07, option (a)): the
SAME function 2.06 uses (`analyze_structure`), per candidate, labels in
FILE seconds, which is the clock `section.source_in` and `splices` are
named in. Step 2.06 itself does not move.

STRICT SCOPE ON TASTE: these tests also assert the new columns are
MEASUREMENTS, not preferences - no ranked, preferred or defaulted label,
no "prefer the chorus" (captain's ruling 2026-09-16; AGENTS.md 10.5).
Taste belongs to the model, never to a hardcoded value.
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

# The structure columns a measured candidate must carry. Labels reach the
# model (they are the choice-time reading the section decision is made
# from); nothing is stored for code, so there is no third key.
STRUCTURE_KEYS = {"song_structure", "song_structure_note"}

# The vocabulary `analyze_structure` labels with. A label outside it is
# a producer change, and the prompt legend would be describing names the
# model never sees.
STRUCTURE_TYPES = {
    "intro", "verse", "chorus", "bridge", "outro", "build", "full",
}

# Words that would mean the bridge had started choosing instead of
# measuring. A candidate carrying any of these is a ranking, and the
# captain ruled taste out of this change.
TASTE_KEYS = {
    "rank", "score", "preferred", "preferred_section",
    "recommendation", "verdict", "prefer_chorus", "avoid_intro",
    "chorus_ok", "best_section", "suitable", "fits_brief",
}


def _two_part(path: Path, seconds: float = 70.0) -> Path:
    """Quiet first half, loud second half, written stdlib-only.

    A 220 Hz sine at two amplitudes: the self-similarity novelty curve
    sees one boundary where the track audibly changes, and the
    energy-profile classifier reads the quiet opening as an intro and
    the loud back half as a chorus.
    """
    rate = 48000
    total = int(rate * seconds)
    frames = bytearray()
    for i in range(total):
        t = i / rate
        amplitude = 0.02 if t < seconds / 2 else 0.4
        value = math.sin(2 * math.pi * 220.0 * t) * amplitude
        frames += struct.pack(
            "<h", int(max(-1.0, min(1.0, value)) * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def _flat(path: Path, seconds: float = 70.0) -> Path:
    """One unchanging tone: no boundary for the novelty curve to find."""
    rate = 48000
    total = int(rate * seconds)
    frames = bytearray()
    for i in range(total):
        t = i / rate
        value = math.sin(2 * math.pi * 220.0 * t) * 0.2
        frames += struct.pack(
            "<h", int(max(-1.0, min(1.0, value)) * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return path


def _spans_cover(duration: float, rows: list) -> None:
    """Ascending, non-overlapping spans covering the file, in file seconds."""
    assert rows, "no structure labels"
    assert rows[0]["start"] == pytest.approx(0.0, abs=1.5)
    assert rows[-1]["end"] == pytest.approx(duration, abs=1.5)
    for first, second in zip(rows, rows[1:]):
        assert set(first) == {"type", "start", "end"}, first
        assert first["type"] in STRUCTURE_TYPES, first["type"]
        assert first["start"] < first["end"]
        assert second["start"] >= first["start"]
        assert second["start"] == pytest.approx(first["end"], abs=1.5)


def test_structure_uses_the_same_code_2_06_runs(tmp_path):
    """The measurement is 2.06's, called per candidate - not a duplicate."""
    import library.tools.analysis.music_pipeline as pipeline

    assert callable(pipeline.analyze_structure)

    track = _two_part(tmp_path / "parts.wav")
    measured = mm.measure_structure_track(str(track))

    assert set(measured) == STRUCTURE_KEYS, set(measured)
    rows = measured["song_structure"]
    _spans_cover(70.0, rows)
    assert measured["song_structure_note"] == ""
    # The quiet opening reads as an intro and the loud back half as a
    # chorus: the labels the section decision is made from.
    assert rows[0]["type"] == "intro"
    assert "chorus" in {row["type"] for row in rows}


def test_a_flat_track_states_spans_rather_than_silence(tmp_path):
    """No boundary is still a measurement: spans, not an empty claim."""
    track = _flat(tmp_path / "flat.wav")
    measured = mm.measure_structure_track(str(track))

    assert measured["song_structure_note"] == ""
    _spans_cover(70.0, measured["song_structure"])


def test_an_unreadable_file_states_absence_rather_than_no_parts(tmp_path):
    """AGENTS.md 10.3: an absent measurement is stated, never defaulted."""
    measured = mm.measure_structure_track(
        str(tmp_path / "missing.wav"))

    assert measured["song_structure"] == []
    assert measured["song_structure_note"] != ""


def test_the_bridge_carries_structure_on_every_candidate(
        tmp_path, monkeypatch):
    """The regression test: drive the real 2.04 bridge over real audio.

    A two-part track and a flat one must come out DISTINGUISHABLE in
    structure - that is the whole point: the choice can now see where
    the chorus is without listening.
    """
    library = tmp_path / "shared_music"
    library.mkdir()
    _two_part(library / "shaped bed.wav")

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    _flat(project / "music" / "flat bed.wav")
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

    for key in STRUCTURE_KEYS:
        assert key in mm.MEASURED_KEYS, key

    by_title = {c["title"]: c for c in catalogue["candidates"]}
    shaped, flat = by_title["shaped bed"], by_title["flat bed"]

    for candidate in (shaped, flat):
        assert candidate["measured"] is True
        for row in candidate["song_structure"]:
            assert set(row) == {"type", "start", "end"}, row
            assert row["type"] in STRUCTURE_TYPES, row["type"]
        assert candidate["song_structure_note"] == "" or \
            candidate["song_structure"] == []

    shaped_types = {row["type"] for row in shaped["song_structure"]}
    flat_types = {row["type"] for row in flat["song_structure"]}
    assert "chorus" in shaped_types
    assert shaped_types != flat_types or \
        len(shaped["song_structure"]) != len(flat["song_structure"])


def test_structure_adds_measurements_not_preferences(tmp_path, monkeypatch):
    """No ranked, preferred or defaulted label on the new fields."""
    library = tmp_path / "shared_music"
    library.mkdir()
    _two_part(library / "shaped bed.wav")

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    _flat(project / "music" / "flat bed.wav")
    (project / "project.yaml").write_text(
        "name: fixture\ntarget_duration_seconds: 60\n", encoding="utf-8")

    monkeypatch.setenv("PIPELINE_MUSIC_LIBRARY", str(library))

    bridge = REPO / "library/steps/step_2_04_music_selection/bridge.py"
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({"project_folder": str(project)}),
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]

    titles = sorted(c["title"] for c in catalogue["candidates"])
    assert titles == ["flat bed", "shaped bed"]
    for candidate in catalogue["candidates"]:
        assert not (set(candidate) & TASTE_KEYS), set(candidate) & TASTE_KEYS
        # Both tracks survive side by side: nothing filtered the flat one
        # for having no chorus.
        assert candidate["measured"] is True


def test_a_candidate_out_on_duration_carries_no_structure_keys():
    """The structure pass costs seconds a track: unmeasured stays unmeasured."""
    out = mm.measure_structure_candidates([{
        "title": "compilation", "audio_path": "/nonexistent/huge.wav",
        "duration_seconds": 11386.88, "duration_ok": False,
        "duration_note": "TOO LONG",
        "measured": False,
        "measurement_note": "not measured: this candidate is already out",
    }])

    assert out[0]["measured"] is False
    assert not (set(out[0]) & STRUCTURE_KEYS), set(out[0]) & STRUCTURE_KEYS


def test_structure_reaches_the_prompt_while_the_grid_does_not():
    """The claim this change exists for: labels arrive in the 2.04 context.

    `song_structure` is a handful of labelled spans, not a raw value
    list, so - unlike `beat_grid` - this step's manifest keeps it on the
    candidate the model names a section from.
    """
    manifest = json.loads(
        (REPO / "library/steps/step_2_04_music_selection"
         / "manifest.json").read_text(encoding="utf-8"))
    fields = manifest["context_fields"]
    assert "-music_candidates.candidates.*.beat_grid" in fields
    assert not any("song_structure" in field for field in fields
                   if field.startswith("-")), (
        "song_structure must survive projection: it is the input the "
        "section decision reads")

    catalogue = {"music_candidates": {"candidates": [{
        "title": "shaped bed",
        "song_structure": [
            {"type": "intro", "start": 0.0, "end": 35.0},
            {"type": "chorus", "start": 35.0, "end": 70.0},
        ],
        "song_structure_note": "",
        "beat_grid": {"beats": [1.0, 1.5], "downbeats": [1.0]},
    }]}, "creative_direction": {"target_mood": "x"}}
    projected = project_fields(catalogue, fields)
    candidate = projected["music_candidates"]["candidates"][0]

    assert candidate["song_structure"] == [
        {"type": "intro", "start": 0.0, "end": 35.0},
        {"type": "chorus", "start": 35.0, "end": 70.0},
    ]
    assert "beat_grid" not in candidate


def test_the_handoff_defines_the_labels_it_hands_over():
    """A column whose units are unstated is not a measurement anyone can use."""
    handoff = (REPO / "library/steps/step_2_04_music_selection"
               / "handoff.md").read_text(encoding="utf-8")
    assert "`song_structure`" in handoff
    assert "`song_structure_note`" in handoff
    # The legend states what a label IS and refuses the conclusion.
    lowered = handoff.lower()
    assert "energy-profile" in lowered or "energy profile" in lowered
