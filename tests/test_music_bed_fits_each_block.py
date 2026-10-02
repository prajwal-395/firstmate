"""The bed is fitted per block to the decided separation, and a shortfall
is reported on the row that misses. History (finding 25, the B4 run):
docs/evidence/music_tests.md#bed-fits-each-block.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_02_audio_mix import mix as mix_module  # noqa: E402

BED_LUFS = -12.0
SEPARATION = 14.0


def _pre_output(speeches):
    """Two `background` windows with the given per-block speech."""
    return {
        "bed_measurements": {"measured": True,
                             "integrated_lufs": BED_LUFS,
                             "title": "bed", "audio_path": "bed.wav"},
        "mix_windows": [
            {"spine_block_position": i + 1,
             "block_type": "speech",
             "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5),
             "music_behavior": "background",
             "carries_speech": True,
             "speech_lufs": speech,
             "speech_loudness": ({"measured": True,
                                  "integrated_lufs": speech}
                                 if speech is not None else
                                 {"measured": False})}
            for i, speech in enumerate(speeches)
        ],
    }


def _by_scope(gain_at_reference):
    return {"background": {"value": gain_at_reference, "answer": SEPARATION},
            "prominent": {"value": gain_at_reference, "answer": SEPARATION}}


def test_each_block_hits_the_decided_separation():
    """The B4 shape: -20 and -39 LUFS speech under one behaviour. One
    level per behaviour misses by 19 dB on the quiet block; a per-block
    fit delivers 14.0 on both."""
    pre = _pre_output([-20.0, -39.0])
    # The scope-level gain, fitted to the loudest block as today.
    reference_gain = round((-20.0 - SEPARATION) - BED_LUFS, 2)
    automation, undetermined = mix_module.solve_automation(
        pre, _by_scope(reference_gain))

    assert undetermined == []
    assert len(automation) == 2
    gains = [row["target_level_db"] for row in automation]
    assert gains[0] != gains[1], (
        f"one gain for both blocks is the finding: {gains}")
    assert abs(gains[0] - gains[1] - 19.0) < 0.01
    for row in automation:
        assert row["separation_target_db"] == SEPARATION
        assert abs(row["separation_delivered_db"] - SEPARATION) < 0.01
        assert row["separation_shortfall_db"] == 0.0


def test_a_shortfall_is_reported_on_the_row_that_misses():
    """A window whose speech was never measured cannot be fitted: it
    keeps the scope gain, delivers nothing, and SAYS so on its own row
    and in the shortfall lines the post-bridge prints."""
    pre = _pre_output([-20.0, None])
    reference_gain = round((-20.0 - SEPARATION) - BED_LUFS, 2)
    automation, undetermined = mix_module.solve_automation(
        pre, _by_scope(reference_gain))

    calm, unmeasured = automation
    assert abs(calm["separation_delivered_db"] - SEPARATION) < 0.01
    assert unmeasured["separation_delivered_db"] is None
    assert unmeasured["separation_shortfall_db"] is None
    assert "speech" in (unmeasured["shortfall_basis"] or "").lower()

    lines = mix_module.shortfall_lines(automation)
    assert any("2" in line and "no measured speech" in line
               for line in lines), lines
    assert not any(line.startswith("Window 1") for line in lines)


def test_a_fallback_gain_carries_no_target_and_no_shortfall():
    """No separation decided (fallback gain): the row states what it
    holds - a gain, not a target - and claims no shortfall either way."""
    pre = _pre_output([-20.0])
    automation, _ = mix_module.solve_automation(
        pre, {"background": {"value": -18.0, "answer": None,
                             "basis": "fallback"},
              "prominent": {"value": -6.0, "answer": None,
                            "basis": "fallback"}})
    (row,) = automation
    assert row["target_level_db"] == -18.0
    assert row["separation_target_db"] is None
    assert row["separation_shortfall_db"] is None
