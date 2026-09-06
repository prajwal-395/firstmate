"""A reel has a spine, it is in the reel's own time, and its words are SOURCE.

The captain's correction of 2026-09-04 is the premise:

    "each reel does have an audio spine, its just the audio in the
     timeline itself"

So the test that matters most is not that `spine_for_reel` returns a
dict - it is that the spine it returns drives the pipeline's OWN
subtitle step, unmodified, with no reel branch anywhere in that step.
`test_the_pipeline_step_captions_a_reel_from_this_spine` is that test,
and it is why `reel_subtitles.py` can be deleted rather than extended.
"""
from dataclasses import dataclass

import pytest

from library.tools import operations
from library.tools import region as region_mod
from library.tools.reel_spine import (
    ALIGNMENT_METHOD,
    ReelSpineError,
    spine_for_reel,
)
from library.tools.spine_contract import REQUIRED_BLOCK_KEYS, is_speech_block


@dataclass
class Moment:
    timeline_start: float
    timeline_end: float
    cta_start: float = None
    cta_end: float = None
    number: int = 1


def word(text, start, end):
    """A transcript word: bare start/end, MASTER timeline seconds."""
    return {"word": text, "start": start, "end": end}


def segment(speaker, text, tl_start, tl_end, source_file, source_start,
            words):
    return {
        "speaker": speaker, "text": text,
        "timeline_start": tl_start, "timeline_end": tl_end,
        "source_file": source_file, "resolve_item_id": source_file,
        "source_start": source_start,
        "source_end": source_start + (tl_end - tl_start),
        "words": words,
    }


def spoken(speaker, text, tl_start, source_file, source_start,
           per_word=0.4):
    """A segment whose words tile its span evenly."""
    parts = text.split()
    words = [word(w, tl_start + i * per_word, tl_start + (i + 1) * per_word)
             for i, w in enumerate(parts)]
    return segment(speaker, text, tl_start, tl_start + len(parts) * per_word,
                   source_file, source_start, words)


@pytest.fixture
def transcript():
    return {"segments": [
        spoken("host", "so the thing nobody tells you about shipping",
               10.0, "cam_a.mov", 100.0),
        spoken("guest", "completely agree with that", 14.0, "cam_b.mov", 200.0),
    ]}


@pytest.fixture
def moment():
    return Moment(timeline_start=10.0, timeline_end=16.0)


# ── The contract ────────────────────────────────────────────────────


def test_the_spine_satisfies_the_block_contract(transcript, moment):
    spine = spine_for_reel(moment, transcript)
    assert spine["structure"], "a reel with speech must produce blocks"
    for block in spine["structure"]:
        missing = [k for k in REQUIRED_BLOCK_KEYS if k not in block]
        assert not missing, f"block {block['position']} is missing {missing}"
        assert is_speech_block(block)
        assert block["alignment_method"] == ALIGNMENT_METHOD


def test_blocks_are_in_REEL_time_not_master_time(transcript, moment):
    """The whole reason this module exists. A reel is its keep ranges laid
    end to end, so a spine timed against the master drifts by the length of
    everything removed before it."""
    spine = spine_for_reel(moment, transcript)
    first = spine["structure"][0]
    assert first["timeline_start"] == pytest.approx(0.0), (
        "the first block of a reel starts at reel second 0, not at the "
        f"master second it was cut from ({first['timeline_start']})")
    # and they run consecutively, because the ranges are laid end to end
    ends = [b["timeline_end"] for b in spine["structure"]]
    starts = [b["timeline_start"] for b in spine["structure"]]
    for previous_end, next_start in zip(ends, starts[1:]):
        assert next_start >= previous_end - 1e-6


def test_word_timings_are_SOURCE_seconds_not_timeline_seconds(transcript,
                                                              moment):
    """AGENTS.md 6: word timings use source_start/source_end.

    This is the collision `region.py` exists for - the temporal index and
    the subtitle plan both use bare start/end for DIFFERENT domains. A
    reel adds a third clock, so getting this wrong is easy and silent.
    """
    spine = spine_for_reel(moment, transcript)
    for block in spine["structure"]:
        words = block["word_timestamps"]
        assert words, "a speech block must carry populated word timings"
        # proven, not asserted by naming convention
        region_mod.assert_domain(words, region_mod.SOURCE, "the reel spine")
        for w in words:
            assert "start" not in w and "end" not in w, (
                "bare start/end is the undeclared form region.py refuses")
        # the first word sits at the block's own source start
        assert words[0]["source_start"] == pytest.approx(
            block["source_start"], abs=0.01)


