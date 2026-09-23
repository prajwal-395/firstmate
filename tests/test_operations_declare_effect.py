"""Every operation declares its effect, in the requirement vocabulary.

The captain's intent: capabilities compose only when each declares what
is true after it runs.  `Operation.requires` already declares
preconditions, derived from `requirements.all_requirements()`; this
file pins the mirror - `Operation.effect` filters that same registry
on `owning_node in r.produced_by` - so an effect can be matched against
a precondition without a second vocabulary.

Fifteen of the 24 operations have a non-empty derived effect.  Nine
are empty, each for a verified reason recorded in
`operations.EMPTY_EFFECT_REASONS` (verdict / artifact / unmodelled
analysis - see that mapping for the per-operation evidence).  The
tripwire below fails when a new operation is added without one: an
operation whose owning node produces no requirement must be classified
there, with its reason, or the suite refuses it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import operations
from library.tools import requirements as R


def _mirror(op) -> tuple:
    """The effect definition, recomputed independently of the property."""
    return tuple(r for r in R.all_requirements() if op.owning_node in r.produced_by)


@pytest.mark.parametrize("op", operations.all(), ids=lambda o: o.name)
def test_effect_is_the_derived_mirror(op):
    """`effect` is derived from the requirement registry, never listed.

    Recomputed here from `all_requirements()` rather than read off the
    property, so a future hand-written override fails loudly instead of
    drifting silently - the same discipline `requires` follows.

    Compared BY NAME: derived requirements carry fresh `check` closures
    on every `all_requirements()` call, so dataclass equality can never
    hold across two calls.  Names are the vocabulary here, and order is
    the registry's own.
    """
    assert [r.name for r in op.effect] == [r.name for r in _mirror(op)]


@pytest.mark.parametrize("op", operations.all(), ids=lambda o: o.name)
def test_effect_speaks_only_the_requirement_vocabulary(op):
    """Every effect member is a real requirement, produced by this node.

    No strings, no second vocabulary: a composer matches these against
    `requires`, which carries the same objects, so anything else would
    be prose in a costume.
    """
    # Derived `state_key` requirements are rebuilt on every
    # `all_requirements()` call, so identity cannot hold - what is pinned
    # is that each effect member matches a registry entry field for
    # field, on every field that is data rather than a fresh closure.
    registry = {r.name: r for r in R.all_requirements()}
    for req in op.effect:
        assert isinstance(req, R.Requirement)
        assert req.name in registry
        twin = registry[req.name]
        assert (
            req.kind,
            req.describe,
            req.produced_by,
            req.consumers,
            req.overridable,
        ) == (
            twin.kind,
            twin.describe,
            twin.produced_by,
            twin.consumers,
            twin.overridable,
        )
        assert req.kind in R.KINDS
        assert op.owning_node in req.produced_by


def test_empty_effect_operations_are_exactly_the_reasoned_nine():
    """The tripwire: a new operation without an effect refuses the suite.

    BOTH directions, because each alone keeps passing while the mapping
    rots: an unlisted empty effect would be an unjustified gap, and a
    listed operation whose node now produces something would be a stale
    reason claiming coverage that exists.
    """
    empty = {op.name for op in operations.all() if not op.effect}
    reasoned = set(operations.EMPTY_EFFECT_REASONS)
    assert empty == reasoned, (
        f"unreasoned empty effects (classify in EMPTY_EFFECT_REASONS): "
        f"{sorted(empty - reasoned)}; "
        f"stale reasons (node now produces something - delete the entry): "
        f"{sorted(reasoned - empty)}"
    )


def test_empty_effect_reasons_are_real():
    """Each reason states its kind and points at the evidence.

    A placeholder survives every structural check, so this one reads
    the text: non-trivial, naming one of the three verified kinds and
    the owning node it excuses.
    """
    by_name = {op.name: op for op in operations.all()}
    for name, reason in operations.EMPTY_EFFECT_REASONS.items():
        assert isinstance(reason, str) and len(reason) >= 100, (
            f"{name}: a reason shorter than this is a placeholder, not a justification"
        )
        assert any(kind in reason for kind in ("VERDICT", "ARTIFACT", "ANALYSIS")), (
            f"{name}: the reason must name its kind - VERDICT, ARTIFACT "
            f"or ANALYSIS, the three the tree was checked against"
        )
        assert by_name[name].owning_node in reason, (
            f"{name}: the reason must name the owning node it excuses, "
            f"so a re-owned operation reads as wrong"
        )


def test_requires_and_effect_share_one_registry():
    """The composition property: an effect can satisfy a precondition.

    Every requirement named by any operation's `effect` is askable
    through `requires` - both properties read the same
    `all_requirements()` pool, so a producer found working backwards
    from a goal is a consumer some operation will refuse without.
    """
    pool = {r.name for r in R.all_requirements()}
    for op in operations.all():
        for req in list(op.effect) + list(op.requires):
            assert req.name in pool
