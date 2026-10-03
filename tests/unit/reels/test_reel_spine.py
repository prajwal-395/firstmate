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
from __future__ import annotations
from dataclasses import dataclass
import pytest
from library.tools import operations
from library.tools import region as region_mod
from library.tools.reel_spine import (
    ALIGNMENT_METHOD,
    spine_for_reel,
)
from library.tools.spine_contract import REQUIRED_BLOCK_KEYS, is_speech_block
import json
from library.tools import reel_semantic_visual as span
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelPlan,
    ReelTimeline,
    check_span_plan,
    verify_reel,
)
from library.tools import scope as scope_mod
from library.tools.region import MASTER, Region
from types import SimpleNamespace
from library.tools.reel_ledger import (
    OutOfWindowRange,
    audit_ranges,
)


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
    # speech that all binds reports nothing - a gate that fires on correct
    # output reads as a defect where there is none (AGENTS.md 10.4)
    assert spine["unbindable_spans"] == []
    assert spine["unbindable_seconds"] == 0.0


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
    # segment text words missing from the alignment are named, too
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


def test_the_reel_spine_rejoins_measured_subtokens_into_sentence_words():
    row = segment(
        "host", "CRM and ChatGPT and CRMs,", 10.0, 12.4,
        "cam_a.mov", 100.0,
        [word("C", 10.0, 10.1), word("Rm", 10.1, 10.4),
         word("and", 10.4, 10.7), word("Chat", 10.7, 11.0),
         word("GPT", 11.0, 12.0), word("and", 12.0, 12.1),
         word("C", 12.1, 12.2), word("RMs,", 12.2, 12.4)],
    )
    spine = spine_for_reel(Moment(10.0, 12.4), {"segments": [row]},
                           ranges=[(10.0, 12.4)])

    block = spine["structure"][0]
    assert block["content"]["text"] == "CRM and ChatGPT and CRMs,"
    assert [entry["word"] for entry in block["word_timestamps"]] == [
        "CRM", "and", "ChatGPT", "and", "CRMs,"]
    assert block["word_timestamps"][0]["source_start"] == pytest.approx(100.0)
    assert block["word_timestamps"][0]["source_end"] == pytest.approx(100.4)
    assert block["word_timestamps"][2]["source_start"] == pytest.approx(100.7)
    assert block["word_timestamps"][2]["source_end"] == pytest.approx(102.0)
    assert block["word_timestamps"][4]["source_start"] == pytest.approx(102.1)
    assert block["word_timestamps"][4]["source_end"] == pytest.approx(102.4)
    assert spine["undetermined_words"] == []


def test_a_word_at_the_half_open_range_end_is_not_captioned_from_float_noise():
    row = segment(
        "host", "alpha best", 10.0, 12.3,
        "cam_a.mov", 100.0,
        [word("alpha", 10.0, 11.0),
         word("best", 12.0 - 1e-10, 12.3)],
    )
    spine = spine_for_reel(Moment(10.0, 12.3), {"segments": [row]},
                           ranges=[(10.0, 12.0)])

    assert spine["structure"][0]["content"]["text"] == "alpha"
    assert [entry["word"] for entry in
            spine["structure"][0]["word_timestamps"]] == ["alpha"]


def test_a_phrase_token_is_captioned_as_one_timed_phrase():
    row = segment(
        "host", "AI sees it", 10.0, 11.0,
        "cam_a.mov", 100.0,
        [word("AI sees", 10.0, 10.6), word("it", 10.6, 11.0)],
    )
    spine = spine_for_reel(Moment(10.0, 11.0), {"segments": [row]},
                           ranges=[(10.0, 11.0)])

    block = spine["structure"][0]
    assert block["content"]["text"] == "AI sees it"
    assert [entry["word"] for entry in block["word_timestamps"]] == [
        "AI sees", "it"]
    assert block["word_timestamps"][0]["source_start"] == pytest.approx(100.0)
    assert block["word_timestamps"][0]["source_end"] == pytest.approx(100.6)
    assert spine["undetermined_words"] == []

    # ... and a phrase token does not caption a sentence phrase it doesn't match
    row["text"] = "AI saw it"
    spine = spine_for_reel(Moment(10.0, 11.0), {"segments": [row]},
                           ranges=[(10.0, 11.0)])
    assert spine["structure"][0]["content"]["text"] == "it"
    assert [entry["word"] for entry in spine["undetermined_words"]] == [
        "AI", "saw"]
    assert spine["undetermined_words"][0]["reason"] == (
        "phrase_token_has_no_individual_word_timing")


