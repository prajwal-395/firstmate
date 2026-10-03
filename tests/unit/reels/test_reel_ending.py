"""Where a reel ends, what draws over its tail, and what refuses.

Reel 13, 2026-09-11: an ending expressed as a keep-range EXTENSION
crossed the master's own cut, admitted twelve frames of the next
speaker, and took the 18-frame switch-off with it - the tail element
no longer fitted the shot it landed on, so `treatment_verify` undid
it. One cause, both of the captain's complaints. Every test below is
synthetic under `tmp_path` (AGENTS.md 8); none reaches Resolve.
"""
from __future__ import annotations
import json
import math
import os
from types import SimpleNamespace
import pytest
from library.tools import reel_build
from library.tools import reel_ending
from library.tools import reel_look
from library.tools import reel_cta_treatment as cta_rx
from library.tools.reel_proposal import CallToAction
from library.tools import closer_fit as fit
import sys
from pathlib import Path
from library.tools.subtitle_segment_id import (
    segment_binding,
    segment_identifier,
)
import ast
import inspect


def _words(*tokens, start=10.0):
    words, cursor = [], start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _transcript():
    return {"segments": [{
        "text": "alpha beta gamma delta epsilon",
        "words": _words("alpha", "beta", "gamma", "delta", "epsilon")}]}


def _shots():
    """Two back-to-back master shots, speaker A then speaker B."""
    return [
        SimpleNamespace(timeline_start=10.0, timeline_end=11.5,
                        source_in=50.0, track_index=1, speaker="A"),
        SimpleNamespace(timeline_start=11.5, timeline_end=13.0,
                        source_in=80.0, track_index=2, speaker="B"),
    ]


def _ending(**over):
    base = {"reel": "Reel 13 - x", "ends_on": {"anchor_phrase": "beta gamma"},
            "tail_element": "tv_power_tail", "reason": "captain marker"}
    base.update(over)
    return base


def _declare(root, endings):
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "reel_ending.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"version": 1, "endings": endings}, handle)


# ── The defect, reproduced and then closed ─────────────────────────

def test_declared_ending_truncates_to_its_shot():
    """The mechanism: a keep range reaching past the master's cut does not
    lengthen the shot that is playing - it adds fourteen frames of the
    next speaker. The ending truncates that back to the shot."""
    transcript = _transcript()
    ranges = [(10.0, 12.1)]
    probe = reel_build.placements(ranges, _shots(), 24.0)
    assert [p["clip"].speaker for p in probe] == ["A", "B"]
    assert round(
        (probe[-1]["source_out"] - probe[-1]["source_in"]) * 24.0) == 14
    out, record = reel_ending.apply_ending(
        ranges, probe, transcript, _ending(), 24.0)
    assert out == [(10.0, 11.5)]
    assert len(record["applied"]) == 1 and not record["stale"]
    assert record["applied"][0]["now"] == [10.0, 11.5]
    # And the reel now places ONE shot: the next speaker is gone.
    after = reel_build.placements(out, _shots(), 24.0)
    assert [p["clip"].speaker for p in after] == ["A"]


def test_body_ending_drops_a_later_played_cta_from_an_earlier_source():
    """A closer can come from an earlier point in the episode than the
    body. Ending on the body must remove that later-played closer, even
    though its master seconds are numerically lower."""
    from library.tools.reel_proposal import CallToAction

    transcript = {
        "segments": [
            {"timeline_start": 10.0, "timeline_end": 12.0,
             "text": "the links in the bio",
             "words": [
                 {"word": "the", "start": 10.0, "end": 10.2,
                  "timed": True},
                 {"word": "links", "start": 10.2, "end": 10.5,
                  "timed": True},
                 {"word": "in", "start": 10.5, "end": 10.7,
                  "timed": True},
                 {"word": "the", "start": 10.7, "end": 10.9,
                  "timed": True},
                 {"word": "bio", "start": 10.9, "end": 11.2,
                  "timed": True},
             ]},
            {"timeline_start": 100.0, "timeline_end": 110.0,
             "text": "and AI really likes that",
             "words": [
                 {"word": "and", "start": 108.0, "end": 108.2,
                  "timed": True},
                 {"word": "AI", "start": 108.2, "end": 108.5,
                  "timed": True},
                 {"word": "really", "start": 108.5, "end": 108.8,
                  "timed": True},
                 {"word": "likes", "start": 108.8, "end": 109.2,
                  "timed": True},
                 {"word": "that", "start": 109.2, "end": 109.6,
                  "timed": True},
             ]},
        ]}
    moment = SimpleNamespace(
        timeline_start=100.0, timeline_end=110.0,
        call_to_action=CallToAction(
            timeline_start=10.0, timeline_end=12.0,
            text="the links in the bio", speaker="SpeakerTwo"))
    ranges = reel_build.reel_ranges(moment, transcript)
    assert ranges == [(100.0, 110.0), (10.0, 12.0)]
    clips = [
        SimpleNamespace(timeline_start=10.0, timeline_end=12.0,
                        source_in=0.0, track_index=1, speaker="SpeakerTwo"),
        SimpleNamespace(timeline_start=100.0, timeline_end=110.0,
                        source_in=0.0, track_index=2, speaker="SpeakerOne"),
    ]
    ending = _ending(
        reel="Reel 26 - write-for-the-question-your-customer-ask",
        ends_on={"anchor_phrase": "and AI really likes that"},
        tail_element="none")
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, clips, 24.0),
        transcript, ending, 24.0)
    assert out == [(100.0, 110.0)]
    assert len(record["applied"]) == 1 and not record["stale"]
    assert record["applied"][0]["dropped_ranges"] == [[10.0, 12.0]]


def test_an_ending_never_extends():
    """The rule the defect teaches. A declaration that would reach past
    the plan's own end holds instead - an ending removes seconds or it
    does nothing."""
    transcript = _transcript()
    ranges = [(10.0, 11.5)]
    probe = reel_build.placements(ranges, _shots(), 24.0)
    out, record = reel_ending.apply_ending(
        ranges, probe, transcript, _ending(), 24.0)
    assert out == [(10.0, 11.5)]
    assert not record["applied"] and len(record["held"]) == 1


def test_no_declaration_passes_the_ranges_through():
    ranges = [(10.0, 12.1)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, _shots(), 24.0),
        _transcript(), None, 24.0)
    assert out == ranges
    assert record == {"applied": [], "held": [], "stale": []}


def test_words_the_reel_no_longer_plays_report_stale():
    transcript = _transcript()
    ranges = [(10.0, 12.1)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, _shots(), 24.0),
        transcript, _ending(ends_on={"anchor_phrase": "words never said"}),
        24.0)
    assert out == ranges and not record["applied"]
    assert len(record["stale"]) == 1
    assert "STALE" in record["stale"][0]["reason"]


# ── The tail element ───────────────────────────────────────────────

def test_tail_element_room_comes_from_its_own_module():
    from library.tools.fusion.played_window import frames_for_ramp
    from library.tools.tv_power import switch_off_frames

    timing = switch_off_frames()
    ramp = (timing["collapse_frames"] + timing["dot_frames"]
            + timing["decay_frames"])
    # Frames of CLIP, which is one more than frames of RAMP - the
    # arithmetic belongs to the check that enforces it, and a hold of
    # exactly the ramp's length is undone as `never_settles`.
    expected = frames_for_ramp(ramp)
    assert expected == ramp + 1
    assert reel_ending.tail_room_frames(_ending()) == expected
    # A project that redeclares the animation redeclares its room. The
    # declaration names the SHAPE, not a half: one shape, two directions
    # (captain, 2026-09-11).
    look = {"power": {"decay_frames": 30}}
    assert reel_ending.tail_room_frames(_ending(), look) == frames_for_ramp(
        ramp - timing["decay_frames"] + 30)
    # `none` is the absence of decoration and needs nothing.
    assert reel_ending.tail_room_frames(_ending(tail_element="none")) == 0
    assert reel_ending.tail_effects(_ending(tail_element="none")) == {}


