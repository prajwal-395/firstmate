"""One refusal shape for every `ren` verb.

A refusal is what happened, why, and the fix (the exact next command
or edit) - rendered the same way at every CLI boundary, with exit code
4. These tests pin the shape, the enforcement (a refusal without a fix
fails at the raise site), the exit-code contract, the boundary
rendering, and the three refusals the audit named as carrying no next
step.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.ren_refusal import (
    REFUSAL_EXIT_CODE,
    RenRefusal,
)


def test_the_shape_carries_what_why_and_fix_and_requires_all_three():
    """A refusal without a why or a fix fails at the raise site."""
    refused = RenRefusal("the plan names no reel 9",
                         "a touchup never invents one",
                         "run `ren propose <project>` first")
    rendered = refused.render()
    assert rendered.startswith("ren: refused - the plan names no reel 9")
    assert "\n  why: a touchup never invents one" in rendered
    assert "\n  fix: run `ren propose <project>` first" in rendered
    assert str(refused) == rendered

    with pytest.raises(ValueError, match="fix"):
        RenRefusal("something happened", "because", "")
    with pytest.raises(ValueError, match="fix"):
        RenRefusal("something happened", "", "do this")


def test_a_refusal_stays_catchable_as_before():
    """Adopting a class changes what its message carries, never who
    catches it: every existing `except ValueError` / `except
    RuntimeError` keeps working."""
    refused = RenRefusal("w", "y", "f")
    assert isinstance(refused, ValueError)
    assert isinstance(refused, RuntimeError)
    with pytest.raises(ValueError):
        raise refused
    with pytest.raises(RuntimeError):
        raise refused


def test_ren_config_init_refuses_in_shape_with_exit_4(tmp_path, monkeypatch):
    """The `ren` boundary renders the refusal and returns 4, not 1."""
    from ren import config as ren_config

    monkeypatch.setenv("REN_CONFIG", str(tmp_path / "config.env"))
    assert ren_config.main(["--init"]) == 0
    with pytest.raises(RenRefusal) as refused:
        ren_config.main(["--init"])
    assert refused.value.fix.startswith("edit ")
    assert "ren: refused - " in str(refused.value)

    from ren import cli as ren_cli
    assert ren_cli.main(["config", "--init"]) == REFUSAL_EXIT_CODE


def test_splice_stray_entries_carry_the_fix():
    """Audit site 1: subtitle_splice's stray-entries refusal named no
    next step."""
    from library.tools.subtitle_splice import SpliceRefused, splice_plan

    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0}]
    with pytest.raises(SpliceRefused) as refused:
        splice_plan(stored, [{"id": "x", "spine_block_position": 9,
                              "timeline_start": 5.0}], [1])
    assert "not in the region" in str(refused.value)
    assert refused.value.fix, "the refusal must carry the fix"
    assert "splice again" in refused.value.fix


def test_craft_role_discipline_guard_carries_the_fix():
    """Audit site 3: craft_role's discipline guard named no next step."""
    import unittest.mock as mock

    from library.tools import craft_role

    role = craft_role.CraftRole(step_id="s", discipline="",
                                addressed_as="", reads_with=("r",),
                                decides=("d",), defers=("f",))
    with mock.patch.object(craft_role, "ROLES", {"s": role}), \
         mock.patch.object(craft_role, "WITHOUT_A_DECLARED_ROLE", ()), \
         mock.patch.object(craft_role, "_REACHES_A_MODEL", frozenset({"s"})):
        with pytest.raises(RenRefusal) as refused:
            craft_role._assert_roles_account_for_every_model_reaching_step()
    assert "no discipline" in refused.value.what
    assert refused.value.fix.endswith("library/tools/craft_role.py")


def test_touchup_unknown_reel_carries_the_fix(tmp_path):
    """Audit site 2: reel_touchup's unknown-reel refusal named no next
    step."""
    import unittest.mock as mock

    from library.tools import reel_proposal, reel_touchup
    from library.tools.ren_refusal import RenRefusal as _RR

    with mock.patch.object(reel_proposal, "read_proposal",
                           return_value=[]), \
         mock.patch.object(reel_proposal, "proposal_path",
                           return_value=str(tmp_path / "proposal.json")):
        with pytest.raises(_RR) as refused:
            reel_touchup.resolve_final_name(str(tmp_path), 9)
    assert "no reel 9" in refused.value.what
    assert "ren propose" in refused.value.fix
