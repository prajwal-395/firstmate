import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from library.processes.edit_video.run_pipeline import (
    load_dag,
    topological_sort,
    get_step_dir,
    _get_ancestors,
    gather_step_inputs
)

def test_dag_valid_step_dirs():
    dag = load_dag()
    assert "nodes" in dag
    for node in dag["nodes"]:
        step_dir = get_step_dir(node)
        assert step_dir.exists(), f"Step directory for node {node['id']} does not exist: {step_dir}"

def test_topological_sort():
    dag = load_dag()
    order = topological_sort(dag)
    assert len(order) == len(dag["nodes"])
    
    # Check that "scan" is before "catalog"
    assert order.index("scan") < order.index("catalog")

def test_get_ancestors():
    dag = load_dag()
    # 'catalog' depends on 'scan'
    ancestors = _get_ancestors("catalog", dag)
    assert "scan" in ancestors
    
    # 'semantic_analysis' depends on 'scan'
    ancestors_sem = _get_ancestors("semantic_analysis", dag)
    assert "scan" in ancestors_sem

def test_gather_step_inputs_projection():
    dag = load_dag()
    state = {
        "step_outputs": {
            "scan": {"raw_footage_files": [{"path": "/a.mov"}]},
            "catalog": {"clip_catalog": [{"clip_id": "c1"}]}
        },
        "project_folder": "/test"
    }
    
    manifest = {
        "interface": {"inputs": [{"name": "raw_footage_files"}]},
        "context_fields": ["raw_footage_files"]
    }
    
    # Test llm_only step type (triggers projection)
    inputs = gather_step_inputs("catalog", dag, state, manifest=manifest, step_type="llm_only")
    assert "raw_footage_files" in inputs
    assert "clip_catalog" not in inputs # If not projected or mapped

def test_error_propagation_missing_required_input():
    dag = load_dag()
    state = {
        "step_outputs": {
            "scan": {} # Missing raw_footage_files
        }
    }
    
    with pytest.raises(RuntimeError) as excinfo:
        gather_step_inputs("catalog", dag, state)
    assert "data_mapping expects key" in str(excinfo.value)