def test_a_shot_too_short_for_its_tail_element_refuses_by_name():
    """The check that was missing. Twelve frames of the next speaker
    cannot carry an 18-frame switch-off, and the build says so instead
    of shipping the reel without it."""
    short = [{"source_in": 0.0, "source_out": 12 / 24.0}]
    with pytest.raises(reel_ending.TailElementHasNoRoom) as excinfo:
        reel_ending.assert_tail_fits(short, _ending(), 24.0)
    message = str(excinfo.value)
    assert "19 frames" in message and "plays 12" in message
    # And a shot with room passes, reporting what it measured.
    roomy = [{"source_in": 0.0, "source_out": 310 / 24.0}]
    measured = reel_ending.assert_tail_fits(roomy, _ending(), 24.0)
    assert measured == {"element": "tv_power_tail", "tail_frames": 19,
                        "shot_frames": 310, "hold": "none", "fits": True}


# ── The declaration ────────────────────────────────────────────────

def test_declaration_round_trips_and_matches_a_staging_name(tmp_path):
    root = str(tmp_path)
    _declare(root, [_ending()])
    assert reel_ending.resolve_ending(root, "Reel 13 - x") == _ending()
    # A staging container resolves to the same declaration, which is
    # what makes an ending survive the rebuild that promotes it.
    assert reel_ending.resolve_ending(
        root, "Reel 13 - x (scratch fm) (rebuild staging)") is not None
    assert reel_ending.resolve_ending(root, "Reel 28 - y") is None
    # A project that declares nothing gets nothing.
    assert reel_ending.resolve_ending(str(tmp_path / "other"), "R") is None


def test_malformed_declarations_refuse():
    for bad, match in (
        ([{"ends_on": {"anchor_phrase": "x"}, "reason": "r"}],
         "names no reel"),
        ([{"reel": "R", "reason": "r"}], "no ends_on"),
        ([{"reel": "R", "ends_on": {"anchor_phrase": "x"},
           "tail_element": "explosion", "reason": "r"}], "tail_element"),
    ):
        with pytest.raises(reel_ending.ReelEndingError, match=match):
            reel_ending.validate_endings(bad)


def test_an_unreadable_declaration_refuses_rather_than_defaulting(tmp_path):
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external)
    with open(os.path.join(external, "reel_ending.json"), "w",
              encoding="utf-8") as handle:
        handle.write("{not json")
    with pytest.raises(reel_ending.ReelEndingError, match="cannot be read"):
        reel_ending.load_endings(root)


# ── The freeze: hold the last frame, play the element over it ──────

def _freeze_ending(**over):
    base = _ending(tail_hold="freeze")
    base.update(over)
    return base


def test_a_freeze_holds_the_last_frame_for_the_elements_own_length():
    """The captain chose the freeze on 2026-09-11 after the switch-off
    played across his whole closing line. The hold's length is the
    ELEMENT's, so "it plays entirely after the words" is true by
    construction rather than by a tuned number."""
    placements = [{"clip": _shots()[0], "source_in": 50.0,
                   "source_out": 50.0 + 310 / 24.0,
                   "snapped_record": 1599, "record": 1599 / 24.0}]
    freeze = reel_ending.plan_freeze(placements, _freeze_ending(), 24.0)
    assert freeze is not None
    assert freeze.duration_frames == reel_ending.tail_room_frames(
        _freeze_ending()) == 19
    # It begins on the frame AFTER the live tail, and holds the last
    # frame that actually plays - never one the reel does not show.
    assert freeze.reel_start_frame == 1599 + 310
    assert freeze.end_frame == 1599 + 310 + 19
    assert freeze.held_source_seconds == pytest.approx(
        50.0 + 310 / 24.0 - 1 / 24.0)
    assert freeze.element == "tv_power_tail"


def test_the_freeze_placement_is_a_picture_clip_that_speaks_nothing():
    placements = [{"clip": _shots()[0], "source_in": 50.0,
                   "source_out": 50.0 + 310 / 24.0,
                   "snapped_record": 1599, "record": 1599 / 24.0}]
    freeze = reel_ending.plan_freeze(placements, _freeze_ending(), 24.0)
    place = reel_ending.freeze_placement(freeze, 24.0)
    assert place["snapped_record"] == 1909
    assert place["track_index"] == _shots()[0].track_index
    assert place["clip"].track_type == "video"
    assert place["source_out"] == pytest.approx(19 / 24.0)
    # An empty master span: a held frame speaks no words, so no
    # word-anchored pass may claim it as a shot - it inherits the
    # treatment of the shot it holds instead.
    assert place["master"] == (0.0, 0.0)
    assert place["freeze"] is True


# --------------------------------------------------------------------------
# From test_reel_ending_cta_default.py
#
# A reel INHERITS its freeze ending from the call to action it closes on.
#
# `test_a_reel_nobody_declared_anything_for_inherits_the_freeze` is the gate:
# it fails the moment a newly planned reel stops inheriting it. Synthetic
# under `tmp_path`; nothing here reaches Resolve or a real project.
#
# History: `docs/evidence/reel_ending.md` (test_reel_ending_cta_default.py).

FPS = 24.0

#: SpeakerOne's closer and SpeakerTwo's, as they sit in this episode - two
#: different passages, two different speakers, one behaviour.
SPEAKERONE_CTA = {
    "timeline_start": 333.8, "timeline_end": 341.27, "speaker": "SpeakerOne",
    "text": "And if you want to see how your brand appears, you should "
            "go check it out. The link's in our bio."}
SPEAKERTWO_CTA = {
    "timeline_start": 809.69, "timeline_end": 819.13, "speaker": "SpeakerTwo",
    "text": "So definitely check it out on our website, also the links "
            "in the bio."}


def _moment(cta, number=99, slug="a-reel-planned-tomorrow"):
    """A plan moment, as `ReelMoment` carries one into the build."""
    return SimpleNamespace(
        number=number, slug=slug,
        timeline_name=f"Reel {number:02d} - {slug}",
        call_to_action=(None if cta is None
                        else SimpleNamespace(**dict(cta, note=""))))


def _transcript_2(cta, words):
    """A transcript whose timed words sit inside `cta`'s own span."""
    start = float(cta["timeline_start"])
    step = (float(cta["timeline_end"]) - start) / (len(words) + 1)
    return {"segments": [{"words": [
        {"word": word, "start": round(start + index * step, 3),
         "end": round(start + index * step + step / 2, 3), "timed": True}
        for index, word in enumerate(words)]}]}


# ── The gate ───────────────────────────────────────────────────────

def test_a_reel_nobody_declared_anything_for_inherits_the_freeze(tmp_path):
    """THE gate for "or will be using this CTA".

    Reel 99 does not exist, is in no declaration file, and was planned
    after every conversation about endings.  It closes on a call to
    action, so it freezes.  If this fails, a new reel has stopped
    inheriting the ending and the captain's instruction is broken again.
    """
    _declare(tmp_path, [{
        "reel": "Reel 13 - the-accounting-firm-ai-called-healthcare",
        "ends_on": {"anchor_phrase": "the link's in our bio"},
        "tail_element": "tv_power_tail", "tail_hold": "freeze",
        "reason": "the one reel anybody wrote a pin for"}])

    ending = reel_ending.resolve_ending(
        str(tmp_path), "Reel 99 - a-reel-planned-tomorrow",
        _moment(SPEAKERONE_CTA))

    assert ending is not None, (
        "a reel closing on a call to action inherited no ending, so its "
        "switch-off will draw over the closing words again")
    assert ending["tail_hold"] == "freeze"
    assert ending["tail_element"] == "tv_power_tail"
    assert reel_ending.is_inherited(ending)
    # And it is not in the file: nothing was hand-copied for it.
    assert reel_ending.declared_ending(
        str(tmp_path), "Reel 99 - a-reel-planned-tomorrow") is None


