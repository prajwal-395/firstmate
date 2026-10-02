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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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


def test_a_reel_answers_only_its_own_entry(tmp_path):
    assert load_authorizations(str(tmp_path / "absent")) == {}
    project = _write(tmp_path, {"authorizations": [_entry()]})
    auths = load_authorizations(project)
    assert authorized_for(auths, 28)["reason"].startswith("captain")
    assert authorized_for(auths, 10) == {}
    assert authorized_for(auths, "28")["reel"] == 28


def test_a_malformed_ruling_file_refuses(tmp_path):
    """No reason, no measured seconds, a double ruling on one reel, and
    a file that is not JSON each refuse."""
    no_seconds = _entry()
    del no_seconds["measured_tail_end"]
    payloads = [
        {"authorizations": [_entry(reason=" ")]},
        {"authorizations": [no_seconds]},
        {"authorizations": [_entry(), _entry()]},
        "{not json",
    ]
    for i, payload in enumerate(payloads):
        project = tmp_path / f"p{i}" / "project"
        (project / "external").mkdir(parents=True)
        (project / "external" / "tail_extend_authorizations.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8")
        with pytest.raises(AuthorizationError):
            load_authorizations(str(project))


def test_the_applied_seconds_are_weighed_against_the_ruling():
    entry = _entry()
    check_applied(28, entry, 2287.14, 0.02)
    check_applied(28, entry, 2287.5, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2300.0, 0.02)
    with pytest.raises(AuthorizationError):
        check_applied(28, entry, 2200.0, 0.02)
