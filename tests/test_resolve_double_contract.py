"""The Resolve behavioural contract, run against the canonical double.

Each case runs one entry of ``tests.resolve_double.CONTRACT_CHECKS``
against a fresh ``make_project()``. The SAME checks run against live
Resolve in ``tests/test_resolve_qualification.py`` - a fake that passes
and reality that fails is the finding, never a reason to weaken the
check here.

Do not add per-file Resolve fakes to cover new surface: extend
``tests/resolve_double.py`` with the measured behaviour, add the check
there, and it is verified on both backends at once.
"""

from __future__ import annotations

import pytest

from tests.resolve_double import CONTRACT_CHECKS, make_project


@pytest.mark.parametrize(
    "name,check",
    [pytest.param(name, check, id=name) for name, check in CONTRACT_CHECKS],
)
def test_contract_holds_on_the_double(name, check):
    check(make_project())


def test_contract_registry_is_nonempty():
    assert len(CONTRACT_CHECKS) >= 20, (
        "the contract is the parity guarantee - it must not shrink into a token"
    )
