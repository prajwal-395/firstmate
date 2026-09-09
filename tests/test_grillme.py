"""grillme: the interactive interview that fills the model's gaps.

`briefing_interview.py` is the interview that is COLLECTED (the pipeline
is not interactive, so steps write questions and the captain answers by
extending the brief). grillme is its interactive counterpart: conducted
WITH the captain, OUTSIDE any pipeline run, as a skill. The two must not
become two spellings of the same thing - one collects for later, the
other conducts now.

What makes grillme different from a fixed questionnaire: it reads what
the project already has first (the brief, the context folder, the
learned context) and asks only about the GAPS. A question the project
already answers is never asked. These tests pin that property on the
deterministic half - coverage and gaps - which the skill runs before it
opens its mouth.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _project(tmp_path, brief=None, context_files=None, learnings=None):
    if brief is not None:
        (tmp_path / "brief.md").write_text(brief, encoding="utf-8")
        (tmp_path / "project.yaml").write_text(
            f"creative_brief: {tmp_path / 'brief.md'}\n", encoding="utf-8")
    else:
        (tmp_path / "project.yaml").write_text("{}\n", encoding="utf-8")
    if context_files:
        (tmp_path / "context").mkdir(exist_ok=True)
        for name, body in context_files.items():
            (tmp_path / "context" / name).write_text(
                body, encoding="utf-8")
    if learnings:
        from library.tools import learned_context
        for l in learnings:
            learned_context.record(str(tmp_path), **l)
    return str(tmp_path)


MUSIC_BRIEF = (
    "# Channel Brief\n\nA cooking channel.\n\n"
    "## Music & Sound Philosophy\n\n"
    "Music is company, never encouragement: acoustic guitar beds, "
    "no percussion under speech.\n\n"
    "## Series\n\nThis video belongs to the series 'Weeknight Dinners'.\n"
)


def test_what_the_brief_answers_is_not_a_gap(tmp_path):
    from library.tools import grillme
    project = _project(tmp_path, brief=MUSIC_BRIEF)
    gaps = grillme.gaps(project)
    gap_topics = {g["topic"] for g in gaps}
    assert "music" not in gap_topics
    assert "series" not in gap_topics


def test_what_nothing_answers_is_a_gap(tmp_path):
    from library.tools import grillme
    project = _project(tmp_path, brief=MUSIC_BRIEF)
    gaps = grillme.gaps(project)
    gap_topics = {g["topic"] for g in gaps}
    # The brief says nothing about captions, grade, or pacing.
    assert {"captions", "grade", "pacing"} <= gap_topics


def test_a_context_file_covers_its_topic(tmp_path):
    from library.tools import grillme
    project = _project(
        tmp_path, brief=MUSIC_BRIEF,
        context_files={"captions.md": (
            "# Captions\n\nLower third, never centre. "
            "Montserrat, dusk-safe yellow.\n")})
    assert "captions" not in {g["topic"] for g in grillme.gaps(project)}


def test_a_settled_decision_covers_its_topic(tmp_path):
    from library.tools import grillme
    project = _project(
        tmp_path, brief=MUSIC_BRIEF,
        learnings=[{"kind": "settled_decision",
                    "statement": "Pacing: hold food close-ups four beats.",
                    "read_by": ["mesh_spine"]}])
    assert "pacing" not in {g["topic"] for g in grillme.gaps(project)}


def test_coverage_names_its_evidence(tmp_path):
    from library.tools import grillme
    project = _project(tmp_path, brief=MUSIC_BRIEF)
    covered = grillme.coverage(project)
    assert covered["music"]["covered"] is True
    assert covered["music"]["evidence"], (
        "covered with no evidence is a claim, not a reading")
    assert covered["captions"]["covered"] is False


def test_an_empty_project_has_every_gap_and_says_so(tmp_path):
    from library.tools import grillme
    project = _project(tmp_path)
    gaps = grillme.gaps(project)
    assert {g["topic"] for g in gaps} == set(grillme.GRILLME_TOPICS)
    for g in gaps:
        assert g["why_it_matters"], (
            "a gap with no reason is a questionnaire item, not a gap")