# ── What a reel cannot supply is SAID, not invented ─────────────────


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


def test_reel03_question_after_removed_take_reaches_the_caption_plan():
    """A positive reel-time gap can still hide a removed source span.

    Reel 03's transcript row carries the lead-in and its recovered CRM
    question on both sides of a struck take. The question starts at
    20.48s on the reel. The spine must split at the compressed gap so
    step 4.01 can map the question's source seconds onto its reel seconds.
    """
    from library.tools.operations import get

    question = "what's the best CRM if I run a 10 person law firm?"
    row_words = [
        word("instead", 237.700, 237.981),
        word("typing", 238.021, 238.462),
        word("best", 238.743, 239.084),
        word("CRMs...", 239.204, 239.851),
        word("What's", 241.891, 242.052),
        word("the", 242.072, 242.132),
        word("best", 242.172, 242.433),
        word("CRM", 242.473, 242.654),
        word("if", 242.694, 242.774),
        word("I", 242.814, 242.874),
        word("run", 242.914, 243.095),
        word("a", 243.135, 243.235),
        word("10", 243.245, 243.395),
        word("person", 243.396, 243.696),
        word("law", 243.716, 243.877),
        word("firm?", 243.917, 244.181),
    ]
    row = segment(
        "Akshita", "instead typing best CRMs... " + question,
        237.700, 244.181, "LC4932.MXF", 167.08779166666665, row_words)
    ranges = [(218.271, 238.710), (241.850, 248.270)]
    reel = Moment(timeline_start=218.271, timeline_end=248.270)

    spine = spine_for_reel(reel, {"segments": [row]}, ranges=ranges)
    recovery = [block for block in spine["structure"]
                if block["timeline_start"] >= 20.0]
    assert len(recovery) == 1
    assert recovery[0]["timeline_start"] == pytest.approx(20.48)

    plan = get("subtitles.plan").run(
        spine, caption_case="lowercase", brand_effect={}, brand_style={},
        project_folder="")
    entries = plan["subtitle_plan"]["subtitle_entries"]
    spoken = [timing for entry in entries for timing in entry["words"]
              if 20.48 <= timing["start"] < 22.77]
    assert [timing["word"] for timing in spoken] == [
        "what's", "the", "best", "CRM", "if", "i", "run", "a",
        "10", "person", "law", "firm?",
    ]
    assert spoken[0]["start"] == pytest.approx(20.48)
    assert spoken[-1]["end"] == pytest.approx(22.77)


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
    # Two people talking over each other is not a duplicate. Dropping one
    # would delete speech that was really said - what to DRAW is 4.01's
    # decision, and it can see the overlap once the cards exist.
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
# a transcript row on SpeakerTwo's mic carries his sentence AND, at its tail,
# the first words of SpeakerOne's, picked up as bleed. `_drop_bleed` cannot
# see it - the rows are not duplicates of each other, one just ends
# inside the other. So the block carries two speakers, and step 4.01,
# which groups WITHIN a block, groups across the change.
#
# The numbers below are reel 05's own, read off
# `pipeline_output/scratch/timeline_transcript/transcript.json` rows
# 204 and 205 of the field test. SpeakerTwo's row is given a clip binding
# here because `timeline_transcript` leaves it unbound and
# `spine_for_reel` drops unbound rows - a separate defect, named in
# CAPTION_UNANCHORED_ROWS, that is not what these tests are about.


