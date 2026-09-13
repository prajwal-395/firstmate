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

REPO_ROOT = Path(__file__).resolve().parent.parent
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

def test_a_row_validates():
    assert reel_caption_row.validate_rows([_row()])[0]["caption_row"] == ROW


def test_a_pixel_row_is_refused():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(row=1385)])


def test_a_zero_row_is_refused():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(row=0.0)])


def test_a_non_numeric_row_is_refused():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(row="low")])


def test_a_rowless_entry_is_refused():
    entry = _row()
    del entry["caption_row"]
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([entry])


def test_a_reasonless_row_is_refused():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(reason="  ")])


def test_two_rows_for_one_reel_refuse():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(), _row(row=0.5)])


def test_a_wrong_version_is_refused():
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.parse_rows({"version": 9, "rows": []})


def test_no_file_is_no_rows(tmp_path):
    assert reel_caption_row.load_rows(str(_project(tmp_path))) == []


def test_a_malformed_file_refuses(tmp_path):
    project = _project(tmp_path)
    _write_rows(project, {"version": 1, "rows": [{"reel": REEL}]})
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.load_rows(str(project))


# ── 2. Reading: override first, project value under it ───────────────

def test_a_reel_row_wins_over_the_project_row(tmp_path):
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(str(project), REEL) == ROW
    assert project_caption_row(str(project), reel_name=REEL) == ROW


def test_a_staging_suffix_reads_the_same_row(tmp_path):
    project = _project(tmp_path)
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(
        str(project), REEL + " (scratch 7) (rebuild staging)") == ROW


def test_an_undeclared_reel_falls_back_to_the_project_row(tmp_path):
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(
        str(project), "Reel 02 - something-else") is None
    assert project_caption_row(
        str(project), reel_name="Reel 02 - something-else") == PROJECT_ROW


def test_no_reel_name_reads_todays_answer_exactly(tmp_path):
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert project_caption_row(str(project)) == PROJECT_ROW


def test_no_declaration_anywhere_is_none(tmp_path):
    project = _project(tmp_path)
    assert project_caption_row(str(project), reel_name=REEL) is None


# ── 3. Survival: the override holds on every rebuild ─────────────────

def test_the_override_holds_on_repeated_reads(tmp_path):
    """The rebuild equivalent: the file the captain wrote is read the
    way each build reads it, twice, and the reel's row is the
    override both times while its sibling keeps the project value."""
    project = _project(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    for _ in range(2):
        assert project_caption_row(str(project), reel_name=REEL) == ROW
        assert project_caption_row(
            str(project),
            reel_name="Reel 02 - something-else") == PROJECT_ROW
        assert project_caption_row(str(project)) == PROJECT_ROW


def test_without_the_override_every_reel_reads_the_project_row():
    """The failing input the survival test above guards: the same
    reads with no override file give every reel the project value -
    which is the inexpressible state this store exists to end."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        project = _project(Path(tmp), {
            "subtitle_position": {"caption_row": PROJECT_ROW,
                                  "reason": "series look"}})
        assert (project_caption_row(str(project), reel_name=REEL)
                == PROJECT_ROW)
