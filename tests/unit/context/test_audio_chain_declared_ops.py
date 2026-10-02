"""Audio-chain plans must validate, render, and report the values they name."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from scipy.io import wavfile

from library.steps.step_5_02_audio_mix.mix import apply_word_gap_ducking
from library.steps.step_5_02_audio_mix.post_bridge import (
    resolve_audio_delivery_plan,
    resolve_music_ducking_plan,
)
from library.steps.step_6_01_render import step as render_step
from library.tools import audio_effects, dialogue_cleanup, otio_mix
from library.tools import master_loudness
from library.tools.master_loudness import (
    MasteringResult,
    master_render_report,
)
from library.tools.project_layout import Area, ProjectLayout


def test_an_audio_ops_request_the_renderer_cannot_honour_refuses():
    """The plan schema catches effect controls the renderer never reads,
    and an empty operation list refuses instead of claiming delivery."""
    request = {
        "source": "speaker.wav",
        "tool": "audio_ops",
        "operations": [{
            "type": "equalizer", "frequency_hz": 300,
            "gain_db": -3, "q": 1, "why": "boxiness",
            "unread_control": 2,
        }],
        "why": "measured room floor and voice spectrum",
    }
    with pytest.raises(dialogue_cleanup.DialogueCleanupRefused) as raised:
        dialogue_cleanup.validate_cleanup_request(request)
    assert "unread keys" in raised.value.why
    assert "unread_control" in raised.value.why

    with pytest.raises(dialogue_cleanup.DialogueCleanupRefused,
                       match="operations.*empty"):
        dialogue_cleanup.validate_cleanup_request(
            dict(request, operations=[], why="clean up the room"))


def test_block_boundary_ramp_does_not_pull_down_a_recovered_gap():
    """Word keys own a word-keyed block; the block ramp stays out of it.

    The ramp between two block levels sat a quarter-block inside each
    block, so a trailing gap recovered after the last word and was then
    ramped back down to the ducked level before the block ended.
    """
    def block(start, end, words, level):
        return {"spine_block_position": start, "timeline_start": start,
                "timeline_end": end, "music_behavior": "background",
                "target_level_db": level, "word_intervals": words,
                "word_gap_level_db": level + 10.0,
                "word_gap_release_ms": 300.0}

    automation = [block(0.0, 8.0, [[0.0, 2.0]], -30.0),
                  block(8.0, 16.0, [[8.0, 14.0]], -24.0)]
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=160,
        fade_seconds=2.0)

    keys = sorted(curve.items())

    def level_at(frame):
        for (left, low), (right, high) in zip(keys, keys[1:]):
            if left <= frame <= right:
                return low + (high - low) * (frame - left) / (right - left)
        raise AssertionError(frame)

    # Block 1 recovers at 2.3 s and holds -20 dB until its last frame;
    # block 2 starts on a word, at its own ducked level.
    assert [level_at(f) for f in (23, 50, 70, 79)] == [-20.0] * 4
    assert level_at(80) == -24.0


def test_audio_ops_chain_stages_the_played_clip_range(tmp_path):
    """A declared EQ chain becomes a stem that OTIO can actually place."""
    source = tmp_path / "speaker.wav"
    source.write_bytes(b"source")
    request = {
        "source": "speaker.wav", "tool": "audio_ops",
        "operations": [{"type": "high_pass", "frequency_hz": 80,
                        "why": "remove measured handling rumble"}],
        "why": "measured low frequency rumble",
    }
    clip = {
        "source_file": str(source), "source_in": 2.0, "source_out": 4.0,
        "timeline_in_frame": 48, "timeline_out_frame": 96,
        "label": "speech_1",
    }

    def extract(_source, _start, _end, output):
        Path(output).write_bytes(b"range")

    def process(input_path, output_path, operations):
        Path(output_path).write_bytes(b"processed")
        return {"input_path": input_path, "output_path": output_path,
                "operations": operations, "delivered": True}

    with patch.object(dialogue_cleanup, "_extract_range", extract), \
            patch.object(dialogue_cleanup, "_match_source_channels",
                         return_value=1), \
            patch.object(audio_effects, "apply_operations", process):
        stems = dialogue_cleanup.stage_audio_chain(
            request, [clip], [], str(tmp_path / "build"))

    assert len(stems) == 1
    assert stems[0]["source_in"] == 2.0
    assert stems[0]["timeline_in_frame"] == 48
    assert stems[0]["tool"] == "audio_ops"
    assert stems[0]["operations"][0]["type"] == "high_pass"
    assert Path(stems[0]["stem_file"]).read_bytes() == b"processed"
    swaps = otio_mix.stem_swaps({"audio": {
        "dialogue_cleanup": {"stems": stems}}})
    assert swaps[0]["stem_file"] == stems[0]["stem_file"]
    assert swaps[0]["start_frame"] == 48


def test_audio_ops_stem_is_measured_and_plays_in_both_channels(tmp_path):
    """A stem is measured, then delivered in the source's channel layout.

    The WAV reader refused the chain's 24-bit stems and the refusal was
    swallowed, so every audio_ops stem on the K2 evals reported speech
    after `None`.  And a mono stem swapped onto a stereo source's clip
    played in the left channel only: the K2 exports carried cleaned
    dialogue about 15 dB down in the right ear.
    """
    sample_rate = 48000
    seconds = np.arange(sample_rate * 3) / sample_rate
    speech = 0.2 * np.sin(2 * np.pi * 220 * seconds)
    source = tmp_path / "speaker.wav"
    stereo = np.stack([speech, speech], axis=1)
    wavfile.write(source, sample_rate,
                  (stereo * np.iinfo(np.int16).max).astype(np.int16))
    request = {
        "source": "speaker.wav", "tool": "audio_ops",
        "operations": [{"type": "high_pass", "frequency_hz": 80,
                        "why": "remove measured handling rumble"}],
        "why": "measured low frequency rumble",
    }
    clip = {
        "source_file": str(source), "source_in": 0.0, "source_out": 3.0,
        "timeline_in_frame": 0, "timeline_out_frame": 72,
        "label": "speech_1",
    }

    stems = dialogue_cleanup.stage_audio_chain(
        request, [clip], [], str(tmp_path / "build"))

    assert isinstance(stems[0]["speech_before_lufs"], float)
    assert stems[0]["speech_after_lufs"] == pytest.approx(
        stems[0]["speech_before_lufs"], abs=1.0)
    _, delivered = wavfile.read(stems[0]["stem_file"])
    assert delivered.ndim == 2 and delivered.shape[1] == 2
    left, right = (np.sqrt(np.mean(delivered[:, c].astype(np.float64) ** 2))
                   for c in (0, 1))
    assert left > 0 and right == pytest.approx(left, rel=1e-3)
    assert stems[0]["channels"] == 2


def test_declared_effects_compile_to_filters_and_wpe_changes_a_spectrum():
    """Each named operation has an execution path, including dereverb."""
    operations = [
        {"type": "high_pass", "frequency_hz": 80, "why": "rumble"},
        {"type": "equalizer", "frequency_hz": 300,
         "gain_db": -3, "q": 1, "why": "boxiness"},
        {"type": "de_ess", "frequency_hz": 6000,
         "reduction_db": 3, "threshold_dbfs": -30, "q": 2,
         "attack_ms": 5, "release_ms": 80, "why": "sibilance"},
        {"type": "dereverb", "strength": 0.5, "why": "room tail"},
    ]

    filters = [audio_effects.ffmpeg_filter(op) for op in operations[:3]]
    assert filters[0] == "highpass=f=80"
    assert filters[1].startswith("equalizer=f=300")
    assert filters[2].startswith("adynamicequalizer=")
    assert "mode=cutabove" in filters[2]

    rng = np.random.default_rng(7)
    spectrum = np.zeros((2, 200), dtype=np.complex128)
    spectrum[:, 8] = 1.0
    for frame in range(9, spectrum.shape[1]):
        spectrum[:, frame] = 0.75 * spectrum[:, frame - 1]
    spectrum += (rng.standard_normal(spectrum.shape)
                 + 1j * rng.standard_normal(spectrum.shape)) * 1e-5
    processed = audio_effects.dereverberate_spectrum(
        spectrum, strength=0.5, taps=3, delay=2, iterations=2)

    assert processed.shape == spectrum.shape
    assert np.isfinite(processed).all()
    assert not np.allclose(processed, spectrum)


def test_declared_high_pass_attenuates_below_its_cutoff(tmp_path):
    """The plan's high-pass reaches samples, not only a filter string."""
    sample_rate = 48000
    seconds = np.arange(sample_rate * 2) / sample_rate
    source_signal = (0.4 * np.sin(2 * np.pi * 40 * seconds)
                     + 0.1 * np.sin(2 * np.pi * 600 * seconds))
    source = tmp_path / "source.wav"
    output = tmp_path / "high_pass.wav"
    wavfile.write(source, sample_rate, source_signal.astype(np.float32))

    audio_effects.apply_operations(str(source), str(output), [{
        "type": "high_pass", "frequency_hz": 80,
        "why": "remove measured low-frequency rumble",
    }])

    out_rate, processed = wavfile.read(output)
    assert out_rate == sample_rate
    spectrum_before = abs(np.fft.rfft(source_signal))
    spectrum_after = abs(np.fft.rfft(processed.astype(np.float64)))
    low_bin, high_bin = 40 * 2, 600 * 2
    assert spectrum_after[low_bin] / spectrum_after[high_bin] \
        < (spectrum_before[low_bin] / spectrum_before[high_bin]) * 0.5


