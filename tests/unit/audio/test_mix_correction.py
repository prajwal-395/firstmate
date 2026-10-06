"""A bed that masks the dialogue must not ship behind a failing number.

The defect these tests name: `render_qa.measure_speech_above_bed` (P3)
judges every speech window's margin against its declared separation
target, a miss fails the build - and nothing deepens the bed and
re-checks.  A masked line ships behind a measured miss.  The correction
loop closes that: it deepens the window's `target_level_db` by the
measured shortfall, re-renders the mix with the correction applied and
re-measures on the re-rendered samples until the window meets its
target or the loop names why it cannot.

The synth fixtures encode media with ffmpeg (seconds-long tones): the
fit the loop re-measures needs real samples, and nothing else in the
repo produces them.
"""

from __future__ import annotations

import subprocess

from library.tools.mix_correction import (
    MixCorrectionLoop,
    compute_duck_deepening,
    correct_masking,
)
from library.tools.correction_loop import (
    VERDICT_CANNOT_CORRECT,
    VERDICT_PASS,
)

SAMPLE_RATE = 8000


def _synth(path, source):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", source,
         "-ar", str(SAMPLE_RATE), "-ac", "1", path],
        check=True, timeout=120)


def _stems(tmp_path):
    """Speech, music and a master whose bed is too hot to meet 12 dB."""
    speech = str(tmp_path / "speech.wav")
    music = str(tmp_path / "music.wav")
    master = str(tmp_path / "master.wav")
    _synth(speech, "sine=frequency=440:duration=4")
    _synth(music, "sine=frequency=110:duration=6")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", speech, "-i", music,
         "-filter_complex",
         "[1:a]volume=0.5[bed];[0:a][bed]amix=inputs=2:normalize=1",
         "-ar", str(SAMPLE_RATE), "-ac", "1", master],
        check=True, timeout=120)
    return speech, music, master


def _automation():
    return [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -3.0,
        "separation_target_db": 12.0,
        "music_behavior": "background",
        "spine_block_position": 0,
    }]


def _spine():
    return [{"position": 0, "block_type": "speech"}]


def _p3(master, music, automation):
    from library.tools.render_qa import measure_speech_above_bed
    return measure_speech_above_bed(
        master, music, automation, 0.0, spine_blocks=_spine(),
        sample_rate=SAMPLE_RATE)


def test_the_deepening_is_the_measured_shortfall():
    # Margin 4 dB against a declared 10: the bed goes 6 dB deeper -
    # the shortfall, not a constant duck depth.
    assert compute_duck_deepening(4.0, 10.0) == 6.0
    assert compute_duck_deepening(10.0, 10.0) == 0.0
    assert compute_duck_deepening(12.0, 10.0) == 0.0


def test_the_loop_deepens_once_by_the_shortfall_and_passes():
    loop = MixCorrectionLoop(4.0, 10.0)
    result = loop.run()

    assert result.passed
    assert result.verdict == VERDICT_PASS
    assert loop.deepening_db == 6.0
    [correction] = result.corrections_applied()
    assert correction == {"deepen_db": 6.0}
    assert result.iteration_count == 2
    assert [it["outcome"] for it in result.as_dict()["iterations"]] == [
        "corrected", "pass"]


def test_a_met_margin_applies_no_deepening():
    loop = MixCorrectionLoop(12.0, 10.0)
    result = loop.run()

    assert result.passed
    assert result.iteration_count == 1
    assert result.corrections_applied() == []
    assert loop.deepening_db == 0.0


def test_masking_is_corrected_and_remeasured_on_rerendered_samples(tmp_path):
    # End to end on synth stems: P3 reports the miss, the loop deepens
    # the window by the shortfall P3 measured, and the re-measure on
    # the re-rendered mix meets the declared target.
    _speech, music, master = _stems(tmp_path)
    automation = _automation()

    opening = _p3(master, music, automation)
    assert isinstance(opening.value, dict)
    [window] = opening.value["windows"]
    assert window["meets_plan"] is False
    shortfall = window["required_margin_db"] - window["margin_db"]
    assert shortfall > 0

    written = {}

    def write_levels(index, level):
        written[index] = level
        automation[index]["target_level_db"] = level

    records = correct_masking(
        master, music, automation, 0.0, p3_value=opening.value,
        write_levels=write_levels, sample_rate=SAMPLE_RATE,
        spine_blocks=_spine())

    assert len(records) == 1
    [record] = records
    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    # The deepening is the shortfall P3 measured, not a constant.
    assert record["deepening_db"] == shortfall
    assert written[0] == -3.0 - shortfall
    assert automation[0]["target_level_db"] == written[0]
    # The verdict is the re-render's verdict: re-measured on samples.
    assert record["remeasured"] is True
    assert record["remeasured_margin_db"] >= (
        record["remeasured_required_margin_db"])


