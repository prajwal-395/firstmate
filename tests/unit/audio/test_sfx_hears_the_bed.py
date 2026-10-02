"""The bed's own level under each block reaches step 4.04 as DATA - a level, never a gain.

The gain is decided at the mix, later. History: docs/evidence/sfx.md.
"""

import pytest

from library.tools.music_behavior import MusicBehaviorError
from library.tools.music_measurement import (
    bed_level_after_gain,
    bed_reading,
    bed_under_block,
)

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
    # The block the shipped sound sat on: its own level, never a gain.
    behaviour, cell = bed_under_block(
        {"block_type": "transition_slot", "position": 1,
         "music_behavior": "prominent"}, MEASURED_BED)
    assert behaviour == "prominent"
    assert "-15.17 LUFS" in cell
    assert "gain" not in cell


def test_an_unmeasured_bed_has_no_level_and_never_zero():
    empty = bed_reading({})
    assert bed_level_after_gain(empty, -18) is None
    _, cell = bed_under_block({"block_type": "speech", "position": 1}, empty)
    assert "unmeasured" in cell
    assert "0" not in cell.split("(")[0]


def test_a_behaviour_outside_the_vocabulary_raises():
    with pytest.raises(MusicBehaviorError):
        bed_under_block({"block_type": "speech", "position": 1,
                         "music_behavior": "ducked"}, MEASURED_BED)


