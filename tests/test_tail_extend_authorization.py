"""The captain's standing yes to one reported tail extension.

The carve-out (`reel_build._analyze_tail_edge` returning `report`
where a tail runs longer than the closing thought) is deliberate:
silently lengthening an accepted reel was the failure mode to avoid.
This file tests the narrow answer the captain has since given for one
reel - the ruling travels as data, applies to its own reel only, still
reports everywhere else, and refuses where the seconds moved since the
ruling was made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools.tail_extend_authorization import (
    AuthorizationError,
    authorized_for,
    check_applied,
    load_authorizations,
)


def _write(path: Path, payload) -> str:
    project = path / "project"
    (project / "external").mkdir(parents=True)
    (project / "external" / "tail_extend_authorizations.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return str(project)


def _entry(**overrides):
    base = {"reel": 28,
            "reason": "captain 2026-09-21: extend it",
            "measured_edge": 2265.6,
            "measured_tail_end": 2287.14,
            "measured_gap": 0.02}
    base.update(overrides)
    return base


def test_no_file_is_no_authorisation(tmp_path):
    assert load_authorizations(str(tmp_path / "absent")) == {}


def test_a_recorded_ruling_loads_keyed_by_reel(tmp_path):
    project = _write(tmp_path, {"authorizations": [_entry()]})
    auths = load_authorizations(project)
    assert auths[28]["reason"] == "captain 2026-09-21: extend it"
    assert auths[28]["measured_tail_end"] == 2287.14


def test_a_reel_answers_only_its_own_entry(tmp_path):
    project = _write(tmp_path, {"authorizations": [_entry()]})
    auths = load_authorizations(project)
    assert authorized_for(auths, 28)["reason"].startswith("captain")
    assert authorized_for(auths, 10) == {}
    assert authorized_for(auths, "28")["reel"] == 28


def test_a_ruling_without_a_reason_refuses(tmp_path):
    project = _write(tmp_path, {"authorizations": [_entry(reason=" ")]})
    with pytest.raises(AuthorizationError):
        load_authorizations(project)


def test_a_ruling_without_measured_seconds_refuses(tmp_path):
    payload = _entry()
    del payload["measured_tail_end"]
    project = _write(tmp_path, {"authorizations": [payload]})
    with pytest.raises(AuthorizationError):
        load_authorizations(project)


def test_a_garbled_file_refuses(tmp_path):
    project = Path(tmp_path) / "project"
    (project / "external").mkdir(parents=True)
    (project / "external" / "tail_extend_authorizations.json").write_text(
        "{not json", encoding="utf-8")
    with pytest.raises(AuthorizationError):
        load_authorizations(str(project))


def test_a_double_ruling_on_one_reel_refuses(tmp_path):
    project = _write(tmp_path, {"authorizations": [_entry(), _entry()]})
    with pytest.raises(AuthorizationError):
        load_authorizations(project)


def test_the_applied_seconds_are_weighed_against_the_ruling():
    entry = _entry()
    check_applied(28, entry, 2287.14, 0.02)
    check_applied(28, entry, 2287.5, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2300.0, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2200.0, 0.02)
