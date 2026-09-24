"""A reel INHERITS its freeze ending from the call to action it closes on.

The captain, 2026-09-11, on Reels 01 and 23: *"this change needs to be
applied to all other reels that currently also use this CTA **or will
be using this CTA**"*.  The second half of that sentence is what these
tests are for.  Four hand-written entries in `external/reel_ending.json`
would have satisfied the four reels he named and failed the
instruction, because a reel planned tomorrow would have closed the old
way with nothing to say so.

So the freeze hangs on `reel_proposal.CallToAction` - the thing a reel
closes ON - and `test_a_reel_nobody_declared_anything_for_inherits_the_
freeze` is the gate: it fails the moment a newly planned reel stops
inheriting it.

Measured on the field test's own plan and stated here because it is why
this cannot hang on a passage or a speaker: the four reels complained
about close on THREE different call-to-action passages and Reel 13 on a
fourth, and Reel 28's closer is CRAIG.

Synthetic under `tmp_path` (AGENTS.md 8); nothing here reaches Resolve
or a real project.
"""

import json
import os
from types import SimpleNamespace

import pytest

from library.tools import reel_build, reel_ending, reel_look

FPS = 24.0

#: Akshita's closer and Craig's, as they sit in this episode - two
#: different passages, two different speakers, one behaviour.
AKSHITA_CTA = {
    "timeline_start": 333.8, "timeline_end": 341.27, "speaker": "Akshita",
    "text": "And if you want to see how your brand appears, you should "
            "go check it out. The link's in our bio."}
CRAIG_CTA = {
    "timeline_start": 809.69, "timeline_end": 819.13, "speaker": "Craig",
    "text": "So definitely check it out on our website, also the links "
            "in the bio."}


def _moment(cta, number=99, slug="a-reel-planned-tomorrow"):
    """A plan moment, as `ReelMoment` carries one into the build."""
    return SimpleNamespace(
        number=number, slug=slug,
        timeline_name=f"Reel {number:02d} - {slug}",
        call_to_action=(None if cta is None
                        else SimpleNamespace(**dict(cta, note=""))))


def _transcript(cta, words):
    """A transcript whose timed words sit inside `cta`'s own span."""
    start = float(cta["timeline_start"])
    step = (float(cta["timeline_end"]) - start) / (len(words) + 1)
    return {"segments": [{"words": [
        {"word": word, "start": round(start + index * step, 3),
         "end": round(start + index * step + step / 2, 3), "timed": True}
        for index, word in enumerate(words)]}]}


def _declare(root, endings):
    external = os.path.join(root, "external")
    os.makedirs(external, exist_ok=True)
    with open(os.path.join(external, "reel_ending.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"version": 1, "endings": endings}, handle)


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
        _moment(AKSHITA_CTA))

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
    """Reel 28 closes on CRAIG, and takes the identical path.

    The captain named him precisely because the first fix was built
    around Akshita's closing line.  Nothing in the mechanism may read
    the speaker to decide the hold.
    """
    akshita = reel_ending.resolve_ending(
        str(tmp_path), "Reel 23 - why-small-business-wins-on-ai",
        _moment(AKSHITA_CTA))
    craig = reel_ending.resolve_ending(
        str(tmp_path), "Reel 28 - the-nail-salon-query-google-cant-answer",
        _moment(CRAIG_CTA))

    for ending in (akshita, craig):
        assert ending["tail_hold"] == "freeze"
        assert ending["tail_element"] == "tv_power_tail"
    # The speaker is RECORDED and takes no part in the decision: the two
    # endings differ only in the reel, the words and whose name is filed.
    assert akshita["cta"]["speaker"] == "Akshita"
    assert craig["cta"]["speaker"] == "Craig"
    assert {k: v for k, v in akshita.items()
            if k not in ("reel", "ends_on", "cta")} == \
           {k: v for k, v in craig.items()
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
        _moment(AKSHITA_CTA))

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
           "speaker": "Akshita",
           "text": "what we fixed with the Lucy visibility system"}
    spoken = ["what", "we", "fixed", "with", "the", "lucie",
              "visibility", "system"]

    ending = reel_ending.cta_default_ending(
        "Reel 07 - number-one-on-google-invisible-to-ai", _moment(cta),
        _transcript(cta, spoken))

    assert "lucie" in ending["ends_on"]["anchor_phrase"]
    assert "lucy" not in ending["ends_on"]["anchor_phrase"]
    # With no transcript the CTA's own text is the fallback, because an
    # ending that names no words at all is worse than one that names
    # the plan's.
    fallback = reel_ending.cta_default_ending("Reel 07 - x", _moment(cta))
    assert "lucy" in fallback["ends_on"]["anchor_phrase"]


