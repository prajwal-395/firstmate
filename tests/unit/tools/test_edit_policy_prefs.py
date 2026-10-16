"""EDIT-012: editorial preferences configurable rather than hardcoded.

`edit_policy` in style.yaml/video.yaml declares the thresholds the EDIT
modules read; a locked project value refuses video overrides by name.
Synthetic projects under `tmp_path`; nothing reaches a real project.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from library.tools import edit_candidates as EC
from library.tools import video_prefs as V


def _project(tmp_path, style_body=None, video_body=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    if style_body is not None:
        (tmp_path / "style.yaml").write_text(
            textwrap.dedent(style_body), encoding="utf-8")
    if video_body is not None:
        (tmp_path / "video.yaml").write_text(
            textwrap.dedent(video_body), encoding="utf-8")
    return str(tmp_path)


def test_undeclared_project_has_no_edit_policy(tmp_path):
    folder = _project(tmp_path, "style: calm\n")
    assert V.load_video_preferences(folder)["style"] == "calm"
    assert V.effective_edit_policy(folder) is None


def test_edit_policy_round_trips_and_reads(tmp_path):
    folder = _project(
        tmp_path,
        """\
        edit_policy:
          min_phrase_seconds: 1.5
          max_qa_gap_seconds: 12
          min_pause_seconds: 0.15
          tech_reject: report
          candidate_policy: earliest
          require_complete_exchange: true
        """)
    policy = V.effective_edit_policy(folder)
    assert policy == {
        "min_phrase_seconds": 1.5,
        "max_qa_gap_seconds": 12.0,
        "min_pause_seconds": 0.15,
        "tech_reject": "report",
        "candidate_policy": "earliest",
        "require_complete_exchange": True,
    }


def test_locked_edit_policy_refuses_video_override(tmp_path):
    folder = _project(
        tmp_path,
        """\
        edit_policy:
          tech_reject: refuse
        locked:
          - edit_policy
        """,
        """\
        edit_policy:
          tech_reject: "off"
        """)
    with pytest.raises(V.LockedVideoPreferenceError):
        V.load_video_preferences(folder)


def test_bad_edit_policy_values_refuse_by_name(tmp_path):
    folder = _project(
        tmp_path,
        """\
        edit_policy:
          tech_reject: cinematic
        """)
    with pytest.raises(V.VideoPreferencesError):
        V.load_video_preferences(folder)
    folder = _project(
        tmp_path,
        """\
        edit_policy:
          min_pause_seconds: -1
        """)
    with pytest.raises(V.VideoPreferencesError):
        V.load_video_preferences(Path(folder))


def test_edit_policy_mirrors_candidate_policies():
    assert tuple(V.EDIT_CANDIDATE_POLICIES) == tuple(EC.CANDIDATE_POLICIES)