def test_each_block_keeps_its_own_source_binding(transcript, moment):
    """The offset is per block and the sign is not constant - eight
    distinct offsets on 001, spread 146.5s. A single correction factor is
    always wrong."""
    spine = spine_for_reel(moment, transcript)
    by_clip = {b["clip_id"]: b for b in spine["structure"]}
    assert by_clip["cam_a.mov"]["source_start"] == pytest.approx(100.0)
    assert by_clip["cam_b.mov"]["source_start"] == pytest.approx(200.0)
    offsets = {b["clip_id"]: b["source_start"] - b["timeline_start"]
               for b in spine["structure"]}
    assert len(set(offsets.values())) > 1, (
        "this fixture is meant to have DIFFERENT per-block offsets; if "
        "they collapsed to one the test below proves nothing")


# ── What a reel cannot supply is SAID, not invented ─────────────────


def test_a_segment_with_no_source_binding_is_dropped_and_counted():
    """`attribute_to_clip` leaves these None when speech straddles a cut.
    A block with an invented clip_id would fail the contract downstream."""
    good = spoken("host", "this one is bound", 10.0, "cam_a.mov", 100.0)
    orphan = spoken("host", "this one straddles a cut", 12.0, "cam_a.mov",
                    120.0)
    # what attribute_to_clip really leaves behind when speech crosses a cut
    orphan["resolve_item_id"] = None
    orphan["source_file"] = None
    orphan["source_start"] = None
    orphan["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 16.0),
                           {"segments": [good, orphan]})
    assert spine["dropped_segments"] == 1
    assert all(b["clip_id"] for b in spine["structure"])


def test_a_reel_whose_speech_was_all_cut_refuses_rather_than_returning_empty():
    """An empty spine is not a spine. Nothing downstream can caption it, so
    it fails here with the reel still in hand."""
    far_away = spoken("host", "nowhere near this reel", 500.0, "cam_a.mov",
                      100.0)
    with pytest.raises(ReelSpineError) as raised:
        spine_for_reel(Moment(10.0, 16.0), {"segments": [far_away]})
    assert "no speech survived" in str(raised.value)


def test_a_transcript_with_no_segments_refuses():
    with pytest.raises(ReelSpineError):
        spine_for_reel(Moment(10.0, 16.0), {"segments": []})


def test_a_closer_from_earlier_in_the_episode_is_not_dropped():
    """A reel's closing CTA may come from BEFORE its body.

    The standalone captioner tested membership against the envelope from
    the first range's start to the last range's end. On a reel whose CTA
    precedes its body that envelope inverts and silently drops every
    caption. Ranges are tested individually here.
    """
    body = spoken("host", "the body of the reel", 100.0, "cam_a.mov", 500.0)
    closer = spoken("host", "follow for more", 10.0, "cam_a.mov", 20.0)
    spine = spine_for_reel(
        Moment(100.0, 104.0), {"segments": [closer, body]},
        ranges=[(100.0, 104.0), (10.0, 13.0)])      # body THEN earlier closer
    texts = [b["content"]["text"] for b in spine["structure"]]
    assert any("body" in t for t in texts), "the body was dropped"
    assert any("follow" in t for t in texts), (
        "the closer was dropped - the envelope bug is back")


# ── The point of the whole exercise ─────────────────────────────────


def test_the_pipeline_step_captions_a_reel_from_this_spine(transcript,
                                                           moment):
    """THE test. Step 4.01, unmodified, captions a reel.

    No reel branch in the step, no standalone module, and the operation
    registry is what invokes it - so the reel path is driven by a named
    operation rather than by a script. This is what makes
    `reel_subtitles.py` deletable rather than foldable.
    """
    spine = spine_for_reel(moment, transcript)
    plan = operations.get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]

    assert entries, "step 4.01 produced no captions from a reel spine"
    # in REEL time
    assert min(e["timeline_start"] for e in entries) == pytest.approx(0.0)
    # both speakers survive, which is the per-speaker styling signal
    assert {e["speaker"] for e in entries} == {"host", "guest"}
    # and every card sits inside the reel, not the master
    reel_end = max(b["timeline_end"] for b in spine["structure"])
    assert max(e["timeline_end"] for e in entries) <= reel_end + 1e-6


def test_the_step_groups_by_measured_pixels_not_by_word_count(transcript,
                                                              moment):
    """Why the fold is a DELETION and not a copy.

    `reel_subtitles.caption_groups` packed 3-6 words a card. Step 4.01
    groups by measured pixels through `safe_area.fits_in_box`, which is
    what AGENTS.md 10.2 requires. Folding the word-count rule in would
    have replaced a conforming implementation with a weaker one.
    """
    spine = spine_for_reel(moment, transcript)
    plan = operations.get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]
    assert any("fit_scale" in e for e in entries), (
        "no card reports a fit_scale, so nothing was measured in pixels")


