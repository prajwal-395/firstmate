"""The speech's own loudness is measured, and no target is invented.

`step_5_02_audio_mix` carried a constant, `SPEECH_LOUDNESS_IS_UNMEASURED`,
naming exactly one missing thing: the separation a window delivers is
speech loudness minus `bed_level_after_gain_lufs`, and nothing in the
pipeline measured the loudness of the speech that plays. It would take
one ffmpeg `loudnorm` pass over the A-roll ranges `a_roll_assignments`
already names.

This is that pass, and it stops there. On project 001, 0 of 8 windows
meet the 18 dB the render check reads off the clip gain, the worst is
+6.27 dB, and the master is -21.72 LUFS against a -14 target. Who owns
those numbers is the captain's open decision
(`mix-levels-need-an-owner`), so nothing here compares the delivered
separation with anything.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import speech_loudness as sl
from library.steps.step_5_02_audio_mix import mix as mix_module

# What a mix engineer decided on one run. No module holds a level since
# the captain's ruling of 2026-09-16, so a test that wants one supplies
# it the way a run does.
DECIDED = {"prominent": -6.0, "background": -18.0}


def define_audio_mix(spine, selection, a_roll_assignments=None,
                     decided=None, answers=None):
    """`{"audio_mix_spec": ...}` for a spine, at decided levels."""
    pre = mix_module.measure(spine, selection, a_roll_assignments)
    by_scope = {
        scope: {"value": value,
                "answer": (answers or {}).get(scope)}
        for scope, value in (DECIDED if decided is None else decided).items()}
    automation, undetermined = mix_module.solve_automation(pre, by_scope)
    # One record per scope, the shape `decide` produces: `answer` is the
    # separation somebody named, and its absence is what tells a decided
    # target apart from a level with no judgement behind it.
    decisions = [{"slot": mix_module.SLOT, "scope": scope,
                  "basis": "reasoned" if entry["answer"] is not None
                           else "fallback",
                  "value": entry["value"], "answer": entry["answer"]}
                 for scope, entry in by_scope.items()]
    return mix_module.assemble(pre, decisions, automation, undetermined)

FFMPEG = shutil.which("ffmpeg") is not None


def _tone(path: Path, seconds: float = 4.0, volume: str = "0.5") -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-af", f"volume={volume}", "-c:a", "aac", str(path)],
        check=True, capture_output=True)
    return path


# ── The measurement ──────────────────────────────────────────────────

def test_an_absent_file_is_an_admitted_absence_not_a_level():
    reading = sl.measure_range("/no/such/file.mov", 0.0, 2.0)
    assert reading["measured"] is False
    assert "not on disk" in reading["reason"]
    assert "integrated_lufs" not in reading


def test_a_zero_length_range_measures_nothing(tmp_path):
    real = tmp_path / "any.mov"
    real.write_text("x")
    reading = sl.measure_range(str(real), 3.0, 3.0)
    assert reading["measured"] is False
    assert "nothing to measure" in reading["reason"]


def test_an_assignment_with_no_range_says_so():
    out = sl.measure_speech_blocks([{"spine_block_position": 4}])
    assert out[4]["measured"] is False
    assert "no source range" in out[4]["reason"]


def test_the_separation_is_arithmetic_over_two_measurements():
    # 001's own numbers: background puts the bed at -33.17 LUFS and the
    # hook's speech measures -26.99.
    assert sl.separation_delivered_db(-26.99, -33.17) == 6.18
    # Either half missing means the answer is missing, never 0.
    assert sl.separation_delivered_db(None, -33.17) is None
    assert sl.separation_delivered_db(-26.99, None) is None


def test_nothing_here_supplies_a_target():
    assert "not measured HERE" in sl.NO_TARGET_IS_SUPPLIED
    assert "never will be" in sl.NO_TARGET_IS_SUPPLIED
    # No module-level number that could be read as one.
    numbers = {name: value for name, value in vars(sl).items()
               if isinstance(value, (int, float))
               and not isinstance(value, bool)
               and not name.startswith("_")}
    assert set(numbers) <= {"SPEECH_AUDIO_STREAM", "FFMPEG_TIMEOUT_SECONDS"}, \
        numbers


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_real_range_is_measured_and_a_quieter_one_reads_quieter(tmp_path):
    loud = _tone(tmp_path / "loud.m4a", volume="0.5")
    quiet = _tone(tmp_path / "quiet.m4a", volume="0.05")

    a = sl.measure_range(str(loud), 0.5, 3.0)
    b = sl.measure_range(str(quiet), 0.5, 3.0)
    assert a["measured"] and b["measured"]
    assert a["seconds"] == 2.5
    assert a["audio_stream"] == sl.SPEECH_AUDIO_STREAM
    # 20 dB of level between them, within the measurement's own slack.
    assert a["integrated_lufs"] - b["integrated_lufs"] == pytest.approx(
        20.0, abs=1.5)


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_block_cut_from_several_segments_names_the_one_it_measured(tmp_path):
    clip = _tone(tmp_path / "clip.m4a", seconds=8.0)
    out = sl.measure_speech_blocks([{
        "spine_block_position": 3,
        "video_segments": [
            {"source_file": str(clip), "video_in": 0.2, "video_out": 1.0},
            {"source_file": str(clip), "video_in": 2.0, "video_out": 6.0},
        ],
    }])
    reading = out[3]
    assert reading["measured"]
    assert reading["segments_in_the_block"] == 2
    assert reading["measured_segment"]["video_in"] == 2.0


# ── What the mix spec records ────────────────────────────────────────

SPINE = {"structure": [
    {"block_type": "hook", "position": "hook", "clip_id": "c1",
     "timeline_start": 0.0, "timeline_end": 2.398,
     "music_behavior": "background", "content": {"text": "a line"}},
    {"block_type": "transition_slot", "position": 1,
     "timeline_start": 2.398, "timeline_end": 5.398,
     "music_behavior": "prominent"},
]}
SELECTION = {"title": "x", "audio_path": "/x.mp3",
             "measurements": {"measured": True, "integrated_lufs": -15.17}}


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_the_mix_spec_records_the_speech_and_the_delivered_separation(
        tmp_path):
    clip = _tone(tmp_path / "speech.m4a", seconds=6.0)
    spec = define_audio_mix(SPINE, SELECTION, [{
        "spine_block_position": "hook",
        "video_segments": [{"source_file": str(clip),
                            "video_in": 0.836, "video_out": 3.234}],
    }])["audio_mix_spec"]

    hook = spec["music_automation"][0]
    assert hook["bed_level_after_gain_lufs"] == -33.17
    assert isinstance(hook["speech_lufs"], float)
    assert hook["separation_delivered_db"] == pytest.approx(
        hook["speech_lufs"] - hook["bed_level_after_gain_lufs"], abs=0.01)
    # Nothing is compared with anything until somebody decides a
    # separation; this fixture supplies levels but no judgement behind
    # them, so the window carries the gain and no target.
    assert hook["separation_target_db"] is None
    assert spec["separation_targets_declared"] is False


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not available")
def test_a_decided_separation_reaches_the_window_that_delivers_it(tmp_path):
    """The reader that had nothing to read. `render_qa` has always looked
    for `separation_target_db` and always found None; since 2026-09-16
    the mix engineer's own number is what lands there."""
    clip = _tone(tmp_path / "speech.m4a", seconds=6.0)
    spec = define_audio_mix(SPINE, SELECTION, [{
        "spine_block_position": "hook",
        "video_segments": [{"source_file": str(clip),
                            "video_in": 0.836, "video_out": 3.234}],
    }], answers={"background": 11.0, "prominent": -2.0})["audio_mix_spec"]

    assert spec["music_automation"][0]["separation_target_db"] == 11.0
    assert spec["separation_targets_declared"] is True


def test_a_block_with_no_assignment_records_none_and_never_zero():
    spec = define_audio_mix(SPINE, SELECTION, [])["audio_mix_spec"]
    for window in spec["music_automation"]:
        assert window["speech_lufs"] is None
        assert window["separation_delivered_db"] is None
    # The plan is still complete: the mix is not gated on the measurement.
    assert spec["music_automation"][0]["target_level_db"] == \
        DECIDED["background"]


def test_this_module_measures_and_names_who_decides():
    """It says what it does NOT answer, and - since 2026-09-16 - who
    does. A measurement module inventing a target would be a strength
    nobody chose; one that cannot say where the target comes from leaves
    a reader to assume."""
    note = sl.NO_TARGET_IS_SUPPLIED
    assert "OUGHT to deliver is not measured HERE" in note
    assert "decided_value" in note
    assert "5.02" in note


def test_the_cost_is_measured_rather_than_assumed():
    assert sl.MEASURED_COST["seconds_per_block"] == 0.23
    assert "project 001" in sl.MEASURED_COST["measured_on"]
