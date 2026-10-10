import json
from pathlib import Path

import pytest

from library.tools import project_model

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "native/ren-protocol/fixtures/project-model.json"


def test_python_reads_and_writes_the_shared_rust_fixture(tmp_path):
    model = project_model.read_project_model(FIXTURE)
    target = tmp_path / "project-model.json"
    project_model.write_project_model(target, model)
    assert json.loads(target.read_text(encoding="utf-8")) == model


def test_unknown_version_and_empty_half_open_interval_are_refused():
    model = json.loads(FIXTURE.read_text(encoding="utf-8"))
    model["schema_version"] = 2
    with pytest.raises(ValueError, match="unsupported project model"):
        project_model.validate_project_model(model)
    model["schema_version"] = 1
    model["observations"][0]["interval"]["end"] = 0
    with pytest.raises(ValueError, match="half-open"):
        project_model.validate_project_model(model)