# ── Two mics, one sentence ──────────────────────────────────────────
#
# Migrated from tests/test_reel_subtitles.py, which tested this against
# the standalone captioner. The behaviour moves to the PRODUCER because
# it is a fact about the transcript: by the time 4.01 sees a spine, one
# sentence on two mics is indistinguishable from two people saying the
# same thing.


def test_mic_bleed_is_dropped_and_the_primary_mic_keeps_the_line():
    """The same words on both mics become ONE block, attributed to the mic
    that heard them first. Two blocks here means every two-camera reel
    captions every sentence twice, under two different speakers."""
    primary = spoken("host", "that is exactly the point", 10.0,
                     "cam_a.mov", 100.0)
    bleed = spoken("guest", "that is exactly the point", 10.08,
                   "cam_b.mov", 200.0)
    spine = spine_for_reel(Moment(10.0, 13.0),
                           {"segments": [primary, bleed]})
    assert len(spine["structure"]) == 1
    assert spine["bleed_blocks_dropped"] == 1
    assert spine["structure"][0]["speaker"] == "host", (
        "the primary mic hears the words first, and its speaker is the "
        "right attribution")


def test_a_partial_bleed_is_still_a_bleed():
    """The second mic often catches only part of the sentence, so a subset
    counts."""
    spine = spine_for_reel(Moment(10.0, 13.0), {"segments": [
        spoken("host", "that is exactly the point i wanted", 10.0,
               "cam_a.mov", 100.0),
        spoken("guest", "exactly the point", 10.1, "cam_b.mov", 200.0),
    ]})
    assert len(spine["structure"]) == 1
    assert spine["bleed_blocks_dropped"] == 1


def test_a_real_interruption_keeps_BOTH_speakers():
    """Two people talking over each other is not a duplicate. Dropping one
    would delete speech that was really said - what to DRAW is 4.01's
    decision, and it can see the overlap once the cards exist."""
    spine = spine_for_reel(Moment(10.0, 14.0), {"segments": [
        spoken("host", "so what i think we should do here is", 10.0,
               "cam_a.mov", 100.0),
        spoken("guest", "no absolutely not", 11.2, "cam_b.mov", 200.0),
    ]})
    assert len(spine["structure"]) == 2
    assert spine["bleed_blocks_dropped"] == 0
    assert {b["speaker"] for b in spine["structure"]} == {"host", "guest"}


def test_the_same_word_far_apart_is_not_a_bleed():
    """Bleed is decided by word overlap AND timing overlap. Two people
    saying "exactly" five seconds apart are two people."""
    spine = spine_for_reel(Moment(10.0, 20.0), {"segments": [
        spoken("host", "exactly", 10.0, "cam_a.mov", 100.0),
        spoken("guest", "exactly", 15.0, "cam_b.mov", 200.0),
    ]})
    assert len(spine["structure"]) == 2
    assert spine["bleed_blocks_dropped"] == 0


# ── A row that carries two speakers ─────────────────────────────────
#
# The captain looked at reel 05 and the captions were wrong. Two of the
# three things they saw come from ONE cause, and it is not the grouper:
# a transcript row on Craig's mic carries his sentence AND, at its tail,
# the first words of Akshita's, picked up as bleed. `_drop_bleed` cannot
# see it - the rows are not duplicates of each other, one just ends
# inside the other. So the block carries two speakers, and step 4.01,
# which groups WITHIN a block, groups across the change.
#
# The numbers below are reel 05's own, read off
# `pipeline_output/scratch/timeline_transcript/transcript.json` rows
# 204 and 205 of the field test. Craig's row is given a clip binding
# here because `timeline_transcript` leaves it unbound and
# `spine_for_reel` drops unbound rows - a separate defect, named in
# CAPTION_UNANCHORED_ROWS, that is not what these tests are about.


def _reel_05_frame_1616_rows():
    """Reel 05's real rows either side of the card the captain saw.

    Craig says "...what is going on here", Akshita starts "So ranking
    tells Google," while his mic is still open, and his row's last two
    words are her first two - the same words, at the same instant.
    """
    craig_words = [
        word("what", 612.731, 612.932), word("is", 613.052, 613.153),
        word("going", 613.193, 613.454), word("on", 613.574, 613.654),
        word("here", 613.715, 613.875), word("yeah", 614.818, 615.059),
        word("so", 615.079, 615.139), word("ranking", 615.159, 615.34),
    ]
    akshita_words = [
        word("So", 614.949, 615.129), word("ranking", 615.169, 615.449),
        word("tells", 615.489, 615.75), word("Google,", 615.89, 616.25),
    ]
    return [
        segment("Craig", "what is going on here yeah so ranking",
                609.380, 615.340, "craig.mov", 1290.0, craig_words),
        segment("Akshita", "So ranking tells Google,",
                614.949, 616.250, "akshita.mov", 1293.918, akshita_words),
    ]


