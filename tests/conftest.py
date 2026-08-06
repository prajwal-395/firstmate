import sys
import os
import json
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

@pytest.fixture
def e2e_project_path():
    path = "/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001"
    if not os.path.exists(path):
        pytest.skip(f"E2E project directory {path} not found")
    return path

@pytest.fixture
def resolve_api():
    try:
        sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            pytest.skip("DaVinci Resolve not open or initialized")
        return resolve
    except ImportError:
        pytest.skip("DaVinciResolveScript module not found")

@pytest.fixture
def step_data_loader(e2e_project_path):
    def _loader(step_name, run_folder="run_002"):
        path = os.path.join(e2e_project_path, run_folder, f"{step_name}.json")
        if not os.path.exists(path):
            pytest.skip(f"Step data {path} not found")
        with open(path, "r") as f:
            return json.load(f)
    return _loader