def test_the_inheritance_does_not_know_who_closes(tmp_path):
    """Reel 28 closes on SPEAKERTWO, and takes the identical path.

    The captain named him precisely because the first fix was built
    around SpeakerOne's closing line.  Nothing in the mechanism may read
    the speaker to decide the hold.
    """
    speakerone = reel_ending.resolve_ending(
        str(tmp_path), "Reel 23 - why-small-business-wins-on-ai",
        _moment(SPEAKERONE_CTA))
    speakertwo = reel_ending.resolve_ending(
        str(tmp_path), "Reel 28 - the-nail-salon-query-google-cant-answer",
        _moment(SPEAKERTWO_CTA))

    for ending in (speakerone, speakertwo):
        assert ending["tail_hold"] == "freeze"
        assert ending["tail_element"] == "tv_power_tail"
    # The speaker is RECORDED and takes no part in the decision: the two
    # endings differ only in the reel, the words and whose name is filed.
    assert speakerone["cta"]["speaker"] == "SpeakerOne"
    assert speakertwo["cta"]["speaker"] == "SpeakerTwo"
    assert {k: v for k, v in speakerone.items()
            if k not in ("reel", "ends_on", "cta")} == \
           {k: v for k, v in speakertwo.items()
            if k not in ("reel", "ends_on", "cta")}


# ── What a declaration is FOR ──────────────────────────────────────

def test_a_per_reel_declaration_overrides_the_inheritance(tmp_path):
    """A pin exists to OVERRIDE the default, never to supply it."""
    _declare(tmp_path, [{
        "reel": "Reel 07 - a-reel-that-wants-its-live-tail",
        "ends_on": {"anchor_phrase": "the link's in our bio"},
        "tail_element": "tv_power_tail", "tail_hold": "none",
        "reason": "the captain asked for the live tail on this one"}])

    ending = reel_ending.resolve_ending(
        str(tmp_path), "Reel 07 - a-reel-that-wants-its-live-tail",
        _moment(SPEAKERONE_CTA))

    assert ending["tail_hold"] == "none"
    assert not reel_ending.is_inherited(ending)


# ── What does NOT inherit ──────────────────────────────────────────

def test_a_reel_that_closes_on_no_cta_inherits_nothing(tmp_path):
    """Three of this episode's thirty-one moments declare no call to
    action.  Those reels end where their body ends and build exactly as
    they did before any of this existed."""
    assert reel_ending.resolve_ending(
        str(tmp_path), "Reel 11 - your-website-is-your-resume",
        _moment(None)) is None
    # And a caller with no plan at all - anything reading endings that
    # is not a reel build - sees declarations only.
    assert reel_ending.resolve_ending(
        str(tmp_path), "Reel 01 - geo-is-comprehension-not-position") is None


# ── The anchor is the CTA's OWN words ──────────────────────────────

def test_the_anchor_is_read_from_the_transcript_not_the_cta_text():
    """Measured on this episode: the CTA at 1168.34-1177.58s reads "the
    Lucy visibility system" in the plan's `text` and "the lucie
    visibility system" in the transcript the anchor is matched against.
    An anchor taken from `text` would have named words no span speaks.
    """
    cta = {"timeline_start": 1168.34, "timeline_end": 1177.58,
           "speaker": "SpeakerOne",
           "text": "what we fixed with the Lucy visibility system"}
    spoken = ["what", "we", "fixed", "with", "the", "lucie",
              "visibility", "system"]

    ending = reel_ending.cta_default_ending(
        "Reel 07 - number-one-on-google-invisible-to-ai", _moment(cta),
        _transcript_2(cta, spoken))

    assert "lucie" in ending["ends_on"]["anchor_phrase"]
    assert "lucy" not in ending["ends_on"]["anchor_phrase"]
    # With no transcript the CTA's own text is the fallback, because an
    # ending that names no words at all is worse than one that names
    # the plan's.
    fallback = reel_ending.cta_default_ending("Reel 07 - x", _moment(cta))
    assert "lucy" in fallback["ends_on"]["anchor_phrase"]


# ── What the inheritance actually does to a build ──────────────────

def _shot(track_index=1, speaker="SpeakerOne"):
    return SimpleNamespace(timeline_start=333.8, timeline_end=341.27,
                           source_in=500.0, track_index=track_index,
                           speaker=speaker, source_file="/tmp/closer.mxf",
                           track_type="video")


def _placements(frames=179):
    return [{"clip": _shot(), "source_in": 500.0,
             "source_out": 500.0 + frames / FPS,
             "snapped_record": 1056, "record": 1056 / FPS,
             "track_index": 1, "speaker": "SpeakerOne",
             "master": (333.8, 341.27)}]


def test_the_inherited_ending_arms_the_element_on_the_held_frame(tmp_path):
    """`reel_look.power_effects` arms the tail element on whatever clip
    sorts last.  With the freeze in the list that is the HOLD, which is
    the whole point - and it is armed as DECLARED, so the renderer
    refuses rather than silently undoing it."""
    inherited = reel_ending.resolve_ending(
        str(tmp_path), "Reel 23 - why-small-business-wins-on-ai",
        _moment(SPEAKERONE_CTA))
    effects = reel_look.power_effects(
        None, "reel_picture_00", "reel_picture_01", ending=inherited)
    tail = effects["reel_picture_01"]
    assert tail["tv_power_tail"] is True
    assert tail["tv_power_tail_declared"] is True


def test_an_inherited_ending_can_never_admit_the_next_shot(tmp_path):
    """The rule the Reel 13 defect taught, which the default must not
    weaken.  The BOUND is what matters, not the direction: the last
    range may move either way and may never reach past the shot that
    speaks the closing words."""
    shots = [SimpleNamespace(timeline_start=333.8, timeline_end=341.55,
                             source_in=500.0, track_index=1,
                             speaker="SpeakerOne"),
             SimpleNamespace(timeline_start=341.55, timeline_end=345.0,
                             source_in=900.0, track_index=2,
                             speaker="SpeakerTwo")]
    cta = dict(SPEAKERONE_CTA)
    spoken = ["you", "should", "go", "check", "it", "out", "the",
              "link's", "in", "our", "bio"]
    transcript = _transcript_2(cta, spoken)
    inherited = reel_ending.resolve_ending(
        str(tmp_path), "Reel 13 - x", _moment(cta), transcript)

    # A range reaching PAST the closing shot - the Reel 13 fault - is
    # truncated back to the shot, never to the next speaker.
    over = [(333.8, 342.03)]
    out, record = reel_ending.apply_ending(
        over, reel_build.placements(over, shots, FPS), transcript,
        inherited, FPS)
    assert out == [(333.8, 341.55)]
    assert len(record["applied"]) == 1
    assert record["applied"][0]["source"] == "call_to_action"
    assert [p["clip"].speaker
            for p in reel_build.placements(out, shots, FPS)] == ["SpeakerOne"]

    # And a range stopping short is extended no further than the same
    # bound, so the outward reading cannot admit SpeakerTwo either.
    short = [(333.8, 339.0)]
    out, _ = reel_ending.apply_ending(
        short, reel_build.placements(short, shots, FPS), transcript,
        inherited, FPS)
    assert out[-1][1] <= 341.55
    assert [p["clip"].speaker
            for p in reel_build.placements(out, shots, FPS)] == ["SpeakerOne"]


# ── The closing breath: the captain's SECOND fault ─────────────────

def test_the_closing_breath_gives_back_the_word_the_aligner_cut():
    """Measured on the footage: Reels 01, 23 and 30 end at 341.270s,
    exactly where WhisperX labels the end of "bio.", and the sound of
    that word is still at 1307 RMS on the last frame they play.  The
    breath is the silence after the closing words - bounded by the
    ending shot's own end and by the next thing anybody says."""
    transcript = {"segments": [{"words": [
        {"word": "bio.", "start": 341.069, "end": 341.270, "timed": True},
        {"word": "so", "start": 342.038, "end": 342.158, "timed": True}]}]}
    # The real numbers off this episode's master: the closing shot runs
    # to 341.550s and the next word is spoken at 342.038s.
    assert reel_ending.closing_breath_end(
        transcript, 341.270, 341.550) == pytest.approx(341.550)
    # Which is exactly where Reel 13 - the reference the captain
    # accepted - ends. The rule reproduces it rather than approaching it.

    # The next WORD bounds it where the shot runs on past the silence.
    assert reel_ending.closing_breath_end(
        transcript, 341.270, 400.0) == pytest.approx(342.038)
    # And an episode with nothing after the closing words has only the
    # shot's own end to stop at.
    assert reel_ending.closing_breath_end(
        {"segments": []}, 341.270, 341.550) == pytest.approx(341.550)


