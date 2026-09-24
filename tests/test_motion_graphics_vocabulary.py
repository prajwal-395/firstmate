"""The motion-graphics roster is a vocabulary, and these are the rules that keep it one.

The roster answers an open captain decision delegated on 2026-08-29:
*"which motion-graphics elements belong in our vocabulary"*. The failure
it closes is the one the round-2 creative audit counted nine times in
twenty-eight decisions - a decision the model was never offered - and the
failure it must not introduce is the one PR #310 spent an audit removing:
a value reaching a frame from a table in this repository rather than from
somebody's declaration.

So these tests check four things a reviewer would otherwise have to take
on trust:

1. **Every entry is an axis, not a value.** Structurally - no field of
   `MotionElement` or `Axis` can hold a magnitude - and textually,
   against the module's own source, so a colour or a duration cannot
   reappear in the prose either.
2. **Nothing in the roster is keyed to one identity.** The engine serves
   a daily channel and client work (2026-08-25), so a channel's name, a
   host's name or one project's slug in the vocabulary is a defect.
3. **The broken renderer did not shape the roster.** Eleven of fifteen
   entries cannot be drawn today. If that ever inverts silently, the
   vocabulary has been trimmed to fit a defect.
4. **It works under either answer to the two open captain decisions** -
   what produces the copy, and whether the model authors a component or
   fills a props schema.
"""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_vocabulary as mgv

MODULE_PATH = mgv.__file__


def _source() -> str:
    with open(MODULE_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _table_source(*names: str) -> str:
    """The source text of the named module-level assignments.

    Scoped deliberately. The module DOCSTRING cites evidence - project
    001's eight empty ProRes segments, the withdrawn cyan, PR numbers -
    and evidence is what a `[why]` link carries in this repository. The
    VOCABULARY is what a consumer reads and what could reach a frame, so
    that is what these scans are pointed at.
    """
    tree = ast.parse(_source())
    chunks = []
    lines = _source().splitlines()
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
        if any(name in names for name in targets):
            chunks.append("\n".join(lines[node.lineno - 1:node.end_lineno]))
    assert chunks, f"none of {names} found as module-level assignments"
    return "\n".join(chunks)


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


#: A value shape in the prose. Each entry names WHY the citation is not a
#: settled value, and every one of them is a withdrawal or a measurement
#: reported about something else. A future value has to be added here
#: with a reason, which is the point.
CITED_VALUES = {
    "#00D4FF": (
        "The withdrawn cyan, named in colour_role's axis so the reason "
        "the axis is a role and not a colour is legible. It is recorded "
        "as withdrawn in generate_motion_props too."
    ),
    "60px": (
        "The withdrawn corner-accent literal, named in frame_accents' "
        "refusals. safe_area.py records the same number as the defect."
    ),
    "5 Hz": (
        "The rate compute_face_presence measures a face centre at - a "
        "fact about another step, reported so tracked_label's "
        "unreachability is specific."
    ),
}

_VALUE_SHAPES = re.compile(
    r"#[0-9a-fA-F]{6}\b"
    r"|\b\d+(?:\.\d+)?\s?(?:px|pt|ms|dB|%|Hz)\b"
    r"|\b\d+(?:\.\d+)?\s?(?:seconds?|frames?|stops?)\b"
)


def test_no_settled_colour_duration_or_size_appears_in_the_vocabulary():
    """The textual half, over exactly what a consumer of the roster reads."""
    text = _table_source("ROSTER", "AXES", "FUNCTIONS", "OUT_OF_VOCABULARY",
                         "ROSTER_LEGEND")
    found = {match.group(0) for match in _VALUE_SHAPES.finditer(text)}
    unexplained = sorted(found - set(CITED_VALUES))
    assert not unexplained, (
        f"{unexplained} look like settled values in the roster. A "
        f"vocabulary defines the axis; the magnitude is the declaring "
        f"author's. If one of these is a citation rather than a value, "
        f"add it to CITED_VALUES with the reason.")
    for cited in CITED_VALUES:
        assert cited in text, (
            f"{cited!r} is recorded in CITED_VALUES and no longer "
            f"appears. Delete the entry rather than leaving a stale "
            f"exemption behind.")


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


def test_the_only_identity_route_is_a_project_supplied_asset():
    """`channel_bug` is the closest an entry comes to identity."""
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


def test_the_drawable_set_the_planner_uses_is_the_rosters_own():
    """`motion_graphics_plan.DRAWABLE` is DERIVED, not a second list.

    A second list of what the renderer can draw is a list that goes
    stale, and the way it goes stale is silent: an entry that becomes
    reachable keeps being dropped as unreachable and the drop is
    recorded as if it were a fact about the renderer.
    """
    from library.tools import motion_graphics_plan as mgp
    assert mgp.DRAWABLE == frozenset(
        e.key for e in mgv.ROSTER if e.reachable == mgv.REACHABLE_NOW)


# ── 4. neutral on the two open captain decisions ─────────────────────

def test_the_roster_names_no_producer_of_copy():
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


def test_the_roster_names_no_authoring_mechanism():
    """Props schema or authored component - the entry reads the same either way.

    An entry names a kind, its axes, its inputs and its refusals. Under
    the props answer the axes ARE the schema; under the authoring answer
    they are the brief the component must honour and the refusals are
    what review checks it against.
    """
    payload = repr(mgv.roster_rows()).lower()
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


# ── The prompt-side route ────────────────────────────────────────────

# ── 4.06's prompt states the renderer's real ceiling ─────────────────
#
# The captain's named failure was that the model planned text graphics
# and small icons when asked for animation, and their reading was that
# better instruction would have fixed it. That is half right:
# docs/ANIMATED_REEL_CEILING.md recorded what the vocabulary cannot
# express at all, and a prompt rewritten without saying so would teach
# the model to ask for things that get dropped by name.
#
# So the prompt states the ceiling - and these pin the two halves of
# that statement that CAN rot silently: a count it asserts about the
# roster, and a limit that would stop being true the day the renderer
# gains the node. A prompt that claims a limit the renderer no longer
# has is as misleading as one that claims a capability it never had.

