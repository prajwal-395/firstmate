"""RUNS: archive last-write-wins directories at the start of each run.

Part of the version model (`library/tools/versions/__init__.py`): a run
is the version of what a pipeline invocation was told and answered.

Three directories under ``pipeline_output/`` are last-write-wins per step
with nothing marking a run boundary:

- ``llm_requests/``   - the prompt each hybrid/LLM step was handed
- ``llm_responses/``  - what the model returned
- ``reasoning/``      - the agent's reasoning trace (what it read, rejected,
  its confidence, and what the pipeline then did with the answer)

A second run silently replaces all three, so the first run's archive
and traces are lost.  This module copies them into a timestamped
subdirectory of ``pipeline_output/run_archives/<run_id>/`` at the
start of a run, before any step has a chance to overwrite them.

The design follows the ``backup_pipeline_data`` pattern in
``project_layout.py``: one call per run process, bounded only by the
number of runs (we do not prune, because traces are small and any
pruning policy is a creative/operational decision).

The runner calls ``archive_previous_run`` once, after the run_id is
minted but before any step is executed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout

# The three last-write-wins directories and the Area they live under.
# Each entry is (Area enum, subdirectory name in the archive).
_ARCHIVABLE = (
    (Area.LLM_REQUESTS, "llm_requests"),
    (Area.LLM_RESPONSES, "llm_responses"),
    (Area.REASONING, "reasoning"),
)


def archive_previous_run(
    project_folder: str,
    run_id: str,
) -> Path | None:
    """Copy any existing last-write-wins files into a per-run archive.

    Returns the archive directory if anything was copied, or ``None``
    when all three source directories were empty or absent.

    The archive is written to
    ``<project>/pipeline_output/run_archives/<run_id>/``.
    A directory that already exists under that run_id is left alone
    (idempotent on retry).
    """
    layout = ProjectLayout(project_folder)
    archive_root = layout.write_path(Area.RUN_ARCHIVES, run_id)

    if archive_root.exists():
        # Already archived under this run_id (retry / resumed run).
        return archive_root

    copied_anything = False

    for area, subdir in _ARCHIVABLE:
        src = layout.read_dir(area)
        if not src.is_dir():
            continue
        files = [p for p in src.iterdir() if p.is_file()]
        if not files:
            continue
        dst = archive_root / subdir
        dst.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(f, dst / f.name)
        copied_anything = True

    if copied_anything:
        return archive_root
    return None