def test_word_gap_curve_recovers_after_declared_release():
    """The bed stays ducked on words, then reaches the declared gap level."""
    automation = [{
        "spine_block_position": 1,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "music_behavior": "background",
        "target_level_db": -30.0,
        "speech_lufs": -16.0,
    }]
    spine = {"structure": [{
        "position": 1, "block_type": "speech", "clip_id": "clip-1",
        "source_start": 10.0, "source_end": 18.0,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "word_timestamps": [
            {"word": "one", "source_start": 11.0, "source_end": 12.0},
            {"word": "two", "source_start": 14.0, "source_end": 15.0},
        ],
    }]}
    plan = {"enabled": True, "duck_db": 10.0,
            "release_ms": 300.0, "why": "10 dB duck, 300 ms recovery"}

    apply_word_gap_ducking(automation, spine, plan)
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=80,
        fade_seconds=0.0)

    assert automation[0]["word_intervals"] == [[1.0, 2.0], [4.0, 5.0]]
    assert automation[0]["word_gap_level_db"] == -20.0
    assert curve[0] == -20.0
    assert curve[10] == -30.0
    assert curve[20] == -30.0
    assert curve[23] == -20.0
    assert curve[40] == -30.0
    assert curve[50] == -30.0
    assert curve[53] == -20.0


