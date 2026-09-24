"""What the dashboard offers for a selected span, and what it refuses.

The refusals are the half that makes this a control surface rather than
a menu, so most of these are about them: that they exist, that they name
the missing thing, and that they name what would produce it.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from library.dashboard.server import app
from library.tools import operations, scope


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(folder), "step_outputs": {}}),
        encoding="utf-8")
    (folder / "project.yaml").write_text("name: P\nslug: p\n", encoding="utf-8")
    return folder


@pytest.fixture
def client(project):
    with patch("library.dashboard.server._get_project_dir",
               return_value=str(project)):
        yield TestClient(app)


def test_a_span_is_answered_with_offers_refusals_and_out_of_scope(client):
    body = client.get("/api/operations?region=45.0-72.0").json()
    assert body["region"]
    for key in ("offers", "refusals", "out_of_scope"):
        assert isinstance(body[key], list)
    # Every operation is accounted for. One that is silently absent is
    # the failure this endpoint exists to avoid.
    named = {o["name"] for o in
             body["offers"] + body["refusals"] + body["out_of_scope"]}
    assert named == set(operations.names())


def test_a_project_scoped_operation_is_reported_not_hidden(client):
    """Ten of the twelve run against the whole project. A captain who
    selected a span deserves to know that is why they are not offered."""
    body = client.get("/api/operations?region=45.0-72.0").json()
    out = {o["name"]: o for o in body["out_of_scope"]}
    region_capable = {o.name for o in operations.all()
                      if scope.REGION in o.scopes}
    assert set(out) == set(operations.names()) - region_capable
    assert out, "nothing was reported out of scope"
    for entry in out.values():
        assert "region" in entry["reason"], entry["reason"]


def test_a_refusal_names_the_missing_thing_and_what_produces_it(client):
    """A picker that hides what it cannot do teaches nothing; one that
    says why is a control surface."""
    body = client.get("/api/operations?region=45.0-72.0").json()
    assert body["refusals"], (
        "an empty project refused nothing - the refusals are sourced from "
        "requirements.check, and if they came from Operation.requires "
        "(empty for all twelve today) every operation would look runnable")
    for op in body["refusals"]:
        assert op["reasons"], f"{op['name']} refused with no reason"
        for why in op["reasons"]:
            assert why["requirement"], "a refusal must name its requirement"
            assert why["why"], "a refusal must say why"
            assert "produced_by" in why






def test_the_endpoint_carries_no_second_spelling_of_an_address(client):
    """`<operation>@<region>` has exactly one owner. The API returns the
    NAME and the REGION as separate fields so the joined form is composed
    in one place, not two."""
    body = client.get("/api/operations?region=45.0-72.0").json()
    for op in body["offers"] + body["refusals"]:
        assert "address" not in op
        assert op["region"]