def test_the_closing_breath_stops_a_full_frame_before_the_next_word():
    """Golden item 6: the breath was bounded by the next word's start in
    SECONDS, and frame placement crossed the bound by a fraction of a
    frame - the reel played into the next speaker's first frame, which
    F25 refused as `played_not_captioned`.

    The captain's measured numbers at the golden rate: "bio." ends at
    341.270s and "so" starts at 342.038s, inside frame 8200 - but the
    seconds bound rounds to frame 8201.  The bound is now in frames,
    so the breath ends where frame 8200 starts and that frame never
    plays."""
    golden_fps = 24000 / 1001
    transcript = {"segments": [{"words": [
        {"word": "our", "start": 340.99, "end": 341.05, "timed": True},
        {"word": "bio.", "start": 341.069, "end": 341.270, "timed": True},
        {"word": "so", "start": 342.038, "end": 342.158, "timed": True}]}]}
    first = math.floor(342.038 * golden_fps)
    assert first == 8200
    # The seconds bound demonstrably crossed it: this is the defect.
    assert round(342.038 * golden_fps) == first + 1

    bound = reel_ending.closing_breath_end(
        transcript, 341.270, 400.0, golden_fps)
    assert bound == pytest.approx(first / golden_fps)
    assert round(bound * golden_fps) <= first

    # End to end: an inherited ending whose word bound binds extends no
    # further than that frame once placed.
    shots = [SimpleNamespace(timeline_start=333.8, timeline_end=400.0,
                             source_in=500.0, track_index=1,
                             speaker="SpeakerOne")]
    ending = {"reel": "Reel 99 - x",
              "ends_on": {"anchor_phrase": "our bio"},
              "tail_element": "none", "reason": "test",
              "source": "call_to_action"}
    ranges = [(333.8, 341.27)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, shots, golden_fps),
        transcript, ending, golden_fps)
    assert len(record["applied"]) == 1
    assert out[-1][1] == pytest.approx(first / golden_fps)
    placed = reel_build.placements(out, shots, golden_fps)
    assert placed, "the ending must leave the closing words placed"
    assert round(out[-1][1] * golden_fps) <= first


def test_a_declared_ending_takes_no_breath(tmp_path):
    """A pin is somebody saying where a reel ends.  Taking them at
    their word is the point of writing one, so a declaration stays
    truncate-only and the breath is the inheritance's alone."""
    _declare(tmp_path, [{
        "reel": "Reel 07 - pinned",
        "ends_on": {"anchor_phrase": "the link's in our bio"},
        "tail_element": "tv_power_tail", "tail_hold": "freeze",
        "reason": "the captain said it ends here"}])
    shots = [SimpleNamespace(timeline_start=333.8, timeline_end=341.55,
                             source_in=500.0, track_index=1,
                             speaker="SpeakerOne")]
    transcript = {"segments": [{"words": [
        {"word": "the", "start": 340.6, "end": 340.7, "timed": True},
        {"word": "link's", "start": 340.7, "end": 340.9, "timed": True},
        {"word": "in", "start": 340.9, "end": 340.95, "timed": True},
        {"word": "our", "start": 340.99, "end": 341.05, "timed": True},
        {"word": "bio", "start": 341.07, "end": 341.27, "timed": True}]}]}
    declared = reel_ending.resolve_ending(
        str(tmp_path), "Reel 07 - pinned", _moment(SPEAKERONE_CTA), transcript)
    assert not reel_ending.is_inherited(declared)
    ranges = [(333.8, 341.27)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, shots, FPS), transcript,
        declared, FPS)
    assert out == ranges
    assert len(record["held"]) == 1 and record["held"][0]["breath_end"] is None


@pytest.mark.parametrize("cta", [SPEAKERONE_CTA])
def test_every_inherited_ending_passes_the_declared_check(cta):
    """A default that would be refused as a hand-written declaration is
    a default nobody could have written down."""
    ending = reel_ending.cta_default_ending("Reel 99 - x", _moment(cta))
    assert reel_ending.validate_endings([ending]) == [ending]
    assert ending["reason"].strip()


# --------------------------------------------------------------------------
# From test_reel_cta_treatment.py
#
# One CTA animation across the fleet, declared once, applied at the source.
#
# Captain's Reel 09 marker, 2026-09-20: the closing card's animation should
# carry every CTA where the Lucie Visibility system is referred to. The
# fleet measures six present variants plus an absent slot, because every
# reel's closer graphic is model-planned per reel. `reel_cta_treatment`
# normalises the closing card - the latest-anchored entry among the card
# elements - to the declared treatment before `resolve_plan` times it.
# Copy and timing stay the plan's own, so a re-render keeps its duration
# and a swap cannot move a reel's length. Only reels whose own CTA names
# the declared scope phrase qualify.
#
# Every test below is synthetic under `tmp_path` (AGENTS.md 8); none
# reaches Resolve or a real project.

GOLD = "#FBF0B8"
SCOPE = "visibility system"

CTA_TEXT = ("we are calling the lucie visibility system "
            "we would love for you to check it out")
BODY_TEXT = "one is about position and the other is comprehension"


def _windows(words, start=60.0, step=0.5):
    out, cursor = [], start
    for word in words:
        out.append({"word": word, "start": round(cursor, 3),
                    "end": round(cursor + 0.4, 3)})
        cursor = round(cursor + step, 3)
    return out


def _speech():
    return _windows((BODY_TEXT + " then " + CTA_TEXT).split())


def _moment_2(text=CTA_TEXT):
    return SimpleNamespace(
        number=9, timeline_name="Reel 09 - x",
        call_to_action=CallToAction(timeline_start=100.0,
                                    timeline_end=110.0,
                                    text=text, speaker="speakertwo"))


def _declare_2(root, treatment=None, scope=SCOPE, reason="captain marker",
             version=1, exclude=()):
    external = os.path.join(str(root), "external")
    os.makedirs(external, exist_ok=True)
    document = {"version": version,
                "scope_cta_contains": scope,
                "exclude_reels": list(exclude),
                "treatment": treatment if treatment is not None else {
                    "element": "title_lockup", "entrance": "fade",
                    "exit": "fade", "anchor": "centre", "color": GOLD},
                "reason": reason}
    with open(os.path.join(external, "reel_cta.json"), "w",
              encoding="utf-8") as handle:
        json.dump(document, handle)
    return str(root)


def _entry(element, phrase, **over):
    base = {"element": element, "anchor_phrase": phrase,
            "hold_seconds": 3.0, "anchor": "top_centre", "row": 0,
            "copy": {"display": phrase.upper()},
            "color": "#aabbcc", "why": "model reasons"}
    base.update(over)
    return base


# ── Absences pass through ────────────────────────────────────────────

def test_no_declaration_passes_the_answer_through_untouched(tmp_path):
    answer = [_entry("quote_card", "lucie visibility system")]
    moment = _moment_2()
    same, report = cta_rx.apply(answer, moment, _speech(), str(tmp_path))
    assert same is answer
    assert answer[0]["element"] == "quote_card"
    assert report == {"applied": [], "stale": [], "treatment": None}


# ── The treatment ────────────────────────────────────────────────────

def test_the_latest_anchored_card_is_treated_and_earlier_is_not(tmp_path):
    """The closing card is positional: the punchline card earlier in
    the reel keeps its treatment, the closer takes the declared one."""
    root = _declare_2(tmp_path)
    answer = [_entry("title_lockup", "one is about position"),
              _entry("title_lockup", "lucie visibility system",
                     colour_role="text")]
    moment = _moment_2()
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert same is answer
    earlier, treated = answer
    assert earlier["element"] == "title_lockup"
    assert earlier["anchor"] == "top_centre"
    assert treated["element"] == "title_lockup"
    assert treated["entrance"] == "fade"
    assert treated["exit"] == "fade"
    assert treated["anchor"] == "centre"
    assert treated["color"] == GOLD
    assert "colour_role" not in treated
    # The plan's own copy, timing, row and reasoning survive.
    assert treated["copy"] == {"display": "LUCIE VISIBILITY SYSTEM"}
    assert treated["hold_seconds"] == 3.0
    assert treated["row"] == 0
    assert treated["why"] == "model reasons"
    assert len(report["applied"]) == 1
    assert report["applied"][0]["index"] == 1
    assert report["applied"][0]["was"]["anchor"] == "top_centre"


