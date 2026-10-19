from library.tools import native_mcp


def test_capability_inventory_distinguishes_discovery_writes_and_arbitrary_code(monkeypatch):
    monkeypatch.setattr(native_mcp, "list_tools", lambda timeout=90: [
        {"name": "get_scripting_api", "description": "Read API"},
        {"name": "update_dctl", "description": "Write LUT shelf"},
        {"name": "run_script", "description": "Execute native script"},
        {"name": "run_script_unsafe", "description": "Execute unsafe native script"},
    ])

    inventory = native_mcp.capability_inventory()
    entries = {entry["name"]: entry for entry in inventory["tools"]}
    assert entries["get_scripting_api"]["mode"] == "discovery_only"
    assert entries["update_dctl"]["axi_route"] == "resolve-axi luts"
    assert entries["run_script"]["mode"] == "arbitrary_code"
    assert entries["run_script_unsafe"]["mode"] == "unsafe_arbitrary_code"
    assert "AXI operation authorization" in inventory["limitations"][0]
