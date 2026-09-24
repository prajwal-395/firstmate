"""A visual lands because of what is being SAID, on the word that says it.

The gap this closes is SELECTION, not drawing. `MotionGraphics/index.tsx`
already draws Vox-shaped things - bars, counters, stamps, accents - and the
model was already asked to plan them. What nobody was ever asked is the
captain's question of 2026-09-08: *"if im talking about money, then having
assets of currency animated in"* - the step that reads "we're talking about
money here" and asks for currency.

Two rules govern the mechanism, and both are the captain's standing rules
applied to a new decision:

1. **The SUBJECT is the model's reasoning, never an engine table.**
   `semantic_visual` carries no keyword-to-icon mapping - no dict that turns
   "money" into "$". The model writes `subject` as free text and names the
   mark itself in `copy`; the engine resolves timing and geometry and never
   reads the subject to decide anything. `test_the_subject_is_inert` is the
   runnable statement: two entries differing only in subject resolve
   identically.
2. **The visual lands ON its word, not near it.** The previous lane's
   finding: things that decorate ACROSS the speech look wrong, things cued
   to their own measured word window look right. An entry names an
   `anchor_phrase` - words from the speech - and the engine searches the
   measured word timings for it (the AGENTS.md 6 discipline: anchored by
   SEARCH, never asserted). No word timings, no landing: the entry is
   dropped by name.

What the asset IS is stated honestly in `semantic_visual.ASSET_SOURCE`:
composed from type and shapes the renderer already draws. No network, no
licence, no fetch - a fetched illustration would need both, and a generated
one would need a model the pipeline does not run.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_plan as mgp
from library.tools import motion_graphics_vocabulary as mgv
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


def test_search_ignores_case_and_punctuation():
    start, _ = sv.find_phrase_window(WORDS, "Lots Of Money,")
    assert start == pytest.approx(1210.981)


def test_a_phrase_that_was_never_said_refuses_by_name():
    with pytest.raises(sv.SemanticVisualError) as exc:
        sv.find_phrase_window(WORDS, "crypto fortune")
    assert exc.value.reason == "anchor_phrase_not_found"


def test_an_untimed_anchor_word_refuses_rather_than_guesses():
    words = [dict(w) for w in WORDS]
    words[7] = {"word": "money", "start": None, "end": None}
    with pytest.raises(sv.SemanticVisualError) as exc:
        sv.find_phrase_window(words, "lots of money")
    assert exc.value.reason == "anchor_word_untimed"


def test_no_word_timings_is_a_refusal_not_an_empty_search():
    with pytest.raises(sv.SemanticVisualError) as exc:
        sv.find_phrase_window([], "money")
    assert exc.value.reason == "no_word_timings_to_anchor_against"


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


def test_the_engine_carries_no_keyword_table():
    """No mapping in `semantic_visual` turns a subject into a mark.

    The model names the mark in `copy`; an engine-side dict from words to
    glyphs would be hardcoded taste (AGENTS.md 10.5), so its absence is
    asserted structurally, not just behaviourally.
    """
    assert sv.find_phrase_window is not None  # the module resolved
    banned = ("KEYWORD", "ICON_TABLE", "GLYPH", "SUBJECT_TABLE",
              "WORD_TO_ASSET", "ASSET_FOR")
    for name in banned:
        assert not hasattr(sv, name), f"semantic_visual.{name} looks like a choice table"
    source_path = sys.modules[sv.__name__].__file__
    with open(source_path, encoding="utf-8") as f:
        source = f.read()
    for literal in ("\"money\":", "'money':", "\"currency\":", "money ->",
                    "money->", "ord(\"$\")"):
        assert literal not in source, f"{literal!r} in semantic_visual.py"


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


def test_collect_word_windows_skips_blocks_with_no_clock():
    spine = {"structure": [
        {"clip_id": "clip_001", "word_timestamps": [
            {"word": "money", "start": 1.0, "end": 1.5}]}]}
    assert sv.collect_word_windows(spine) == []