def test_a_closer_in_another_card_element_is_converted(tmp_path):
    """quote_card, stat_callout and list_build closers all take the
    title_lockup treatment; their copy is kept, not re-tiered."""
    root = _declare_2(tmp_path)
    for element in ("quote_card", "stat_callout", "list_build"):
        answer = [_entry(element, "lucie visibility system")]
        _, report = cta_rx.apply(answer, _moment_2(), _speech(), root)
        assert answer[0]["element"] == "title_lockup", element
        assert answer[0]["copy"] == {"display": "LUCIE VISIBILITY SYSTEM"}
        assert len(report["applied"]) == 1


def test_a_declared_seconds_entry_is_never_attributed_by_time(tmp_path):
    """A beat accent on a cut names no words; it cannot be the closing
    card whatever frame it sits on."""
    root = _declare_2(tmp_path)
    answer = [_entry("beat_accent", "", start_seconds=62.0,
                     duration_seconds=0.4)]
    del answer[0]["anchor_phrase"]
    _, report = cta_rx.apply(answer, _moment_2(), _speech(), root)
    assert answer[0]["element"] == "beat_accent"
    assert report["applied"] == []
    assert len(report["stale"]) == 1


# ── Explicit exclusions ──────────────────────────────────────────────

def test_an_excluded_reel_is_untouched_even_in_scope(tmp_path):
    """Reel 08's class: in scope, but unifying its early card would
    restyle a non-closer, so the declaration spares it by name."""
    root = _declare_2(tmp_path, exclude=[
        "Reel 08 - top-three-on-google-hallucinated-by-ai"])
    moment = SimpleNamespace(
        number=8, timeline_name="Reel 08 - top-three-on-google-hallucinated-by-ai",
        call_to_action=CallToAction(timeline_start=100.0,
                                    timeline_end=110.0, text=CTA_TEXT,
                                    speaker="speakertwo"))
    answer = [_entry("title_lockup", "lucie visibility system")]
    same, report = cta_rx.apply(answer, moment, _speech(), root)
    assert same is answer
    assert answer[0]["anchor"] == "top_centre"
    assert report["applied"] == []
    assert len(report["stale"]) == 1
    assert "explicitly excluded" in report["stale"][0]["reason"]


# ── The declaration is checked, never asserted ───────────────────────

def test_a_malformed_declaration_refuses(tmp_path):
    """Every malformed or unreadable declaration refuses at load, by row:
    bad treatment fields, a blank scope or reason, an unknown version,
    a malformed exclusion list, and a file that is not JSON."""
    root = str(tmp_path)
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    path = os.path.join(external, "reel_cta.json")
    good_treatment = {"element": "title_lockup", "entrance": "fade",
                      "exit": "fade", "anchor": "centre", "color": GOLD}

    def doc(**over):
        base = {"version": 1, "scope_cta_contains": SCOPE,
                "treatment": good_treatment, "reason": "x"}
        base.update(over)
        return json.dumps(base)

    bad_documents = [
        doc(treatment=dict(good_treatment, element="ticker_tape")),
        doc(treatment=dict(good_treatment, element="subject_emblem")),
        doc(treatment=dict(good_treatment, entrance="wipe")),
        doc(treatment=dict(good_treatment, anchor="everywhere")),
        doc(treatment=dict(good_treatment, color="gold")),
        doc(scope_cta_contains="  "),
        doc(reason="  "),
        doc(version=99),
        *(doc(exclude_reels=bad) for bad in ("Reel 08", ["  "], [42], "")),
        "{not json",
    ]
    for document in bad_documents:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(document)
        with pytest.raises(cta_rx.ReelCtaTreatmentError):
            cta_rx.load_treatment(root)


# --------------------------------------------------------------------------
# From test_closer_fit.py
#
# Closer fit: the share count is measured, fit is judged, nothing is scored.
#
# `library/tools/closer_fit.py` answers the captain's 2026-09-19 ruling:
# reuse is not a defect (so grouping never de-duplicates and never
# proposes to), topical misfit is (so the model is given the reel's own
# kept words and its closer and answers with a reason), and the subset is
# measured before anything is re-cut (so the survey reports a list and a
# count, and the build only reports).
#
# These tests pin what is measured, what is deliberately NOT decided, and
# that a verdict without a reason reads as unjudged rather than as a
# verdict - the last of which is what stops this becoming a gate that
# fires on correct output.

def _cta(start: float, end: float):
    return SimpleNamespace(timeline_start=start, timeline_end=end)


def _moment_3(number: int, body: tuple, closer=None):
    return SimpleNamespace(
        number=number,
        timeline_start=float(body[0]),
        timeline_end=float(body[1]),
        call_to_action=_cta(*closer) if closer else None,
        timeline_name=f"Reel {number:02d}",
        approval=SimpleNamespace(value="approved"),
    )


def _segment(start: float, words: list, speaker: str = "SpeakerOne"):
    """One transcript segment with evenly spaced timed words."""
    step = 0.4
    timed = [{"word": token, "start": start + i * step,
              "end": start + (i + 1) * step, "timed": True}
             for i, token in enumerate(words)]
    end = start + len(words) * step
    return {"timeline_start": start, "timeline_end": end,
            "speaker": speaker, "text": " ".join(words),
            "words": timed}


def _transcript_3(*segments):
    return {"segments": list(segments)}


# ── Reuse is grouped, never judged ───────────────────────────────────

def test_shared_closers_group():
    cases = [
        (((1, (100.0, 107.0)), (2, (100.0, 107.0))), [1, 2]),
        # A shared clip re-snapped by fractions of a second is one group:
        # the live plan carries 328.61-341.27 and 328.61-342.03 as one.
        (((3, (328.61, 341.27)), (13, (328.61, 342.03))), [3, 13]),
    ]
    for closers, reels in cases:
        moments = [_moment_3(n, (20.0 * i, 20.0 * i + 10.0), c)
                   for i, (n, c) in enumerate(closers)]
        groups = fit.reuse_groups(moments)
        assert [(g["reels"], g["count"]) for g in groups] == [(reels, 2)]


# ── The context is the reel's own kept words ─────────────────────────

def _body_and_closer():
    body = _segment(0.0, "reviews build trust and authority for a company".split())
    closer = _segment(100.0, "check it out on our website today".split(),
                      speaker="SpeakerOne")
    transcript = _transcript_3(body, closer)
    moment = _moment_3(27, (0.0, 2.8), (100.0, 102.8))
    return moment, transcript


def test_closer_cutting_into_a_sentence_is_said():
    """A closer opening mid-sentence carries the sentence it cuts."""
    segment = _segment(100.0, "so check it out on our website today".split())
    transcript = _transcript_3(
        _segment(0.0, "reviews build trust".split()), segment)
    # Starts on "check", inside the segment's "so ... today".
    moment = _moment_3(27, (0.0, 1.2), (100.4, 103.2))
    context = fit.fit_context(moment, transcript, None)

    assert context["closer_cut_from_sentence"] == \
        "so check it out on our website today"


# ── The reader takes verdicts, never magnitudes ──────────────────────

def test_the_reader_takes_a_reasoned_verdict_only():
    cases = [
    ({"verdict": "misfit", "reason": "answers what the reel never asked"},
     {"verdict": "misfit", "reason": "answers what the reel never asked"}),
    # There is no magnitude in this judgement, so none is read.
    ({"verdict": "follows", "reason": "lands it", "score": 0.92,
      "confidence": 0.99},
     {"verdict": "follows", "reason": "lands it"}),
    # An unknown verdict, or one nobody can show the captain, is unjudged.
    ({"verdict": "somewhat", "reason": "maybe"}, "unjudged"),
    ({"verdict": "misfit", "reason": "  "}, "unjudged"),
    ({"verdict": "misfit"}, "unjudged"),
    ]
    for answer, expected in cases:
        read = fit.read_fit_answer(answer)
        if expected == "unjudged":
            assert read["verdict"] == "unjudged" and read["reason"], answer
        else:
            assert read == expected


