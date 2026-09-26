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


def test_the_reel_spine_never_promotes_an_interpolated_word_to_timing():
    row = segment(
        "host", "alpha unavailable omega", 10.0, 13.0,
        "cam_a.mov", 100.0,
        [word("alpha", 10.0, 10.5),
         {"word": "unavailable", "start": 11.0, "end": 11.4,
          "timed": False},
         word("omega", 12.0, 13.0)],
    )
    spine = spine_for_reel(Moment(10.0, 13.0), {"segments": [row]},
                           ranges=[(10.0, 13.0)])

    block = spine["structure"][0]
    assert [item["word"] for item in block["word_timestamps"]] == [
        "alpha", "omega"]
    assert block["content"]["text"] == "alpha omega"
    assert block["word_timestamps"][1]["source_start"] == pytest.approx(102.0)
    assert spine["undetermined_words"] == [{
        "word": "unavailable",
        "speaker": "host",
        "master_segment_start": 10.0,
        "master_segment_end": 13.0,
        "reason": "no_measured_word_interval",
        "reel_membership": "unknown_without_word_timing",
    }]


def test_the_reel_spine_does_not_interpolate_a_missing_word(monkeypatch):
    from library.tools import timeline_transcript

    def no_interpolation(_words):
        raise AssertionError("reel caption timing must stay measured")

    monkeypatch.setattr(timeline_transcript, "interpolate_untimed_words",
                        no_interpolation)
    row = segment(
        "host", "alpha unavailable omega", 10.0, 13.0,
        "cam_a.mov", 100.0,
        [word("alpha", 10.0, 10.5), {"word": "unavailable"},
         word("omega", 12.0, 13.0)],
    )
    spine = spine_for_reel(Moment(10.0, 13.0), {"segments": [row]},
                           ranges=[(10.0, 13.0)])
    assert spine["undetermined_words"][0]["word"] == "unavailable"


def test_segment_text_words_missing_from_the_alignment_are_named():
    row = segment(
        "host", "alpha missing omega", 10.0, 12.0,
        "cam_a.mov", 100.0,
        [word("alpha", 10.0, 10.5), word("omega", 11.5, 12.0)],
    )
    spine = spine_for_reel(Moment(10.0, 12.0), {"segments": [row]},
                           ranges=[(10.0, 12.0)])

    assert spine["structure"][0]["content"]["text"] == "alpha omega"
    assert spine["undetermined_words"] == [{
        "word": "missing",
        "speaker": "host",
        "master_segment_start": 10.0,
        "master_segment_end": 12.0,
        "reason": "word_not_present_in_transcript_alignment",
        "reel_membership": "unknown_without_word_timing",
    }]


def test_a_phrase_token_does_not_give_each_word_the_same_timing():
    row = segment(
        "host", "AI sees it", 10.0, 11.0,
        "cam_a.mov", 100.0,
        [word("AI sees", 10.0, 10.6), word("it", 10.6, 11.0)],
    )
    spine = spine_for_reel(Moment(10.0, 11.0), {"segments": [row]},
                           ranges=[(10.0, 11.0)])

    assert spine["structure"][0]["content"]["text"] == "it"
    assert [entry["word"] for entry in spine["undetermined_words"]] == [
        "AI", "sees"]
    assert {entry["reason"] for entry in spine["undetermined_words"]} == {
        "phrase_token_has_no_individual_word_timing"}


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


# ── Rendering a reel, not only planning it ──────────────────────────


def test_a_reel_segment_renders_through_the_operation(tmp_path, monkeypatch,
                                                      transcript, moment):
    """Captioning a reel is half; rendering is the half that makes a file.

    Step 4.05's own per-segment entry point, driven through
    `subtitles.render_segment`, against a reel spine. Remotion itself is
    stubbed - what is under test is that the STEP's render path drives a
    reel with no reel branch in it, and binds every segment to the
    reel's timeline. The FILENAME carries no timeline (provenance-rooted
    identity since 2026-09-10: speaker plus source span plus drawing
    digest, so identical pixels share a file and different pixels can
    never overwrite each other) - the timeline survives in the recorded
    binding, which is what this asserts.
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
    # Full canvas, stated: the tight default measures its box off a
    # decoded probe render through the Remotion engine, which is the
    # half this test stubs - geometry is orthogonal to the reel
    # binding under test, and the tight path is covered by the
    # delivery tests where the renderer is real.
    segments = [op.run(props, out, "reel_03", remotion_dir=out,
                       overlay_geometry="full")
                for props in props_list]

    assert all(s is not None for s in segments), "a segment failed to render"
    # bound to the REEL's timeline in the placement record, which is
    # what stops a reel's overlay being mistaken for the master's -
    # while the filename stays timeline-free (provenance plus digest),
    # so different pixels can never overwrite each other
    assert all(s["binding"]["timeline"] == "reel_03" for s in segments), (
        [s["binding"] for s in segments])
    assert len({s["segment_id"] for s in segments}) == len(segments), (
        "two segments captioning different speech share a filename: "
        + str([s["segment_id"] for s in segments]))
    # in reel time
    assert min(s["timeline_start"] for s in segments) == pytest.approx(0.0)
    # and it really wrote the props the renderer reads
    written = list(tmp_path.glob("*_props.json"))
    assert len(written) == len(segments)


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