def test_a_row_carrying_two_speakers_is_cut_at_the_speaker_change():
    """Reel 05's frame-1616 card. It read "here yeah so ranking" - the end
    of Craig's sentence and the start of Akshita's on one card.

    Two people cannot say one word at one instant, so "so ranking" on
    Craig's mic at 615.079 is Akshita's, heard 0.13s after her own mic
    took it. It is cut from HIS block, which is where the card boundary
    is decided: 4.01 never groups across a block.
    """
    spine = spine_for_reel(Moment(609.0, 619.0),
                           {"segments": _reel_05_frame_1616_rows()})
    assert spine["cross_speaker_words_cut"] == 2
    craig = [b for b in spine["structure"] if b["speaker"] == "Craig"]
    assert len(craig) == 1
    text = craig[0]["content"]["text"]
    assert text == "what is going on here yeah", text
    assert [w["word"] for w in craig[0]["word_timestamps"]][-1] == "yeah"


def test_the_card_the_captain_saw_no_longer_carries_two_speakers():
    """The same rows, through step 4.01, as cards.

    Before the cut the last Craig card read "here yeah so ranking" and
    the next Akshita card read "so ranking tells google," - the same
    phrase twice, back to back, under two different speakers' styling.
    That is what the captain saw at frames 1616 and 1628.
    """
    spine = spine_for_reel(Moment(609.0, 619.0),
                           {"segments": _reel_05_frame_1616_rows()})
    plan = operations.get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")
    entries = sorted(plan["subtitle_plan"]["subtitle_entries"],
                     key=lambda e: e["timeline_start"])

    craig_cards = [e["text"] for e in entries if e["speaker"] == "Craig"]
    assert not any("ranking" in t for t in craig_cards), craig_cards
    assert craig_cards[-1] == "here yeah", craig_cards

    akshita = " ".join(e["text"] for e in entries
                       if e["speaker"] == "Akshita")
    assert akshita == "so ranking tells google,"


def test_no_speech_is_lost_by_the_cut():
    """The words removed from Craig's block are still captioned, by the
    speaker who said them. A cut that deletes speech is worse than the
    card it fixes, and the whole rule rests on this being true."""
    spine = spine_for_reel(Moment(609.0, 619.0),
                           {"segments": _reel_05_frame_1616_rows()})
    said = set()
    for block in spine["structure"]:
        said |= {w["word"].lower().strip(",.") for w in block["word_timestamps"]}
    assert {"so", "ranking", "tells", "google"} <= said


def test_a_cut_that_would_orphan_a_word_is_CANCELLED():
    """The one thing the whole rule rests on, made to fail.

    Four rows, each overlapping the next on one word. Two of them are
    NOTHING but the other mic's words - the host's "yeah exactly" (its
    "yeah" is the guest's, its "exactly" the next guest row's) and the
    guest's "exactly right" (its "right" is the next host row's). Taken
    on their own both cuts are correct, and together they delete
    "exactly" from the reel: no other block carries it.

    So the host's cut is given up ENTIRELY, which is the conservative
    direction - a block that may still carry a foreign word, never a word
    that no block carries. The guest row, whose own words survive
    elsewhere, is still emptied.
    """
    rows = [
        segment("guest", "yeah okay", 10.5, 11.6, "cam_b.mov", 200.0,
                [word("yeah", 10.5, 11.2), word("okay", 11.25, 11.6)]),
        segment("host", "yeah exactly", 11.0, 12.4, "cam_a.mov", 100.0,
                [word("yeah", 11.0, 11.4), word("exactly", 12.0, 12.4)]),
        segment("guest", "exactly right", 12.1, 13.0, "cam_b.mov", 210.0,
                [word("exactly", 12.1, 12.5), word("right", 12.6, 13.0)]),
        segment("host", "right then", 12.7, 13.6, "cam_a.mov", 110.0,
                [word("right", 12.7, 13.1), word("then", 13.2, 13.6)]),
    ]
    spine = spine_for_reel(Moment(10.0, 14.0), {"segments": rows})

    said = [w["word"] for b in spine["structure"]
            for w in b["word_timestamps"]]
    assert "exactly" in said, said
    assert spine["cross_speaker_blocks_emptied"] == 1
    host_blocks = [b["content"]["text"] for b in spine["structure"]
                   if b["speaker"] == "host"]
    assert host_blocks == ["yeah exactly", "right then"], host_blocks


