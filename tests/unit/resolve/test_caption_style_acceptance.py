"""The live acceptance adapter must exercise caption.style, not refuse it."""

from tests.live import editorial_live_adapter as live_adapter


def test_restyle_captions_live_failure_is_not_a_hardcoded_design_refusal(
        monkeypatch):
    """Defect: the live run rejected restyle_captions before execution."""
    assert "restyle_captions" not in live_adapter._REFUSAL_NOTES
    monkeypatch.setattr(live_adapter, "_route_executing", lambda *args,
                        **kwargs: {"selected": "resolve_axi",
                                   "selection_correct": True})
    operations = [
        {"op": "caption.style", "unique_id": "cap-1", "style": "minimal"},
        {"op": "caption.style", "unique_id": "cap-2", "style": "minimal"},
    ]
    case = {
        "operations": operations,
        "receipt": {"status": "committed"},
        "per_caption": [
            {"unique_id": "cap-1", "verified": True,
             "text_preserved": True, "timing_preserved": True,
             "style_applied": True},
            {"unique_id": "cap-2", "verified": True,
             "text_preserved": True, "timing_preserved": True,
             "style_applied": True},
        ],
        "collateral_changes": [],
    }

    result = live_adapter._restyle_captions_evidence(
        case, 1.0, "Apply style", {"operation": "caption.style"}, {})

    assert all(result["checks"].values())
    assert result["receipt"]["status"] == "committed"
    assert len(result["observed"]["per_caption"]) == 2
