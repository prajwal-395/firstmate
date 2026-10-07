"""Media relink closed live in Ren Qualification, through the broker.

Proves `resolve-axi relink --apply` re-points a moved import and verifies
by reading File Path back - the Resolve-live half of the ingest track.
Runs ONLY inside a granted borrow-resolve window (`REN_LIVE_RESOLVE=1`):
without it the module skips before any Resolve contact, so no default
pytest run, dependent selection or gate can reach the captain's instance.

Through the broker only: the test submits one
`resolve.qualification_session` job (EXCLUSIVE whole-take lease,
`qualification_project.borrowed()` with restore + read-back, project
read back by the exact name "Ren Qualification"). No direct Resolve
calls here - the steps are `resolve_axi` argv the broker runs on its
own connection, each under `resolve_deadline.call_with_deadline`. Never
touches a captain project or timeline; media imported here is the test's
own copy under tmp_path, deleted from the pool in the final step.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

QUALIFICATION_PROJECT = "Ren Qualification"


def _live_enabled() -> bool:
    return os.environ.get("REN_LIVE_RESOLVE") == "1"


def test_relink_repoints_a_moved_import_live(tmp_path):
    """Ingest, move on disk, relink via broker, verify File Path reads back."""
    if not _live_enabled():
        pytest.skip("drives the live Resolve; runs only with "
                    "REN_LIVE_RESOLVE=1 inside a granted borrow-resolve window")
    from library.tools import qualification_project as qp
    from library.tools.resolved import client

    if client.ping() is None:
        pytest.skip("no ren-resolved broker serving")
    media = qp.ensure_media()
    src = media["qual_a"]
    tag = uuid.uuid4().hex[:8]
    original = tmp_path / f"relink-proof-{tag}-orig.mp4"
    moved = tmp_path / f"relink-proof-{tag}-moved.mp4"
    shutil.copyfile(src, original)
    shutil.copyfile(src, moved)
    old_path, new_path = str(original), str(moved)

    session = {
        "project": QUALIFICATION_PROJECT,
        "steps": [
            {"resolve_axi": ["ingest", str(original),
                             "--project", QUALIFICATION_PROJECT, "--apply"]},
            {"resolve_axi": ["pool", "--project", QUALIFICATION_PROJECT]},
            {"resolve_axi": ["relink", "--from", old_path, "--to", new_path,
                             "--project", QUALIFICATION_PROJECT]},
            {"resolve_axi": ["relink", "--from", old_path, "--to", new_path,
                             "--project", QUALIFICATION_PROJECT, "--apply"]},
            {"resolve_axi": ["pool", "--project", QUALIFICATION_PROJECT]},
        ],
    }
    submitted = client.submit("resolve.qualification_session", session,
                              qualification=True)
    job = client.result(submitted["id"], wait=300.0)
    assert job["state"] == "done", f"broker {job['state']}: {job!r}"
    steps = job["result"]["steps"]
    assert len(steps) == 5, f"expected 5 step receipts: {steps!r}"
    assert steps[0]["exit_code"] == 0, f"ingest failed: {steps[0]!r}"
    assert "ingested" in steps[0]["stdout"], steps[0]
    assert old_path in steps[1]["stdout"], (
        f"pool does not list the import: {steps[1]!r}")
    assert steps[2]["exit_code"] == 0, f"relink plan failed: {steps[2]!r}"
    assert "relink_plan" in steps[2]["stdout"], steps[2]
    assert steps[3]["exit_code"] == 0, f"relink apply failed: {steps[3]!r}"
    assert "relinked" in steps[3]["stdout"], steps[3]
    assert "verified" in steps[3]["stdout"], steps[3]
    assert new_path in steps[4]["stdout"], (
        f"pool does not read back the new path: {steps[4]!r}")
    assert job["result"]["restored"]["project"] != QUALIFICATION_PROJECT, (
        f"borrowed from the qualification project itself: {job['result']!r}")
    print(json.dumps({"proof": "relink-live",
                      "old": old_path, "new": new_path,
                      "job": submitted["id"]}))