def test_an_interruption_INSIDE_a_row_is_reported_and_not_cut():
    """A foreign run bracketed by the row's own speaker on both sides is
    two people talking over each other, not a bleed tail. Cutting it
    would leave the block with a hole in it, joining two passages that
    were never adjacent - so it is counted and left alone."""
    host = segment("host", "i think exactly right and then we move on",
                   10.0, 13.6, "cam_a.mov", 100.0,
                   [word("i", 10.0, 10.4), word("think", 10.4, 10.8),
                    word("exactly", 10.8, 11.2), word("right", 11.2, 11.6),
                    word("and", 11.6, 12.0), word("then", 12.0, 12.4),
                    word("we", 12.4, 12.8), word("move", 12.8, 13.2),
                    word("on", 13.2, 13.6)])
    guest = segment("guest", "exactly right no way", 10.79, 12.39,
                    "cam_b.mov", 200.0,
                    [word("exactly", 10.79, 11.19), word("right", 11.19, 11.59),
                     word("no", 11.6, 12.0), word("way", 12.0, 12.39)])
    spine = spine_for_reel(Moment(10.0, 14.0), {"segments": [host, guest]})
    host_block = [b for b in spine["structure"] if b["speaker"] == "host"]
    assert len(host_block) == 1
    assert host_block[0]["content"]["text"] == \
        "i think exactly right and then we move on"
    assert spine["cross_speaker_words_cut"] == 0
    assert spine["cross_speaker_middle_runs"] == 1


def test_the_same_word_at_a_different_second_is_not_a_bleed_tail():
    """Two people saying "exactly" seconds apart are two people. The cut
    reads the word AND the instant, so a shared vocabulary is not
    evidence of anything."""
    spine = spine_for_reel(Moment(10.0, 20.0), {"segments": [
        spoken("host", "so what i think exactly", 10.0, "cam_a.mov", 100.0),
        spoken("guest", "exactly what i meant", 15.0, "cam_b.mov", 200.0),
    ]})
    assert spine["cross_speaker_words_cut"] == 0
    assert len(spine["structure"]) == 2


def test_a_block_that_is_NOTHING_but_bleed_is_dropped_and_counted():
    """A row whose every word another mic carries at the same instant has
    nothing of its own left after the cut, so it goes rather than being
    kept as an empty block the spine contract would reject."""
    spine = spine_for_reel(Moment(10.0, 14.0), {"segments": [
        segment("host", "the whole point", 10.0, 11.2, "cam_a.mov", 100.0,
                [word("the", 10.0, 10.4), word("whole", 10.4, 10.8),
                 word("point", 10.8, 11.2)]),
        segment("guest", "point of", 10.9, 11.7, "cam_b.mov", 200.0,
                [word("point", 10.9, 11.3), word("of", 11.35, 11.7)]),
        segment("host", "of it entirely", 11.4, 12.6, "cam_a.mov", 110.0,
                [word("of", 11.4, 11.8), word("it", 11.8, 12.1),
                 word("entirely", 12.1, 12.6)]),
    ]})
    assert spine["cross_speaker_blocks_emptied"] == 1
    assert [b["speaker"] for b in spine["structure"]] == ["host", "host"]


def test_no_card_from_a_reel_spine_ever_splits_a_word(transcript, moment):
    """A card's words are a contiguous SLICE of its block's words.

    The exact invariant, with no threshold in it: `split_into_groups`
    partitions by index, so it cannot cut a word in half or put one word
    on two cards, and this pins that end to end rather than by reading
    it. The fragment `goo` the captain was shown was never on a card -
    it was `reel_build`'s own filename, which slugged the card text and
    truncated it at thirty characters (`slug(cap["text"], "notext")[:30]`,
    deleted with `reel_subtitles.py` in #559).
    """
    spine = spine_for_reel(moment, transcript)
    plan = operations.get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]
    by_block: dict = {}
    for entry in sorted(entries, key=lambda e: e["timeline_start"]):
        by_block.setdefault(entry["spine_block_position"], []).append(entry)

    for block in spine["structure"]:
        cards = by_block.get(block["position"], [])
        if not cards:
            continue
        carried = [w["word"].lower() for card in cards
                   for w in card["words"]]
        spoken_words = [w["word"].lower()
                        for w in block["word_timestamps"]]
        assert carried == spoken_words, (
            f"block {block['position']}'s cards are not a partition of its "
            f"words: {carried} vs {spoken_words}")
        for card in cards:
            assert card["text"].split() == [w["word"] for w in card["words"]]


