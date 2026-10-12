"""T1: an unfilled scaffold brief is not an attached brief.

A `ren new` project declares `creative_brief: brief.md`, but the file is
still the starter the scaffold wrote - intake answers as facts, the rest
open headings. That must read as NONE_DECLARED (so the run interviews)
until the project writes something of its own into it. A declared path
whose file is missing reads the same way. An explicit
`attach_creative_brief: true` keeps the attached reading.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import brief_attachment as ba
from library.tools import briefing_chat
from library.tools.project_registry import create_project


def _new_project(tmp_path) -> Path:
    create_project("demo", name="Demo", root=tmp_path)
    return tmp_path / "demo"


def test_fresh_scaffold_reads_as_none_declared(tmp_path):
    project = _new_project(tmp_path)
    got = ba.read_declaration(str(project))
    assert got.reading == ba.NONE_DECLARED
    assert got.interview
    assert not got.attached
    assert "unfilled scaffold" in got.basis
    assert briefing_chat.should_interview(got.attached, "agent") is True
    line = ba.describe(got)
    assert "brief.md is the unfilled scaffold, so Ren will ask" in line


def test_one_written_line_makes_it_attached(tmp_path):
    project = _new_project(tmp_path)
    brief = project / "brief.md"
    brief.write_text(
        brief.read_text(encoding="utf-8")
        + "\nWe are making a launch video for new parents.\n",
        encoding="utf-8",
    )
    got = ba.read_declaration(str(project))
    assert got.reading == ba.ATTACHED
    assert not got.interview
    assert briefing_chat.should_interview(got.attached, "agent") is False


def test_missing_brief_file_reads_as_none_declared(tmp_path):
    import yaml

    project = _new_project(tmp_path)
    yaml_path = project / "project.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["pipeline"]["creative_brief"] = "gone.md"
    yaml_path.write_text(yaml.dump(data), encoding="utf-8")
    got = ba.read_declaration(str(project))
    assert got.reading == ba.NONE_DECLARED
    assert got.interview
    assert "missing file" in got.basis
    assert briefing_chat.should_interview(got.attached, "agent") is True


def test_explicit_attach_true_keeps_todays_behaviour(tmp_path):
    import yaml

    project = _new_project(tmp_path)
    yaml_path = project / "project.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    data["pipeline"]["attach_creative_brief"] = True
    yaml_path.write_text(yaml.dump(data), encoding="utf-8")
    got = ba.read_declaration(str(project))
    assert got.reading == ba.ATTACHED


def test_scaffold_writes_the_interview_request_and_a_filled_brief_writes_none(
    tmp_path,
):
    """The runner files `llm_requests/briefing_interview.json` post-catalog
    when the interview is owed (agent backend); a filled brief files none."""

    project = _new_project(tmp_path)
    state = {"project_folder": str(project)}
    out = briefing_chat.conduct_if_needed(
        str(project), dict(state), "agent", llm_timeout=0,
        save=lambda p, s: None)
    assert out.get("brief_answers") == []
    req = (project / "pipeline_output" / "llm_requests"
           / "briefing_interview.json")
    assert req.is_file(), "scaffold project filed no interview request"

    project2 = tmp_path / "filled"
    create_project("filled", name="Filled", root=tmp_path)
    (project2 / "brief.md").write_text(
        (project2 / "brief.md").read_text(encoding="utf-8")
        + "\nWe are making a launch video for new parents.\n",
        encoding="utf-8",
    )
    state2 = {"project_folder": str(project2),
              "creative_brief": "brief.md"}
    out2 = briefing_chat.conduct_if_needed(
        str(project2), dict(state2), "agent", llm_timeout=0,
        save=lambda p, s: None)
    assert out2.get("brief_answers") is None
    req2 = (project2 / "pipeline_output" / "llm_requests"
            / "briefing_interview.json")
    assert not req2.exists(), "filled brief filed an interview request"