# ── The prompt asks fit, and only fit ────────────────────────────────


# ── The build-time lines report, never gate ──────────────────────────


def test_a_verdict_line_names_a_misfit_and_flags_moved_words_stale():
    moment, transcript = _body_and_closer()
    context = fit.fit_context(moment, transcript, None)
    misfit = fit.verdict_lines(27, context, {
        "verdict": "misfit", "reason": "answers nothing asked",
        "content_hash": context["content_hash"]})
    assert any("MISFIT" in line and "answers nothing asked" in line
               for line in misfit)
    stale = fit.verdict_lines(27, context, {
        "verdict": "follows", "reason": "lands it",
        "content_hash": "movedwords00"})
    assert any("STALE" in line for line in stale)


# --------------------------------------------------------------------------
# From test_closer_redraw.py
#
# A `redraw_closer` pin in `captain_edits` opens a shared closer on a
# word-anchored sentence start, names which closer it moves, and survives
# rebuilds.
#
# History: docs/evidence/reel_shared_closer.md.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import captain_edits
from library.tools.project_layout import ProjectLayout

OLD_START = 321.610
NEW_START = 319.358
CTA_END = 328.231
GROWTH = 2.252

ANCHOR = "it's exactly why we've been building this platform we're calling"
FROM = "we're calling the lucy visibility system"

CLOSER_TEXT = (
    "we're calling the lucy visibility system we'd love for you to go "
    "check it out see how ai sees you the links in the bio")


# ── The fixture: four reels, one shared closer, measured timings ──────

def _seg(speaker, text, start, end, uid, words=()):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "resolve_item_id": uid}
    if words:
        seg["words"] = [
            {"word": w, "start": s, "end": e, "timed": True}
            for w, s, e in words]
    return seg


def _tx():
    """The CTA geometry in miniature, at the brief's own numbers.

    seg1 ends WITH a period ("... to be the answer."): the pause before
    "it's" is a sentence break, which is what lets the redrawn closer
    open clean. seg2 carries no terminal punctuation, so the CURRENT
    start opens mid-sentence and the bar fires - the defect.
    """
    answer_words = [
        ("we", 312.75, 313.1), ("kept", 313.15, 313.6),
        ("asking", 313.65, 314.2), ("what", 314.25, 314.6),
        ("the", 314.65, 314.9), ("old", 314.95, 315.4),
        ("playbook", 315.45, 316.1), ("claimed", 316.15, 316.8),
        ("to", 316.85, 317.1), ("be", 317.15, 317.5),
        ("the", 317.55, 317.9), ("answer.", 318.2, 318.875),
    ]
    head_words = [
        ("it's", 319.358, 319.6), ("exactly", 319.65, 320.1),
        ("why", 320.15, 320.4), ("we've", 320.45, 320.7),
        ("been", 320.75, 320.95), ("building", 321.0, 321.2),
        ("this", 321.25, 321.4), ("platform", 321.4, 321.61),
    ]
    closer_tokens = CLOSER_TEXT.split()
    cursor, closer_words = OLD_START, []
    step = (CTA_END - OLD_START) / len(closer_tokens)
    for token in closer_tokens:
        closer_words.append((token, round(cursor, 3),
                             round(cursor + step - 0.02, 3)))
        cursor += step
    closer_words[-1] = (closer_words[-1][0], closer_words[-1][1], CTA_END)
    assert closer_words[0][1] == OLD_START
    return {
        "segments": [
            _seg("SpeakerTwo", "the old ranking game stops paying", 10.0, 25.0,
                 "b2a"),
            _seg("SpeakerOne", "people ask full sentences now", 25.0, 40.0,
                 "b2b"),
            _seg("SpeakerTwo", "client site earns zero clicks daily", 50.0, 80.0,
                 "b9a"),
            _seg("SpeakerOne", "buyers trust visible proof fast", 80.0, 110.0,
                 "b9b"),
            _seg("SpeakerTwo", "competitor publishes answers weekly", 120.0,
                 150.0, "b20a"),
            _seg("SpeakerOne", "search summary quotes them instead", 150.0,
                 180.0, "b20b"),
            _seg("SpeakerTwo", "owners write helpful guides nightly", 200.0,
                 230.0, "b26a"),
            _seg("SpeakerOne", "callers mention those pages often", 230.0,
                 260.0, "b26b"),
            _seg("SpeakerTwo",
                 "we kept asking what the old playbook claimed to be "
                 "the answer.", 312.75, 318.875, "lead", words=answer_words),
            _seg("SpeakerTwo",
                 "it's exactly why we've been building this platform",
                 319.358, 321.61, "head", words=head_words),
            _seg("SpeakerTwo", CLOSER_TEXT, 321.61, 328.231, "closer",
                 words=closer_words),
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _moment_4(number, start, end):
    from library.tools.reel_proposal import CallToAction, ReelMoment
    return ReelMoment(
        number=number, slug=f"reel-{number:02d}", reason="a full exchange",
        timeline_start=start, timeline_end=end,
        call_to_action=CallToAction(
            timeline_start=OLD_START, timeline_end=CTA_END,
            text=CLOSER_TEXT, speaker="SpeakerTwo"))


def _moments():
    return [_moment_4(2, 10.0, 40.0), _moment_4(9, 50.0, 110.0),
            _moment_4(20, 120.0, 180.0), _moment_4(26, 200.0, 260.0)]


def _pin(**over):
    edit = {"kind": "redraw_closer", "anchor_phrase": ANCHOR,
            "from_phrase": FROM,
            "reason": "captain 2026-09-10: start the CTA from "
                      "\"it's exactly why\", not mid-clause"}
    edit.update(over)
    return edit


def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _write_edits_file(project, edits):
    directory = project / "external"
    directory.mkdir(exist_ok=True)
    (directory / "captain_edits.json").write_text(
        json.dumps({"key": "captain_edits",
                    "source": "captain, 2026-09-10", "value": edits}),
        encoding="utf-8")


# ── 1. The pin validates, word-anchored ────────────────────────────────

def test_a_malformed_pin_is_refused_by_name():
    cases = [
        # No from_phrase would redraw EVERY closer in the batch.
        ({k: v for k, v in _pin().items() if k != "from_phrase"},
         "from_phrase"),
        # A timecode pin breaks the moment anything upstream re-times.
        (_pin(new_start=NEW_START), "start"),
    ]
    for edit, named in cases:
        with pytest.raises(captain_edits.CaptainEditError) as exc:
            captain_edits.validate_edits([edit])
        assert named in str(exc.value).lower()


# ── 2. The redraw moves all four, end fixed ────────────────────────────

def test_the_built_reel_opens_its_closer_on_its_exactly_why():
    """From the built timeline's OWN speech (`played_speech` over the
    ranges the builder lays down), not from the plan's declared text."""
    from library.tools.reel_quality_bar import played_speech
    from library.tools.reel_build import reel_ranges
    tx = _tx()
    moments, _, _, _ = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    for moment in moments:
        ranges = reel_ranges(moment, tx)
        assert ranges[-1][0] == pytest.approx(NEW_START)
        lines = played_speech(moment, tx, with_words=True)
        closer_lines = [line for line in lines
                        if line["range"] == len(ranges) - 1]
        opening = " ".join(line["text"] for line in closer_lines)
        assert opening.startswith("it's exactly why"), opening
        first_word = closer_lines[0]["words"][0]
        assert first_word["word"] == "it's"
        # The closer is laid down LAST, so the reel second of "it's" is
        # exactly the body's played length: the word opens the closer.
        assert first_word["at"] == pytest.approx(
            moment.timeline_end - moment.timeline_start)


# ── 3. The warning fires before, quiet after ───────────────────────────


# ── 4. Durability: the store, twice ────────────────────────────────────


# ── 5. Honest failures ─────────────────────────────────────────────────

def test_an_unplaceable_anchor_reports_stale_and_moves_nothing(tmp_path):
    """The passage reworded so the anchor matches nothing: the pin
    reports STALE loudly and no span moves."""
    tx = _tx()
    for segment in tx["segments"]:
        segment["text"] = segment["text"].replace(
            "it's exactly why we've been building", "motivation here runs")
        for word in segment.get("words") or []:
            word["word"] = word["word"].replace("it's", "motivation")
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        _moments(), tx, [_pin()])
    assert applied == [] and held == []
    assert len(stale) == 1 and ANCHOR in stale[0]["reason"]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            OLD_START)
    _an_overlapping_word_still_refuses()