def test_positions_are_renumbered_after_a_bleed_drop():
    """`position` is block identity. A gap in it after a drop would make
    two spines of the same reel disagree about which block is which."""
    spine = spine_for_reel(Moment(10.0, 20.0), {"segments": [
        spoken("host", "first line here", 10.0, "cam_a.mov", 100.0),
        spoken("host", "the duplicated sentence", 12.0, "cam_a.mov", 120.0),
        spoken("guest", "the duplicated sentence", 12.08, "cam_b.mov", 200.0),
        spoken("host", "last line here", 15.0, "cam_a.mov", 150.0),
    ]})
    positions = [b["position"] for b in spine["structure"]]
    assert positions == list(range(len(positions))), positions


# ── Rendering a reel, not only planning it ──────────────────────────


def test_a_reel_segment_renders_through_the_operation(tmp_path, monkeypatch,
                                                      transcript, moment):
    """Captioning a reel is half; rendering is the half that makes a file.

    Step 4.05's own per-segment entry point, driven through
    `subtitles.render_segment`, against a reel spine. Remotion itself is
    stubbed - what is under test is that the STEP's render path drives a
    reel with no reel branch in it, and names the segment for the reel's
    timeline.
    """
    import types

    spine = spine_for_reel(moment, transcript)
    plan = operations.get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")

    from library.steps.step_4_05_render_subtitles.generate_remotion_props import (
        generate_subtitle_props_per_block,
    )
    props_list = generate_subtitle_props_per_block(
        plan["subtitle_plan"], fps=30, width=1080, height=1920,
        audio_spine=spine)
    assert props_list, "4.05's props generator produced nothing for a reel"

    op = operations.get("subtitles.render_segment")
    module = operations.load_step_module(op.owning_dir, op.body)
    monkeypatch.setattr(module, "subprocess", types.SimpleNamespace(
        run=lambda *a, **k: types.SimpleNamespace(returncode=0, stderr=""),
        TimeoutExpired=Exception))

    out = str(tmp_path)
    segments = [op.run(props, out, "reel_03", remotion_dir=out)
                for props in props_list]

    assert all(s is not None for s in segments), "a segment failed to render"
    # named for the REEL's timeline, which is what stops a reel's overlay
    # overwriting the master's in a per-project directory
    assert all("reel-03" in s["segment_id"] for s in segments), (
        [s["segment_id"] for s in segments])
    # in reel time
    assert min(s["timeline_start"] for s in segments) == pytest.approx(0.0)
    # and it really wrote the props the renderer reads
    written = list(tmp_path.glob("*_props.json"))
    assert len(written) == len(segments)


def test_the_render_operation_accepts_a_region_scope():
    """`subtitles.render_segment` is the seam a region-scoped redo reaches,
    so it must declare REGION - increment 5 threads the scope through it."""
    op = operations.get("subtitles.render_segment")
    assert operations.REGION in op.scopes
    op.check_scope(operations.scope_mod.region("45.0-72.0"))


# ── The closer plays last, whenever it was cut from ─────────────────


def _reel_with_a_borrowed_closer():
    """A reel whose CTA is cut from EARLIER in the episode than its body."""
    body = spoken("host", "and that is why it matters so much to me",
                  100.0, "cam_a.mov", 500.0, per_word=0.35)
    closer = spoken("host", "follow for more like this", 10.0,
                    "cam_a.mov", 20.0, per_word=0.35)
    return ({"segments": [closer, body]},
            [(100.0, 103.5), (10.0, 11.75)])      # body THEN closer


def test_blocks_are_ordered_by_the_REEL_not_the_master():
    """`position` is block identity and 4.05 names rendered segments by it.

    A closer cut from earlier in the episode plays LAST. Sorting by master
    second puts it first, so position stops meaning play order and the
    rendered segment names disagree with the timeline.
    """
    transcript, ranges = _reel_with_a_borrowed_closer()
    spine = spine_for_reel(Moment(100.0, 103.5), transcript, ranges=ranges)
    starts = [b["timeline_start"] for b in spine["structure"]]
    assert starts == sorted(starts), (
        f"blocks are not in reel order: {starts}")
    assert "why it matters" in spine["structure"][0]["content"]["text"], (
        "the body must be first on the reel")
    assert "follow for more" in spine["structure"][-1]["content"]["text"], (
        "the closer must be last on the reel")


