"""A visual lands because of what is being SAID, on the word that says it.

The SUBJECT is the model's free text and the engine never reads it to
decide (`test_the_subject_is_inert`); the visual lands ON its anchor
phrase, found by SEARCH in the measured word timings, or is refused by
name. Rationale and the captain's ask: `docs/evidence/semantic_visual.md`.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_plan as mgp
from library.tools import semantic_visual as sv

FPS = 30
# The fixture words ride at real timeline seconds (the reel_09 "money"
# moment sits past the twenty-minute mark), so the test timeline must
# contain them: a 60-second clock would drop every anchored entry as
# outside_the_timeline, which is the resolver working, not failing.
DURATION = 1300.0

# Words in TIMELINE seconds, the shape `collect_word_windows` produces:
# "like huge ad campaigns and lots of money but"
WORDS = [
    {"word": "like", "start": 1209.0, "end": 1209.3},
    {"word": "huge", "start": 1209.4, "end": 1209.7},
    {"word": "ad", "start": 1209.72, "end": 1209.85},
    {"word": "campaigns", "start": 1209.92, "end": 1210.461},
    {"word": "and", "start": 1210.801, "end": 1210.921},
    {"word": "lots", "start": 1210.981, "end": 1211.181},
    {"word": "of", "start": 1211.221, "end": 1211.281},
    {"word": "money", "start": 1211.361, "end": 1211.621},
    {"word": "but", "start": 1212.161, "end": 1212.522},
]


def money_entry(**kw):
    base = {
        "element": "subject_emblem",
        "subject": "money - paid advertising budgets",
        "anchor_phrase": "lots of money",
        "hold_seconds": 2.0,
        "anchor": "middle_right",
        "copy": {"display": "$", "supporting": "ad budgets"},
        "color": "#F5C518",
    }
    base.update(kw)
    return base


def resolve(plan, words=WORDS, duration=DURATION):
    return mgp.resolve_plan(plan, timeline_duration=duration, fps=FPS,
                            palette_roles={},
                            word_windows=words)


# ── 1. the window lands on the word ───────────────────────────────────

def test_the_emblem_lands_on_its_word_window():
    """`lots of money` spans 1210.981-1211.621; the moment starts there."""
    start, end = sv.find_phrase_window(WORDS, "lots of money")
    assert start == pytest.approx(1210.981)
    assert end == pytest.approx(1211.621)
    # The search ignores case and punctuation.
    start, _ = sv.find_phrase_window(WORDS, "Lots Of Money,")
    assert start == pytest.approx(1210.981)


def test_an_unlandable_anchor_refuses_by_name():
    """Never said, an untimed anchor word, no word timings at all: each
    refuses by reason rather than guessing a window."""
    untimed = [dict(w) for w in WORDS]
    untimed[7] = {"word": "money", "start": None, "end": None}
    for words, phrase, reason in [
            (WORDS, "crypto fortune", "anchor_phrase_not_found"),
            (untimed, "lots of money", "anchor_word_untimed"),
            ([], "money", "no_word_timings_to_anchor_against")]:
        with pytest.raises(sv.SemanticVisualError) as exc:
            sv.find_phrase_window(words, phrase)
        assert exc.value.reason == reason


# ── 2. the subject is the model's, and the engine never reads it ──────

def test_the_subject_is_inert():
    """Same phrase, different subjects: identical timing and geometry.

    If the engine consulted the subject - a keyword table by any other
    name - "money" and "potatoes" would resolve differently. They do not:
    the subject travels onto the moment as provenance and nothing reads
    it to decide.
    """
    first = resolve([money_entry()])
    second = resolve([money_entry(subject="potatoes - a root vegetable")])
    assert first.moments and second.moments
    a, b = dict(first.moments[0]), dict(second.moments[0])
    a.pop("subject"), b.pop("subject")
    assert a == b


# ── 3. the plan honours the anchor ─────────────────────────────────────

def test_resolve_plan_lands_an_anchored_entry_on_its_words():
    resolved = resolve([money_entry()])
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    moment = resolved.moments[0]
    assert moment["timeline_start"] == pytest.approx(1210.981, abs=0.034)
    assert moment["timeline_end"] == pytest.approx(1210.981 + 2.0, abs=0.034)
    assert moment["timing_basis"] == "word_window:lots of money"
    assert moment["subject"] == "money - paid advertising budgets"


def test_two_timings_is_ambiguous_and_refused():
    """An entry naming BOTH an anchor phrase and explicit seconds claims
    two timings. The engine does not pick one - choosing would be the
    chooser (AGENTS.md 10.5) - so the entry is dropped as conflicting."""
    resolved = resolve([money_entry(start_seconds=5.0, duration_seconds=1.0)])
    assert not resolved.moments
    assert resolved.dropped[0].reason == "conflicting_timing"


# ── 5. spine words reach the resolver in timeline seconds ──────────────

def test_collect_word_windows_maps_source_onto_the_timeline():
    """Spine word timings ride in SOURCE seconds (AGENTS.md 6); the block
    carries both clocks, so the resolver reads timeline seconds."""
    spine = {"structure": [{
        "clip_id": "clip_001",
        "timeline_start": 100.0,
        "source_start": 50.0,
        "word_timestamps": [
            {"word": "lots", "start": 60.981, "end": 61.181},
            {"word": "of", "start": 61.221, "end": 61.281},
            {"word": "money", "start": 61.361, "end": 61.621},
        ],
    }]}
    windows = sv.collect_word_windows(spine)
    assert [(w["word"], round(w["start"], 3)) for w in windows] == [
        ("lots", 110.981), ("of", 111.221), ("money", 111.361)]
    start, end = sv.find_phrase_window(windows, "lots of money")
    assert start == pytest.approx(110.981)
    assert end == pytest.approx(111.361 + 0.26)
    # A block with no timeline clock contributes nothing.
    assert sv.collect_word_windows({"structure": [
        {"clip_id": "clip_001", "word_timestamps": [
            {"word": "money", "start": 1.0, "end": 1.5}]}]}) == []

