"""A region of the timeline is an address, and the domain is part of it.

`library/tools/region.py` is a defect guard before it is a convenience,
so most of what is asserted here is a REFUSAL: a word list that names no
domain, a conversion asked for without a block, an interval with its ends
the wrong way round.

The numbers in `test_the_collision_this_module_exists_for` and
`test_offsets_are_per_block_and_the_sign_is_not_constant` are project
001's real measurements, kept as literals so the test fails if the
premise ever stops being true.
"""

import pytest

from library.tools.region import (
    DomainError,
    MASTER,
    Region,
    TimelineMismatch,
    SOURCE,
    TIMELINE,
    assert_domain,
    domain_of,
    owner_of_track,
    parse,
    read_words,
    resolve,
    to_source_words,
    to_timeline_words,
)
from library.tools.spine_contract import (
    block_at,
    blocks_overlapping,
    source_to_timeline,
    timeline_to_source,
)


def _block(position, timeline_start, timeline_end, clip_id="clip_001",
           source_start=0.0, words=None):
    """One spine block, contract-shaped."""
    duration = timeline_end - timeline_start
    return {
        "position": position,
        "block_type": "speech" if clip_id else "transition_slot",
        "clip_id": clip_id,
        "source_start": source_start if clip_id else None,
        "source_end": (source_start + duration) if clip_id else None,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "word_timestamps": words or [],
        "alignment_method": "whisperx" if clip_id else None,
    }


# Project 001's real spine, reduced to what an address needs.  Eight
# speech-bearing blocks, five non-speech, 56.605s.
_001 = [
    _block("hook", 0.000, 2.398, "clip_011", 0.836),
    _block(1, 2.398, 5.398, None),
    _block(2, 5.398, 8.380, "clip_011", 9.699),
    _block(3, 8.380, 18.427, "clip_017", 30.073),
    _block(4, 18.427, 21.927, None),
    _block(5, 21.927, 32.076, "clip_017", 45.441),
    _block(6, 32.076, 35.076, None),
    _block(7, 35.076, 38.616, "clip_011", 63.135),
    _block(8, 38.616, 41.322, "clip_011", 119.234),
    _block(9, 41.322, 44.322, None),
    _block(10, 44.322, 51.142, "clip_011", 173.639),
    _block(11, 51.142, 52.605, "clip_012", 33.941),
    _block(12, 52.605, 56.605, None),
]


# ── The conversions ──────────────────────────────────────────────────

def test_offsets_are_per_block_and_the_sign_is_not_constant():
    """The reason a conversion needs a block and not a project constant."""
    offsets = {b["position"]: round(b["source_start"] - b["timeline_start"], 3)
               for b in _001 if b["clip_id"] is not None}
    assert len(set(offsets.values())) == 8, offsets
    assert offsets["hook"] == 0.836
    assert offsets[10] == 129.317
    # Block 11 plays later on the timeline than blocks cut from a LATER
    # source second, so its offset runs the other way.  Any implementation
    # that assumes source >= timeline is wrong here.
    assert offsets[11] == -17.201
    assert min(offsets.values()) < 0 < max(offsets.values())


# ── blocks_overlapping ───────────────────────────────────────────────

def test_blocks_overlapping_is_half_open_at_a_shared_boundary():
    """Abutting blocks partition the timeline; a boundary belongs to one."""
    touched = blocks_overlapping(_001, 2.398, 5.398)
    assert [b["position"] for b in touched] == [1]


def test_zero_length_interval_returns_the_containing_block():
    assert block_at(_001, 12.0)["position"] == 3
    assert [b["position"] for b in blocks_overlapping(_001, 12.0, 12.0)] == [3]


def test_past_the_end_of_the_timeline_returns_nothing_rather_than_clamping():
    assert blocks_overlapping(_001, 56.605, 60.0) == []
    assert block_at(_001, 100.0) is None


# ── Region ───────────────────────────────────────────────────────────

def test_region_refuses_a_reversed_or_negative_interval():
    with pytest.raises(ValueError) as exc:
        Region(MASTER, 10.0, 5.0)
    assert "precedes start" in str(exc.value)
    with pytest.raises(ValueError) as exc:
        Region(MASTER, -1.0, 5.0)
    assert "negative" in str(exc.value)


def test_region_clipped_to_a_block_never_exceeds_it():
    window = Region(MASTER, 45.0, 72.0).clipped_to(_001[10])
    assert (window.start, window.end) == (45.0, 51.142)


@pytest.mark.parametrize("bad", [""])
def test_parse_refuses_anything_it_would_have_to_guess_at(bad):
    with pytest.raises(ValueError):
        parse(bad)


# ── resolve ──────────────────────────────────────────────────────────

