"""The temporal index joins regional motion to completed vision profiles."""
import json
from pathlib import Path

from library.tools import capabilities, footage_intelligence

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


def test_analysis_composition_runs_semantic_then_temporal_then_prosody():
    """The normal `ren analyze` composition satisfies the route, unskipped.

    Regression for the 2026-10-14 failure, when the workflow
    qualification skipped `temporal.index` and `prosody.analyse` because
    the orchestrator could not supply the temporal operation's required
    semantic documents: the composed order runs the producer first, and
    each link of the semantic -> temporal -> prosody chain is a declared
    effect the next capability requires.
    """
    order = footage_intelligence.compose(footage_intelligence.ANALYSIS)
    assert (order.index("semantics.analyse")
            < order.index("temporal.index")
            < order.index("prosody.analyse"))

    semantics = capabilities.get("semantics.analyse")
    temporal = capabilities.get("temporal.index")
    prosody = capabilities.get("prosody.analyse")
    assert "state.temporal_index.semantic_analysis_documents" in \
        semantics.effects
    assert "state.prosody_analysis.temporal_event_indices" in \
        temporal.effects
    assert "state.prosody_analysis.temporal_event_indices" in \
        prosody.requires
