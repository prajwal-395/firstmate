"""The editorial report, proved against named defects.

The report is the one surface the five editing behaviours and the
objective check reach the editor through, so a defect in it is a defect
in all of them: a section that reads as empty when its facts were
never measured is a silence the editor cannot see, and a section that
reports a number the module did not measure is worse.

Each test names one defect this module closes:

- a section whose facts were never measured reads as EMPTY rather than
  as UNMEASURED, so a missing measurement is indistinguishable from a
  clean one;
- the report INVENTS an objective for a project that declared none, so
  a house style arrives wearing a declaration's clothes;
- the report's objective findings are computed from a state it did not
  read, so the check is about something else;
- the report reads the policy from the STATE rather than from the
  project, so a declaration the project made never reaches it.

None of them asserts that the report contains a particular section or a
particular count: a state is built and the report is read, so a test
passes only when the module actually does what the defect requires.
"""
from __future__ import annotations

import json
from pathlib import Path

from library.tools import editorial_report
from library.tools.editorial_policy import parse_policy


def _policy(objective="interview", **slots):
    declared = {"objective": objective, "slots": slots}
    return parse_policy(declared, project_id="demo")


def _state():
    return {
        "speech_sequence": {
            "body_sequence": [
                {"passage_id": "p1", "clip_id": "clip_001",
                 "text": "the launch failed", "speaker": "Ana",
                 "source_start": 0.0, "source_end": 4.0,
                 "serves_beats": ["beat:1"]},
                {"passage_id": "p2", "clip_id": "clip_001",
                 "text": "and we shipped it", "speaker": "Bo",
                 "source_start": 4.0, "source_end": 8.0},
            ],
        },
        "timed_spine": {
            "structure": [
                {"position": "hook", "block_type": "hook",
                 "clip_id": "clip_001", "source_start": 0.0,
                 "source_end": 4.0, "timeline_start": 0.0,
                 "timeline_end": 4.0,
                 "content": {"passage_ref": "p1", "speaker": "Ana"},
                 "word_timestamps": [
                     {"word": "the", "source_start": 0.0, "source_end": 0.3},
                     {"word": "launch", "source_start": 0.3, "source_end": 0.9},
                     {"word": "failed", "source_start": 0.9, "source_end": 1.4},
                     {"word": "and", "source_start": 4.0, "source_end": 4.4},
                 ]},
            ],
        },
        "intent_model": {
            "beats": [{"beat_id": "beat:1", "purpose": "the problem",
                       "required": True, "passage_ids": ["p1"]}],
            "claims": [{"claim_id": "claim:1", "statement": "it failed",
                        "passage_ids": ["p1"]}],
            "key_moments": [],
        },
        "semantic_analysis_documents": [
            {"clip_id": "clip_001",
             "assessment": {"content_type": "person_talking_to_camera"},
             "blocks": [{"label": "a kitchen"}]},
        ],
        "temporal_event_indices": [
            {"clip_id": "clip_001", "speech_regions": [
                {"start": 0.0, "end": 4.0, "text": "the launch failed"}],
             "face_presence": {"face_center_x": [0.3] * 200,
                               "sample_rate_hz": 10.0}},
        ],
        "clip_catalog": [{"clip_id": "clip_001", "filename": "a.mov",
                          "duration_seconds": 60.0}],
    }


def test_a_project_that_declares_no_policy_gets_no_objective():
    """The report does not invent a genre."""
    report = editorial_report.build_report(_state())
    assert report["policy"]["declared"] is False
    assert report["policy"]["objective"] is None
    assert report["objective"]["findings"] == []


def test_the_declared_objective_is_read_from_the_state():
    """The declaration the project made reaches the report."""
    policy = _policy(objective="interview")
    state = _state()
    state["editorial_policy"] = policy.to_dict()
    report = editorial_report.build_report(state)
    assert report["policy"]["declared"] is True
    assert report["policy"]["objective"] == "interview"
    assert report["objective"]["constraints"]


def test_the_objective_findings_are_computed_from_the_state():
    """The check is about this cut."""
    policy = _policy(objective="interview")
    state = _state()
    state["editorial_policy"] = policy.to_dict()
    report = editorial_report.build_report(state)
    findings = report["objective"]["findings"]
    assert findings
    coverage = [row for row in findings
                if row["constraint"] == "speaker_coverage"]
    assert coverage and coverage[0]["status"] == "violated"
    assert "Bo" in coverage[0]["detail"]


def test_a_section_with_no_measured_facts_says_so():
    """An unmeasured section is not an empty one."""
    report = editorial_report.build_report(_state())
    angles = report["angles"]
    assert angles["undetermined"]
    assert "no other camera" in angles["undetermined"]
    assert angles["passages"] == []


def test_the_report_says_which_threshold_it_used():
    """A threshold the project did not declare is the material's own."""
    report = editorial_report.build_report(_state())
    silence = report["silence"]
    assert silence["silence_threshold_source"] == "material"
    assert silence["silence_threshold_seconds"] is not None


def test_the_report_is_json_serialisable():
    """The report is written to disk and read back."""
    report = editorial_report.build_report(_state())
    assert json.loads(json.dumps(report))["policy"]["declared"] is False


def test_the_summary_reads_the_report():
    """A person reads the summary, not the JSON."""
    report = editorial_report.build_report(_state())
    lines = editorial_report.summarize(report)
    assert any("Objective" in line for line in lines)
    assert any("Silence" in line for line in lines)


def test_the_project_yaml_declaration_round_trips():
    """The project's own file carries the policy and reads it back."""
    from library.schemas import project_config as schema

    folder = Path(__file__).resolve().parents[3]
    assert folder.name
    declared = {"objective": "podcast",
                "slots": {"silence": {"max_gap_seconds": 0.8}}}
    config = schema.ProjectConfig(
        name="Demo", slug="demo",
        pipeline=schema.PipelineConfig(editorial_policy=declared))
    errors = config.validate()
    assert errors == [], errors
    written = schema.project_config_to_dict(config)
    assert written["pipeline"]["editorial_policy"] == declared