def test_an_extension_into_its_own_body_is_refused_loudly():
    """Extending the closer backwards must not double-play: a reel
    whose body already contains the anchor keeps its span, with the
    reason naming the overlap."""
    tx = _tx()
    reel = _moment_4(2, 10.0, 40.0)
    body_covering_anchor = _moment_4(7, 300.0, 322.0)
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [reel, body_covering_anchor], tx, [_pin()])
    assert [r["reel"] for r in applied] == [2]
    assert len(stale) == 1 and stale[0].get("reel") == 7
    assert "overlap" in stale[0]["reason"].lower()
    assert body_covering_anchor.call_to_action.timeline_start == pytest.approx(
        OLD_START) or moments[1].call_to_action.timeline_start == pytest.approx(
            OLD_START)


def test_a_closer_opening_on_neither_phrase_is_left_alone():
    """A reel closing on a DIFFERENT invitation is not this pin's
    business: untouched, and the pin is not reported stale for it."""
    from library.tools.reel_proposal import CallToAction
    from dataclasses import replace
    tx = _tx()
    other = replace(
        _moment_4(5, 400.0, 440.0),
        call_to_action=CallToAction(
            timeline_start=400.0, timeline_end=406.0, text="",
            speaker="SpeakerTwo"))
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [other], tx, [_pin()])
    assert applied == [] and stale == []
    assert moments[0].call_to_action.timeline_start == pytest.approx(400.0)


# ── 6. Selection draws the pinned start on regeneration ────────────────

def test_selection_redraws_a_regenerated_proposal(tmp_path):
    """The model still names the old start; the proposal that lands
    carries the pinned one - so regeneration cannot re-emit the defect."""
    from library.steps.step_3_04_select_reels.post_bridge import resolve
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    tx = _tx()
    bodies = [(2, 10.0, 40.0), (9, 50.0, 110.0), (20, 120.0, 180.0),
              (26, 200.0, 260.0)]
    llm_output = {"moments": [
        {"start": start, "end": end, "slug": f"reel-{number:02d}",
         "reason": "a full exchange",
         "cta": {"start": OLD_START, "end": CTA_END,
                 "note": "the shared invitation"}}
        for number, start, end in bodies]}
    result = resolve(llm_output, {"timeline_transcript": tx,
                                  "project_folder": str(project)})
    selection = result["reel_selection"]
    assert len(selection["moments"]) == 4
    for stored in selection["moments"]:
        cta = stored["call_to_action"]
        assert cta["timeline_start"] == pytest.approx(NEW_START)
        assert cta["timeline_end"] == pytest.approx(CTA_END)
    assert sorted(r["reel"] for r in
                  selection["captain_closer_redraws"]["applied"]) == [
                      1, 2, 3, 4]  # selection renumbers sequentially


# ── 7. Both ends of the pin must still be spoken ───────────────────────

def test_a_pin_checks_both_phrases_against_measured_speech(tmp_path):
    """The external correspondence check covers `from_phrase` too: a
    pin whose old opening is gone from the speech is drift, refused by
    name rather than applied to the wrong closer."""
    from library.tools import external_inputs
    from library.tools.external_inputs import ExternalStateError
    project = _project(tmp_path)
    _write_edits_file(project, [_pin()])
    speech = ("it's exactly why we've been building this platform we're "
              "calling the lucy visibility system from start to finish")
    state = {"step_outputs": {"speech_sequence": {"body_sequence": [
        {"text": speech}]}}}
    supplied = external_inputs.load(str(project), state)
    assert "1 edit(s)" in supplied["captain_edits"].checked
    _write_edits_file(project, [_pin(from_phrase="zebras on mars")])
    with pytest.raises(ExternalStateError) as exc:
        external_inputs.load(str(project), state)
    assert "zebras on mars" in str(exc.value)


# ── 8. The real geometry: mid-row on a word edge ─────────────────────
#
# The fixture above rows the head ("it's exactly why ... platform")
# as its own segment, so the pin lands on a segment edge. The real
# transcript rows it INSIDE the previous row ([318.091, 321.530]:
# "to be the answer it's exactly why we've been building this
# platform") - WhisperX's chunking, not a sentence. The pin refused
# that opening as "no longer on a clean segment edge" and the rebuild
# faithfully reproduced the wrong CTA forever. A mid-row opening on a
# clean timed-word edge is the captain's ruling standing over the
# rowing, and applies; a mid-WORD opening still refuses.

def _tx_merged():
    """The real geometry: answer and head share one bound row."""
    tx = _tx()
    lead = [s for s in tx["segments"] if s["resolve_item_id"] == "lead"][0]
    head = [s for s in tx["segments"] if s["resolve_item_id"] == "head"][0]
    merged = dict(lead)
    merged["text"] = lead["text"] + " " + head["text"]
    merged["timeline_end"] = head["timeline_end"]
    merged["words"] = lead["words"] + head["words"]
    tx["segments"] = [
        merged if s["resolve_item_id"] == "lead" else s
        for s in tx["segments"]
        if s["resolve_item_id"] != "head"]
    return tx


def _an_overlapping_word_still_refuses():
    """An overlapping speaker's word strictly containing the opening:
    starting there would cut their word in half, so the pin reports
    STALE rather than shipping a half-word."""
    tx = _tx_merged()
    tx["segments"].append(
        _seg("SpeakerOne", "yeah", 319.0, 319.5, "overlap",
             words=[("yeah", 319.0, 319.5)]))
    moments, applied, held, stale = captain_edits.apply_closer_redraws(
        [_moment_4(2, 10.0, 40.0)], tx, [_pin()])
    assert applied == [] and held == []
    assert len(stale) == 1 and stale[0].get("reel") == 2
    assert "word edge" in stale[0]["reason"]
    assert moments[0].call_to_action.timeline_start == pytest.approx(
        OLD_START)


def test_a_pinned_cta_passes_validation_where_unpinned_refuses():
    """Regeneration draws the pinned start and then validates: without
    the pin's own word-edge guarantee the whole-segments refusal would
    break every future selection run - a recorded decision breaking
    the loop it closed. Every other reel, and every body span, is
    checked exactly as before."""
    from library.tools.reel_proposal import (
        ProposalError, _check_call_to_action)
    tx = _tx_merged()
    moments, applied, _, _ = captain_edits.apply_closer_redraws(
        [_moment_4(9, 50.0, 110.0)], tx, [_pin()])
    assert [r["reel"] for r in applied] == [9]
    redrawn = moments[0]
    duration = max(redrawn.timeline_end,
                   redrawn.call_to_action.timeline_end)
    label = "reel 9 ('your-website-is-only-20-percent')"
    with pytest.raises(ProposalError) as exc:
        _check_call_to_action(redrawn, tx, duration, label)
    assert "cuts 1 segment" in str(exc.value)
    _check_call_to_action(redrawn, tx, duration, label, pinned=True)


# ── 9. Durability through the write side ─────────────────────────────

