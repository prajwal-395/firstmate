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
