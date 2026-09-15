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

def test_the_roster_is_well_formed():
    mgv.assert_roster_is_well_formed()


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


def test_every_entry_is_declared_on_axes_and_states_its_refusals():
    for element in mgv.ROSTER:
        assert element.axes, f"{element.key} is declared on no axis"
        assert element.never, f"{element.key} records no refusals"
        assert element.earns_its_place, f"{element.key} says when it earns nothing"
        assert element.needs, f"{element.key} says what it needs: nothing"
        for axis in element.axes:
            assert axis in mgv.AXES_BY_NAME


def test_every_axis_says_what_it_resolves_against():
    """An axis with nothing to resolve against is a number waiting to happen."""
    for axis in mgv.AXES:
        assert axis.resolved_against, axis.name
        assert not _VALUE_SHAPES.search(axis.ranges_over), axis.name


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


def test_every_unreachable_entry_says_what_is_missing():
    for element in mgv.ROSTER:
        if element.reachable == mgv.REACHABLE_NOW:
            continue
        assert element.reachability_note, element.key
        assert element.reachable in (mgv.NEEDS_RENDERER_WORK,
                                     mgv.NEEDS_MEASUREMENT)


def test_a_measurement_gap_is_not_filed_as_a_renderer_gap():
    """tracked_label needs a track nothing measures, which is a different half."""
    tracked = mgv.ELEMENTS_BY_KEY["tracked_label"]
    assert tracked.reachable == mgv.NEEDS_MEASUREMENT
    assert "object_segmentation" in tracked.reachability_note


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

def test_no_function_carries_the_roster():
    """The SFX library's failure, checked for here before it happens.

    41 of that library's 78 entries are one emotional register, so a
    catalogue that looks large is mostly one thing and the useful
    entries are hard to find. No function here may hold more than a
    third of the roster.
    """
    spread = mgv.register_spread()
    assert sum(spread.values()) == len(mgv.ROSTER)
    ceiling = len(mgv.ROSTER) // 3
    over = {name: n for name, n in spread.items() if n > ceiling}
    assert not over, (
        f"{over} exceeds {ceiling} of {len(mgv.ROSTER)} entries. A "
        f"register carrying a third of the vocabulary is the SFX "
        f"library's shape.")
    assert all(n > 0 for n in spread.values()), (
        f"an empty function is a register nobody can plan in: {spread}")


def test_the_boundary_names_the_enumeration_that_owns_each_exclusion():
    """A planner naming a transition is told which module owns it."""
    owners = {
        "caption_word_emphasis": "subtitle_style",
        "kinetic_typography": "caption_word_emphasis",
        "shape_wipe_transition": "transition_vocabulary",
        "zoom_emphasis": "plan_vfx",
        "screen_shake": "zoom_emphasis",
        "light_leak": "transition_vocabulary",
        "intro_card": "bookends",
        "end_card": "bookend",
        "emoji_sticker": "channel_bug",
        "watermark_tile": "channel_bug",
    }
    assert set(mgv.OUT_OF_VOCABULARY) == set(owners)
    for key, owner in owners.items():
        assert owner in mgv.OUT_OF_VOCABULARY[key], key


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

def test_the_whole_roster_serialises_into_a_prompt_table():
    """Nothing is shortlisted: whatever selects a shortlist is the chooser."""
    from library.tools.toon_serializer import json_to_toon

    rows = mgv.roster_rows()
    assert len(rows) == len(mgv.ROSTER)
    table = json_to_toon(rows)
    assert "stat_callout" in table
    assert "tracked_label" in table
    assert set(rows[0]) == set(mgv.ROSTER_LEGEND), (
        "every column a model reads must be defined in ROSTER_LEGEND, "
        "the way music_measurement.MEASUREMENT_LEGEND defines its own.")
    reachable_only = mgv.roster_rows(include_unreachable=False)
    assert 0 < len(reachable_only) < len(rows)


def test_the_legend_defines_columns_and_concludes_nothing():
    for column, meaning in mgv.ROSTER_LEGEND.items():
        assert meaning
        assert not _VALUE_SHAPES.search(meaning), column


def test_describe_roster_reports_the_spread_and_the_reachability():
    text = mgv.describe_roster()
    assert "MOTION-GRAPHICS ROSTER" in text
    for element in mgv.ROSTER:
        assert element.key in text
    assert "needs_renderer_work" in text
    assert "AXES" in text


def test_the_cli_check_passes():
    assert mgv.main(["--check"]) == 0
    assert mgv.main([]) == 0


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

HANDOFF_4_06 = (
    Path(__file__).resolve().parents[1] / "library" / "steps"
    / "step_4_06_render_motion_graphics" / "handoff.md"
)


def _handoff_4_06() -> str:
    return " ".join(HANDOFF_4_06.read_text(encoding="utf-8").split())


def test_the_prompt_s_copy_counts_match_the_roster():
    """The prompt tells the model how copy-shaped this roster is, to
    explain why reaching for text is not purely its own doing. A count
    that drifts from the roster is a false explanation."""
    from collections import Counter

    counts = Counter(row["copy"] for row in mgv.roster_rows())
    handoff = _handoff_4_06()
    words = {11: "eleven", 3: "three", 4: "four", 18: "Eighteen"}
    assert f"{words[18]} elements" in handoff, (
        f"the roster holds {len(mgv.ELEMENTS_BY_KEY)} elements and "
        f"the prompt says otherwise")
    assert len(mgv.ELEMENTS_BY_KEY) == 18
    for value, count in (("required", counts["required"]),
                         ("optional", counts["optional"]),
                         ("none", counts["none"])):
        assert count in words, (
            f"copy={value} is now {count} and this test only spells "
            f"{sorted(words)}; update both it and the prompt")
        assert words[count] in handoff, (
            f"the prompt no longer states that copy is {value} on "
            f"{words[count]} elements")


def test_the_prompt_names_every_element_that_draws_no_copy():
    """These four are the whole non-copy set, and the prompt leans on
    that fact to say where the non-text range is."""
    handoff = _handoff_4_06()
    silent = [row["element"] for row in mgv.roster_rows()
              if row["copy"] == "none"]
    assert len(silent) == 4
    for element in silent:
        assert f"`{element}`" in handoff, element


def test_the_prompt_states_the_tracked_anchor_is_unavailable():
    """`tracked` is the one anchor with no measurement behind it, and
    `tracked_label` is the one element that is not reachable_now. The
    day either changes, this prompt is lying to the planner."""
    handoff = _handoff_4_06()
    assert "`tracked`" in handoff
    unreachable = [key for key, element in mgv.ELEMENTS_BY_KEY.items()
                   if element.reachable != mgv.REACHABLE_NOW]
    assert unreachable == ["tracked_label"], (
        f"the unreachable set is now {unreachable}; 4.06's prompt names "
        f"only tracked_label and would be stating a stale ceiling")


def test_the_prompt_does_not_promise_a_video_node():
    """No element in this layer composites a video file. `BrandMotion`
    exists and belongs to the full-frame and bookend path; the prompt
    says so rather than letting the planner reach for it."""
    composition = (
        Path(__file__).resolve().parents[1] / "remotion-subtitles"
        / "src" / "compositions" / "MotionGraphics" / "index.tsx"
    ).read_text(encoding="utf-8")
    assert "OffthreadVideo" not in composition, (
        "MotionGraphics gained a video node - 4.06's prompt still tells "
        "the planner it has none")
    assert "No element composites a video file" in _handoff_4_06()