def test_a_recorded_pin_survives_the_read_and_two_rebuilds(tmp_path):
    """The whole loop through the dormant store: record (the write
    side, correspondence-checked against measured speech) -> load
    (the reader the build uses) -> apply, twice. The first rebuild
    redraws; the second holds. That is what "persist" means."""
    project = _project(tmp_path)
    transcript_path = (project / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(json.dumps(_tx()), encoding="utf-8")
    edit, action = captain_edits.record_edit(
        str(project), _pin(), "captain, 2026-09-10")
    assert action == "recorded"
    moments = _moments()
    for round in range(2):
        edits = captain_edits.load_edits(str(project))
        assert edits == [edit]
        moments, applied, held, stale = (
            captain_edits.apply_closer_redraws(moments, _tx(), edits))
        assert stale == []
        if round == 0:
            assert sorted(r["reel"] for r in applied) == [2, 9, 20, 26]
            assert held == []
        else:
            assert applied == []
            assert sorted(h["reel"] for h in held) == [2, 9, 20, 26]
    for moment in moments:
        assert moment.call_to_action.timeline_start == pytest.approx(
            NEW_START)
        assert moment.call_to_action.timeline_end == pytest.approx(CTA_END)


# ── 10. The gate derives what the build placed ────────────────────────
#
# The build redraws approved moments in memory and places the redrawn
# spans; the verifier derived the un-pinned file and read Reel 09's
# +2.25s CTA growth as 54 dropped frames, failing a correct build.
# The gate applies the same pins before deriving anything.


# --------------------------------------------------------------------------
# From test_reel_segment_collision.py
#
# Two reels that share a closer SHARE one caption file.
#
# Several reels legitimately close on the same spoken CTA (same clip, same
# source span); with identical pixels they must compute one filename, so the
# closer renders once. The captain's sixteen real reel names are a repository
# fixture so this runs on every machine. History: docs/evidence/reel_shared_closer.md.
# Different-pixel and two-content-key refusals: tests/unit/captions/test_subtitle_render.py.

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


# --------------------------------------------------------------------------
# From test_ending_and_caption_wiring.py
#
# The reel build CONSULTS the ending and the caption-timing owners.
#
# A router nobody is forced to call is a document, not a mechanism
# (`library/tools/edit_depth.py`). `tests/unit/reels/test_reel_ending.py` and
# `tests/unit/captions/test_caption_timing.py` prove each owner does what it says;
# this file proves the BUILD asks them, and fails the day a refactor
# stops asking. The reel build path needs Resolve, a project and a
# rendered caption set, so the consultation is asserted structurally -
# on the call graph of `rebuild_reels_in_project` and on the seam each
# call sits at - which is the same evidence
# `tests/unit/resolve/test_captain_edits.py::test_builder_threading_names_ranges`
# accepts for the trims beside it.

SOURCE = inspect.getsource(reel_build)


def _function(name):
    tree = ast.parse(SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not in reel_build")


def _calls(node):
    """Every `a.b(...)` call spelled inside a function, as 'a.b'."""
    out = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute) and isinstance(
                func.value, ast.Name):
            out.append(f"{func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name):
            out.append(func.id)
    return out


# ── The ending owner ───────────────────────────────────────────────


def test_the_tail_element_decides_what_draws_over_the_tail():
    """The behavioural half of the wiring, run for real: a declared
    `none` draws nothing on the last clip, a declared switch-off draws
    it, and no declaration keeps the unconditional arm that shipped
    before this existed."""
    from library.tools import reel_look

    unconditional = reel_look.power_effects({}, "first", "last")
    assert unconditional["last"]["tv_power_tail"] is True
    declared = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "tv_power_tail", "reason": "r"})
    assert declared["last"]["tv_power_tail"] is True
    silent = reel_look.power_effects(
        {}, "first", "last",
        ending={"reel": "R", "ends_on": {"anchor_phrase": "x"},
                "tail_element": "none", "reason": "r"})
    assert "last" not in silent
    # The switch-ON is never the ending's business.
    for out in (unconditional, declared, silent):
        assert out["first"]["tv_power_head"] is True


def test_reel13s_defect_cannot_pass_the_check_that_now_runs():
    """The exact numbers off the captain's timeline: SpeakerTwo's twelve
    frames could not carry the eighteen-frame switch-off, and the
    build now refuses instead of dropping it to stderr."""
    ending = {"reel": "Reel 13", "ends_on": {"anchor_phrase": "our bio"},
              "tail_element": "tv_power_tail", "reason": "captain"}
    fps = 24000 / 1001
    speakertwo = [{"source_in": 0.0, "source_out": 12 / fps}]
    with pytest.raises(reel_ending.TailElementHasNoRoom):
        reel_ending.assert_tail_fits(speakertwo, ending, fps)
    speakerone = [{"source_in": 0.0, "source_out": 310 / fps}]
    assert reel_ending.assert_tail_fits(
        speakerone, ending, fps)["shot_frames"] == 310


# ── The caption-timing owner ───────────────────────────────────────


# ── Promotion says what it is about to discard ─────────────────────


# ── The verifier reads the same owners the builder does ────────────


def test_a_declared_short_card_is_reported_and_an_authored_one_fails():
    """The captain trimmed his last closer card to three frames and
    recorded why. A gate that FAILS correct output is no more coverage
    than one that cannot fail - but only a RECORDED pin reaches the
    exemption."""
    from library.tools import reel_conformance_verifier as verifier

    fps = 24000 / 1001
    cards = [{"text": "the link's in our bio", "reel_start": 1900 / fps,
              "reel_end": 1903 / fps, "frames": 3},
             {"text": "a flash nobody chose", "reel_start": 500 / fps,
              "reel_end": 503 / fps, "frames": 3}]
    findings = verifier.check_short_captions(
        "Reel 13", cards, fps, declared_short=((1900, 3),))
    assert len(findings) == 2
    by_text = {f.detail["text"][:10]: f for f in findings}
    declared = by_text["the link's"]
    assert declared.severity == "warning" and declared.detail["declared"]
    assert "caption_timing.json" in declared.message
    authored = by_text["a flash no"]
    assert authored.severity == "error" and not authored.detail["declared"]
    # With no declaration at all, the floor is exactly as hard as it was.
    assert all(f.severity == "error"
               for f in verifier.check_short_captions("Reel 13", cards, fps))


# ── The freeze reaches the timeline, the comp and the plan ─────────


def test_the_tail_element_arms_on_the_hold_not_the_live_tail():
    """The join that decides whether the switch-off plays over her last
    words or after them."""
    from library.tools import reel_look

    ending = {"reel": "R", "ends_on": {"anchor_phrase": "x"},
              "tail_element": "tv_power_tail", "tail_hold": "freeze",
              "reason": "r"}
    live = {"clip": type("C", (), {"source_file": "/f/shot.mov",
                                   "track_index": 1, "speaker": "A",
                                   "track_type": "video"})(),
            "source_in": 0.0, "source_out": 310 / 24.0,
            "record": 0.0, "snapped_record": 0}
    freeze = reel_ending.plan_freeze([live], ending, 24.0)
    with_hold = reel_build._with_freeze([live], freeze, 24.0)
    assert len(with_hold) == 2 and with_hold[-1]["freeze"] is True
    manifest = reel_look.fusion_manifest(with_hold, {}, [], 24.0,
                                         ending=ending)
    per_clip = manifest["fusion_effects"]["per_clip"]
    # The HOLD is clip 01 and carries the switch-off; the live tail
    # (clip 00) carries only the switch-on, because it is also first.
    assert per_clip["reel_picture_01"]["tv_power_tail"] is True
    assert "tv_power_tail" not in per_clip["reel_picture_00"]
    assert per_clip["reel_picture_00"]["tv_power_head"] is True
    # Without the hold the element falls back onto the live tail, which
    # is what the captain saw across his closing line.
    alone = reel_look.fusion_manifest([live], {}, [], 24.0, ending=ending)
    assert alone["fusion_effects"]["per_clip"][
        "reel_picture_00"]["tv_power_tail"] is True


def test_no_declaration_adds_no_freeze_anywhere():
    assert reel_build._with_freeze([{"a": 1}], None, 24.0) == [{"a": 1}]


# ── The hold is found by its FILE, not by being last on the row ─────

class _FakeItem:
    """Enough of a Resolve timeline item for the freeze lookup."""

    def __init__(self, path, properties=None):
        self._path = path
        self._props = dict(properties or {})
        self.copied_onto = []

    def GetMediaPoolItem(self):
        return self

    def GetClipProperty(self, key):
        return self._path if key == "File Path" else ""

    def GetProperty(self, *args):
        return dict(self._props)

    def SetProperty(self, key, value):
        self._props[key] = value
        return True

    def CopyGrades(self, targets):
        self.copied_onto.extend(targets)
        return True


class _FakeTimeline:
    def __init__(self, items):
        self._items = list(items)

    def GetItemListInTrack(self, media, index):
        return list(self._items) if media == "video" and index == 1 else []


class _FakeFreeze:
    track_index = 1
    held_from = 1255
    duration_frames = 19

    def __init__(self, path):
        self.rendered_path = path
