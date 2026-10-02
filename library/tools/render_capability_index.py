"""What this pipeline can draw and animate, DERIVED from both halves.

**The problem this closes.**  A drawing capability lives in two files
that nothing joined: ``motion_graphics_vocabulary.ROSTER`` declares an
element and a reachability flag in Python, and
``remotion-subtitles/src/compositions/MotionGraphics/index.tsx`` either
has a node that draws it or it does not.  ``motion_graphics_plan.DRAWABLE``
is derived from the Python flag alone, so **the flag decides what the
pipeline may plan and nothing checks the flag against the renderer.**

Measured on 2026-09-07, before this module existed, the two halves had
already drifted in BOTH directions:

* ``lower_third`` was flagged ``needs_renderer_work`` with the note "No
  component".  The component had been written - ``index.tsx`` draws a
  backdrop-blurred attribution block with a coloured left rule - so a
  capability that genuinely rendered was refused at plan time by
  ``renderer_cannot_draw_it_yet``.  A declaration understating the
  renderer costs a real capability just as silently as one overstating
  it.
* The ``entrance``/``exit`` axis declared ``typewriter`` in both
  positions.  ``entranceTransform`` reveals character by character;
  ``exitTransform`` returns ``{}`` under a comment claiming characters
  "disappear in reverse", and no reverse reveal exists anywhere.  A
  render at mid-exit put the ink at x 235..845 against 232..847 held -
  the same glyphs at lower opacity.  ``exit: typewriter`` was a fade
  wearing another name.
* ``glitch`` returned a ``textShadow`` carrying the chromatic split from
  the container, and ``Runs`` sets its own ``textShadow`` on every run,
  which overrides an inherited one.  Rendered at mid-ramp, the maximum
  ``|R-B|`` over every visible pixel was **0**; the same measurement on a
  deliberately red element returns 255, so the instrument sees colour
  when colour is there.  The RGB split never reached a frame.

None of the three was findable from either file alone.  This module is
the join: it reads the roster and the axes from Python, reads the
composition's own dispatch out of the TSX source, and reports one row
per capability with a verdict.  :func:`assert_index_is_consistent`
raises when the two halves disagree - **in both directions**, because a
capability declared and not drawn and a capability drawn and not
declared are the same defect seen from opposite ends (AGENTS.md 10.4).

**Derived, never listed.**  There is no hand-written table here.  Every
row comes from ``ROSTER``, from ``AXES_BY_NAME`` or from a parse of the
composition source, so an element added to either half appears here on
the next run and a disagreement fails a test rather than ageing into a
document.  The one thing this module states of its own is
:data:`NO_RAMP_CHARACTERS` - which axis positions mean "no animation at
all" and therefore correctly have no switch arm.

**What a parse can and cannot prove.**  Finding ``element.element ===
"list_build"`` in the source proves the composition has a node for
``list_build``.  It does NOT prove the node puts ink on a frame.  The
render-backed half of that claim is
``tests/contracts/test_render_capability_index.py::test_every_declared_entrance_is_distinguishable_from_a_fade``,
which renders each character and compares the ink's own geometry against
the held frame.  This module carries the cheap half so it can run
everywhere; that test carries the expensive half and names the
environment that runs it.

**And a parse that finds nothing must fail.**  A regex over a file that
has moved returns an empty set, and an empty set compared against an
empty set passes.  :func:`assert_index_is_consistent` refuses an empty
parse before it compares anything, for the reason a database sweep
reporting zero while erroring on 1,948 columns was not evidence of a
clean database.

Run it::

    python3 -m library.tools.render_capability_index

``docs/RENDER_CAPABILITY_CEILING.md`` is the prose account: what is
reachable, what remains, what it would cost and whether it is
automatable from data the pipeline already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, List, Tuple

from library.tools import full_frame_element
from library.tools import motion_graphics_plan as plan
from library.tools import motion_graphics_vocabulary as vocabulary


class RenderCapabilityMismatch(AssertionError):
    """The Python declaration and the composition disagree."""


_COMPOSITIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "remotion-subtitles" / "src" / "compositions"
)

#: The composition this index reads for the OVERLAY surface.  One path,
#: here, so a move breaks loudly rather than leaving the parse quietly
#: empty.
COMPOSITION = _COMPOSITIONS_DIR / "MotionGraphics" / "index.tsx"

#: The FULL-FRAME surface (#606).  A second roster, a second composition,
#: and the same join: `full_frame_element.ROSTER` declares what may be
#: planned and `FullFrameCard/index.tsx` either draws it or does not.
FULL_FRAME_COMPOSITION = _COMPOSITIONS_DIR / "FullFrameCard" / "index.tsx"

#: The two surfaces this pipeline draws on, and which composition owns
#: each.  An overlay element is drawn OVER picture; a full-frame element
#: is drawn IN PLACE OF it.
SURFACES: Dict[str, Path] = {
    "overlay": COMPOSITION,
    "full_frame": FULL_FRAME_COMPOSITION,
}

#: The motion helpers `FullFrameCard` must import from `MotionGraphics`
#: rather than respell.
#:
#: This is what makes the entrance/exit rows below true of BOTH surfaces
#: with one set of rows. If the card composition ever defines its own
#: ramp, those rows silently start describing only half the engine, so
#: the import is checked rather than assumed - and
#: `full_frame_element.MOTION_CHARACTERS` is already imported from the
#: overlay axis for the same reason.
SHARED_MOTION_EXPORTS: Tuple[str, ...] = (
    "elementOpacity", "entranceTransform", "exitTransform",
    "typewriterShown", "typewriterSplit", "typewriterCursorOn",
)

#: Axis positions that mean "no animation", and therefore correctly have
#: no arm in `entranceTransform` or `exitTransform`.
#:
#: `cut` is the absence of a ramp and `fade` is opacity alone, which
#: `elementOpacity` owns rather than the transform switch.  Both are
#: implemented; neither can be found by looking for a `case` arm.  This
#: is the only thing this module asserts about the composition that it
#: does not read out of it, and it is a fact about what the words mean -
#: the same reading `transition_vocabulary.CUT_TYPES` takes of a value
#: meaning "nothing is drawn" (AGENTS.md 10.5).
NO_RAMP_CHARACTERS: FrozenSet[str] = frozenset({"cut", "fade"})

#: An axis position the plan REFUSES rather than draws, and the reason it
#: refuses under.  A position here is not a gap in the renderer: nothing
#: reaches the composition asking for it, so the composition correctly
#: has no arm.  Read out of `motion_graphics_plan` so the two cannot
#: drift.
REFUSED_ANCHORS: Dict[str, str] = {
    plan.ANCHOR_NEEDS_MEASUREMENT: "anchor_needs_a_measurement_nothing_takes",
}


# ── Verdicts ─────────────────────────────────────────────────────────
#
# Four, and they are exhaustive over (declared reachable?, implemented?).

#: Declared reachable and the composition has a node for it.
DRAWS = "draws"

#: Declared reachable and the composition has NO node.  The plan will
#: pass it through and the render will draw nothing - the defect class
#: this repository has spent a week removing.
DECLARED_BUT_NOT_DRAWN = "declared_but_not_drawn"

#: The composition draws it and the declaration says it cannot.
#: `motion_graphics_plan.DRAWABLE` is derived from the declaration, so
#: the plan DROPS it: a real capability, unreachable through the
#: pipeline.
DRAWN_BUT_NOT_DECLARED = "drawn_but_not_declared"

#: Declared unreachable and not drawn.  Agreed, and the remainder.
NOT_YET = "not_yet"

#: Refused before it reaches the renderer, by name.  Agreed.
REFUSED = "refused"

VERDICTS: Tuple[str, ...] = (
    DRAWS, DECLARED_BUT_NOT_DRAWN, DRAWN_BUT_NOT_DECLARED, NOT_YET, REFUSED)

#: The verdicts that are a disagreement between the two halves.
DISAGREEMENTS: Tuple[str, ...] = (DECLARED_BUT_NOT_DRAWN, DRAWN_BUT_NOT_DECLARED)


@dataclass(frozen=True)
class Capability:
    """One drawing or animation capability, and what each half says.

    `surface` is `overlay` (drawn OVER picture) or `full_frame` (drawn IN
    PLACE OF it); `motion` is the entrance/exit vocabulary, which both
    surfaces share one implementation of.  `kind` is which axis it is on
    - an `element`, an `entrance`, an `exit` or an `anchor`.  `declared`
    is what the Python half says; `implemented` is what the parse of the
    composition found.  `verdict` is the join, and `evidence` names where
    the parse saw it so a row can be checked by hand.
    """

    kind: str
    key: str
    declared: str
    implemented: bool
    verdict: str
    evidence: str
    surface: str = "overlay"


# ── Reading the composition ──────────────────────────────────────────


def _source() -> str:
    """The composition's source, read once per call.

    Not cached: a cached read would survive an edit inside one pytest
    session and report the file as it was when the first test ran.
    """
    return COMPOSITION.read_text(encoding="utf-8")


def drawn_elements(source: str | None = None) -> FrozenSet[str]:
    """Element keys the composition has a drawing node for.

    ``DrawnElement`` dispatches with a chain of
    ``if (element.element === "key")``, so the key is exactly what a plan
    names and the parse is a literal match rather than an inference.
    """
    text = _source() if source is None else source
    return frozenset(re.findall(r'element\.element\s*===\s*"([a-z_]+)"', text))


#: An arm whose whole body is ``return {}`` - it matches the case and
#: produces no style.  Counting one as an implementation is how
#: ``exit: typewriter`` passed for a reverse reveal it never had: the arm
#: existed, carried a comment describing the behaviour, and returned
#: nothing.  A name with no reader draws nothing and says nothing
#: (AGENTS.md 10.2).
_EMPTY_ARM = re.compile(r'case\s+"([a-z_]+)"\s*:\s*(?://[^\n]*\n\s*)*return\s*\{\s*\}\s*;')


def _switch_arms(source: str, function_name: str) -> FrozenSet[str]:
    """The ``case "x":`` labels inside one exported function that DO something.

    Bounded by the next ``export const`` so an arm belonging to a
    neighbouring switch cannot be credited to this one - the shape of
    error that let a producer/consumer survey credit six outputs to the
    lines writing their own key.

    An arm returning an empty object is excluded.  It is present in the
    source and absent from the frame, and the whole point of this module
    is that those are not the same thing.
    """
    start = source.find(f"export const {function_name}")
    if start < 0:
        return frozenset()
    end = source.find("export const ", start + 1)
    body = source[start:] if end < 0 else source[start:end]
    labels = set(re.findall(r'case\s+"([a-z_]+)"\s*:', body))
    return frozenset(labels - set(_EMPTY_ARM.findall(body)))


#: The function both compositions call to ask how much of a typewriter
#: reveal is on screen.  It takes the entrance AND the exit, which is the
#: whole point: an earlier `typewriterProgress` took the entrance alone,
#: so `exit: "typewriter"` was a fade in both compositions.
_REVEAL_FUNCTION = "typewriterShown"


def _reveal_covers(source: str, direction: str) -> bool:
    """Whether the shared reveal really answers for this direction.

    ``typewriter`` moves characters rather than the container, so its
    transform arm is empty by design and the implementation is the
    reveal.  A direction counts as covered only when
    :data:`_REVEAL_FUNCTION` reads THAT direction's own field - reading
    one field and letting it stand for the other is exactly what let
    ``exit: typewriter`` claim a reverse reveal it never performed.
    """
    definition = re.search(
        r"export const " + _REVEAL_FUNCTION + r"\s*=\s*\((.*?)\n\};",
        source, re.S)
    if definition is None:
        return False
    body = definition.group(1)
    # And it has to be CALLED, not merely defined: a helper nothing
    # invokes draws nothing.
    if len(re.findall(re.escape(_REVEAL_FUNCTION), source)) < 2:
        return False
    if direction == "exit":
        return bool(re.search(r'exit\s*===\s*"typewriter"', body))
    # The entrance path delegates to `typewriterProgress`, which is the
    # entrance-only helper; either spelling counts as reading it.
    return bool(re.search(r'entrance\s*===\s*"typewriter"', body)
                or "typewriterProgress" in body)


def _text_level_characters(source: str, direction: str) -> FrozenSet[str]:
    """Characters implemented at the TEXT level rather than as a transform."""
    return frozenset({"typewriter"}) if _reveal_covers(source, direction) else frozenset()


def implemented_characters(direction: str, source: str | None = None) -> FrozenSet[str]:
    """Every entrance or exit character the composition really implements.

    The union of three routes: the ``no ramp`` characters the words
    themselves define, the transform switch's own arms, and the
    text-level reveals.
    """
    if direction not in ("entrance", "exit"):
        raise ValueError(f"direction is entrance or exit, not {direction!r}")
    text = _source() if source is None else source
    function_name = "entranceTransform" if direction == "entrance" else "exitTransform"
    return (
        NO_RAMP_CHARACTERS
        | _switch_arms(text, function_name)
        | _text_level_characters(text, direction)
    )


def implemented_anchors(source: str | None = None) -> FrozenSet[str]:
    """Grid positions ``anchorStyle`` places from the safe area."""
    text = _source() if source is None else source
    return _switch_arms(text, "anchorStyle")


# ── The join ─────────────────────────────────────────────────────────


def _element_rows(source: str) -> List[Capability]:
    drawn = drawn_elements(source)
    rows: List[Capability] = []
    for element in vocabulary.ROSTER:
        is_drawn = element.key in drawn
        reachable = element.reachable == vocabulary.REACHABLE_NOW
        if reachable and is_drawn:
            verdict = DRAWS
        elif reachable:
            verdict = DECLARED_BUT_NOT_DRAWN
        elif is_drawn:
            verdict = DRAWN_BUT_NOT_DECLARED
        else:
            verdict = NOT_YET
        rows.append(Capability(
            kind="element",
            key=element.key,
            declared=element.reachable,
            implemented=is_drawn,
            verdict=verdict,
            evidence=(f'{COMPOSITION.name}: element.element === "{element.key}"'
                      if is_drawn else element.reachability_note),
        ))
    return rows


def _character_rows(source: str, direction: str) -> List[Capability]:
    implemented = implemented_characters(direction, source)
    rows: List[Capability] = []
    for position in vocabulary.AXES_BY_NAME[direction].positions:
        drawn = position in implemented
        if position in NO_RAMP_CHARACTERS:
            evidence = "no ramp: the word means no animation"
        elif drawn:
            evidence = f"{COMPOSITION.name}: {direction}Transform or a text-level reveal"
        else:
            evidence = f"no arm in {direction}Transform and no text-level reveal"
        rows.append(Capability(
            kind=direction,
            key=position,
            # An axis position is DECLARED by being in the axis at all:
            # `motion_graphics_plan` accepts every position the axis
            # names, so the plan can ask for any of them.
            declared=vocabulary.REACHABLE_NOW,
            implemented=drawn,
            verdict=DRAWS if drawn else DECLARED_BUT_NOT_DRAWN,
            evidence=evidence,
        ))
    return rows


def _anchor_rows(source: str) -> List[Capability]:
    implemented = implemented_anchors(source)
    rows: List[Capability] = []
    for position in vocabulary.AXES_BY_NAME["anchor"].positions:
        if position in REFUSED_ANCHORS:
            rows.append(Capability(
                kind="anchor", key=position,
                declared=vocabulary.NEEDS_MEASUREMENT,
                implemented=position in implemented,
                verdict=REFUSED,
                evidence=f"dropped as {REFUSED_ANCHORS[position]}",
            ))
            continue
        drawn = position in implemented
        rows.append(Capability(
            kind="anchor", key=position,
            declared=vocabulary.REACHABLE_NOW,
            implemented=drawn,
            verdict=DRAWS if drawn else DECLARED_BUT_NOT_DRAWN,
            evidence=(f"{COMPOSITION.name}: anchorStyle" if drawn
                      else "no arm in anchorStyle"),
        ))
    return rows


def shared_motion_exports_missing() -> Tuple[str, ...]:
    """Motion helpers `FullFrameCard` fails to import from `MotionGraphics`.

    Empty when the two surfaces share one drawing of every character.  A
    name here means the entrance/exit rows describe the overlay only, and
    the index would be over-claiming for the card surface.
    """
    text = FULL_FRAME_COMPOSITION.read_text(encoding="utf-8")
    block = re.search(r'import\s*\{([^}]*)\}\s*from\s*"\.\./MotionGraphics"', text)
    imported = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", block.group(1))) if block else set()
    return tuple(name for name in SHARED_MOTION_EXPORTS if name not in imported)


def _full_frame_rows() -> List[Capability]:
    """The card surface: one roster, one composition, the same join."""
    text = FULL_FRAME_COMPOSITION.read_text(encoding="utf-8")
    # `FullFrameCard` draws ONE kind, so its dispatch is the exported
    # component rather than a switch. The parse is for that export.
    draws_a_card = bool(re.search(
        r"export const FullFrameCard: React\.FC", text))
    rows: List[Capability] = []
    for element in full_frame_element.ROSTER:
        reachable = element.reachable == full_frame_element.REACHABLE_NOW
        if reachable and draws_a_card:
            verdict = DRAWS
        elif reachable:
            verdict = DECLARED_BUT_NOT_DRAWN
        elif draws_a_card:
            verdict = DRAWN_BUT_NOT_DECLARED
        else:
            verdict = NOT_YET
        rows.append(Capability(
            kind="element", key=element.key, declared=element.reachable,
            implemented=draws_a_card, surface="full_frame",
            verdict=verdict,
            evidence=(f"{FULL_FRAME_COMPOSITION.name}: export const FullFrameCard"
                      if draws_a_card else element.reachability_note),
        ))
    return rows


def index(source: str | None = None) -> Tuple[Capability, ...]:
    """Every drawing and animation capability, with both halves' account."""
    text = _source() if source is None else source
    rows: List[Capability] = []
    rows.extend(_element_rows(text))
    rows.extend(_full_frame_rows())
    rows.extend(_character_rows(text, "entrance"))
    rows.extend(_character_rows(text, "exit"))
    rows.extend(_anchor_rows(text))
    return tuple(rows)


