"""A touch is judged on what IT changes, not on the reel it lands on.

Ren refused five of the captain's reels for ANY edit - Reels 07, 08, 09,
10, 13 - because `assert_every_frame_covered(..., row="V1")` refused any
interior V1 gap in the post-edit plan, and those reels' approved build
shape is a V1 hole covered by a V2 cutaway (Reel 07: V1 gap 116..665
with V2 at 116..665). A logo swap on V6 never touches V1, so the gap
is not the edit's defect and must not refuse it - while an edit that
OPENS a new V1 gap must still refuse exactly as before.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402


def _clip(start: int, duration: int, name: str = "c") -> dict:
    return {"record_in": start, "record_out": start + duration,
            "duration": duration, "left_offset": 0, "name": name}


def _reel_07_tracks() -> list:
    """Reel 07's shape: V1 gap 116..665 under a V2 cutaway, logo on V6."""
    return [
        {"type": "video", "index": 1,
         "clips": [_clip(0, 116, "v1-head"), _clip(665, 335, "v1-tail")]},
        {"type": "video", "index": 2,
         "clips": [_clip(116, 549, "v2-cutaway")]},
        {"type": "video", "index": 6,
         "clips": [_clip(900, 100, "old-logo")]},
    ]


def test_preexisting_v1_gap_covered_by_v2_does_not_refuse_a_v6_swap():
    """Ren refused Reels 07/08/09/10/13 for a V6 logo swap they never
    touch: the approved build carries a V1 hole under a V2 cutaway and
    the check read the hole as the swap's defect."""
    tracks = _reel_07_tracks()
    swap = ce.Insertion(track_type="video", track_index=6,
                        media_pool_item=None, left_offset=0, duration=100,
                        record_frame=900, name="new-logo", properties={})
    spans = ce.assert_every_frame_covered(tracks, [], [swap], row="V1")
    assert spans == [(0, 116), (665, 1000)]


def test_a_v1_gap_the_edit_opens_still_refuses():
    """The other half: shifting a V1 item so the plan opens a hole the
    reel did not have must still refuse, naming the new gap."""
    tracks = [{"type": "video", "index": 1,
               "clips": [_clip(0, 100, "head"), _clip(100, 100, "tail")]}]
    shifted = ce.ItemChange(
        track_type="video", track_index=1, item_index=1,
        record_frame=150, duration=100, left_offset=0,
        previous_record=100, previous_duration=100, how=ce.SHIFT,
        name="tail")
    with pytest.raises(ce.PlacementNotVerified) as refusal:
        ce.assert_every_frame_covered(tracks, [shifted], [], row="V1")
    assert "[100, 150]" in str(refusal.value)
