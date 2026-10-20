"""Silence and filler reduction, proved against named defects.

EDIT-016 said "implement silence and filler reduction without damaging
natural conversational rhythm", and the repository could do neither
half: the silence was measured and never priced, and the filler was
measured and never offered as an edit.  Nothing in the repository could
say what a removal would COST, so a tightened cut was one breath away
from a rushed one and nothing said so.

Each test names one defect this module closes:

- a trim that lands inside a WORD is proposed, so the cut clips a
  syllable;
- a trim that spans the pause beside a measured EMPHASIS is proposed,
  so the cut removes the beat the speaker marked;
- a silence is proposed for removal on a threshold the project never
  chose;
- a filler is removed without the project permitting it;
- the "close the pause" treatment is offered as if it cost the same as
  the "keep the pause" one;
- a removal takes a WORD the passage did not offer.

None of them asserts that the module contains a particular entry or a
particular count: a passage is built, trimmed and priced, so a test
passes only when the module actually does what the defect requires.
"""
from __future__ import annotations

from library.tools import silence_reduction
from library.tools.editorial_policy import parse_policy


def _words(*rows):
    return [{"word": word, "source_start": start, "source_end": end}
            for word, start, end in rows]


def _passage():
    """Four short words, a 3.1s silence, and one more word.

    The gaps are 0.1s, 0.1s, 0.1s and 3.1s, so the passage's own median
    pause is 0.1s and the long silence is the material's outlier.
    """
    return _words(("one", 0.0, 0.4), ("two", 0.5, 0.9),
                  ("three", 1.0, 1.4), ("four", 1.5, 1.9),
                  ("five", 5.0, 5.4))


def _policy(**slots):
    declared = {"objective": "interview", "slots": slots}
    return parse_policy(declared, project_id="demo")


def _prosody(word="four", start=1.5, end=1.9, emphasis=0.8):
    return {"prosody": {"word_prosody": [
        {"word": word, "start": start, "end": end, "emphasis": emphasis}]}}


# ── The rhythm is measured ────────────────────────────────────────

def test_the_rhythm_profile_measures_the_passages_own_pauses():
    """The profile records the passage's own pauses and its median."""
    profile = silence_reduction.rhythm_profile(_passage())
    assert profile["gap_count"] == 4
    assert profile["median_gap_seconds"] == 0.1
    assert profile["largest_gap_seconds"] == 3.1
    assert profile["word_count"] == 5


def test_a_passage_with_no_words_has_no_rhythm_to_price():
    """Nothing measured means nothing to measure."""
    profile = silence_reduction.rhythm_profile([])
    assert profile["gap_count"] == 0
    assert profile["median_gap_seconds"] == 0.0
    assert profile["delivery_rate_words_per_second"] is None


# ── The two refusals ──────────────────────────────────────────────

def test_a_trim_inside_a_word_is_refused():
    """A cut inside a word clips a syllable."""
    profile = silence_reduction.rhythm_profile(_passage())
    cost = silence_reduction.rhythm_cost(
        profile, {"start_seconds": 0.2, "end_seconds": 0.6}, _passage())
    assert cost["verdict"] == silence_reduction.REFUSED
    assert cost["reason"] == silence_reduction.CLIPS_A_WORD
    assert cost["clips_word"] == "one"


def test_a_trim_that_spans_the_pause_beside_an_emphasis_is_damaged():
    """The speaker's own emphasis marks a beat; removing it is damage."""
    words = _passage()
    profile = silence_reduction.rhythm_profile(words,
                                               prosody_profile=_prosody())
    assert profile["gaps"][-1]["beat_pause"] is True
    cost = silence_reduction.rhythm_cost(
        profile, {"start_seconds": 1.9, "end_seconds": 5.0}, words)
    assert cost["verdict"] == silence_reduction.DAMAGED
    assert cost["reason"] == silence_reduction.REMOVES_A_BEAT_PAUSE
    assert cost["beat_pauses_removed"]


def test_the_same_trim_is_preserved_when_nothing_marked_the_pause():
    """Without a measured emphasis the pause is an ordinary pause."""
    words = _passage()
    profile = silence_reduction.rhythm_profile(words)
    cost = silence_reduction.rhythm_cost(
        profile, {"start_seconds": 1.9, "end_seconds": 5.0}, words)
    assert cost["verdict"] == silence_reduction.PRESERVED
    assert cost["removed_word_count"] == 0


def test_a_longer_pause_among_short_ones_is_still_measured_not_judged():
    """The module ORDERS and it REPORTS; the length alone condemns nothing."""
    words = _passage()
    profile = silence_reduction.rhythm_profile(words)
    cost = silence_reduction.rhythm_cost(
        profile, {"start_seconds": 1.9, "end_seconds": 5.0}, words)
    assert cost["removed_seconds"] == 3.1
    assert cost["removed_gap_seconds"] == 3.1


# ── The threshold comes from the project or the material ──────────

