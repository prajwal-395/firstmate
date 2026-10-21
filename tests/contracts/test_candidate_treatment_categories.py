"""The category vocabulary must cover every operation Ren can carry out.

A plan's blast radius is counted in categories.  An operation with no
category would make a risky plan look small, and a plan that looks small
is the plan the engine prefers - so an incomplete vocabulary is a
defect in the comparison itself, not a gap in a lookup table.
"""

from __future__ import annotations

from library.tools import candidate_treatments as ct
from library.tools import edit_algebra, edit_operations


def test_every_operation_the_registry_declares_has_a_category():
    """Catches: a new operation landing without a category, so a plan
    using it would report a smaller blast radius than it has."""
    unclassified = sorted(op for op in edit_algebra.ALGEBRA
                          if op not in ct.OP_CATEGORIES
                          and op != ct.PLAN_CHANGE)
    assert unclassified == []


def test_every_typed_edit_operation_has_a_category():
    """Catches: a typed operation the front door accepts landing without a
    category, so the proposal's untouched categories would lie."""
    unclassified = sorted(op for op in edit_operations.OP_OWNERS
                          if op not in ct.OP_CATEGORIES)
    assert unclassified == []


def test_no_category_names_a_layer_that_does_not_exist():
    """Catches: a category outside the vocabulary, so two plans would be
    counted against different scales."""
    unknown = sorted({name for name in ct.OP_CATEGORIES.values()
                      if name not in ct.CATEGORIES})
    assert unknown == []


def test_a_planning_operation_carries_the_category_of_what_it_plans():
    """Catches: a `plan_change` counted as nothing, so a plan that changes
    the dialogue would report no dialogue touched."""
    candidate = ct.treatment("t1", "n", "r", [
        {"op": ct.PLAN_CHANGE,
         "params": {"operation_type": "speech_selection"}}])
    assert ct._categories(candidate) == [ct.DIALOGUE]