def test_no_caption_card_spans_the_closers_seam():
    """Migrated from tests/test_reel_subtitles.py.

    The body's last words and the closer's first are different passages of
    the episode; a card joining them is a sentence nobody said. The
    standalone captioner enforced this with an explicit seam list. It is
    now STRUCTURAL: 4.01 groups within a block, and the closer is its own
    block, so a card cannot span the seam. Proven rather than assumed.
    """
    transcript, ranges = _reel_with_a_borrowed_closer()
    spine = spine_for_reel(Moment(100.0, 103.5), transcript, ranges=ranges)
    seam = spine["structure"][0]["timeline_end"]

    plan = operations.get("subtitles.plan").run(
        spine, brand_effect={}, brand_style={}, project_folder="")
    spanning = [e for e in plan["subtitle_plan"]["subtitle_entries"]
                if e["timeline_start"] < seam - 1e-6
                and e["timeline_end"] > seam + 1e-6]
    assert not spanning, (
        f"a card spans the closer's seam at {seam:.2f}s: {spanning}")


def test_a_bad_take_seam_is_NOT_a_flush_point():
    """Also migrated. The seams a bad-take cut leaves join speech the
    editor made contiguous on purpose, so a card reading across one is a
    sentence as spoken. Only the CLOSER's seam is a boundary - and it is
    one because it is a block boundary, not because of a seam list."""
    one = spoken("host", "so the point i want to make", 10.0, "cam_a.mov",
                 100.0)
    # a bad take removed between them; both halves are one sentence
    two = spoken("host", "is that it compounds", 20.0, "cam_a.mov", 130.0)
    spine = spine_for_reel(Moment(10.0, 22.0), {"segments": [one, two]},
                           ranges=[(10.0, 12.8), (20.0, 21.6)])
    assert len(spine["structure"]) == 2
    # they are ADJACENT on the reel - the cut left no gap
    assert spine["structure"][1]["timeline_start"] == pytest.approx(
        spine["structure"][0]["timeline_end"], abs=0.01)


def test_a_segment_straddling_a_cut_is_CLIPPED_not_dropped():
    """A cut rarely lands exactly on a segment boundary.

    Dropping the whole segment because one edge fell in a removed take
    loses speech the reel really plays, silently - which is the worst way
    to lose it. The surviving words are kept and the block is clipped to
    them.
    """
    # eight words at 0.4s each: 10.0-13.2. The range ends mid-segment.
    straddler = spoken("host", "one two three four five six seven eight",
                       10.0, "cam_a.mov", 100.0)
    spine = spine_for_reel(Moment(10.0, 12.0), {"segments": [straddler]},
                           ranges=[(10.0, 12.0)])
    block = spine["structure"][0]
    assert len(block["word_timestamps"]) < 8, "nothing was clipped"
    assert block["word_timestamps"], "everything was dropped"
    # the block still starts where its first surviving word does
    assert block["word_timestamps"][0]["source_start"] == pytest.approx(
        block["source_start"], abs=0.01)


def test_unanchored_row_on_a_real_clip_is_captioned():
    """A row with no binding of its own, whose PLAYED words sit on one clip.

    The row straddles a cut so `attribute_to_clip` returned None and it
    used to be dropped whole. The part the reel plays does have an
    answer, and this is it. Measured on the captain's nineteen no such
    row exists - every uncaptioned word sits where its speaker has no
    clip - so this pins the mechanism rather than a live case.
    """
    from library.tools import reel_spine

    transcript = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 4.0, "speaker": "Craig",
         "resolve_item_id": "clip-a", "source_start": 100.0,
         "source_end": 104.0, "text": "anchored speech here",
         "words": [{"word": "anchored", "start": 0.0, "end": 1.0,
                    "timed": True},
                   {"word": "speech", "start": 1.0, "end": 2.0,
                    "timed": True}]},
        # No binding, but its words sit inside clip-a's reach.
        {"timeline_start": 2.5, "timeline_end": 3.5, "speaker": "Craig",
         "resolve_item_id": None, "source_start": None, "source_end": None,
         "text": "wholly different unrelated wording",
         "words": [{"word": "wholly", "start": 2.5, "end": 3.0,
                    "timed": True},
                   {"word": "different", "start": 3.0, "end": 3.5,
                    "timed": True}]},
    ]}

    class _Moment:
        timeline_start, timeline_end = 0.0, 4.0
        call_to_action = None

    spine = reel_spine.spine_for_reel(_Moment(), transcript, [(0.0, 4.0)])
    added = [b for b in spine["structure"] if b["from_unanchored_row"]]
    assert len(added) == 1, "the played part of the row has a clip"
    assert added[0]["clip_id"] == "clip-a"
    assert added[0]["unanchored_band"] == "unique"
    assert spine["unanchored_blocks"] == 1
    assert spine["unanchored_seconds"] > 0


