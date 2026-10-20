"""Defect-naming contracts for isolated AXI-14 fixture materialization."""

import json
import tempfile
from pathlib import Path

import pytest

from tests.live import matrix_fixture_factory as factory


def test_matrix_factory_declares_required_source_and_timeline_variants():
    dimensions = {tuple(asset.get("size", ())) for asset in factory.ASSET_RECIPES}
    assert (3840, 2160) in dimensions
    assert (1080, 1920) in dimensions
    assert any(asset["codec"] == "prores" for asset in factory.ASSET_RECIPES)
    assert any(asset["id"] == "wav-only" for asset in factory.ASSET_RECIPES)
    assert {(tuple(row["resolution"]), row["frame_rate"])
            for row in factory.TIMELINE_PROFILES} == {
                ((w, h), rate)
                for w, h in ((1920, 1080), (3840, 2160), (1080, 1920))
                for rate in ("24", "25", "30000/1001", "60")}


def test_fixture_factory_records_native_state_and_refusal_recipes():
    grade = factory.TOPOLOGY_RECIPES["graded_project"]
    assert grade["verify_grade_readback"]
    assert grade["method"].startswith("TimelineItem.SetCDL")
    assert grade["cdl"]["Saturation"] == "1.05"
    assert factory.TOPOLOGY_RECIPES["fusion_composition"][
        "verify_comp_identity_and_tool_count"]
    assert factory.TOPOLOGY_RECIPES["ambiguous_targets"][
        "expected_result"] == "refuse_without_mutation"
    assert factory.TOPOLOGY_RECIPES["stale_snapshots"][
        "expected_result"] == "refuse_without_mutation"
    assert factory.TOPOLOGY_RECIPES["staged_promotion"][
        "verify_promotion_and_history"]


def test_fixture_cleanup_rejects_an_unowned_directory(tmp_path):
    marker = tmp_path / factory.MARKER
    marker.write_text(json.dumps({"schema": factory.SCHEMA,
                                  "owned_root": str(tmp_path)}),
                      encoding="utf-8")
    with pytest.raises(ValueError, match="owned temp roots"):
        factory.cleanup_fixture(marker)


def test_fixture_cleanup_refuses_unmanifested_disposable_resources():
    root = Path(tempfile.mkdtemp(prefix="ren-axi14-fixture-test-"))
    marker = root / factory.MARKER
    marker.write_text(json.dumps({"schema": factory.SCHEMA,
                                  "fixture_id": "test",
                                  "owned_root": str(root),
                                  "asset_recipes": []}), encoding="utf-8")
    (root / "unexpected-resource").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory differs"):
        factory.cleanup_fixture(marker)
    assert root.exists()
    (root / "unexpected-resource").unlink()
    marker.unlink()
    root.rmdir()


def test_fixture_cleanup_removes_only_verified_owned_tree():
    root = Path(tempfile.mkdtemp(prefix="ren-axi14-fixture-test-"))
    marker = root / factory.MARKER
    marker.write_text(json.dumps({"schema": factory.SCHEMA,
                                  "fixture_id": "test",
                                  "owned_root": str(root),
                                  "asset_recipes": []}), encoding="utf-8")
    result = factory.cleanup_fixture(marker)
    assert result["cleanup_inventory_verified"] is True
    assert result["cleanup_status"] == "verified_removed"
    assert not root.exists()
