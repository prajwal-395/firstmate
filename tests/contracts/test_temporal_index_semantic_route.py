"""The temporal index joins regional motion to completed vision profiles."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_temporal_index_declares_and_receives_semantic_profiles():
    manifest = json.loads((
        REPO / "library/steps/step_1_04_temporal_index/manifest.json"
    ).read_text(encoding="utf-8"))
    inputs = {item["name"]: item
              for item in manifest["interface"]["inputs"]}
    assert inputs["semantic_analysis_documents"]["required"] is True

    dag = json.loads((
        REPO / "library/processes/edit_video/dag.json"
    ).read_text(encoding="utf-8"))
    assert any(
        edge["from"] == "semantic_analysis"
        and edge["to"] == "temporal_index"
        and edge["data_mapping"].get("semantic_analysis_documents")
        == "semantic_analysis_documents"
        for edge in dag["edges"]
    )
