"""The capability -> machine-needs table holds to the registry it mirrors.

`machine_needs` is declared rather than derived (doctor cannot import
the registry: it imports torch), so each of these names the drift that
would make `ren doctor --for` lie.
"""

from library.tools import capabilities
from library.tools import machine_needs as mn


def test_every_capability_has_a_needs_row_and_nothing_else_does():
    registry = set(capabilities.ids())
    table = set(mn.CAPABILITY_NEEDS)
    assert registry - table == set(), "capabilities doctor cannot answer for"
    assert table - registry == set(mn.FRONT_DOOR), "rows naming no capability"


def test_every_derived_environment_requirement_is_a_required_need():
    for spec in capabilities.all():
        required = mn.needs_of(spec.id).requires
        for env in spec.assumes_machine:
            assert env in mn.ENV_REQUIREMENT_NEED, (
                f"{spec.id}: {env} has no need in ENV_REQUIREMENT_NEED")
            assert mn.ENV_REQUIREMENT_NEED[env] in required, (
                f"{spec.id} refuses without {env}, but its needs row does "
                f"not require {mn.ENV_REQUIREMENT_NEED[env]}")


def test_a_capability_that_needs_a_model_answer_requires_a_harness():
    for spec in capabilities.all():
        if spec.needs_model_answer:
            assert "chat_harness" in mn.needs_of(spec.id).requires, spec.id


def test_the_table_uses_only_its_own_vocabulary():
    assert mn.problems() == []


def test_a_failing_optional_need_degrades_and_a_required_one_refuses():
    assert mn.availability("footage.search", {"model.search_embedding"})[0] \
        == mn.DEGRADED
    assert mn.availability("render.build", {"resolve.studio"}) \
        == (mn.UNAVAILABLE, ("resolve.studio",), {})
    assert mn.availability("project.inspect", {"resolve.studio"})[0] \
        == mn.AVAILABLE