# ── What the inheritance actually does to a build ──────────────────

def _shot(track_index=1, speaker="Akshita"):
    return SimpleNamespace(timeline_start=333.8, timeline_end=341.27,
                           source_in=500.0, track_index=track_index,
                           speaker=speaker, source_file="/tmp/closer.mxf",
                           track_type="video")


def _placements(frames=179):
    return [{"clip": _shot(), "source_in": 500.0,
             "source_out": 500.0 + frames / FPS,
             "snapped_record": 1056, "record": 1056 / FPS,
             "track_index": 1, "speaker": "Akshita",
             "master": (333.8, 341.27)}]


def test_an_inherited_ending_plans_the_same_freeze_a_declared_one_does(
        tmp_path):
    inherited = reel_ending.resolve_ending(
        str(tmp_path), "Reel 01 - geo-is-comprehension-not-position",
        _moment(AKSHITA_CTA))
    freeze = reel_ending.plan_freeze(_placements(), inherited, FPS)

    assert freeze is not None
    # The hold's length is the ELEMENT's own, never a number stated here.
    assert freeze.duration_frames == reel_ending.tail_room_frames(inherited)
    # It begins on the frame after the live tail: the animation starts
    # after the last word rather than over it.
    assert freeze.reel_start_frame == 1056 + 179
    measured = reel_ending.assert_tail_fits(_placements(), inherited, FPS)
    assert measured["fits"] and measured["hold"] == "freeze"


def test_the_inherited_ending_arms_the_element_on_the_held_frame(tmp_path):
    """`reel_look.power_effects` arms the tail element on whatever clip
    sorts last.  With the freeze in the list that is the HOLD, which is
    the whole point - and it is armed as DECLARED, so the renderer
    refuses rather than silently undoing it."""
    inherited = reel_ending.resolve_ending(
        str(tmp_path), "Reel 23 - why-small-business-wins-on-ai",
        _moment(AKSHITA_CTA))
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
                             speaker="Akshita"),
             SimpleNamespace(timeline_start=341.55, timeline_end=345.0,
                             source_in=900.0, track_index=2,
                             speaker="Craig")]
    cta = dict(AKSHITA_CTA)
    spoken = ["you", "should", "go", "check", "it", "out", "the",
              "link's", "in", "our", "bio"]
    transcript = _transcript(cta, spoken)
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
            for p in reel_build.placements(out, shots, FPS)] == ["Akshita"]

    # And a range stopping short is extended no further than the same
    # bound, so the outward reading cannot admit Craig either.
    short = [(333.8, 339.0)]
    out, _ = reel_ending.apply_ending(
        short, reel_build.placements(short, shots, FPS), transcript,
        inherited, FPS)
    assert out[-1][1] <= 341.55
    assert [p["clip"].speaker
            for p in reel_build.placements(out, shots, FPS)] == ["Akshita"]


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
                             speaker="Akshita")]
    transcript = {"segments": [{"words": [
        {"word": "the", "start": 340.6, "end": 340.7, "timed": True},
        {"word": "link's", "start": 340.7, "end": 340.9, "timed": True},
        {"word": "in", "start": 340.9, "end": 340.95, "timed": True},
        {"word": "our", "start": 340.99, "end": 341.05, "timed": True},
        {"word": "bio", "start": 341.07, "end": 341.27, "timed": True}]}]}
    declared = reel_ending.resolve_ending(
        str(tmp_path), "Reel 07 - pinned", _moment(AKSHITA_CTA), transcript)
    assert not reel_ending.is_inherited(declared)
    ranges = [(333.8, 341.27)]
    out, record = reel_ending.apply_ending(
        ranges, reel_build.placements(ranges, shots, FPS), transcript,
        declared, FPS)
    assert out == ranges
    assert len(record["held"]) == 1 and record["held"][0]["breath_end"] is None


@pytest.mark.parametrize("cta", [AKSHITA_CTA])
def test_every_inherited_ending_passes_the_declared_check(cta):
    """A default that would be refused as a hand-written declaration is
    a default nobody could have written down."""
    ending = reel_ending.cta_default_ending("Reel 99 - x", _moment(cta))
    assert reel_ending.validate_endings([ending]) == [ending]
    assert ending["reason"].strip()