def test_word_gap_recovery_is_held_until_the_next_word():
    """Resolve interpolates linearly between keys, so a gap needs a hold key.

    With only the recovery key after one word and the ducked key on the
    next, every gap was a ramp back down that started the moment it
    recovered: the K2 exports delivered 2-5 dB of a planned 10 dB.
    """
    automation = [{
        "spine_block_position": 1,
        "timeline_start": 0.0, "timeline_end": 8.0,
        "music_behavior": "background",
        "target_level_db": -30.0,
        "speech_lufs": -16.0,
        "word_intervals": [[1.0, 2.0], [4.0, 5.0]],
        "word_gap_level_db": -20.0,
        "word_gap_release_ms": 300.0,
    }]
    curve = otio_mix.music_curve(
        automation, fps=10, clip_start_frame=0, clip_frame_count=80,
        fade_seconds=0.0)

    def level_at(frame):
        keys = sorted(curve.items())
        for (left, low), (right, high) in zip(keys, keys[1:]):
            if left <= frame <= right:
                return low + (high - low) * (frame - left) / (right - left)
        raise AssertionError(frame)

    # Leading gap and the gap between the words stay at the recovered level
    # until one frame before the next word, which starts ducked.
    assert [level_at(f) for f in (0, 5, 9)] == [-20.0, -20.0, -20.0]
    assert level_at(10) == -30.0
    assert [level_at(f) for f in (23, 31, 39)] == [-20.0, -20.0, -20.0]
    assert level_at(40) == -30.0


