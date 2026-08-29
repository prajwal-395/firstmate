"""The music step must be handed measurements, not filenames.

Step 2.04 chooses the bed under the whole video.  Until this module it saw
`title, audio_path, duration_seconds, source, duration_ok, duration_note`
and was asked to score candidates against the creative direction's
emotional landscape - nothing musical in the list at all.  The run of
record wrote it down: *"on the filenames alone the 'rise' track reads as
forbidden and Sickick reads as neutral - i.e. the context as supplied
points at the wrong answer."*  It got the right answer by shelling out to
ffmpeg; a model without a shell cannot.

These tests build their own audio with ffmpeg under `tmp_path` and check
the two halves that matter: that a flat bed and a bed that climbs 30 dB
come out DIFFERENT in the numbers the choice turns on, and that nothing
computed here is a taste label.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import music_measurement as mm  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# Every key `measure_track` is allowed to emit. A key outside this set is
# either a measurement nobody declared or - the failure this guards - a
# label somebody computed.
EMITTED_KEYS = {
    "measured", "measurement_note",
    "integrated_lufs", "loudness_range_lu", "true_peak_dbtp",
    "rms_spread_db", "window_seconds", "window_spread_db",
    "window_envelope_dbfs", "speech_band_ratio_db",
    # Which section of the track plays is the model's decision, and this
    # is what it decides from - one row per playable span, measured.
    "track_sections", "track_sections_note",
}

# The two emitted keys that are not a number or a curve of numbers.
SECTION_ROW_KEYS = {"start_seconds", "end_seconds", "mean_dbfs", "spread_db"}


# Six 15s steps spanning 30 dB: the fixture shape of a track that opens
# near silence and lands loud, which is what no single clip gain can sit
# under. On 001 this is the Sickick instrumental, -43 -> -13 dBFS by 30s.
CLIMBING_BED = [(15.0, v) for v in (0.002, 0.006, 0.02, 0.06, 0.12, 0.2)]


def _tone(path: Path, seconds: float, volume: float = 1.0,
          frequency: int = 220) -> Path:
    """A sine at `frequency` Hz, `seconds` long, at a constant `volume`."""
    return _steps(path, [(seconds, volume)], frequency=frequency)


def _steps(path: Path, segments, frequency: int = 220) -> Path:
    """`[(seconds, volume), ...]` concatenated into one file.

    Built as steps rather than with a `volume` expression because an
    ffmpeg filter argument splits on commas, so every `min(a, b)` in an
    expression has to be escaped - and a level that is stated per segment
    is a level the assertions can be written against.
    """
    inputs = []
    for seconds, volume in segments:
        inputs += ["-f", "lavfi", "-i",
                   f"sine=frequency={frequency}:duration={seconds}"]
    chain = "".join(
        f"[{i}:a]volume={volume}[v{i}];" for i, (_, volume) in enumerate(segments)
    )
    chain += "".join(f"[v{i}]" for i in range(len(segments)))
    chain += f"concat=n={len(segments)}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", chain, "-map", "[out]",
         "-ar", "48000", "-ac", "2", str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return path


# ── the numbers the decision actually turned on ──────────────────────

def test_a_flat_bed_and_a_climbing_bed_do_not_measure_the_same(tmp_path):
    """The whole point: two files, one flat, one climbing 30 dB.

    On 001 this is the difference between the piano (flat at -16 dBFS
    across the played window) and the Sickick instrumental (-43 -> -13 by
    30s). A 30 dB swing under a voice that sometimes whispers has no
    single clip gain that works, and that is the fact the filename could
    never carry.
    """
    flat = _tone(tmp_path / "flat.wav", 90.0, 0.2)
    # Six 15s steps climbing 30 dB, so the played window covers four.
    climb = _steps(tmp_path / "climb.wav", CLIMBING_BED)

    flat_m = mm.measure_track(str(flat), window_seconds=60.0)
    climb_m = mm.measure_track(str(climb), window_seconds=60.0)

    assert flat_m["measured"] is True
    assert climb_m["measured"] is True

    assert flat_m["window_spread_db"] < 1.0
    assert climb_m["window_spread_db"] > 20.0

    envelope = climb_m["window_envelope_dbfs"]
    assert len(envelope) == mm.ENVELOPE_BUCKETS
    assert envelope[0] < envelope[-1] - 20.0, envelope
    assert all(b >= a - 0.5 for a, b in zip(envelope, envelope[1:])), envelope
    assert flat_m["window_envelope_dbfs"][0] == pytest.approx(
        flat_m["window_envelope_dbfs"][-1], abs=0.5)


def test_loudness_and_range_come_off_the_file(tmp_path):
    loud = _tone(tmp_path / "loud.wav", 40.0, 0.5)
    quiet = _tone(tmp_path / "quiet.wav", 40.0, 0.05)

    loud_m = mm.measure_track(str(loud), window_seconds=30.0)
    quiet_m = mm.measure_track(str(quiet), window_seconds=30.0)

    assert loud_m["integrated_lufs"] > quiet_m["integrated_lufs"] + 15.0
    assert loud_m["true_peak_dbtp"] > quiet_m["true_peak_dbtp"] + 15.0
    # A steady tone has no loudness range worth the name.
    assert loud_m["loudness_range_lu"] < 2.0


def test_the_spread_is_not_max_minus_min(tmp_path):
    """A fade-out tail must not be read as the track's dynamic range.

    On 001's own files max-minus-min puts the spread at 67.4 dB and
    99.9 dB on tracks whose real swing is 11.5 and 15.5, because a fade
    tail and a run of digital silence are both in the min. The percentile
    read is what makes the number mean anything.
    """
    # A long flat track with a short fade-out tail 40 dB down. The tail
    # is 2.6% of the windows, which is what a real fade-out is - and what
    # makes max-minus-min report 40 dB of range that is not there.
    faded = _steps(tmp_path / "faded.wav",
                   [(150.0, 0.2), (2.0, 0.02), (2.0, 0.002)])
    measured = mm.measure_track(str(faded), window_seconds=60.0)

    windows = mm.rms_windows(str(faded))
    audible = [w for w in windows if w > mm.SILENCE_FLOOR_DBFS]
    naive = max(audible) - min(audible)

    assert measured["rms_spread_db"] < naive - 10.0, (
        measured["rms_spread_db"], naive)


def test_the_speech_band_ratio_separates_a_bass_bed_from_a_midrange_one(
        tmp_path):
    """A bed sitting in the voice's band is not the same as one below it."""
    low = _tone(tmp_path / "low.wav", 30.0, 0.3, frequency=80)
    mid = _tone(tmp_path / "mid.wav", 30.0, 0.3, frequency=1000)

    low_m = mm.measure_track(str(low), window_seconds=30.0)
    mid_m = mm.measure_track(str(mid), window_seconds=30.0)

    assert mid_m["speech_band_ratio_db"] > low_m["speech_band_ratio_db"] + 10.0