def test_resolve_turns_a_timestamp_into_footage():
    """The whole point: '45.0-72.0s' becomes clips and source seconds."""
    address = resolve(Region(MASTER, 45.0, 72.0), _001)
    assert address.positions == [10, 11, 12]
    assert address.clip_ids == ["clip_011", "clip_012"]
    spans = {s.block_position: (s.source_start, s.source_end)
             for s in address.source_spans}
    assert spans[10] == (174.317, 180.459)
    assert spans[11] == (33.941, 35.404)


def test_a_non_speech_block_is_reported_but_contributes_no_footage():
    """Block 12 is 001's outro: inside the region, no clip behind it."""
    address = resolve(Region(MASTER, 45.0, 72.0), _001)
    assert 12 in address.positions
    assert 12 not in [s.block_position for s in address.source_spans]


# ── The domain guard ─────────────────────────────────────────────────

_INDEX_WORDS = [{"word": "i", "start": 0.836, "end": 0.872}]
_CAPTION_WORDS = [{"word": "i", "start": 0.000, "end": 0.036}]
_SPINE_WORDS = [{"word": "i", "source_start": 0.836, "source_end": 0.872}]


def test_the_collision_this_module_exists_for():
    """One spoken word, two artifacts, same keys, 0.836s apart on 001."""
    assert _INDEX_WORDS[0].keys() == _CAPTION_WORDS[0].keys()
    drift = _INDEX_WORDS[0]["start"] - _CAPTION_WORDS[0]["start"]
    assert drift == pytest.approx(0.836)
    assert drift == pytest.approx(
        _001[0]["source_start"] - _001[0]["timeline_start"])


def test_bare_start_end_names_no_domain_and_is_refused():
    """The guard REFUSES; it cannot classify - the two are identical."""
    assert domain_of(_INDEX_WORDS) is None
    assert domain_of(_CAPTION_WORDS) is None
    for words in (_INDEX_WORDS, _CAPTION_WORDS):
        with pytest.raises(DomainError) as exc:
            assert_domain(words, SOURCE, "test")
        assert "name no time domain" in str(exc.value)
        assert "0.836" in str(exc.value)


def test_words_in_the_wrong_named_domain_are_refused():
    timeline_words = to_timeline_words(_SPINE_WORDS, _001[0])
    with pytest.raises(DomainError) as exc:
        assert_domain(timeline_words, SOURCE, "test")
    assert "expected source" in str(exc.value)


def test_read_words_refuses_to_relabel_an_already_named_list():
    with pytest.raises(DomainError):
        read_words(_SPINE_WORDS, TIMELINE)


def test_the_conversion_a_naive_splice_would_have_skipped():
    """Copying the key across puts 001's first caption 25 frames late."""
    naive = _INDEX_WORDS[0]["start"]
    converted = to_timeline_words(
        read_words(_INDEX_WORDS, SOURCE), _001[0])[0]["timeline_start"]
    assert converted == pytest.approx(0.0)
    assert naive - converted == pytest.approx(0.836)
    assert round((naive - converted) * 30) == 25


def test_a_conversion_cannot_be_asked_for_without_a_block():
    """There is no project-wide offset, so the block is not optional."""
    with pytest.raises(TypeError):
        to_timeline_words(_SPINE_WORDS)


# ── The timeline is part of the type ────────────────────────────
#
# Firstmate-decided 2026-09-05: `Region` carries WHICH TIMELINE, `Scope`
# carries WHAT SHAPE. A master-timeline region and a reel-timeline region
# are the domain collision above one level up - a reel is its keep ranges
# laid end to end, so reel second 12.0 and master second 12.0 are
# different moments and nothing about the numbers says so.


def test_a_region_knows_which_timeline_it_is_on():
    assert Region(MASTER, 1.0, 2.0).timeline is MASTER
    assert Region("reel_03", 1.0, 2.0).timeline == "reel_03"
    # "" is how `subtitle_segment_id.timeline_scope` reports an unnamed
    # timeline, and it must not become a timeline literally called "".
    assert Region("", 1.0, 2.0).timeline is MASTER
    assert Region("  ", 1.0, 2.0).timeline is MASTER


def test_the_same_span_on_two_timelines_is_two_different_regions():
    """The whole point: identical numbers, different moments."""
    assert Region(MASTER, 12.0, 15.0) != Region("reel_03", 12.0, 15.0)


def test_mixing_timelines_is_refused_rather_than_converted():
    master = Region(MASTER, 45.0, 72.0)
    with pytest.raises(TimelineMismatch) as exc:
        resolve(master, _001, timeline="reel_03")
    assert "reel_03" in str(exc.value)


def test_a_text_form_that_disagrees_with_its_argument_is_refused():
    """One silent winner is how a reel span gets read against the master."""
    with pytest.raises(TimelineMismatch):
        parse("reel_03@45.0-72.0", timeline="reel_09")
