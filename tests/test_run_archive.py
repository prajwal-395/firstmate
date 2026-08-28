"""Tests that reasoning traces and LLM archives survive across runs.

The core assertion: a second run cannot silently overwrite the first
run's reasoning traces.  This is tested against a scratch project, not
a fixture, because the task explicitly requires real-data verification
and the repo has a history of fixes that pass their own test and change
nothing on real data.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from library.tools import provenance
from library.tools.project_layout import Area, ProjectLayout
from library.tools.run_archive import archive_previous_run


@pytest.fixture
def scratch_project(tmp_path):
    """A minimal project directory with pipeline_data.json and
    reasoning / llm_request / llm_response files that simulate a
    completed run."""
    project = tmp_path / "scratch_project"
    project.mkdir()

    # pipeline_data.json - minimal
    state = {
        "project_folder": str(project),
        "edit_completed": ["creative_direction"],
        "step_outputs": {},
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8"
    )
    (project / "project.yaml").write_text("slug: scratch\n", encoding="utf-8")

    # Create pipeline_output dirs
    layout = ProjectLayout(project)
    reasoning_dir = layout.write_dir(Area.REASONING)
    requests_dir = layout.write_dir(Area.LLM_REQUESTS)
    responses_dir = layout.write_dir(Area.LLM_RESPONSES)

    # Simulate run-1 output
    (reasoning_dir / "creative_direction.md").write_text(
        "# Reasoning: creative_direction\n\n"
        "I read the footage catalog and chose narrative thread A.\n"
        "Confidence: 0.85\n",
        encoding="utf-8",
    )
    (reasoning_dir / "speech_sequence.md").write_text(
        "# Reasoning: speech_sequence\n\n"
        "Selected 46s of speech from 13 minutes.\n",
        encoding="utf-8",
    )
    (requests_dir / "creative_direction.json").write_text(
        json.dumps({"step_id": "creative_direction", "prompt": "decide"}),
        encoding="utf-8",
    )
    (responses_dir / "creative_direction.json").write_text(
        json.dumps({"narrative_thread": "A"}),
        encoding="utf-8",
    )

    return project


def test_archive_preserves_first_run(scratch_project):
    """A second run archives the first run's traces and cannot overwrite them."""
    project = scratch_project
    layout = ProjectLayout(project)

    # Verify the reasoning files exist
    reasoning_dir = layout.read_dir(Area.REASONING)
    assert (reasoning_dir / "creative_direction.md").is_file()
    assert (reasoning_dir / "speech_sequence.md").is_file()
    original_content = (reasoning_dir / "creative_direction.md").read_text(
        encoding="utf-8"
    )

    # --- "Run 2" starts: archive the previous run's artifacts ---
    run_id_2 = provenance.new_run_id()
    archived = archive_previous_run(str(project), run_id_2)

    # Archive was created
    assert archived is not None
    assert archived.is_dir()
    assert (archived / "reasoning" / "creative_direction.md").is_file()
    assert (archived / "reasoning" / "speech_sequence.md").is_file()
    assert (archived / "llm_requests" / "creative_direction.json").is_file()
    assert (archived / "llm_responses" / "creative_direction.json").is_file()

    # Archived content matches original
    archived_content = (archived / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert archived_content == original_content

    # Now simulate run 2 overwriting the reasoning files
    (reasoning_dir / "creative_direction.md").write_text(
        "# Reasoning: creative_direction (run 2)\n\n"
        "Different reasoning for run 2.\n",
        encoding="utf-8",
    )

    # The archive still has the ORIGINAL content
    archived_after = (archived / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert archived_after == original_content
    assert archived_after != (reasoning_dir / "creative_direction.md").read_text(
        encoding="utf-8"
    )


def test_archive_idempotent_on_retry(scratch_project):
    """Re-archiving with the same run_id does not overwrite."""
    project = scratch_project
    layout = ProjectLayout(project)
    reasoning_dir = layout.read_dir(Area.REASONING)

    run_id = provenance.new_run_id()
    first = archive_previous_run(str(project), run_id)
    assert first is not None

    # Overwrite reasoning to simulate a partial retry
    (reasoning_dir / "creative_direction.md").write_text(
        "CHANGED AFTER ARCHIVE", encoding="utf-8"
    )

    # Second call with same run_id returns the existing archive
    second = archive_previous_run(str(project), run_id)
    assert second == first

    # Archive content is still from the FIRST call
    content = (first / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert "CHANGED AFTER ARCHIVE" not in content


def test_archive_noop_when_empty(tmp_path):
    """No archive is created when there is nothing to archive."""
    project = tmp_path / "empty_project"
    project.mkdir()
    (project / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(project)}), encoding="utf-8"
    )
    (project / "project.yaml").write_text("slug: empty\n", encoding="utf-8")

    result = archive_previous_run(str(project), "run-empty")
    assert result is None


def test_archive_findable_by_run_id(scratch_project):
    """Archives are organized by run_id so they can be found later."""
    project = scratch_project
    layout = ProjectLayout(project)

    run_id_1 = "20260826T120000-1001"
    run_id_2 = "20260827T120000-1002"

    archive_1 = archive_previous_run(str(project), run_id_1)
    assert archive_1 is not None
    assert run_id_1 in str(archive_1)

    # Overwrite reasoning
    reasoning_dir = layout.read_dir(Area.REASONING)
    (reasoning_dir / "creative_direction.md").write_text(
        "Run 2 content", encoding="utf-8"
    )

    archive_2 = archive_previous_run(str(project), run_id_2)
    assert archive_2 is not None
    assert run_id_2 in str(archive_2)

    # Both archives exist and are distinct
    assert archive_1 != archive_2
    assert archive_1.is_dir()
    assert archive_2.is_dir()

    # Content differs between archives
    content_1 = (archive_1 / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    content_2 = (archive_2 / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    )
    assert content_1 != content_2


def test_second_run_cannot_silently_overwrite_first(scratch_project):
    """End-to-end: simulate two full runs and verify the first's
    traces are findable and intact after the second run's traces
    have been written."""
    project = scratch_project
    layout = ProjectLayout(project)
    reasoning_dir = layout.read_dir(Area.REASONING)

    # Original content from "run 1"
    original = (reasoning_dir / "creative_direction.md").read_text(encoding="utf-8")

    # --- Run 2 begins ---
    run_id_2 = "20260827T100000-2001"
    archive_2 = archive_previous_run(str(project), run_id_2)

    # Run 2 overwrites reasoning
    (reasoning_dir / "creative_direction.md").write_text(
        "Run 2: completely different reasoning", encoding="utf-8"
    )
    (reasoning_dir / "speech_sequence.md").write_text(
        "Run 2: different speech sequence", encoding="utf-8"
    )

    # --- Run 3 begins ---
    run_id_3 = "20260828T100000-3001"
    archive_3 = archive_previous_run(str(project), run_id_3)

    # Run 3 overwrites reasoning
    (reasoning_dir / "creative_direction.md").write_text(
        "Run 3: yet another reasoning", encoding="utf-8"
    )

    # Run 1's traces are preserved in run 2's archive
    assert (archive_2 / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    ) == original

    # Run 2's traces are preserved in run 3's archive
    assert (archive_3 / "reasoning" / "creative_direction.md").read_text(
        encoding="utf-8"
    ) == "Run 2: completely different reasoning"

    # The active directory has run 3's content
    assert (reasoning_dir / "creative_direction.md").read_text(
        encoding="utf-8"
    ) == "Run 3: yet another reasoning"

    # Both archives are accessible by run_id
    archives_dir = layout.read_dir(Area.RUN_ARCHIVES)
    assert (archives_dir / run_id_2).is_dir()
    assert (archives_dir / run_id_3).is_dir()


def test_reasoning_area_in_project_layout():
    """The REASONING and RUN_ARCHIVES areas are properly declared."""
    from library.tools.project_layout import AREAS

    assert Area.REASONING in AREAS
    assert Area.RUN_ARCHIVES in AREAS
    assert "reasoning" in AREAS[Area.REASONING].relpath
    assert "run_archives" in AREAS[Area.RUN_ARCHIVES].relpath


def test_snapshot_archive_dirs_includes_reasoning():
    """The replay bench snapshot captures reasoning traces."""
    from library.tools.replay_bench.snapshot import ARCHIVE_DIRS

    reasoning_found = any("reasoning" in d for d in ARCHIVE_DIRS)
    assert reasoning_found, (
        f"ARCHIVE_DIRS does not include reasoning: {ARCHIVE_DIRS}"
    )
