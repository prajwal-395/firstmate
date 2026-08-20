"""An entry node can only get what its own manifest asks for.

`validate_sfx_library` is an entry node: no incoming edges, so no
`data_mapping` can route anything to it. Its `step.py` reads
`data["sfx_library"]` and exits 1 without it, and its manifest declared
`interface.inputs: []`. The step was therefore unrunnable on any project
whose `pipeline_data.json` did not already carry the key from some earlier
era - which is every new project, and project 001 the moment its state was
cleared.

The runner now injects a PROCESS_LEVEL_INPUT only into steps that declare
it, so the manifest is the request. These tests hold the two ends
together: the step must declare what its code requires, and the runner must
honour the declaration.
"""

import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    PROCESS_LEVEL_INPUTS,
    gather_step_inputs,
)

STEPS_ROOT = REPO_ROOT / "library" / "steps"
DAG = json.loads(
    (REPO_ROOT / "library" / "processes" / "edit_video" / "dag.json").read_text()
)


def _step_dir(node_id: str) -> Path:
    for node in DAG["nodes"]:
        if node["id"] == node_id:
            return REPO_ROOT / "library" / node["step_ref"]
    raise AssertionError(f"{node_id} is not in the DAG")


def _keys_read_from_stdin_payload(step_py: Path) -> set:
    """Every `data.get("x")` / `data["x"]` in the step's main().

    Reads the producer's own source rather than a fixture, which is the
    only way this catches a rename.
    """
    tree = ast.parse(step_py.read_text())
    keys = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (node.func.attr == "get"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "data"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)):
                keys.add(node.args[0].value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "data" and isinstance(node.slice, ast.Constant):
                keys.add(node.slice.value)
    return keys


def test_entry_steps_declare_the_process_inputs_their_code_reads():
    entry_nodes = DAG["entry_nodes"]
    assert entry_nodes, "the DAG has no entry nodes"

    for node_id in entry_nodes:
        step_dir = _step_dir(node_id)
        step_py = step_dir / "step.py"
        manifest_path = step_dir / "manifest.json"
        if not step_py.exists() or not manifest_path.exists():
            continue

        declared = {
            inp.get("name")
            for inp in json.loads(manifest_path.read_text())
            .get("interface", {})
            .get("inputs", [])
        }
        needed = _keys_read_from_stdin_payload(step_py) & set(PROCESS_LEVEL_INPUTS)
        missing = needed - declared
        assert not missing, (
            f"Entry step '{node_id}' reads {sorted(missing)} from its stdin "
            f"payload but does not declare it in manifest.json. An entry node "
            f"has no incoming edges, so a declaration is the only way the "
            f"value can reach it."
        )


def test_gather_step_inputs_supplies_a_declared_process_input():
    state = {
        "project_folder": "/tmp/project",
        "sfx_library": "/tmp/sfx",
        "music_library": "/tmp/music",
        "step_outputs": {},
    }
    manifest = {"interface": {"inputs": [{"name": "sfx_library"}]}}
    inputs = gather_step_inputs("validate_sfx_library", DAG, state, manifest)
    assert inputs["sfx_library"] == "/tmp/sfx"


def test_gather_step_inputs_does_not_broadcast_undeclared_process_inputs():
    """Declaring is asking. A step that did not ask does not receive."""
    state = {
        "project_folder": "/tmp/project",
        "sfx_library": "/tmp/sfx",
        "music_library": "/tmp/music",
        "step_outputs": {},
    }
    manifest = {"interface": {"inputs": [{"name": "sfx_library"}]}}
    inputs = gather_step_inputs("validate_sfx_library", DAG, state, manifest)
    assert "music_library" not in inputs
