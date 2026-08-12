import pytest
from library.tools.validate_dag_contracts import run_validation

def test_all_dag_contracts_valid():
    """
    Test that all data_mapping edges in dag.json match the 
    interface.inputs and interface.outputs schemas of the target manifests.
    """
    errors = run_validation()
    assert errors == 0, f"Expected 0 contract mismatches, but found {errors}. Run validate_dag_contracts.py for details."
