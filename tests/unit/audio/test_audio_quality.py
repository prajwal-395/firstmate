"""FI-12 acceptance: calibrated synthetic audio produces stable measurements."""
from __future__ import annotations

import wave

import numpy as np
import pytest

from library.tools import audio_quality, source_memory

RATE = 48_000


def _write_stereo_fixture(path):
    seconds = np.arange(RATE * 2, dtype=np.float64) / RATE
    left = np.zeros(RATE * 2, dtype=np.float64)
    right = np.zeros(RATE * 2, dtype=np.float64)
    left[:RATE] = 0.25 * np.sin(2 * np.pi * 440 * seconds[:RATE])
    right[:RATE] = 0.50 * np.sin(2 * np.pi * 220 * seconds[:RATE])
    # Deterministic room-noise segment on channel two, at a known amplitude.
    rng = np.random.default_rng(12012)
    right[RATE:] = rng.uniform(-0.01, 0.01, RATE)
    interleaved = np.column_stack([left, right])
    pcm = np.rint(interleaved * 32768).clip(-32768, 32767).astype("<i2")
    pcm[RATE * 2 - 100, 0] = 32767
    pcm[RATE * 2 - 99, 0] = -32768
    pcm[RATE * 2 - 98, 0] = -32767
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())


def test_known_signal_fixture_measures_units_clipping_noise_and_silence(
        tmp_path):
    source = tmp_path / "fi12-known-signals.wav"
    memory_root = tmp_path / "memory"
    _write_stereo_fixture(source)

    result = audio_quality.measure_audio_quality(str(source), root=memory_root)

    assert result["channel_count"] == 2
    left, right = result["channels"]
    assert left["integrated_lufs"] == pytest.approx(-15.5, abs=1.5)
    assert right["integrated_lufs"] == pytest.approx(-9.5, abs=1.5)
    assert right["true_peak_dbtp"] == pytest.approx(-6.0, abs=1.5)
    assert left["clipping"]["clipped_sample_count"] == 3
    assert left["clipping"]["sample_count"] == RATE * 2
    assert left["clipping"]["clipped_fraction"] == pytest.approx(3 / (RATE * 2))
    assert left["intervals"][0]["spectral_centroid_hz"] == pytest.approx(440, abs=2)
    assert right["intervals"][0]["spectral_centroid_hz"] == pytest.approx(220, abs=2)
    assert right["noise_floor_dbfs"] == pytest.approx(-44.7, abs=2.0)
    assert right["snr_db"] == pytest.approx(35.7, abs=3.0)
    assert left["intervals"][0]["tonal_peak_hz"] == pytest.approx(440, abs=2)
    assert left["intervals"][0]["speech_band_energy_fraction"] == pytest.approx(1.0, abs=0.02)
    assert left["intelligibility_proxies"]["interpretation"].startswith("frequency coverage proxy")
    assert left["silence_intervals"]
    assert left["silence_intervals"][0]["start_seconds"] == pytest.approx(1.0)
    assert result["method"].startswith("ffmpeg-pcm-s16le/48kHz")

    # The source memory slot persists once, by content identity, and returns
    # the same measurement rather than silently recomputing it.
    stored = source_memory.read_audio_quality(result["content_digest"], memory_root)
    assert {key: value for key, value in stored.items()
            if key != "evidence_envelope"} == result
    source_memory.validate_evidence_envelope(stored["evidence_envelope"])
    reused = audio_quality.measure_audio_quality(str(source), root=memory_root)
    assert reused == result

    # A digest match alone cannot return a pre-v2 record that lacks the new
    # interval proxies; method-version mismatch forces one fresh measurement.
    stored["method"] = audio_quality.METHOD.replace("FI-12-v2", "FI-12-v1")
    source_memory.write_audio_quality(result["content_digest"], stored, memory_root)
    upgraded = audio_quality.measure_audio_quality(str(source), root=memory_root)
    assert upgraded["method"] == audio_quality.METHOD
    assert "tonal_peak_hz" in upgraded["channels"][0]["intervals"][0]


def test_audio_quality_slot_refuses_digest_mismatch(tmp_path):
    with pytest.raises(ValueError, match="digest does not match"):
        source_memory.write_audio_quality("expected", {"content_digest": "other"},
                                          tmp_path)


# ── Bringing the lane into a run ───────────────────────────────────
#
# The lane was measured out of band, so a pipeline run that never called
# it left EVERY audio reading absent - and the technical leg's
# `usable_speech` indicator absent with it, on every candidate the
# selection offered. The two tests below name that defect: a source the
# run has never measured must get its reading inside the run, and a source
# that CANNOT be measured must be reported with its reason rather than
# left to read as a zero.