def test_unanchored_row_with_no_clip_under_it_is_still_dropped():
    """Refusing on a measured absence, not inventing a binding."""
    from library.tools import reel_spine

    transcript = {"segments": [
        {"timeline_start": 0.0, "timeline_end": 2.0, "speaker": "Craig",
         "resolve_item_id": "clip-a", "source_start": 100.0,
         "source_end": 102.0, "text": "anchored speech here",
         "words": [{"word": "anchored", "start": 0.0, "end": 1.0,
                    "timed": True},
                   {"word": "speech", "start": 1.0, "end": 2.0,
                    "timed": True}]},
        # Craig has no clip anywhere near this, so it stays dropped.
        {"timeline_start": 8.0, "timeline_end": 9.0, "speaker": "Craig",
         "resolve_item_id": None, "source_start": None, "source_end": None,
         "text": "wholly different unrelated wording",
         "words": [{"word": "wholly", "start": 8.0, "end": 8.5,
                    "timed": True},
                   {"word": "different", "start": 8.5, "end": 9.0,
                    "timed": True}]},
    ]}

    class _Moment:
        timeline_start, timeline_end = 0.0, 10.0
        call_to_action = None

    spine = reel_spine.spine_for_reel(_Moment(), transcript, [(0.0, 10.0)])
    assert spine["unanchored_blocks"] == 0
    assert spine["dropped_segments"] == 1


# ── An uncaptioned stretch is REPORTED, never guessed at ────────────


def test_an_uncaptioned_stretch_is_reported_with_its_reel_seconds():
    """A row nothing can bind is dropped, and the count alone does not
    say WHERE. The captain looking at reel 05 needs the seconds."""
    good = spoken("host", "this one is bound", 10.0, "cam_a.mov", 100.0)
    orphan = spoken("host", "nobody wrote these words down", 12.4,
                    "cam_a.mov", 120.0)
    orphan["resolve_item_id"] = None
    orphan["source_file"] = None
    orphan["source_start"] = None
    orphan["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 18.0),
                           {"segments": [good, orphan]})
    spans = spine["unbindable_spans"]
    assert len(spans) == 1
    assert spans[0]["speaker"] == "host"
    assert spans[0]["reel_start"] == pytest.approx(2.4, abs=0.01)
    assert spine["unbindable_seconds"] > 0.0
    assert spine["unbindable_seconds"] == pytest.approx(
        sum(s["seconds"] for s in spans))


def test_the_seconds_are_the_WORDS_not_the_row_envelope():
    """A row with no binding is usually Whisper joining two utterances
    across a silence. Field test row 206 spans 20.7s and holds 6.0s of
    words; reporting the envelope would treble what is really missing."""
    good = spoken("host", "bound", 10.0, "cam_a.mov", 100.0)
    orphan = segment("host", "start end", 12.0, 24.0, "cam_a.mov", 120.0,
                     [word("start", 12.0, 12.4), word("end", 23.6, 24.0)])
    orphan["resolve_item_id"] = None
    orphan["source_file"] = None
    orphan["source_start"] = None
    orphan["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 26.0), {"segments": [good, orphan]})
    assert spine["unbindable_seconds"] == pytest.approx(0.8, abs=0.05), (
        "the envelope is 12 seconds and the words are 0.8 of it")


def test_a_reel_whose_speech_all_binds_reports_nothing(transcript, moment):
    """The other direction. A gate that fires on correct output reads as
    a defect where there is none (AGENTS.md 10.4)."""
    spine = spine_for_reel(moment, transcript)
    assert spine["unbindable_spans"] == []
    assert spine["unbindable_seconds"] == 0.0


def test_a_row_whose_seconds_another_block_captions_is_not_reported():
    """The second mic hears the same sentence, so the same reel seconds
    already carry a card. Reporting them would be the check firing on
    correct output."""
    good = spoken("host", "the same words at the same instant", 10.0,
                  "cam_a.mov", 100.0)
    bleed = spoken("guest", "the same words at the same instant", 10.02,
                   "cam_b.mov", 200.0)
    bleed["resolve_item_id"] = None
    bleed["source_file"] = None
    bleed["source_start"] = None
    bleed["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 18.0),
                           {"segments": [good, bleed]})
    assert spine["dropped_segments"] == 1, "the bleed row is still dropped"
    assert spine["unbindable_spans"] == [], (
        "its words are on screen under the anchored row's card")


def test_a_row_cut_out_of_the_reel_entirely_is_not_reported():
    """Its speech was removed on purpose, so the reel is missing nothing."""
    good = spoken("host", "this one is bound", 10.0, "cam_a.mov", 100.0)
    elsewhere = spoken("host", "cut from this reel", 300.0, "cam_a.mov",
                       400.0)
    elsewhere["resolve_item_id"] = None
    elsewhere["source_file"] = None
    elsewhere["source_start"] = None
    elsewhere["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 16.0),
                           {"segments": [good, elsewhere]})
    assert spine["unbindable_spans"] == []
