"""The mix declared the bed, received it, and never opened it.

Step 5.02 declared `music_selection` and `creative_direction` REQUIRED and
`enhancement_spec` optional, and read none of the three - the reader
discipline guard named all three the moment its deterministic-step blind
spot was fixed (`python3 -m library.tools.input_contract --bad`).

That is not a paperwork defect.  A `music_behavior` word is a RELATIVE dB
applied to whatever level the music file already carries, so whether the
planned offset lands is decided by the bed's own loudness - and on 001
the bed is mastered 8.6 dB hotter than the speech, so -18 dB of clip gain
buys about 12.5 dB of separation and 1 of 11 speech-bearing windows met
the margin the check judged it against.  The step could not see which
track was chosen, let alone how loud it is.

These tests follow the measurements end to end: 2.04 measures every
candidate, the CHOSEN one's scalars travel on `music_selection`, and 5.02
reads them.  They also pin the two honesty properties the wiring is worth
nothing without - an unmeasured bed is an admitted absence and never a
level of 0, and the render-side check says when the margin it judged
against was a clip gain rather than a separation somebody declared.

Nothing here asserts a level, and since 2026-09-16 there is no level to
assert: the captain removed the five clip gains, step 5.02 asks a mix
engineer for the SEPARATION over these same measurements, and the gain is
solved from it (`library/tools/decided_value.py`).  What this file pins is
unchanged by that - the measurements reach the step, an unmeasured bed is
an admitted absence, and the render-side check says which kind of number
it judged against.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import music_measurement as mm  # noqa: E402
from library.tools import music_behavior as mb  # noqa: E402
from library.tools import render_qa  # noqa: E402

STEP_DIR = REPO / "library" / "steps" / "step_5_02_audio_mix"
SELECTION_STEP_DIR = REPO / "library" / "steps" / "step_2_04_music_selection"


# What a mix engineer decided on one run. The test supplies it because no
# module holds one any more.
DECIDED = {"prominent": -7.5, "background": -19.5}


def _mix_spec(spine, selection, decided=None):
    """`audio_mix_spec` for one spine and one bed, at decided levels."""
    from library.steps.step_5_02_audio_mix import mix as mix_module

    pre = mix_module.measure(spine, selection)
    by_scope = {scope: {"value": value, "answer": None}
                for scope, value in (DECIDED if decided is None
                                     else decided).items()}
    automation, undetermined = mix_module.solve_automation(pre, by_scope)
    return mix_module.assemble(pre, [], automation,
                               undetermined)["audio_mix_spec"]


def _load_post_bridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "step_2_04_post_bridge_under_test", SELECTION_STEP_DIR / "post_bridge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _candidate(audio_path: str) -> dict:
    """One measured candidate, in the shape `measure_candidates` emits."""
    return {
        "title": "rise",
        "source": "library",
        "audio_path": audio_path,
        "duration_ok": True,
        "measured": True,
        "measurement_note": "",
        "integrated_lufs": -13.9,
        "loudness_range_lu": 6.0,
        "true_peak_dbtp": -0.4,
        "rms_spread_db": 5.7,
        "window_seconds": 60.0,
        "window_spread_db": 3.1,
        "speech_band_ratio_db": -2.8,
        # The two that must NOT travel onward.
        "window_envelope_dbfs": [-16.3] * mm.ENVELOPE_BUCKETS,
        "track_sections": [
            {"start_seconds": 0.0, "end_seconds": 60.0,
             "mean_dbfs": -16.3, "spread_db": 3.1},
        ],
        "track_sections_note": "",
    }


def _spine(*behaviours) -> dict:
    return {"structure": [
        {"position": index, "block_type": "speech", "content": "words",
         "timeline_start": float(index * 2), "timeline_end": float(index * 2 + 2),
         "music_behavior": behaviour}
        for index, behaviour in enumerate(behaviours)
    ]}


# ── The measurements travel with the track that was chosen ──────────

def test_the_chosen_track_carries_its_own_scalars():
    chosen = _candidate("/library/rise.mp3")
    other = dict(_candidate("/library/other.mp3"), integrated_lufs=-22.0)

    measurements = mm.selection_measurements(
        {"audio_path": "/library/rise.mp3"}, [other, chosen])

    assert measurements["measured"] is True
    assert measurements["integrated_lufs"] == -13.9, \
        "the scalars must come from the CHOSEN candidate, not the first one"
    assert measurements["speech_band_ratio_db"] == -2.8


def test_no_raw_value_list_travels_onward():
    """AGENTS.md 10.1: `music_selection` is declared whole by two prompts."""
    measurements = mm.selection_measurements(
        {"audio_path": "/library/rise.mp3"}, [_candidate("/library/rise.mp3")])

    for withheld in mm.WITHHELD_FROM_THE_SELECTION:
        assert withheld not in measurements, (
            f"{withheld} reached `music_selection`, which `plan_transitions` "
            f"and `mesh_spine` declare whole - it would land in both prompts")


def test_a_track_that_was_not_measured_says_so():
    measurements = mm.selection_measurements(
        {"audio_path": "/downloads/fetched.mp3"},
        [_candidate("/library/rise.mp3")])

    assert measurements["measured"] is False
    assert "not one of the 1 measured candidates" in \
        measurements["measurement_note"]
    assert "integrated_lufs" not in measurements, \
        "an unmeasured bed has no level, not a level of 0"


# ── Step 5.02 reads them ────────────────────────────────────────────

def test_the_mix_reads_the_bed_and_says_where_the_gain_puts_it():
    spec = _mix_spec(
        _spine("background", "prominent"),
        {"title": "rise", "audio_path": "/library/rise.mp3",
         "measurements": mm.selection_measurements(
             {"audio_path": "/library/rise.mp3"},
             [_candidate("/library/rise.mp3")])})

    assert spec["bed"]["measured"] is True
    assert spec["bed"]["integrated_lufs"] == -13.9
    assert spec["bed"]["speech_band_ratio_db"] == -2.8

    background, prominent = spec["music_automation"]
    # Where the gain PUTS the bed is arithmetic on the measurement, not a
    # chosen number - and the gain itself is now the decided one.
    assert background["target_level_db"] == DECIDED["background"]
    assert background["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["background"])
    assert prominent["bed_level_after_gain_lufs"] == pytest.approx(
        -13.9 + DECIDED["prominent"])


# ── The check says what it judged against ───────────────────────────

def _fake_master(monkeypatch, *, music_gain_db: float, speech_db: float):
    """A two-second master: a known bed under a known voice."""
    import numpy as np  # a hard dependency of render_qa itself

    rate = 48000
    n = rate * 2
    rng = np.random.default_rng(7)
    music = rng.standard_normal(n) * 0.1
    speech = rng.standard_normal(n) * (10 ** (speech_db / 20.0))
    mix = music * (10 ** (music_gain_db / 20.0)) + speech

    def decode(path, sample_rate=rate):
        return (music if path == "MUSIC" else mix).astype("float32")

    monkeypatch.setattr(render_qa, "_decode_mono", decode)


def _automation(separation_target_db):
    return [{
        "spine_block_position": 0,
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "music_behavior": "background",
        "target_level_db": DECIDED["background"],
        "separation_target_db": separation_target_db,
    }]


BLOCKS = [{"position": 0, "block_type": "speech"}]


def test_the_check_names_a_clip_gain_when_that_is_what_it_judged(monkeypatch):
    _fake_master(monkeypatch, music_gain_db=DECIDED["background"],
                 speech_db=-20.0)

    result = render_qa.measure_speech_above_bed(
        "MASTER", "MUSIC", _automation(None), 0.0, BLOCKS)

    window = result.value["windows"][0]
    assert window["required_margin_basis"] == "clip_gain_read_as_separation"
    assert window["required_margin_db"] == abs(DECIDED["background"])
    assert result.threshold["judged_on_clip_gain"] is True
    assert "CLIP GAIN" in result.detail, (
        "the report must say the margin was a clip gain, or a reader takes "
        "it for a separation somebody declared")
