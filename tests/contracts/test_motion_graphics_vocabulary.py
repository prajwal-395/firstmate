"""The motion-graphics roster is a vocabulary, and these are the rules that keep it one.

Every entry is an axis, not a value; nothing is keyed to one identity; the
renderer did not shape the roster; and it takes neither open captain decision
(what produces copy, props schema vs authored component). AGENTS.md 16.
History: docs/evidence/motion_graphics_vocabulary.md#the-tests.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_vocabulary as mgv

# ── 1. an axis, not a value ──────────────────────────────────────────

def test_no_field_of_an_entry_or_an_axis_can_hold_a_magnitude():
    """The structural half: nothing here has a numeric field to read.

    A renderer can only take a value out of this table through a field.
    Every field of both dataclasses is a string, a tuple of strings or a
    bool, so there is no colour, duration, size or intensity for one to
    take - whatever the prose says.
    """
    allowed = {"str", "tuple[str, ...]", "bool"}
    for cls in (mgv.MotionElement, mgv.Axis):
        for name, field in cls.__dataclass_fields__.items():
            annotation = str(field.type).replace("'", "")
            assert annotation in allowed, (
                f"{cls.__name__}.{name} is annotated {annotation!r}. A "
                f"field that can hold a number is a place for a settled "
                f"value; the magnitude belongs to whoever declares it.")


def test_no_axis_carries_a_default_or_a_bound():
    mgv.assert_no_settled_values()


# ── 2. series-neutral ────────────────────────────────────────────────

#: Identity tokens that really exist in this repository and in the
#: captain's own style specification. None of them may appear in what a
#: consumer of the roster reads.
IDENTITY_TOKENS = (
    "prajwal", "lucie", "pipeline_edit", "firstmate",
    "casey", "neistat", "scott yu-jan", "yu-jan",
    "bass guitar",
)


def test_nothing_in_the_roster_is_keyed_to_one_identity():
    """Checked over the rows a consumer gets, not over the module's evidence.

    The engine serves a daily channel and client work (2026-08-25), so a
    vocabulary that names one of them is a defect rather than a
    shortcut, and would make every client's video look like the
    captain's.
    """
    payload = " ".join([
        repr(mgv.roster_rows()),
        repr(mgv.axis_rows()),
        repr(mgv.FUNCTIONS),
        repr(mgv.OUT_OF_VOCABULARY),
        repr(mgv.ROSTER_LEGEND),
    ]).lower()
    hits = sorted(token for token in IDENTITY_TOKENS if token in payload)
    assert not hits, (
        f"the roster names {hits}. Nothing in the vocabulary may be "
        f"specific to one channel, host or project.")
    assert "001" not in payload, (
        "the roster names project 001. A vocabulary entry justified by "
        "one project is that project's declaration, not a vocabulary "
        "entry.")
    # The only identity route is a project-supplied asset (channel_bug).
    bug = mgv.ELEMENTS_BY_KEY["channel_bug"]
    assert "asset" in bug.axes
    assert any("engine" in refusal and "artwork" in refusal
               for refusal in bug.never), (
        "channel_bug must refuse artwork this engine ships; AGENTS.md "
        "section 14 puts artwork with the project that owns the series.")


# ── 3. the renderer did not shape the roster ─────────────────────────

def test_every_reachable_entry_is_drawn_by_the_composition_by_name():
    """The implicit roster nobody wrote down, absorbed rather than discarded.

    `MotionGraphics/index.tsx` used to draw an upper third, corner
    brackets and a progress bar behind three booleans, and this test
    grepped for the boolean NAMES. Since 2026-09-02 the composition
    dispatches on the ROSTER KEY itself - `element.element ===
    "title_lockup"` - so the grep is now for the keys, which is the
    stronger statement: it fails if an entry claims to be reachable and
    the renderer has no arm for it.
    """
    composition = os.path.join(
        PROJECT_ROOT, "remotion-subtitles", "src", "compositions",
        "MotionGraphics", "index.tsx")
    with open(composition, "r", encoding="utf-8") as handle:
        tsx = handle.read()

    reachable = [e for e in mgv.ROSTER if e.reachable == mgv.REACHABLE_NOW]
    assert reachable
    for element in reachable:
        assert element.reachability_note
        assert f'"{element.key}"' in tsx, (
            f"{element.key} is marked reachable_now and the composition "
            f"has no arm for it. The roster's reachable_now entries are a "
            f"claim about that file.")
    # The planner's DRAWABLE is DERIVED from the roster, not a second
    # list that would go stale silently.
    from library.tools import motion_graphics_plan as mgp
    assert mgp.DRAWABLE == frozenset(
        e.key for e in mgv.ROSTER if e.reachable == mgv.REACHABLE_NOW)


# ── 4. neutral on the two open captain decisions ─────────────────────

def test_the_roster_takes_neither_open_captain_decision():
    """What a graphic SAYS, and where the words come from, stays the captain's.

    Every entry declares only WHETHER it needs a text payload. A model
    writing the copy, a project declaring it, a transcript supplying it
    and a template carrying it all satisfy the same entry.
    """
    assert set(mgv.COPY_SOURCE_IS_UNSET) == {"copy_source",
                                             "component_authoring"}
    payload = repr(mgv.roster_rows()).lower()
    for producer in ("creative_direction", "the model writes",
                     "llm", "transcript supplies", "project declares"):
        assert producer not in payload, (
            f"the roster names {producer!r} as a source of copy. That is "
            f"an open captain decision as of 2026-08-29.")
    for element in mgv.ROSTER:
        assert element.copy in ("required", "optional", "none")
    # Props schema or authored component - the entry reads the same.
    for mechanism in ("props schema", "react component", "tsx",
                      "authored component", "input props"):
        assert mechanism not in payload, (
            f"the roster names {mechanism!r}. Whether the model authors "
            f"components or fills a props schema is an open captain "
            f"decision as of 2026-08-29.")


# ── The shape of the roster ──────────────────────────────────────────

def test_a_key_outside_the_roster_is_refused_by_name():
    assert mgv.canonical_key("stat_callout") == "stat_callout"
    assert mgv.canonical_key("  Stat_Callout ") == "stat_callout"
    assert mgv.canonical_key("zoom_emphasis") is None
    assert "plan_vfx" in mgv.refusal_reason("zoom_emphasis")
    assert mgv.refusal_reason("stat_callout") == ""
    assert "not a motion-graphics element" in mgv.refusal_reason("sparkles")


def test_a_malformed_entry_is_refused():
    """The well-formedness check can fail, so it is coverage."""
    broken = mgv.MotionElement(
        key="bad", function="identify", what_it_is="x",
        earns_its_place="x", needs="x", never=("x",),
        axes=("no_such_axis",), copy="none",
        reachable=mgv.REACHABLE_NOW)
    original = mgv.ROSTER
    mgv.ROSTER = original + (broken,)
    try:
        with pytest.raises(mgv.MotionVocabularyError):
            mgv.assert_roster_is_well_formed()
    finally:
        mgv.ROSTER = original