def test_the_declared_target_is_never_moved(tmp_path):
    # The correction meets the intent; it does not move the goalposts.
    _speech, music, master = _stems(tmp_path)
    automation = _automation()
    opening = _p3(master, music, automation)

    correct_masking(
        master, music, automation, 0.0, p3_value=opening.value,
        write_levels=lambda i, level: automation[i].__setitem__(
            "target_level_db", level),
        sample_rate=SAMPLE_RATE, spine_blocks=_spine())

    assert automation[0]["separation_target_db"] == 12.0
    assert automation[0]["target_level_db"] < -3.0


def test_an_overridden_span_is_skipped_never_corrected():
    # The defect this names: "correcting" a span the captain explicitly
    # wants the bed over. An overridden miss is recorded as skipped
    # with its override's id, and its level is never touched.
    automation = _automation()
    p3_value = {"windows": [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -3.0,
        "separation_target_db": 12.0,
        "margin_db": 4.0,
        "required_margin_db": 12.0,
        "required_margin_basis": "declared_separation_target",
        "music_over_speech_override": "ovr-comic-swell",
        "judged": True,
        "meets_plan": False,
    }]}

    def write_levels(index, level):
        raise AssertionError("an overridden span must never be rewritten")

    [record] = correct_masking(
        "unused.wav", "unused-bed.wav", automation, 0.0,
        p3_value=p3_value, write_levels=write_levels,
        sample_rate=SAMPLE_RATE)

    assert record["verdict"] == "skipped_override"
    assert record["passed"] is True
    assert "ovr-comic-swell" in record["detail"]
    assert automation[0]["target_level_db"] == -3.0


def test_a_clip_gain_fallback_window_is_not_corrected():
    # No declared separation, no margin anybody chose to miss: a window
    # judged on the clip-gain fallback is measured and reported by P3,
    # and the loop leaves it alone.
    automation = [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -18.0,
        "music_behavior": "background",
        "spine_block_position": 0,
    }]
    p3_value = {"windows": [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -18.0,
        "separation_target_db": None,
        "margin_db": 9.0,
        "required_margin_db": 18.0,
        "required_margin_basis": "clip_gain_read_as_separation",
        "music_over_speech_override": None,
        "judged": True,
        "meets_plan": False,
    }]}

    def write_levels(index, level):
        raise AssertionError("a fallback window must never be rewritten")

    assert correct_masking(
        "unused.wav", "unused-bed.wav", automation, 0.0,
        p3_value=p3_value, write_levels=write_levels,
        sample_rate=SAMPLE_RATE) == []
    assert automation[0]["target_level_db"] == -18.0


def test_an_unrenderable_mix_reports_arithmetic_not_samples(tmp_path):
    # The files are gone, so no sample re-measure can run - and the
    # record says the pass is the loop's arithmetic, never a sample
    # pass that never ran.
    automation = _automation()
    p3_value = {"windows": [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -3.0,
        "separation_target_db": 12.0,
        "margin_db": 4.0,
        "required_margin_db": 12.0,
        "required_margin_basis": "declared_separation_target",
        "music_over_speech_override": None,
        "judged": True,
        "meets_plan": False,
    }]}

    [record] = correct_masking(
        str(tmp_path / "gone.wav"), str(tmp_path / "gone-bed.wav"),
        automation, 0.0, p3_value=p3_value,
        sample_rate=SAMPLE_RATE)

    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    assert record["deepening_db"] == 8.0
    assert record["remeasured"] is False
    assert "arithmetic" in record["detail"]


def test_a_silent_window_miss_is_declined_explicitly():
    # A silent window is judged on the bed's audibility, never on a
    # declared separation - so there is nothing to deepen toward. The
    # loop declines on the record instead of vanishing or, worse,
    # "correcting" toward a target nobody declared.
    automation = [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -96.0,
        "music_behavior": "silent",
        "spine_block_position": 0,
    }]
    p3_value = {"windows": [{
        "timeline_start": 0.0,
        "timeline_end": 4.0,
        "target_level_db": -96.0,
        "separation_target_db": None,
        "margin_db": 3.0,
        "required_margin_db": None,
        "required_margin_basis": None,
        "music_behavior": "silent",
        "music_over_speech_override": None,
        "judged": True,
        "meets_plan": False,
    }]}

    def write_levels(index, level):
        raise AssertionError("a silent window must never be rewritten")

    [record] = correct_masking(
        "unused.wav", "unused-bed.wav", automation, 0.0,
        p3_value=p3_value, write_levels=write_levels,
        sample_rate=SAMPLE_RATE)

    assert record["verdict"] == "skipped_silent"
    assert automation[0]["target_level_db"] == -96.0


def test_a_miss_beyond_correction_names_why_it_cannot(tmp_path):
    # A window the arithmetic cannot close: MixCorrectionLoop always
    # closes a finite shortfall, so this pins the loop's own honesty
    # boundary - a margin that is not a number refuses explicitly.
    loop = MixCorrectionLoop(float("nan"), 10.0)
    result = loop.run()

    assert not result.passed
    assert result.verdict == VERDICT_CANNOT_CORRECT