# ── measure, never classify ──────────────────────────────────────────

def test_nothing_emitted_is_a_taste_label(tmp_path):
    """AGENTS.md 10.5: the pipeline never invents a creative judgement.

    A computed 'uplifting' would be exactly the fabrication this table
    exists to remove, so the emitted keys are enumerated and every value
    is a number, a list of numbers, a bool, or a stated reason.
    """
    track = _tone(tmp_path / "t.wav", 30.0, 0.2)
    measured = mm.measure_track(str(track), window_seconds=20.0)

    assert set(measured) <= EMITTED_KEYS, set(measured) - EMITTED_KEYS
    for key, value in measured.items():
        if key in ("measured", "measurement_note", "track_sections_note"):
            continue
        if key == "track_sections":
            for row in value:
                assert set(row) == SECTION_ROW_KEYS, row
                assert row["mean_dbfs"] is None or isinstance(
                    row["mean_dbfs"], (int, float))
            continue
        if isinstance(value, list):
            assert all(v is None or isinstance(v, (int, float)) for v in value)
        else:
            assert isinstance(value, (int, float)), (key, value)

    for declined in ("bpm", "key", "musical_key", "genre", "mood", "energy",
                     "instrumentation", "recommendation", "rank", "score"):
        assert declined not in measured


def test_the_legend_defines_every_key_that_is_emitted(tmp_path):
    """A column whose units are unstated is not a measurement anyone can use.

    Step 2.04's handoff.md is under a captain freeze and cannot name the
    new columns, so the definitions ship beside the numbers. A key with no
    legend entry reaches the model as a bare float.
    """
    track = _tone(tmp_path / "t.wav", 30.0, 0.2)
    measured = mm.measure_track(str(track), window_seconds=20.0)

    for key in measured:
        assert key in mm.MEASUREMENT_LEGEND, key
    assert set(mm.MEASUREMENT_LEGEND) == EMITTED_KEYS


