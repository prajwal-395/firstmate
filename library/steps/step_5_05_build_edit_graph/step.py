#!/usr/bin/env python3
"""
Step 5.5: Build the Editorial/Edit Graph

Derives the editorial/edit graph from the run's recorded decisions and
emits it as a state artifact. The graph is DERIVED, never authored: this
step reads pipeline_data.json, builds one node per decision the planners
made, and derives the dependency edges, intent links, evidence links and
constraints. Planners decide exactly as they did before this step
existed - the graph is what a reader queries, not a second decider.

Classification: Deterministic / Data Processing
Archetype: Data Processing
Idempotent: Yes

Input:  { "project_folder": "<absolute path>" }
Output: {
    "edit_graph": {
        "graph_id": "edit_graph",
        "node_count": <int>,
        "nodes": [ { node_id, node_type, producer, intent_refs,
                     evidence_refs, constraints, depends_on, payload } ]
    }
}
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools import edit_graph
from library.tools.project_layout import ProjectLayout


def build_edit_graph(project_folder: str) -> dict:
    """
    Derive the editorial/edit graph from the project's pipeline_data.json.

    A state key the run has not produced is simply absent - the graph
    builds from whatever decisions exist, so a run that stopped after
    the spine still gets the blocks and passages. The spine is required
    by the manifest: without the timed structure there is no edit to
    graph, and the refusal names the producer.
    """
    state = {}
    path = ProjectLayout(project_folder).pipeline_data_path
    if Path(path).is_file():
        state = json.loads(Path(path).read_text(encoding="utf-8"))

    graph = edit_graph.build_graph(state)
    return {"edit_graph": graph}


def main():
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder")

    if not project_folder:
        print(json.dumps({
            "error": "Missing required input: project_folder",
            "step": "5.5_build_edit_graph"
        }))
        sys.exit(1)

    try:
        result = build_edit_graph(project_folder)
    except (OSError, ValueError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "5.5_build_edit_graph"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
