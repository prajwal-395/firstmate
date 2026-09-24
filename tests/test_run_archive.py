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










