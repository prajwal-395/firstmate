"""The declaration and the composition must say the same thing.

Both directions.  A capability declared reachable and not drawn renders
nothing; a capability drawn and declared unreachable is dropped by
`resolve_plan` and never reaches a frame.  Before this file existed the
tree carried one of each and neither was visible from a single file -
see `library/tools/render_capability_index.py` for the measurement.

`test_the_gate_can_fail_in_both_directions` is the AGENTS.md 10.4 half:
a gate that cannot fail reads as coverage.
"""

import pytest

from library.tools import motion_graphics_vocabulary as vocabulary
from library.tools import render_capability_index as index


def test_the_declaration_and_the_composition_agree():
    index.assert_index_is_consistent()


def test_the_index_covers_every_declared_capability():
    rows = index.index()
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row.kind, set()).add(row.key)

    from library.tools import full_frame_element
    assert by_kind["element"] == ({e.key for e in vocabulary.ROSTER}
                                  | {e.key for e in full_frame_element.ROSTER})
    for axis in ("entrance", "exit", "anchor"):
        assert by_kind[axis] == set(vocabulary.AXES_BY_NAME[axis].positions), (
            f"the {axis} axis and the index disagree about which positions "
            f"exist")


def test_every_row_carries_a_verdict_the_module_names():
    for row in index.index():
        assert row.verdict in index.VERDICTS, row


def test_the_gate_can_fail_in_both_directions():
    """A declaration ahead of the renderer, and one behind it."""
    source = index.COMPOSITION.read_text(encoding="utf-8")

    # Ahead: the composition loses the node for an element the roster
    # says is reachable.
    drawn = sorted(index.drawn_elements(source))
    assert drawn, "parsed no element dispatch; the instrument is broken"
    victim = drawn[0]
    ahead = source.replace(f'element.element === "{victim}"',
                           'element.element === "__gone__"')
    with pytest.raises(index.RenderCapabilityMismatch) as caught:
        index.assert_index_is_consistent(ahead)
    assert victim in str(caught.value)

    # Behind: the composition gains a node for an element the roster says
    # it cannot draw.  Only run where such an element exists; when every
    # entry is reachable there is nothing to understate.
    unreachable = [e.key for e in vocabulary.ROSTER
                   if e.reachable != vocabulary.REACHABLE_NOW]
    if unreachable:
        behind = source.replace(
            f'element.element === "{victim}"',
            f'element.element === "{victim}" || element.element === '
            f'"{unreachable[0]}"')
        with pytest.raises(index.RenderCapabilityMismatch) as caught:
            index.assert_index_is_consistent(behind)
        assert unreachable[0] in str(caught.value)


def test_a_parse_that_finds_nothing_is_refused_rather_than_passing():
    """An empty parse agrees with every declaration.

    The instrument-scepticism half: a regex over a file that has moved
    returns an empty set, and an empty set compared against an empty set
    passes clean.
    """
    with pytest.raises(index.RenderCapabilityMismatch) as caught:
        index.assert_index_is_consistent("// nothing here at all\n")
    assert "broken instrument" in str(caught.value)


def test_the_reveal_must_read_the_direction_it_claims_to_answer():
    """Reading the entrance and letting it stand for the exit is the defect.

    A `typewriterShown` that never looks at `exit` cannot implement an
    exit reveal, however many callers pass one in.
    """
    source = index.COMPOSITION.read_text(encoding="utf-8")
    assert "typewriter" in index.implemented_characters("entrance", source)
    assert "typewriter" in index.implemented_characters("exit", source)

    entrance_only = source.replace('exit === "typewriter"',
                                   'entrance === "typewriter"')
    assert "typewriter" not in index.implemented_characters("exit", entrance_only)
    with pytest.raises(index.RenderCapabilityMismatch):
        index.assert_index_is_consistent(entrance_only)


def test_both_surfaces_share_one_drawing_of_every_character():
    """The entrance/exit rows describe two compositions with one set of rows.

    That is only true while `FullFrameCard` reuses the overlay's helpers,
    so the import is checked rather than assumed.
    """
    assert index.shared_motion_exports_missing() == ()


def test_the_full_frame_surface_is_in_the_index():
    from library.tools import full_frame_element
    keys = {row.key for row in index.index() if row.surface == "full_frame"}
    assert keys == {e.key for e in full_frame_element.ROSTER}


def test_an_arm_that_returns_nothing_is_not_an_implementation():
    """`case "x": return {};` matches and draws nothing.

    This is how `exit: typewriter` passed for a reverse reveal it never
    performed: the arm existed, carried a comment describing the
    behaviour, and returned an empty object.
    """
    source = (
        'export const exitTransform = (a, b, c) => {\n'
        '  switch (c) {\n'
        '    case "slide": { return { transform: "translateY(1px)" }; }\n'
        '    // a comment claiming a behaviour\n'
        '    case "typewriter":\n'
        '      return {};\n'
        '  }\n'
        '};\n'
        'export const next = 1;\n'
    )
    found = index.implemented_characters("exit", source)
    assert "slide" in found
    assert "typewriter" not in found, (
        "an empty arm was counted as an implementation")


def test_the_no_ramp_characters_are_the_only_ones_stated_here():
    """`cut` and `fade` have no switch arm and are still implemented.

    They are the absence of a ramp and opacity alone, which
    `elementOpacity` owns.  Everything else in the index is read out of
    the source.
    """
    assert index.NO_RAMP_CHARACTERS == {"cut", "fade"}
    for character in index.NO_RAMP_CHARACTERS:
        assert character in vocabulary.AXES_BY_NAME["entrance"].positions
        assert character in vocabulary.AXES_BY_NAME["exit"].positions