def disagreements(source: str | None = None) -> Tuple[Capability, ...]:
    """The rows where the two halves do not agree."""
    return tuple(row for row in index(source) if row.verdict in DISAGREEMENTS)


def assert_index_is_consistent(source: str | None = None) -> None:
    """Raise unless the declaration and the composition say the same thing.

    Checked in both directions.  A capability declared reachable that the
    composition cannot draw renders nothing; a capability the composition
    draws that the declaration calls unreachable is dropped by
    ``resolve_plan`` and never reaches a frame.  Neither is visible from
    one file.
    """
    text = _source() if source is None else source

    # A parse that found nothing agrees with everything.  Refuse it
    # before comparing, so a moved file or a renamed dispatch fails here
    # rather than reporting a clean index.
    if not vocabulary.ROSTER:
        raise RenderCapabilityMismatch("the roster is empty; there is nothing to check")
    for name, found in (
        ("element dispatch", drawn_elements(text)),
        ("entranceTransform arms", _switch_arms(text, "entranceTransform")),
        ("exitTransform arms", _switch_arms(text, "exitTransform")),
        ("anchorStyle arms", _switch_arms(text, "anchorStyle")),
    ):
        if not found:
            raise RenderCapabilityMismatch(
                f"parsed no {name} out of {COMPOSITION}. A parse that finds "
                f"nothing agrees with every declaration, so this is a broken "
                f"instrument rather than a clean result."
            )

    missing = shared_motion_exports_missing()
    if missing:
        raise RenderCapabilityMismatch(
            f"{FULL_FRAME_COMPOSITION.name} no longer imports "
            f"{', '.join(missing)} from MotionGraphics. The entrance and "
            f"exit rows of this index are ONE set of rows describing BOTH "
            f"surfaces, which is only true while the card composition "
            f"reuses the overlay's drawing of each character. A second "
            f"drawing means this index over-claims for the card."
        )

    bad = disagreements(text)
    if not bad:
        return
    lines = [
        f"  {row.kind} {row.key!r}: declared {row.declared}, "
        f"{'drawn' if row.implemented else 'not drawn'} - {row.evidence}"
        for row in bad
    ]
    raise RenderCapabilityMismatch(
        "the motion-graphics declaration and the composition disagree:\n"
        + "\n".join(lines)
        + "\n\nA capability declared reachable and not drawn renders nothing. "
          "A capability drawn and declared unreachable is dropped by "
          "motion_graphics_plan.resolve_plan and never reaches a frame. "
          "Fix whichever half is wrong; do not relax this check."
    )


