"""Nothing in the pipeline predicted whether a sound would be heard.

The one SFX project 001 shipped plays at -14 dB at 2.398s, which is the
exact frame the bed goes `prominent` - -6 dB, the loudest music in the
video. Step 4.04 was routed `timed_spine`, which carries `music_behavior`
per block, so it COULD have seen that; nothing asked it to, and
`volume_level` is a four-word ladder (subtle -18 / low -14 / medium -10 /
prominent -6) with no relation to what the sound measures or to what is
under it.

The bed's own level at each block now travels into that step's candidate
table as DATA, defined in 4.04's own prompt - and it states a level and
never a gain, because the gain is decided later, at the mix, over
measurements this step does not yet have.

**It states a level and never a target.** What separation a sound should
have over the bed is the same undeclared decision
`music_behavior.SEPARATION_TARGETS_DB` is empty for.
"""

import importlib.util
from pathlib import Path

import pytest

from library.tools.music_behavior import MusicBehaviorError
from library.tools.music_measurement import (
    bed_level_after_gain,
    bed_reading,
    bed_under_block,
)

ROOT = Path(__file__).resolve().parents[1]

# 001's own chosen track.
MEASURED_BED = bed_reading({
    "title": 'Sickick - "Infected" (Instrumental)',
    "measurements": {"measured": True, "integrated_lufs": -15.17,
                     "speech_band_ratio_db": -8.6},
})


def test_the_bed_level_is_the_gain_applied_to_what_the_bed_measures():
    assert bed_level_after_gain(MEASURED_BED, -6) == -21.17
    # The number step 5.02 records for 001's fade_out block, exactly.
    assert bed_level_after_gain(MEASURED_BED, -12) == -27.17


def test_an_unmeasured_bed_has_no_level_and_never_zero():
    empty = bed_reading({})
    assert bed_level_after_gain(empty, -18) is None
    _, cell = bed_under_block({"block_type": "speech", "position": 1}, empty)
    assert "unmeasured" in cell
    assert "0" not in cell.split("(")[0]


def test_the_block_the_shipped_sound_sat_on_reads_prominent():
    behaviour, cell = bed_under_block(
        {"block_type": "transition_slot", "position": 1,
         "music_behavior": "prominent"}, MEASURED_BED)
    assert behaviour == "prominent"
    assert cell == ("the bed plays here at -15.17 LUFS of its own; how far "
                    "it is pushed down under this block is decided at the "
                    "mix, not yet")
    # Since 2026-09-16 the gain is decided at the mix, never quoted here.
    assert "gain" not in cell


def test_a_planned_silence_says_so_rather_than_reporting_a_level():
    behaviour, cell = bed_under_block(
        {"block_type": "transition_slot", "position": 9,
         "music_behavior": "silent"}, MEASURED_BED)
    assert behaviour == "silent"
    assert "no music at all" in cell
    assert "LUFS" not in cell


def test_a_behaviour_outside_the_vocabulary_raises():
    with pytest.raises(MusicBehaviorError):
        bed_under_block({"block_type": "speech", "position": 1,
                         "music_behavior": "ducked"}, MEASURED_BED)


def test_the_prompt_states_a_level_and_never_a_target():
    """The definition moved into 4.04's prompt when the freeze lifted, so
    the no-target rule is asserted where the model reads it."""
    handoff = " ".join((ROOT / "library" / "steps" / "step_4_04_plan_sfx"
                        / "handoff.md").read_text(encoding="utf-8").split())
    assert "`music_behavior`" in handoff and "`bed_under_it`" in handoff
    lowered = handoff.lower()
    assert "no separation target is declared anywhere" in lowered
    # An unmeasured bed is a stated absence, never a zero or a silence -
    # the wording moved since, the concept stands where the model reads it.
    assert '"unmeasured"' in handoff
    assert "never" in lowered and "the bed is silent" in lowered
    # The prompt must not turn the two facts into a level to read off.
    start = lowered.index("what a level is measured against")
    for instruction in ("should be at", "aim for", "at least"):
        assert instruction not in lowered[start:]


def test_the_table_carries_the_bed_and_the_prompt_defines_it():
    spec = importlib.util.spec_from_file_location(
        "sfx_bridge", ROOT / "library" / "steps" / "step_4_04_plan_sfx"
        / "bridge.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    rows = bridge.build_sfx_candidates({
        "timed_spine": {"structure": [
            {"block_type": "transition_slot", "position": 1,
             "music_behavior": "prominent", "visual_note": "the parking lot",
             "timeline_start": 2.398, "timeline_end": 5.398},
            {"block_type": "speech", "position": 2, "clip_id": "clip_011",
             "source_start": 9.699, "source_end": 12.681,
             "music_behavior": "background",
             "content": {"text": "today is march 25th"}},
        ]},
        "temporal_event_indices": [],
        "b_roll_assignments": [],
        "music_selection": {
            "title": "x",
            "measurements": {"measured": True, "integrated_lufs": -15.17}},
    })
    by_id = {r["segment_id"]: r for r in rows}
    assert by_id[1]["music_behavior"] == "prominent"
    assert by_id[2]["music_behavior"] == "background"
    # Both rows state the bed's OWN level - it does not vary by block, the
    # behaviour column carries the per-block difference - and neither quotes
    # a gain: that is decided at the mix, not here.
    expected_cell = ("the bed plays here at -15.17 LUFS of its own; how far "
                     "it is pushed down under this block is decided at the "
                     "mix, not yet")
    assert by_id[1]["bed_under_it"] == expected_cell
    assert by_id[2]["bed_under_it"] == expected_cell
    assert "gain" not in by_id[1]["bed_under_it"]

    # The definition is in the PROMPT now, not shipped beside the table.
    # The freeze that forced a `sfx_candidates_legend` dict was lifted
    # 2026-09-09, and a definition living in two places is worse than
    # either.
    source = (ROOT / "library" / "steps" / "step_4_04_plan_sfx"
              / "bridge.py").read_text()
    assert '"sfx_candidates_legend"' not in source
    assert '"music_behavior",' in source and '"bed_under_it"' in source
