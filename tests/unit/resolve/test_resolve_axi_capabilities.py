from argparse import Namespace

from library.tools.resolve_axi_core_commands import cmd_capabilities


def test_capability_search_finds_overlay_resize_by_editorial_request(capsys):
    assert cmd_capabilities(Namespace(
        operation="", search="resize existing lower third", json=False,
    )) == 0
    output = capsys.readouterr().out
    assert "overlay.resize" in output
    assert "matches[" in output
    assert "algebra_operations" not in output


def test_capability_detail_reports_schema_backends_evidence_and_cost(capsys):
    assert cmd_capabilities(Namespace(
        operation="overlay.resize", search="", json=True,
    )) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"]["required"] == ["unique_id", "width", "height"]
    assert payload["eligible_backends"] == []
    assert any(row["id"] == "reel_touchup" and not row["eligible"]
               for row in payload["backends"])
    assert payload["recovery_limits"]["qualified_backends"] == []
    assert payload["measured_cost"]["status"] == "unmeasured"


def test_capability_list_is_concise_toon_and_json_is_structured(capsys):
    assert cmd_capabilities(Namespace(
        operation="", search="", json=False,
    )) == 0
    output = capsys.readouterr().out
    assert "operations[" in output
    assert "overlay.resize" in output
    assert "native_mcp" not in output

    assert cmd_capabilities(Namespace(
        operation="", search="", json=True,
    )) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert any(row["id"] == "overlay.resize" for row in payload["operations"])