# ── Reading it ───────────────────────────────────────────────────────

#: What each column of `rows()` means, for a reader and for a prompt.
#: The shape `motion_graphics_vocabulary.ROSTER_LEGEND` takes.
INDEX_LEGEND: Dict[str, str] = {
    "surface": "overlay (drawn OVER picture) or full_frame (drawn IN PLACE "
               "of it). Entrance and exit are shared by both.",
    "kind": "Which axis of the drawing surface: element, entrance, exit or anchor.",
    "key": "The name a plan uses.",
    "declared": "What the Python half says: reachable_now, needs_renderer_work "
                "or needs_measurement.",
    "implemented": "Whether the composition has a node, an arm or a reveal for it.",
    "verdict": "The join of the two. " + ", ".join(VERDICTS) + ".",
    "evidence": "Where the parse saw it, or why it did not.",
}


def rows(source: str | None = None) -> List[Dict[str, str]]:
    """The index as rows, for a table or a prompt."""
    return [
        {
            "surface": row.surface,
            "kind": row.kind,
            "key": row.key,
            "declared": row.declared,
            "implemented": "yes" if row.implemented else "no",
            "verdict": row.verdict,
            "evidence": row.evidence,
        }
        for row in index(source)
    ]


def describe_index(source: str | None = None) -> str:
    """The index as text."""
    out: List[str] = []
    all_rows = index(source)
    groups = [
        ("OVERLAY ELEMENT", lambda r: r.kind == "element" and r.surface == "overlay"),
        ("FULL-FRAME ELEMENT", lambda r: r.kind == "element" and r.surface == "full_frame"),
        ("ENTRANCE (both surfaces)", lambda r: r.kind == "entrance"),
        ("EXIT (both surfaces)", lambda r: r.kind == "exit"),
        ("ANCHOR", lambda r: r.kind == "anchor"),
    ]
    for title, keep in groups:
        subset = [row for row in all_rows if keep(row)]
        out.append(f"{title}  ({len(subset)})")
        for row in subset:
            mark = "+" if row.verdict in (DRAWS,) else (
                "." if row.verdict in (NOT_YET, REFUSED) else "!")
            out.append(f"  {mark} {row.key:22s} {row.verdict:24s} {row.evidence}")
        out.append("")
    counts: Dict[str, int] = {}
    for row in all_rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    out.append("  ".join(f"{verdict}={counts.get(verdict, 0)}" for verdict in VERDICTS))
    return "\n".join(out)


def main() -> int:
    print(describe_index())
    try:
        assert_index_is_consistent()
    except RenderCapabilityMismatch as exc:
        print()
        print(exc)
        return 1
    print("\nthe declaration and the composition agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
