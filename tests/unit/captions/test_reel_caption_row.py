"""One reel's caption row, falling back to the project value.

The caption row was one project-level fraction
(`pipeline.subtitle_position.caption_row`): a reel that needed its
own band had nowhere to say it. `external/reel_caption_row.json`
(`library/tools/reel_caption_row.py`) is the per-reel override,
preferred over the project value wherever the row is read and falling
back to it where the reel declares none.

Fail-before: `library.tools.reel_caption_row` has no `declared_row`
and `project_caption_row` takes no reel - every test here errors on
the attribute or the argument, not on an assertion.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import reel_caption_row
from library.tools.subtitle_style import project_caption_row


REEL = "Reel 01 - the-cta"
ROW = 0.72
PROJECT_ROW = 0.83
REASON = "captain: clears the lower third on this reel"


def _row(reel=REEL, row=ROW, reason=REASON):
    return {"reel": reel, "caption_row": row, "reason": reason}


def _project(tmp_path, pipeline_block=None):
    project = tmp_path / "project"
    project.mkdir()
    body = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        body["pipeline"] = pipeline_block
    (project / "project.yaml").write_text(yaml.safe_dump(body),
                                          encoding="utf-8")
    (project / "external").mkdir()
    return project


def _write_rows(project, body):
    (project / "external" / "reel_caption_row.json").write_text(
        json.dumps(body), encoding="utf-8")


# ── 1. The declaration validates, loudly ─────────────────────────────


def test_the_declaration_validates_loudly(tmp_path):
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(row=1385)])  # a pixel row
    project = _project(tmp_path)
    assert reel_caption_row.load_rows(str(project)) == []  # no file
    _write_rows(project, {"version": 1, "rows": [{"reel": REEL}]})
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.load_rows(str(project))


def test_a_reel_row_wins_over_the_project_row_through_a_staging_suffix(
        tmp_path):
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(str(project), REEL) == ROW
    assert project_caption_row(str(project), reel_name=REEL) == ROW
    assert reel_caption_row.declared_row(
        str(project), REEL + " (scratch 7) (rebuild staging)") == ROW


def test_everything_else_reads_the_project_row(tmp_path):
    """An undeclared reel, no reel name, and no override file at all
    each read the project value exactly."""
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    assert project_caption_row(str(project), reel_name=REEL) == PROJECT_ROW
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(
        str(project), "Reel 02 - something-else") is None
    assert project_caption_row(
        str(project), reel_name="Reel 02 - something-else") == PROJECT_ROW
    assert project_caption_row(str(project)) == PROJECT_ROW
