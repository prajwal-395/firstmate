"""The live AXI case for a caption text fix, judged from read-back.

The defect this lane fixed: `caption_fix` could not address a rendered
caption card, so the live batch had no case for it and the operation
the product's own reels are made of was never exercised on live
Resolve.

These tests pin the harness wiring - the instruction the blind driver
reads, the target it resolves from live fixture state, and the checks
the evidence function judges - so a case that stops meaning what it
says fails here rather than in a live run.
"""
from __future__ import annotations

import pytest

from tests.live import blind_driver
from tests.live import editorial_live_adapter as adapter


def _case(**overrides):
    """One judged caption-fix case, as the live batch records it."""
    case = {
        "operation": {"op": "caption_fix", "unique_id": "card-1",
                      "text": "hold the opening"},
        "receipt": {"status": "committed"},
        "card_before": {"unique_id": "card-1", "name": "axc-caption-1.mov",
                        "record": [96, 144],
                        "source_path": "/seg/axc-caption-1.mov"},
        "card_after": {"unique_id": "card-1", "name": "axc-caption-1_new.mov",
                       "record": [96, 144],
                       "source_path": "/seg/axc-caption-1_new.mov"},
        "rendered_text": "hold the opening",
        "rendered_timing": [0, 47],
        "previous_file": "/seg/axc-caption-1.mov",
        "new_file": "/seg/axc-caption-1_new.mov",
        "collateral_changes": [],
    }
    case.update(overrides)
    return case


def _row(case):
    return adapter._fix_caption_text_evidence(
        case, 0.0, "fix the caption text", {"operation": "caption_fix"},
        {})


def test_the_instruction_reads_as_a_caption_fix():
    """The driver reads the instruction, not an operation id."""
    plan = blind_driver.interpret(adapter.CAPTION_FIX_INSTRUCTION)

    assert plan["case"] == "fix_caption_text"
    assert plan["role"] == "selected_caption_card"
    assert plan["operations"] == [
        {"op": "caption_fix", "unique_id": "blind-caption",
         "text": "hold the opening"}]


def test_the_target_is_the_rendered_card_on_the_timeline():
    """A caption card is a rendered .mov on the timeline, and exactly
    one of them resolves."""
    evidence = {"items": [
        {"kind": "video", "name": "axc-caption-1.mov", "unique_id": "card-1",
         "record": [96, 144]},
    ]}
    target = blind_driver.resolve_target("selected_caption_card", evidence,
                                         "fix_caption_text")

    assert target["uniquely_resolved"] is True
    assert target["unique_id"] == "card-1"

    two = {"items": evidence["items"] + [
        {"kind": "video", "name": "axc-caption-2.mov", "unique_id": "card-2",
         "record": [144, 192]}]}
    assert blind_driver.resolve_target(
        "selected_caption_card", two, "fix_caption_text"
    )["uniquely_resolved"] is False


def test_the_case_passes_when_the_text_changed_and_the_timing_did_not():
    row = _row(_case())

    assert row["checks"]["caption_text_changed"] is True
    assert row["checks"]["caption_timing_preserved"] is True
    assert row["checks"]["caption_item_kept"] is True
    assert row["checks"]["committed_receipt"] is True
    assert row["checks"]["no_collateral_changes"] is True
    assert row["collateral_changes"] == []


def test_a_card_that_kept_its_old_text_fails_the_case():
    """Defect: a fix that re-rendered the card with the SAME words
    would read as a fix and ship nothing."""
    row = _row(_case(rendered_text="tighten the opening"))

    assert row["checks"]["caption_text_changed"] is False


def test_a_card_whose_timing_moved_fails_the_case():
    row = _row(_case(card_after={
        "unique_id": "card-1", "name": "axc-caption-1_new.mov",
        "record": [96, 150], "source_path": "/seg/axc-caption-1_new.mov"}))

    assert row["checks"]["caption_timing_preserved"] is False


def test_a_missing_fixture_card_is_a_categorised_refusal():
    with pytest.raises(RuntimeError, match=r"\[missing_fixture_content:"):
        adapter._fix_caption_text_evidence(
            None, 0.0, "fix the caption text", {}, {})