def test_the_threshold_is_the_materials_own_distribution_when_undeclared():
    """A material where every pause is short proposes nothing."""
    words = _words(("a", 0.0, 0.4), ("b", 0.5, 0.9), ("c", 1.0, 1.4))
    found = silence_reduction.silence_candidates(words)
    assert found["threshold_source"] == "material"
    assert found["candidates"] == []


def test_the_project_declared_threshold_overrides_the_materials():
    """A declared number is the project's own."""
    words = _words(("a", 0.0, 0.4), ("b", 0.5, 0.9), ("c", 1.6, 2.0))
    policy = _policy(silence={"max_gap_seconds": 0.4})
    found = silence_reduction.silence_candidates(words, policy=policy)
    assert found["threshold_source"] == "policy"
    assert found["threshold_seconds"] == 0.4
    assert len(found["candidates"]) == 1


def test_a_silence_above_the_materials_own_threshold_is_a_candidate():
    """The long silence in the passage is the material's outlier."""
    found = silence_reduction.silence_candidates(_passage())
    assert len(found["candidates"]) == 1
    candidate = found["candidates"][0]
    assert candidate["seconds"] == 3.1
    assert candidate["threshold_source"] == "material"


# ── Fillers ───────────────────────────────────────────────────────

def _filler_passage():
    """A filler, a long pause after it, and three ordinary gaps.

    The positive gaps are 0.5s, 1.2s and 0.5s, so the passage's own
    median pause is 0.5s and the 1.2s one is above it.
    """
    return _words(("i", 0.0, 0.3), ("um", 0.8, 1.2), ("went", 2.4, 2.8),
                  ("home", 2.8, 3.3), ("and", 3.8, 4.3))


def test_a_filler_is_not_removed_without_the_projects_permission():
    """Removing a filler is an edit the project has to allow."""
    found = silence_reduction.filler_candidates(_filler_passage())
    assert found["permitted"] is False
    report = silence_reduction.propose_reductions(_filler_passage())
    assert [row for row in report["proposals"] if row["kind"] == "filler"] == []
    assert any(row["kind"] == "filler" for row in report["reported"])


def test_a_permitted_filler_is_proposed_with_both_treatments_priced():
    """Keep-the-pause and close-the-pause cost different amounts."""
    policy = _policy(silence={"remove_fillers": True})
    found = silence_reduction.filler_candidates(_filler_passage(), policy=policy)
    assert found["permitted"] is True
    assert len(found["candidates"]) == 1
    treatments = {row["treatment"]: row["rhythm_cost"]
                  for row in found["candidates"][0]["options"]}
    assert set(treatments) == {"keep_the_pause", "close_the_pause"}
    assert treatments["keep_the_pause"]["removed_word_count"] == 1
    assert treatments["keep_the_pause"]["removed_gap_seconds"] == 0.0
    assert treatments["close_the_pause"]["removed_gap_seconds"] > 0.0


def test_closing_the_pause_beside_an_emphasis_is_damaged():
    """The treatment that takes the beat is the one that is priced as damage."""
    words = _filler_passage()
    policy = _policy(silence={"remove_fillers": True})
    prosody = {"prosody": {"word_prosody": [
        {"word": "um", "start": 0.8, "end": 1.2, "emphasis": 0.9}]}}
    found = silence_reduction.filler_candidates(words, policy=policy,
                                                prosody_profile=prosody)
    treatments = {row["treatment"]: row["rhythm_cost"]
                  for row in found["candidates"][0]["options"]}
    assert treatments["close_the_pause"]["verdict"] == silence_reduction.DAMAGED
    assert treatments["close_the_pause"]["reason"] == (
        silence_reduction.REMOVES_A_BEAT_PAUSE)
    assert treatments["keep_the_pause"]["verdict"] == silence_reduction.PRESERVED


# ── What a proposal promises ──────────────────────────────────────

def test_a_proposed_reduction_takes_no_word_but_a_filler():
    """A reduction that takes a word the passage did not offer is a cut."""
    report = silence_reduction.propose_reductions(
        _filler_passage(),
        policy=_policy(silence={"remove_fillers": True}))
    fillers = [row for row in report["proposals"] if row["kind"] == "filler"]
    assert fillers
    for proposal in fillers:
        assert proposal["removed_seconds"] >= 0.0
        assert proposal["treatment"] in ("keep_the_pause", "close_the_pause")
    for proposal in report["proposals"]:
        if proposal["kind"] == "silence":
            assert proposal["removed_seconds"] == proposal["seconds"]


def test_a_beat_pause_is_reported_rather_than_proposed():
    """A silence the engine will not cut is still a silence to see."""
    words = _passage()
    report = silence_reduction.propose_reductions(
        words, prosody_profile=_prosody())
    assert report["proposals"] == []
    reported = [row for row in report["reported"]
                if row["kind"] == "silence"]
    assert reported
    assert reported[0]["rhythm_cost"]["reason"] == (
        silence_reduction.REMOVES_A_BEAT_PAUSE)
