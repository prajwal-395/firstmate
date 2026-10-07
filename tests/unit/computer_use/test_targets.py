"""The AppTarget selection and the Canva scratch-design gate.

Each test names the defect it catches. The load-bearing ones are the
two refusals: a gate that defaults open would let the loop touch a
real Canva design, and background input claimed without a measurement
would move the captain's cursor on someone else's authority.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.targets import (
    CANVA_SCRATCH_ENV,
    TargetError,
    canva_target,
    check_canva_design,
    resolve_target,
    select_target,
)


def test_resolve_target_names_the_measured_background_routes():
    """Only the probe's AX rows are claimed: AX press and set-value
    reached Resolve in the background, while posted clicks, drags,
    scrolls and keys changed 0 pixels - a target claiming those would
    drive Qt controls that never move."""
    target = resolve_target()

    assert target.background_input is True
    assert target.background_routes == ("ax_press", "ax_set_value")
    assert "0 pixels" in target.background_why
    assert target.has_executor is True


def test_canva_target_claims_no_background_input_with_an_executor():
    """Chrome background input is measured filtered (probe doc), so the
    target reports False with the measurement - but the loop observes
    and focuses through the driver now, so it carries an executor. A
    target claiming background input would act on hope; one with no
    executor could not run at all."""
    target = canva_target()

    assert target.bundle_id == "com.google.Chrome"
    assert target.background_input is False
    assert target.background_routes == ()
    assert "measured filtered" in target.background_why
    assert target.has_executor is True


def test_a_non_scratch_design_is_refused(monkeypatch):
    """A design outside the allowlist is refused naming the variable -
    the qualification-project analog: the loop touches scratch work,
    never the real thing."""
    monkeypatch.setenv(
        CANVA_SCRATCH_ENV, "https://www.canva.com/design/SCRATCH/edit"
    )

    with pytest.raises(TargetError) as excinfo:
        check_canva_design("https://www.canva.com/design/REAL/edit")

    assert CANVA_SCRATCH_ENV in excinfo.value.render()
    assert excinfo.value.code == "TARGET_NOT_SCRATCH"


def test_an_empty_allowlist_refuses_everything(monkeypatch):
    """With the variable unset, even a plausible design URL is refused -
    the gate defaults closed."""
    monkeypatch.delenv(CANVA_SCRATCH_ENV, raising=False)

    with pytest.raises(TargetError):
        check_canva_design("https://www.canva.com/design/SCRATCH/edit")


def test_a_scratch_design_passes_the_gate(monkeypatch):
    """An allowlisted URL returns its entry - the gate admits exactly
    what the captain named, by exact match, parsing no URL semantics."""
    monkeypatch.setenv(
        CANVA_SCRATCH_ENV,
        "https://www.canva.com/design/AAA/edit, https://www.canva.com/design/SCRATCH/edit ",
    )

    assert (
        check_canva_design("https://www.canva.com/design/SCRATCH/edit")
        == "https://www.canva.com/design/SCRATCH/edit"
    )


def test_an_unknown_target_is_refused_not_guessed():
    """A target name outside the registry is refused with the list -
    the selector never invents a target."""
    with pytest.raises(TargetError) as excinfo:
        select_target("premiere")

    assert "resolve" in excinfo.value.why and "canva" in excinfo.value.why