def test_numeric_mix_values_the_request_states_are_preserved_exactly():
    """MX3.1's 10 dB / 300 ms duck and -16 LUFS / -1 dBTP delivery are
    not rounded into engine defaults by planning."""
    plan = resolve_music_ducking_plan({
        "music_ducking_plan": {
            "enabled": True, "duck_db": 10, "release_ms": 300,
            "why": "request states 10 dB and 300 ms",
        },
    })
    assert plan["duck_db"] == 10.0
    assert plan["release_ms"] == 300.0

    plan = resolve_audio_delivery_plan({
        "audio_delivery_plan": {
            "dialogue_target_lufs": -16,
            "true_peak_ceiling_dbtp": -1,
            "why": "request states both delivery targets",
        },
    })
    assert plan["dialogue_target_lufs"] == -16.0
    assert plan["true_peak_ceiling_dbtp"] == -1.0


def test_master_report_points_only_to_the_measured_delivery_file(tmp_path):
    """The export report cannot keep pointing at Resolve's unmastered file."""
    raw = tmp_path / "reel_pre_master.mp4"
    final = tmp_path / "reel.mp4"
    raw.write_bytes(b"raw")
    final.write_bytes(b"mastered")
    result = MasteringResult(
        input_path=str(raw), output_path=str(final), already_compliant=False,
        input_i=-21.02, input_tp=0.24, output_i=-14.1, output_tp=-1.4)

    with patch("library.tools.master_loudness.normalize_to_delivery",
               return_value=result) as normalize:
        report = master_render_report({"output_path": str(raw)}, str(final))

    normalize.assert_called_once_with(
        str(raw), str(final), target_lufs=-14.0,
        true_peak_ceiling=-1.0)
    assert report["raw_output_path"] == str(raw)
    assert report["output_path"] == str(final)
    assert report["mastering"]["input"]["lufs"] == -21.02
    assert report["mastering"]["output"]["true_peak_dbtp"] == -1.4
    assert report["size_bytes"] == len(b"mastered")


def test_mastering_measures_and_delivers_the_lufs_and_true_peak_target(tmp_path):
    """The output gets re-measured against -14 LUFS and -1 dBTP."""
    sample_rate = 48000
    seconds = np.arange(sample_rate * 5) / sample_rate
    waveform = 0.08 * np.sin(2 * np.pi * 1000 * seconds)
    source = tmp_path / "quiet.wav"
    output = tmp_path / "delivery.mp4"
    wavfile.write(source, sample_rate,
                  (waveform * np.iinfo(np.int16).max).astype(np.int16))

    from library.tools.master_loudness import normalize_to_delivery

    result = normalize_to_delivery(str(source), str(output))

    assert result.output_i == pytest.approx(-14.0, abs=0.5)
    assert result.output_tp <= -1.0

    explicit_output = tmp_path / "dialogue_target.mp4"
    explicit = normalize_to_delivery(
        str(source), str(explicit_output), target_lufs=-16.0)
    assert explicit.output_i == pytest.approx(-16.0, abs=0.5)
    assert explicit.output_tp <= -1.0


