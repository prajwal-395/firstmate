import json

from library.tools import resolve_capability_sync as sync


def test_diff_only_emits_new_capabilities_as_not_implemented():
    current = {"resolve:api": {"capabilities": ["api:Timeline.NewCall", "api:Timeline.OldCall"]}}
    baseline = {"resolve:api": {"capabilities": ["api:Timeline.OldCall"]}}

    rows = sync.compare(current, baseline)

    assert rows == [{
        "source": "resolve:api",
        "upstream_id": "api:Timeline.NewCall",
        "change": "new",
        "status": "documented, not implemented",
        "stage": "evaluation",
        "qualification_status": "not qualified",
        "required_next_stage": "live qualification",
        "production_enabled": False,
    }]


def test_diff_reports_changed_qualified_ui_capability():
    current = {"ui": {"capabilities": ["ui:press"], "capability_fingerprints": {"ui:press": "new"}}}
    baseline = {"ui": {"capabilities": ["ui:press"], "capability_fingerprints": {"ui:press": "old"}}}

    row, = sync.compare(current, baseline)

    assert row["upstream_id"] == "ui:press"
    assert row["change"] == "changed"
    assert row["revision"] == "new"
    assert row["status"] == "documented, not implemented"
    assert row["qualification_status"] == "not qualified"
    assert row["required_next_stage"] == "live qualification"
    assert row["production_enabled"] is False


def test_first_record_establishes_baseline_without_filing_everything(tmp_path, monkeypatch):
    source_file = tmp_path / "sources.json"
    ledger_file = tmp_path / "ledger.json"
    source_file.write_text(json.dumps({"baseline_initialized": False, "baseline": {}}), encoding="utf-8")
    observations = {"ui:resolve-control": {"fingerprint": "f", "capabilities": ["ui:press"]}}
    monkeypatch.setattr(sync, "discover", lambda _sources: observations)

    report = sync.scan(record=True, sources_path=source_file, ledger_path=ledger_file)

    assert report["candidates"] == []
    assert json.loads(source_file.read_text(encoding="utf-8"))["baseline_initialized"] is True
    assert not ledger_file.exists()


def test_record_files_candidates_and_never_changes_algebra_registry(tmp_path, monkeypatch):
    source_file = tmp_path / "sources.json"
    ledger_file = tmp_path / "ledger.json"
    source_file.write_text(json.dumps({
        "baseline_initialized": True,
        "baseline": {"api": {"fingerprint": "old", "capabilities": ["api:Timeline.Known"]}},
    }), encoding="utf-8")
    observations = {"api": {"fingerprint": "new", "capabilities": ["api:Timeline.Known", "api:Timeline.New"]}}
    monkeypatch.setattr(sync, "discover", lambda _sources: observations)
    before = dict(sync.edit_algebra.OP_STATUS)

    report = sync.scan(record=True, sources_path=source_file, ledger_path=ledger_file)

    assert report["candidates"][0]["status"] == "documented, not implemented"
    ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert ledger["candidates"] == report["candidates"]
    assert sync.edit_algebra.OP_STATUS == before
    assert json.loads(source_file.read_text(encoding="utf-8"))["baseline"]["api"]["fingerprint"] == "new"


def test_candidate_comparison_does_not_mutate_inputs():
    observations = {"source": {"capabilities": ["api:New"]}}
    baseline = {"source": {"capabilities": []}}
    sync.compare(observations, baseline)
    assert observations["source"]["capabilities"] == ["api:New"]
    assert baseline["source"]["capabilities"] == []


def test_installed_api_method_signatures_are_revisioned():
    first = "class Timeline:\n    def SetName(self, name: str): ...\n"
    changed = "class Timeline:\n    def SetName(self, name: str, enabled: bool = True): ...\n"

    old_method, = sync._api_methods(first)
    new_method, = sync._api_methods(changed)

    assert old_method.startswith("Timeline.SetName#")
    assert new_method.startswith("Timeline.SetName#")
    assert old_method != new_method


def test_changelog_only_files_added_capabilities():
    changelog = """## 21.2
Notes:
- A compatibility note.
Added:
- Timeline.AddExample
Addressed:
- A bug fix.
"""

    assert sync._changelog_entries(changelog) == {
        "api-change:21.2:Timeline.AddExample#" + sync._sha(b"Timeline.AddExample")[:12]
    }


def test_third_party_capability_scan_uses_mcp_declarations_only():
    source = """@mcp.tool()\ndef add_clip(item):\n    return item\n\ndef helper():\n    return None\n"""

    tools = sync._mcp_tools(source)

    assert len(tools) == 1
    assert next(iter(tools)).startswith("mcp:add_clip#")