def _reel_05_frame_1616_rows():
    """Reel 05's real rows either side of the card the captain saw.

    SpeakerTwo says "...what is going on here", SpeakerOne starts "So ranking
    tells Google," while his mic is still open, and his row's last two
    words are her first two - the same words, at the same instant.
    """
    speakertwo_words = [
        word("what", 612.731, 612.932), word("is", 613.052, 613.153),
        word("going", 613.193, 613.454), word("on", 613.574, 613.654),
        word("here", 613.715, 613.875), word("yeah", 614.818, 615.059),
        word("so", 615.079, 615.139), word("ranking", 615.159, 615.34),
    ]
    speakerone_words = [
        word("So", 614.949, 615.129), word("ranking", 615.169, 615.449),
        word("tells", 615.489, 615.75), word("Google,", 615.89, 616.25),
    ]
    return [
        segment("SpeakerTwo", "what is going on here yeah so ranking",
                609.380, 615.340, "speakertwo.mov", 1290.0, speakertwo_words),
        segment("SpeakerOne", "So ranking tells Google,",
                614.949, 616.250, "speakerone.mov", 1293.918, speakerone_words),
    ]


def test_a_row_carrying_two_speakers_is_cut_at_the_speaker_change():
    """Reel 05's frame-1616 card. It read "here yeah so ranking" - the end
    of SpeakerTwo's sentence and the start of SpeakerOne's on one card.

    Two people cannot say one word at one instant, so "so ranking" on
    SpeakerTwo's mic at 615.079 is SpeakerOne's, heard 0.13s after her own mic
    took it. It is cut from HIS block, which is where the card boundary
    is decided: 4.01 never groups across a block.
    """
    spine = spine_for_reel(Moment(609.0, 619.0),
                           {"segments": _reel_05_frame_1616_rows()})
    assert spine["cross_speaker_words_cut"] == 2
    speakertwo = [b for b in spine["structure"] if b["speaker"] == "SpeakerTwo"]
    assert len(speakertwo) == 1
    text = speakertwo[0]["content"]["text"]
    assert text == "what is going on here yeah", text
    assert [w["word"] for w in speakertwo[0]["word_timestamps"]][-1] == "yeah"
    # no speech is lost by the cut: the words removed from SpeakerTwo's block are
    # still captioned, by the speaker who said them
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
        {"timeline_start": 0.0, "timeline_end": 4.0, "speaker": "SpeakerTwo",
         "resolve_item_id": "clip-a", "source_start": 100.0,
         "source_end": 104.0, "text": "anchored speech here",
         "words": [{"word": "anchored", "start": 0.0, "end": 1.0,
                    "timed": True},
                   {"word": "speech", "start": 1.0, "end": 2.0,
                    "timed": True}]},
        # No binding, but its words sit inside clip-a's reach.
        {"timeline_start": 2.5, "timeline_end": 3.5, "speaker": "SpeakerTwo",
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
    """A row nothing can bind (`attribute_to_clip` leaves it None when
    speech straddles a cut) is dropped and counted - an invented clip_id
    would fail the contract downstream - and the count alone does not say
    WHERE. The captain looking at reel 05 needs the seconds."""
    good = spoken("host", "this one is bound", 10.0, "cam_a.mov", 100.0)
    orphan = spoken("host", "nobody wrote these words down", 12.4,
                    "cam_a.mov", 120.0)
    orphan["resolve_item_id"] = None
    orphan["source_file"] = None
    orphan["source_start"] = None
    orphan["source_end"] = None
    spine = spine_for_reel(Moment(10.0, 18.0),
                           {"segments": [good, orphan]})
    assert spine["dropped_segments"] == 1
    assert all(b["clip_id"] for b in spine["structure"])
    spans = spine["unbindable_spans"]
    assert len(spans) == 1
    assert spans[0]["speaker"] == "host"
    assert spans[0]["reel_start"] == pytest.approx(2.4, abs=0.01)
    assert spine["unbindable_seconds"] > 0.0
    assert spine["unbindable_seconds"] == pytest.approx(
        sum(s["seconds"] for s in spans))
    # A row with no binding is usually Whisper joining two utterances
    # across a silence. Field test row 206 spans 20.7s and holds 6.0s of
    # words; reporting the envelope would treble what is really missing.
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


# --------------------------------------------------------------------------
# From test_reel_span_record.py
#
# The span plan is recorded where the pipeline reads it, and refused when empty.
#
# PR 774's resolver distinguishes a model that chose stillness
# (`span_no_events_planned` - a decision) from a model whose every beat
# was refused (`span_every_event_dropped` - the absence of a decision
# surviving). This file proves the three halves that connect that
# distinction to the build:
#
# 1. A RECORD: `span_record_for_build` resolves one reel's answer and
#    returns it in the V6 record's own convention - same REVIEW area,
#    same `{"format": ..., "plans": [...]}` envelope, same merge-per-reel
#    write - filed by `write_span_records` and read back by
#    `read_span_records` / `span_record_for_reel`.
# 2. A GRADE: F23 (`check_span_plan`) refuses an all-refused plan and
#    passes a deliberate stillness, and `verify_reel` threads it through.
# 3. No look values anywhere on the path: a resolved moment carries what
#    is SHOWN and its measured window, never a colour, size, font or
#    motion value.
#
# The placer is OUT on purpose: moments carry `shows` as free-text
# provenance and segments need declared look values, so laying moments
# on PR 776's windows needs its own change carrying those decisions.
# What is proven here is that an all-refused plan cannot build green
# and silently while that placer is still missing.

def _transcript():
    """Two keep ranges' worth of timed words, in MASTER seconds."""
    return {"segments": [{
        "words": [
            {"word": "he", "start": 10.5, "end": 10.7, "timed": True},
            {"word": "plays", "start": 11.0, "end": 11.4, "timed": True},
            {"word": "with", "start": 11.5, "end": 11.8, "timed": True},
            {"word": "his", "start": 12.0, "end": 12.2, "timed": True},
            {"word": "mind", "start": 12.5, "end": 13.0, "timed": True},
            {"word": "has", "start": 20.5, "end": 20.9, "timed": True},
            {"word": "vision", "start": 21.5, "end": 22.0, "timed": True},
        ]}]}


def _ranges():
    return [(10.0, 14.0), (20.0, 26.0)]


class _Moment:
    number = 9
    timeline_name = "Reel 09 - plays with his mind"


def _answer_file(project, beats):
    responses = project / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True, exist_ok=True)
    (responses / "reel_span_09.json").write_text(
        json.dumps({"span_visual_plan": beats}), encoding="utf-8")