def _write_tone(path, seconds=2.0, hz=440.0):
    """A mono tone in the speech band, so the band fraction is a number."""
    count = int(RATE * seconds)
    times = np.arange(count, dtype=np.float64) / RATE
    tone = 0.25 * np.sin(2 * np.pi * hz * times)
    pcm = np.rint(tone * 32768).clip(-32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())
    return path


def test_a_source_the_run_never_measured_gets_its_reading_inside_the_run(
        tmp_path):
    """The reading a run never took must be taken by the run that needs it.

    Before this, the only way an audio reading existed was a human
    calling the lane by hand; every pipeline run inherited an absent
    `usable_speech` indicator for every candidate.
    """
    source = _write_tone(tmp_path / "spoken.wav")
    memory_root = tmp_path / "memory"

    outcome = audio_quality.ensure_measured(str(source), root=memory_root)

    assert outcome["status"] == "measured"
    assert outcome["record"]["channel_count"] == 1
    band = outcome["record"]["channels"][0]["intervals"][0][
        "speech_band_energy_fraction"]
    assert band == pytest.approx(1.0, abs=0.05)

    # The reading is stored, so the next call does not measure it again:
    # the lane is asked once per source, not once per run.
    again = audio_quality.ensure_measured(str(source), root=memory_root)
    assert again["status"] == "already_measured"
    assert again["record"]["content_digest"] == (
        outcome["record"]["content_digest"])


def test_a_source_with_no_audio_is_reported_and_stays_absent(tmp_path):
    """A source nothing can be heard in has NO reading, and says why.

    A zero would read as a measured value - a source with no speech band
    energy is not a source whose speech band carries no energy.
    """
    silent_video = tmp_path / "no_audio.mov"
    import subprocess

    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
         "-c:v", "libx264", "-y", str(silent_video)],
        check=False, capture_output=True)
    if not silent_video.exists():
        pytest.skip("ffmpeg did not write the fixture")
    memory_root = tmp_path / "memory"

    outcome = audio_quality.ensure_measured(str(silent_video),
                                            root=memory_root)

    assert outcome["status"] == "unmeasurable"
    assert outcome["record"] is None
    assert outcome["reason"]
    assert audio_quality.stored_record(str(silent_video),
                                        root=memory_root) is None

    report = audio_quality.ensure_readings(
        [{"clip_id": "clip_001", "path": str(silent_video)}],
        root=memory_root)
    assert report["measured"] == []
    assert report["unmeasurable"]["clip_001"]
    assert report["already_measured"] == []


def test_one_run_measures_a_bounded_number_of_sources_and_says_which(
        tmp_path):
    """A catalog of many candidates cannot spend an hour decoding audio.

    The sources that WERE measured are stored, so the next run continues
    with the ones this one did not reach - and the report names the ones
    it did not, rather than dropping them silently.
    """
    memory_root = tmp_path / "memory"
    clips = []
    for index in range(3):
        # A different tone per file: the reading is filed under the
        # source's CONTENT, so three identical files would be one source.
        source = _write_tone(tmp_path / f"tone_{index}.wav",
                             hz=440.0 + index * 110.0)
        clips.append({"clip_id": f"clip_{index}", "path": str(source)})

    first = audio_quality.ensure_readings(clips, root=memory_root,
                                          max_sources=2)

    assert first["measured"] == ["clip_0", "clip_1"]
    assert list(first["not_reached"]) == ["clip_2"]
    assert first["sources"] == 3

    # The next run picks up where this one stopped: the two it measured
    # are stored, and the third is measured now.
    second = audio_quality.ensure_readings(clips, root=memory_root,
                                           max_sources=2)
    assert second["already_measured"] == ["clip_0", "clip_1"]
    assert second["measured"] == ["clip_2"]
    assert second["not_reached"] == {}


def test_a_source_too_long_for_this_run_is_named_and_left_alone(tmp_path):
    """The lane decodes a WHOLE source, so a run must say it will not.

    A long file measured in the selection would cost half an hour of
    the run's wall clock. The source that was not measured must be named
    with the reason, and it must carry no reading - an absent reading is
    honest, a fabricated one is not.
    """
    memory_root = tmp_path / "memory"
    source = _write_tone(tmp_path / "long.wav")

    report = audio_quality.ensure_readings(
        [{"clip_id": "clip_long", "path": str(source),
          "duration_seconds": 5000.0}],
        root=memory_root, max_source_seconds=1800.0)

    assert report["measured"] == []
    assert "5000s" in report["not_reached"]["clip_long"]
    assert audio_quality.stored_record(str(source), root=memory_root) is None
