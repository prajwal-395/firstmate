"""Preview decisions that need a plan, but no encoded media or Resolve."""

from types import SimpleNamespace

import pytest


def test_preview_refuses_a_reel_number_absent_from_the_plan(
        monkeypatch, tmp_path):
    from library.tools import reel_deliver, reel_preview

    monkeypatch.setattr(reel_deliver, "_proposal_moments", lambda _p: [])
    with pytest.raises(reel_preview.PreviewRefused, match="no reel 7"):
        reel_preview.reel_targets(str(tmp_path), [7])


def test_preview_refuses_an_unapproved_plan_entry_without_building(
        monkeypatch, tmp_path):
    from library.tools import reel_deliver, reel_preview
    from library.tools.reel_proposal import Approval

    moment = SimpleNamespace(
        number=1, approval=Approval.PROPOSED, slug="check-the-answer",
        timeline_name="Reel 01 - check-the-answer")
    monkeypatch.setattr(reel_deliver, "_proposal_moments",
                        lambda _p: [moment])
    with pytest.raises(reel_preview.PreviewRefused, match="not approved"):
        reel_preview.reel_targets(str(tmp_path), [1])