def _resolving_beat():
    return {"segment": 1, "shows": "a mind, illustrated",
            "anchor_phrase": "his mind", "lead_seconds": 0.2,
            "why": "the line is about playing with the mind"}


def _refused_beat():
    # "goalkeeper" is spoken nowhere in `_transcript`: the resolver
    # drops this as `anchor_phrase_not_found`, and with nothing else
    # proposed the plan lands on SPAN_EVERY_EVENT_DROPPED. This is the
    # concrete input that makes the F23 refusal fire.
    return {"segment": 1, "shows": "a goalkeeper",
            "anchor_phrase": "goalkeeper",
            "why": "not in the speech"}


# ── The record: resolve, file, read back ─────────────────────────────

def test_a_resolved_plan_records_its_moments(tmp_path):
    project = tmp_path / "proj"
    _answer_file(project, [_resolving_beat()])
    record = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert record["reel"] == "Reel 09 - plays with his mind"
    assert record["basis"] == span.SPAN_EVENTS_PLANNED
    assert record["proposed"] == 1 and record["resolved"] == 1
    assert len(record["moments"]) == 1
    assert record["moments"][0]["event_start"] == 2.0 - 0.2
    assert record["dropped"] == []


def test_no_answer_file_is_not_a_decision_for_no_pictures(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    record = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert record["basis"] == span.SPAN_NOT_PLANNED
    assert record["moments"] == []


# ── The file: the V6 convention, a span payload ──────────────────────

def test_records_merge_per_reel_the_v6_way(tmp_path):
    project = tmp_path / "proj"
    _answer_file(project, [_resolving_beat()])
    first = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    path = span.write_span_records(str(project), [first])
    assert path.endswith("span_visual_plans.json")
    with open(path, "r", encoding="utf-8") as handle:
        stored = json.load(handle)
    assert stored["format"] == "span_visual_plans/1"

    # A partial build recording a second reel must not delete the first.
    other = dict(first, reel="Reel 10 - vision")
    span.write_span_records(str(project), [other])
    records = span.read_span_records(str(project))
    assert span.span_record_for_reel(records, "Reel 09 - plays with his mind")[
        "basis"] == span.SPAN_EVENTS_PLANNED
    assert span.span_record_for_reel(records, "Reel 10 - vision") is not None

    # Re-recording a reel replaces it whole, including with an emptier basis.
    span.write_span_records(
        str(project), [dict(first, basis=span.SPAN_NO_EVENTS_PLANNED,
                            moments=[])])
    records = span.read_span_records(str(project))
    assert span.span_record_for_reel(records, "Reel 09 - plays with his mind")[
        "basis"] == span.SPAN_NO_EVENTS_PLANNED
    assert span.span_record_for_reel(records, "No such reel") is None
    assert span.span_record_for_reel(None, "Reel 09 - plays with his mind") is None


# ── The grade: every-dropped refuses, stillness passes ───────────────

def _record(basis, proposed=0, reasons=()):
    return {"reel": "Reel 09 - plays with his mind", "basis": basis,
            "entries": [{}] * proposed,
            "dropped": [{"element": "a goalkeeper", "reason": reason,
                         "what_the_reason_means": "...",
                         "detail": "..."} for reason in reasons],
            "moments": [],
            "proposed": proposed, "resolved": 0}


def test_an_all_refused_span_plan_fails_f23():
    findings = check_span_plan(
        "Reel 09 - plays with his mind",
        _record(span.SPAN_EVERY_EVENT_DROPPED, proposed=1,
                reasons=["anchor_phrase_not_found"]))
    assert [f.finding_class for f in findings] == [FindingClass.F23]
    assert findings[0].severity == "error"
    assert "every one was refused" in findings[0].message
    assert "anchor_phrase_not_found" in findings[0].message


# ── End to end: resolve, record, grade ───────────────────────────────

def test_every_dropped_and_no_events_reach_different_outcomes(tmp_path):
    """The defect this change closes: counting moments alone cannot tell
    an all-refused plan from a deliberately still reel. The RECORD can,
    and the grade follows it."""
    project = tmp_path / "proj"

    _answer_file(project, [_refused_beat()])
    refused = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 09 - plays with his mind")
    assert refused["basis"] == span.SPAN_EVERY_EVENT_DROPPED
    assert refused["proposed"] == 1 and refused["resolved"] == 0
    assert refused["moments"] == []
    assert [d["reason"] for d in refused["dropped"]] == [
        "anchor_phrase_not_found"]
    span.write_span_records(str(project), [refused])

    _answer_file(project, [])
    still = span.span_record_for_build(
        _Moment(), _transcript(), _ranges(), str(project),
        fps=30.0, timeline_name="Reel 10 - vision")
    # an empty answer is a DECISION for no pictures
    assert still["basis"] == span.SPAN_NO_EVENTS_PLANNED
    assert still["moments"] == [] and still["dropped"] == []
    span.write_span_records(str(project), [still])

    records = span.read_span_records(str(project))
    refused_findings = check_span_plan(
        "Reel 09 - plays with his mind",
        span.span_record_for_reel(records, "Reel 09 - plays with his mind"))
    still_findings = check_span_plan(
        "Reel 10 - vision",
        span.span_record_for_reel(records, "Reel 10 - vision"))
    assert [f.finding_class for f in refused_findings] == [FindingClass.F23]
    assert still_findings == []


def _plan():
    return ReelPlan(
        reel_name="Reel 09 - plays with his mind", reel_number=9,
        plan_seconds=10.0, plan_frames=300.0,
        span_start=0.0, span_end=10.0, placements=(),
        keep_ranges=((0.0, 10.0),))


def _timeline():
    return ReelTimeline(
        reel_name="Reel 09 - plays with his mind", fps=30.0,
        total_frames=300, video_items=(), audio_items=(),
        caption_items=())


def test_verify_reel_refuses_an_all_refused_span_plan():
    result = verify_reel(
        _plan(), _timeline(),
        span_plan=_record(span.SPAN_EVERY_EVENT_DROPPED, proposed=1,
                         reasons=["anchor_phrase_not_found"]))
    assert FindingClass.F23 in [f.finding_class for f in result.errors]


# --------------------------------------------------------------------------
# From test_reel_span_visual.py
#
# A span can be planned from its speech: request, accept, refuse.
#
# Blocker 1 (`docs/SPAN_RENDERER_CAPABILITY.md` §3.1): no model plans a
# span. `reel_semantic_visual` builds a planning request only for the V6
# overlay layer. This file tests the span's own ask - a request built from
# measured word windows, a schema the answer must satisfy, and a resolver
# that binds picture events to real word timings and REFUSES what it
# cannot bind - without Resolve and without a model.
#
# The shape matched is the V6 one in the same module: `write_request`
# (request file), `read_answer` (answer file), `motion_graphics_plan`
# `resolve_plan` (named drops). The span reuses that pattern with its own
# reasons, because the overlay machinery does not generalise: it resolves
# element keys, anchors, colours and copy against the overlay roster, and
# a span beat is none of those - it is a noun illustrated, cued to words,
# leading them.
#
# What the reference needs (`docs/ANIMATION_FIRST_REFERENCE.md` §1):
# every picture event illustrates a NOUN from the spoken line, and events
# lead their nouns. So each event names `shows` (the noun), an
# `anchor_phrase` (words from its own segment), and `lead_seconds` (how
# far before the anchor the picture lands). The output carries what is
# SHOWN and no look values - no colour, no size, no font, no motion
# values - and an entry carrying any of those is refused rather than
# read past.

def _words():
    """The measured evidence, in REEL seconds: segment 1 is reel 0-4,
    segment 2 is reel 4-10."""
    return span.span_segment_words(_ranges(), _transcript())


# ── The request is built from measured word windows ──────────────────

def test_the_request_carries_each_segments_measured_words(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    path = span.write_span_request(
        _Moment(), _transcript(), _ranges(), str(project), fps=30.0)
    assert path.endswith("reel_span_09.json")
    with open(path, "r", encoding="utf-8") as handle:
        request = json.load(handle)
    assert request["step_id"] == "reel_span_visual"
    assert "span_visual_plan" in request["expected_schema"]
    context = request["context"]
    assert "mind" in context and "vision" in context
    words = _words()
    assert [w["word"] for w in words[0]] == [
        "he", "plays", "with", "his", "mind"]
    assert (words[0][4]["start"], words[0][4]["end"]) == (2.5, 3.0)
    assert [w["word"] for w in words[1]] == ["has", "vision"]


# ── The resolver binds what it can ───────────────────────────────────

def test_a_noun_anchored_event_with_a_lead_resolves():
    resolved = span.resolve_span_plan(
        [{"segment": 1, "shows": "a mind, illustrated",
          "anchor_phrase": "his mind", "lead_seconds": 0.2,
          "why": "the line is about playing with the mind"}],
        segment_words=_words(), ranges=_ranges())
    assert len(resolved.moments) == 1
    moment = resolved.moments[0]
    assert moment["anchor_start"] == 2.0
    assert moment["anchor_end"] == 3.0
    assert moment["event_start"] == 2.0 - 0.2
    assert moment["timing_basis"] == "word_window:his mind"
    assert moment["shows"] == "a mind, illustrated"
    assert resolved.basis == span.SPAN_EVENTS_PLANNED


# ── ... and refuses what it cannot ───────────────────────────────────

def test_an_event_the_resolver_cannot_bind_is_refused_by_name():
    table = [
        ({"segment": 1, "shows": "a goalkeeper",
          "anchor_phrase": "goalkeeper", "why": "not in the speech"},
         "anchor_phrase_not_found"),
        ({"segment": 1, "shows": "a whistle on the first word",
          "anchor_phrase": "he", "lead_seconds": 1.0,
          "why": "a lead longer than the anchor's distance to the edge"},
         "beat_outside_segment"),
        ({"segment": 1, "shows": "a mind", "anchor_phrase": "mind",
          "color": "#123456", "font_size": 96, "entrance": "scale",
          "why": "taste smuggled into a picture plan"},
         "look_value_in_picture_plan"),
        ({"segment": 1, "shows": "a mind", "why": "no anchor at all"},
         "no_anchor_declared"),
    ]
    for beat, reason in table:
        resolved = span.resolve_span_plan(
            [beat], segment_words=_words(), ranges=_ranges())
        assert resolved.moments == [], reason
        assert resolved.basis == span.SPAN_EVERY_EVENT_DROPPED, reason
        assert [d.reason for d in resolved.dropped] == [reason]


# ── The answer file reads like the V6 one ────────────────────────────

def test_a_malformed_span_answer_reads_as_unanswered(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "llm_responses").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses" / "reel_span_09.json").write_text(
        '{"span_visual_plan": "not a list"}', encoding="utf-8")
    assert span.read_span_answer(str(project), 9) is None


# --------------------------------------------------------------------------
# From test_scope_reel.py
#
# A reel is a SHAPE of scope, not a kind of region.
#
# Firstmate-decided 2026-09-05. `Region.timeline` answers WHICH TIMELINE;
# `Scope.kind` answers WHAT SHAPE - PROJECT, CLIP, REGION, REEL. The two
# axes are independent, and a reel cannot be a REGION for two measured
# reasons:
#
#   cardinality - a reel is a LIST of keep ranges with its bad takes cut
#                 out (`reel_build.keep_ranges`); a REGION is one span;
#   time base   - the ranges are on the reel's own timeline, which is its
#                 kept ranges laid end to end, so the same number means a
#                 different moment than on the master.
#
# Bolting either onto REGION would make one of the two silent.

def test_a_reel_range_is_not_a_bare_pair_of_floats():
    """Pairs are accepted at the door and become Regions immediately."""
    reel = scope_mod.reel([(0.0, 5.0)], timeline="reel_03")
    assert reel.reel_ranges[0] == Region("reel_03", 0.0, 5.0)
    with pytest.raises(scope_mod.ScopeError):
        scope_mod.reel([3.0], timeline="reel_03")


def test_a_malformed_reel_is_refused_by_name():
    table = [
        # empty would read as the whole project and redo everything
        (lambda: scope_mod.reel([], timeline="reel_03"), "whole project"),
        (lambda: scope_mod.reel([Region("reel_03", 0.0, 5.0),
                                 Region("reel_09", 6.0, 7.0)]),
         "ONE timeline"),
        # reel_time returns the first containing range, so an overlap
        # gives one second two answers
        (lambda: scope_mod.reel([(0.0, 5.0), (3.0, 8.0)], timeline="reel_03"),
         "overlap"),
    ]
    for build, says in table:
        with pytest.raises(scope_mod.ScopeError) as exc:
            build()
        assert says in str(exc.value)


def test_play_order_is_preserved_and_time_order_is_not_required():
    """A closing CTA legitimately sits EARLIER on the master than the body
    (`reel_build.reel_ranges` appends it LAST), so ordering the ranges by
    time would move the closer into the middle of the reel."""
    reel = scope_mod.reel([(30.0, 40.0), (5.0, 8.0)], timeline="reel_03")
    assert [r.start for r in reel.reel_ranges] == [30.0, 5.0]


def test_reel_ranges_on_a_non_reel_scope_are_refused():
    """__post_init__ was EXTENDED to REEL, not loosened for it."""
    with pytest.raises(scope_mod.ScopeError) as exc:
        scope_mod.Scope(scope_mod.PROJECT,
                        reel_ranges=(Region(MASTER, 0.0, 1.0),))
    assert "reel" in str(exc.value).lower()


# --------------------------------------------------------------------------
# From test_reel_out_of_window.py
#
# A placed range must touch its reel's declared windows - or else.
#
# Reel 13 of the field test played 7.6 seconds drawn from another
# moment's pool after its own closer: the stored closer ended at
# 342.03s, Reel 05's declared body opens at 342.038s, and the snap
# widened the closer end to the bound segment edge at 349.54s.
# `reel_ledger.audit_ranges` is the build-time assertion that catches
# the next such range, and the ledger it files is the WHY on disk.
# These tests pin both halves with the measured numbers - synthetic
# windows, never a real project (tests never reach one).

def _moment(body, closer=None):
    cta = None
    if closer is not None:
        cta = SimpleNamespace(timeline_start=closer[0],
                              timeline_end=closer[1])
    return SimpleNamespace(timeline_start=body[0], timeline_end=body[1],
                           call_to_action=cta)


# The measured shape: Reel 13 declared, Reel 05 declared, and what the
# snap made of Reel 13's closer (cta_end 342.03 -> 349.54 through
# SpeakerTwo's opening "So").
R13_BODY = (889.92, 956.64)
R13_CLOSER = (328.608, 342.03)
R05_BODY = (342.038, 413.851)
R13_RANGES_UNENDED = [(889.89, 956.68), (328.54, 349.54)]

SIBLINGS = {
    13: {"body": R13_BODY, "closer": R13_CLOSER},
    5: {"body": R05_BODY, "closer": (179.83, 192.391)},
}


def test_clean_ranges_and_small_word_edge_cover_pass_silently():
    ledger = audit_ranges(
        number=13, staging="s", final="f",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        ranges=[(889.92, 956.64), (328.608, 342.03)],
        sibling_windows=SIBLINGS)
    assert ledger["disjoint"] == []
    assert all(row["overhang_seconds"] == {"before": 0.0, "after": 0.0}
               for row in ledger["ranges"])
    assert [row["origin"] for row in ledger["ranges"]] == ["body", "closer"]
    # Reel 02's measured cover (closer end +0.22s over "bio.") reads
    # as a small overhang into nobody's pool - kept, unattributed.
    ledger = audit_ranges(
        number=2, staging="s", final="f",
        stored_body=(127.84, 160.83), stored_closer=(319.28, 328.231),
        ranges=[(127.84, 160.83), (319.28, 328.45)],
        sibling_windows={2: {"body": (127.84, 160.83),
                             "closer": (319.28, 328.231)}})
    assert ledger["disjoint"] == []
    assert ledger["ranges"][1]["overhang_seconds"]["after"] == (
        pytest.approx(0.219))
    assert ledger["ranges"][1]["invades"] == []


def test_reel_13_overextension_is_kept_named_and_attributed():
    """The defect shape: kept (word-edge cover is legitimate), but the
    7.51s overhang is measured and Reel 05's pool is named."""
    ledger = audit_ranges(
        number=13, staging="s", final="f",
        stored_body=R13_BODY, stored_closer=R13_CLOSER,
        repaired_body=(889.89, 956.68),
        repaired_closer=(328.54, 349.54),
        ranges=R13_RANGES_UNENDED,
        sibling_windows=SIBLINGS,
        repair_moves=[{"boundary": "cta_end", "was": 342.03,
                       "now": 349.54, "through": "So"}])
    assert ledger["disjoint"] == []
    closer_row = ledger["ranges"][1]
    assert closer_row["origin"] == "closer"
    assert closer_row["overhang_seconds"]["after"] == pytest.approx(7.51)
    invaded = closer_row["invades"]
    assert len(invaded) == 1
    assert invaded[0]["reel"] == 5
    assert invaded[0]["window"] == "body"
    assert invaded[0]["seconds"] == pytest.approx([342.038, 349.54])
    # The repair move that did it is on the record.
    assert ledger["repaired"]["moves"][0]["boundary"] == "cta_end"


def test_wholly_foreign_range_refuses_with_ledger_attached():
    """A range no declared window touches is refused for this reel -
    and the refusal carries the ledger, because a skipped reel is
    exactly when the WHY is needed."""
    with pytest.raises(OutOfWindowRange) as caught:
        audit_ranges(
            number=13, staging="s", final="f",
            stored_body=R13_BODY, stored_closer=R13_CLOSER,
            ranges=[(889.92, 956.64), (500.0, 510.0)],
            sibling_windows=SIBLINGS)
    assert "500.00-510.00" in str(caught.value)
    ledger = caught.value.ledger
    assert ledger["disjoint"] == [1]
    assert ledger["ranges"][1]["invades"] == []
