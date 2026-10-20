"""The generation pin is a number, however it arrives.

Measured live on 2026-10-20, on the Ren Scratch - breaktest project, while
running the punch list's section 12 demonstration: `ren revise` prepared a
request pinned to base generation "1", the recorded head WAS 1, and the
request was refused with

    request base generation 1 is stale; recorded head is 1

The pin had arrived as a STRING - the `edit_spec` schema declares
`base_generation` a non-empty string, and `ren revise` carries the
model's program straight into the request - and the staleness check
compared it with an int. `1 != "1"`, so EVERY request pinned to the head
was refused as stale, and the wording named the same number twice.

These tests name that: the pin is converted where the request's invariant
lives, and a pin that cannot be read refuses rather than being dropped.
"""

from __future__ import annotations

import pytest

from library.tools import edit_request
from library.tools.edit_request import EditRequest, EditRequestError


def _request(**extra) -> EditRequest:
    base = {"id": "req-1", "project": "Podcast", "timeline": "Reel 09",
            "operations": [edit_request.EditOperation(
                op="clip.set_property",
                target=edit_request.EditTarget(unique_id="clip-1",
                                               name="a.mov",
                                               track_type="video",
                                               track_index=1),
                params={"key": "Pan", "value": 5.0},
                expected_effect="set Pan on clip-1")]}
    base.update(extra)
    return EditRequest(**base)


def test_a_string_pin_becomes_the_number_it_names():
    """Catches: the pin compared as a string against an int generation,
    so a request pinned to the head was refused as stale."""
    request = _request(expected_base_generation="1")
    assert request.expected_base_generation == 1
    assert request.expected_base_generation == 1  # equal to the recorded head


def test_an_int_pin_is_unchanged():
    assert _request(expected_base_generation=7).expected_base_generation == 7


def test_no_pin_stays_no_pin():
    assert _request().expected_base_generation is None


def test_a_pin_that_is_not_a_number_refuses():
    """A pin nobody can read cannot protect the base, so it refuses
    instead of being dropped."""
    with pytest.raises(EditRequestError, match="not a generation number"):
        _request(expected_base_generation="head")


def test_the_model_revision_pin_is_converted_too():
    assert _request(expected_model_revision="4").expected_model_revision == 4
