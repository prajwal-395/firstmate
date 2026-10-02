"""Two reels that share a closer SHARE one caption file.

Several reels legitimately close on the same spoken CTA (same clip, same
source span); with identical pixels they must compute one filename, so the
closer renders once. The captain's sixteen real reel names are a repository
fixture so this runs on every machine. History: docs/evidence/reel_shared_closer.md.
Different-pixel and two-content-key refusals: tests/unit/captions/test_subtitle_render.py.
"""

import json
from pathlib import Path


from library.tools.subtitle_segment_id import (
    segment_binding,
    segment_identifier,
)

FIELD_TEST_REEL_NAMES = (
    Path(__file__).resolve().parents[2] / "fixtures" / "field_test_16_reel_names.json")


def _field_test_reel_names() -> list:
    """All sixteen names the field test produced, from the fixture.

    Never from a path outside this repository: that is what made this
    check an always-skip on every machine but one.
    """
    return json.loads(FIELD_TEST_REEL_NAMES.read_text())["reel_names"]


# One closer, shared. The shape a shared CTA really has: same clip, same
# source seconds, same block position within each reel.
CLOSER = {"source_clip_id": "clip_004", "source_start": 742.1,
          "source_end": 746.9, "block_position": "closer", "speaker": None}

CLOSER_DIGEST = "c" * 64


def _binding(timeline):
    return segment_binding(timeline=timeline, **CLOSER)


def test_reels_sharing_one_closer_share_one_filename():
    """The old collision is the new sharing.

    Every reel in a pass gets its own timeline value, and all of them
    compute the same filename for identical closer pixels. The readable
    half carries no timeline, so the sixteen real reel names cannot
    separate what the digest already proved identical - which is what
    renders the shared closer ONCE instead of sixteen times.
    """
    names = {segment_identifier(_binding(n), CLOSER_DIGEST)
             for n in _field_test_reel_names()}
    assert len(names) == 1, names