def test_declined_measurements_records_why_each_one_is_out():
    assert "bpm" in mm.DECLINED_MEASUREMENTS
    for name, reason in mm.DECLINED_MEASUREMENTS.items():
        assert len(reason) > 40, name


# ── an absent measurement is stated, never defaulted ─────────────────

def test_a_candidate_out_on_duration_is_not_opened_and_says_so():
    """AGENTS.md 10.3: a blank column and a deliberate omission read the
    same, and only one of them is honest."""
    candidates = [
        {"title": "compilation", "audio_path": "/nonexistent/huge.wav",
         "duration_seconds": 11386.88, "duration_ok": False,
         "duration_note": "TOO LONG"},
    ]
    out = mm.measure_candidates(candidates, window_seconds=60.0)

    assert mm.should_measure(candidates[0]) is False
    assert out[0]["measured"] is False
    assert "not measured" in out[0]["measurement_note"]
    assert "integrated_lufs" not in out[0]
    # The input list is not mutated.
    assert "measured" not in candidates[0]


def test_a_file_with_no_level_is_reported_unmeasured_not_zero(tmp_path):
    silent = tmp_path / "silent.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", "10", str(silent)],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    measured = mm.measure_track(str(silent), window_seconds=10.0)

    assert measured["measured"] is False
    assert "integrated_lufs" not in measured
    assert "window" in measured["measurement_note"]


def test_an_unreadable_file_is_reported_unmeasured(tmp_path):
    bogus = tmp_path / "not-audio.wav"
    bogus.write_bytes(b"this is not a wave file")
    measured = mm.measure_track(str(bogus), window_seconds=60.0)

    assert measured["measured"] is False
    assert "integrated_lufs" not in measured


# ── the bridge really ships it ───────────────────────────────────────

def test_the_bridge_emits_the_measurements_and_the_legend(tmp_path,
                                                          monkeypatch):
    """Drive the real bridge against a project built under tmp_path.

    Nothing here reaches a real project: the music library and the project
    folder are both temporary (AGENTS.md 8, "No test reaches a real
    project").
    """
    library = tmp_path / "shared_music"
    library.mkdir()
    _tone(library / "steady bed.wav", 90.0, 0.2)

    project = tmp_path / "proj"
    (project / "music").mkdir(parents=True)
    _steps(project / "music" / "climbing bed.wav", CLIMBING_BED)
    _tone(project / "music" / "too short.wav", 2.0, 0.2)
    (project / "project.yaml").write_text(
        "name: fixture\ntarget_duration_seconds: 60\n", encoding="utf-8")

    monkeypatch.setenv("PIPELINE_MUSIC_LIBRARY", str(library))

    bridge = REPO / "library/steps/step_2_04_music_selection/bridge.py"
    proc = subprocess.run(
        [sys.executable, str(bridge)],
        input=json.dumps({"project_folder": str(project)}),
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    catalogue = json.loads(proc.stdout)["music_candidates"]

    assert set(catalogue["measurement_legend"]) == EMITTED_KEYS

    by_title = {c["title"]: c for c in catalogue["candidates"]}
    assert by_title["steady bed"]["measured"] is True
    assert by_title["climbing bed"]["measured"] is True
    assert by_title["too short"]["measured"] is False

    assert by_title["steady bed"]["window_spread_db"] < 1.0
    assert by_title["climbing bed"]["window_spread_db"] > 20.0
    assert by_title["climbing bed"]["window_seconds"] == 60.0