def test_mastering_keeps_the_mix_level_changes_it_was_handed(tmp_path):
    """Mastering is one static gain; it never rides the mix's levels.

    loudnorm's `linear=true` falls back to dynamic mode whenever the gain
    would push the true peak over target - every K2 eval master - and that
    AGC pulled a bed's planned level changes back together.
    """
    sample_rate = 48000
    seconds = np.arange(sample_rate * 12) / sample_rate
    carrier = np.sin(2 * np.pi * 1000 * seconds)
    # 8 s ducked, 4 s recovered 10 dB louder; a peaky click every half
    # second makes the linear gain infeasible under the true-peak ceiling.
    waveform = np.where(seconds < 8.0, 0.02, 0.02 * 10 ** (10 / 20)) * carrier
    waveform[::sample_rate // 2] = 0.95
    source = tmp_path / "mix.wav"
    output = tmp_path / "delivery.mp4"
    wavfile.write(source, sample_rate,
                  (waveform * np.iinfo(np.int16).max).astype(np.int16))

    master_loudness.normalize_to_delivery(str(source), str(output))

    from library.tools.render_qa import _decode_mono
    delivered = _decode_mono(str(output))

    def rms_db(left, right):
        part = delivered[int(left * sample_rate):int(right * sample_rate)]
        return 20 * np.log10(np.sqrt(np.mean(part ** 2)))

    assert rms_db(9.0, 11.5) - rms_db(2.0, 7.5) == pytest.approx(10.0, abs=0.5)


def test_master_retries_when_aac_reencode_overshoots_true_peak(tmp_path):
    """AAC re-encoding can overshoot the limiter's requested true-peak ceiling."""
    source = tmp_path / "resolve_render.mp4"
    output = tmp_path / "delivery.mp4"
    source.write_bytes(b"raw resolve export")
    readings = iter([
        {"input_i": -21.0, "input_tp": 0.2, "input_lra": 8.0,
         "input_thresh": -32.0, "target_offset": 0.0},
        {"input_i": -14.73, "input_tp": 0.13, "input_lra": 7.0,
         "input_thresh": -25.0, "target_offset": 0.0},
        {"input_i": -14.0, "input_tp": -1.1, "input_lra": 7.0,
         "input_thresh": -25.0, "target_offset": 0.0},
    ])
    filters = []

    def encode(argv, **_kwargs):
        filters.append(argv[argv.index("-af") + 1])
        Path(argv[-1]).write_bytes(b"encoded delivery")

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    with patch.object(master_loudness, "_loudnorm_measure",
                      side_effect=lambda _path: next(readings)), \
            patch.object(master_loudness, "_audio_sample_rate",
                         return_value=48000), \
            patch.object(master_loudness.subprocess, "run",
                         side_effect=encode) as run:
        result = master_loudness.normalize_to_delivery(
            str(source), str(output))

    assert run.call_count == 2
    # First pass: the measured gain under a -1.5 dBTP limiter.  Second:
    # the gain corrected by the measured 0.73 LU shortfall, the limiter
    # lowered by the measured 1.13 dB overshoot plus the retry margin.
    assert filters[0] == master_loudness.static_gain_filter(7.0, -1.5, 48000)
    assert filters[1] == master_loudness.static_gain_filter(
        7.73, -3.13, 48000)
    assert result.normalization_attempts == 2
    assert result.output_i == -14.0
    assert result.output_tp == -1.1
    assert output.read_bytes() == b"encoded delivery"


def test_pipeline_export_masters_the_scratch_render_before_reporting(tmp_path):
    """Step 6.01 exports the mastered path while preserving the raw render."""
    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(str(project))
    scratch = str(layout.write_dir(Area.SCRATCH, step="render"))
    exports = str(layout.write_dir(Area.EXPORTS, step="render"))
    raw = os.path.join(scratch, "Timeline_1_pre_master.mp4")
    mastered = os.path.join(exports, "Timeline_1.mp4")

    class Completed:
        returncode = 0
        stderr = ""
        stdout = '{"output_path": "' + raw + '", "size_bytes": 3}'

    with patch.object(render_step.subprocess, "run", return_value=Completed()) \
            as run, patch(
                "library.tools.master_loudness.master_render_report",
                return_value={"output_path": mastered,
                              "raw_output_path": raw}) as master:
        report = render_step._export_timeline(
            "Timeline_1", {"project_folder": str(project)},
            {"project": {"name": "Project"},
             "audio_mix": {"delivery_lufs_target": -16.0,
                           "delivery_true_peak_ceiling_dbtp": -1.0}})

    command = run.call_args.args[0]
    assert command[command.index("--output-dir") + 1] == scratch
    assert command[command.index("--name") + 1] == "Timeline_1_pre_master"
    master.assert_called_once_with(
        {"output_path": raw, "size_bytes": 3}, mastered,
        target_lufs=-16.0, true_peak_ceiling=-1.0)
    assert report["output_path"] == mastered
