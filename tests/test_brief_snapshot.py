"""The creative brief has a versioned home now (AGENTS.md 3, 10.1).

The hole, executable: a `project.yaml`-declared brief path may sit
outside the project folder, where the generated allow-list cannot name
it.  Before this change, nothing versioned recorded WHAT the build
read - only WHERE `project.yaml` said it was.  `read_snapshot`
returning None on such a project IS that hole; returning the verbatim
bytes after `record_brief_for_run` is the fix.  Both halves are pinned
below, so the test fails on the old shape (no snapshot written, lookup
answers nothing) and passes on the new one.

The snapshot lands under `pipeline_output/provenance/`, which the
allow-list already versions - so the coverage assertion derives its
paths from `brief_snapshot.snapshot_relpaths()` (the PR 1051 pattern:
inputs from the writing module, never from the allow-list) and fails
naming the store if a future narrowing drops it.
"""

import hashlib
import json
import subprocess

from library.tools import brief_snapshot
from library.tools import build_version_control as bvc

BRIEF_TEXT = """# Channel brief

## Voice

We whisper, never shout.

## Colour

Warm highlights, honest shadows.
"""


def _git(project, *args):
    proc = subprocess.run(
        ["git", *args], cwd=str(project), capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _outside_brief(tmp_path, text=BRIEF_TEXT):
    """A brief that lives OUTSIDE the project, like the captain's own."""
    planning = tmp_path / "planning-tree"
    planning.mkdir()
    src = planning / "creative_brief.md"
    src.write_text(text, encoding="utf-8")
    return src


def test_no_snapshot_means_no_versioned_answer(tmp_path):
    """The hole, pinned: an outside brief with no snapshot explains nothing.

    `read_snapshot` is the versioned answer to "what brief did this
    build read".  On the old shape - the brief declared, read by the
    steps, snapshotted nowhere - it answers None.  This assertion held
    before the fix and still holds for a project the fixed runner never
    ran; it is the "fails on the old shape" half beside
    `test_snapshot_records_what_the_build_read` below.
    """
    bare = tmp_path / "bare-project"
    bare.mkdir()

    assert brief_snapshot.read_snapshot(str(bare)) is None
    assert list(bare.glob("pipeline_output/provenance/*")) == []


def test_snapshot_records_what_the_build_read(tmp_path):
    """The fix: verbatim bytes plus the binding, at a fixed home."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    report = brief_snapshot.record_brief_for_run(str(project), str(src))

    assert report["snapshotted"] is True
    assert report["source"] == str(src)
    assert report["sha256"] == hashlib.sha256(
        BRIEF_TEXT.encode("utf-8")).hexdigest()

    md_rel, json_rel = brief_snapshot.snapshot_relpaths()
    assert (project / md_rel).read_text(encoding="utf-8") == BRIEF_TEXT
    record = json.loads((project / json_rel).read_text(encoding="utf-8"))
    assert record["source"] == str(src)
    assert record["sha256"] == report["sha256"]
    assert record["bytes"] == len(BRIEF_TEXT.encode("utf-8"))
    assert record["lines"] == BRIEF_TEXT.count("\n") + 1
    assert record["recorded_at"]

    # The lookup answers what the build read, byte for byte.
    found = brief_snapshot.read_snapshot(str(project))
    assert found is not None
    assert found["content"] == BRIEF_TEXT
    assert found["record"]["sha256"] == report["sha256"]


def test_relative_declaration_resolves_against_the_project(tmp_path):
    """A project-relative brief resolves the way the runner reads it."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "creative_brief.md").write_text(BRIEF_TEXT, encoding="utf-8")

    report = brief_snapshot.record_brief_for_run(
        str(project), "creative_brief.md")

    assert report["snapshotted"] is True
    found = brief_snapshot.read_snapshot(str(project))
    assert found is not None
    assert found["content"] == BRIEF_TEXT






def test_edited_brief_supersedes_and_stays_distinct(tmp_path):
    """A later edit to the outside file lands as a new snapshot version."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()

    first = brief_snapshot.record_brief_for_run(str(project), str(src))
    src.write_text(BRIEF_TEXT + "\n## New rule\n\nShout once.\n",
                   encoding="utf-8")
    second = brief_snapshot.record_brief_for_run(str(project), str(src))

    assert second["snapshotted"] is True
    assert second.get("unchanged") is not True
    assert second["sha256"] != first["sha256"]
    assert brief_snapshot.read_snapshot(str(project))["content"] == (
        src.read_text(encoding="utf-8"))


def test_no_brief_clears_a_stale_snapshot(tmp_path):
    """A run that reads no brief must not keep claiming yesterday's."""
    src = _outside_brief(tmp_path)
    project = tmp_path / "project"
    project.mkdir()
    assert brief_snapshot.record_brief_for_run(
        str(project), str(src))["snapshotted"] is True

    # The next run declines the brief: load_pipeline_state leaves
    # state["creative_brief"] unset, so the hook passes "".
    report = brief_snapshot.record_brief_for_run(str(project), "")

    assert report["snapshotted"] is False
    assert sorted(report["cleared"]) == sorted(
        [p.split("/")[-1] for p in brief_snapshot.snapshot_relpaths()])
    assert brief_snapshot.read_snapshot(str(project)) is None
    md_rel, json_rel = brief_snapshot.snapshot_relpaths()
    assert not (project / md_rel).exists()
    assert not (project / json_rel).exists()




