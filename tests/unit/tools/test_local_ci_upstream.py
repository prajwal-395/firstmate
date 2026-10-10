from __future__ import annotations

import json

import pytest

from library.tools.local_ci import upstream


def test_weekly_schedule_uses_iso_week_and_retries_failed_after_backoff(
        tmp_path):
    monday = 1791244800.0  # 2026-10-05 UTC
    assert upstream.week_key(monday) == "2026-W41"
    assert upstream.due(str(tmp_path), monday)["intake"] is True

    token = upstream.begin_weekly(str(tmp_path), monday)
    path = tmp_path / "upstream-intake.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["weekly"]["2026-W41"].update({"status": "failed", "failed_at": monday})
    path.write_text(json.dumps(state), encoding="utf-8")

    assert upstream.due(str(tmp_path), monday + 5 * 60 * 60)["intake"] is False
    assert upstream.due(str(tmp_path), monday + 6 * 60 * 60)["intake"] is True
    assert upstream.begin_weekly(str(tmp_path), monday + 6 * 60 * 60) == token


def test_completed_week_is_not_repeated(tmp_path):
    now = 1791244800.0
    token = upstream.begin_weekly(str(tmp_path), now)
    upstream.finish_weekly(token, {"status": "no_candidates"}, str(tmp_path), now)
    assert upstream.due(str(tmp_path), now + 30)["intake"] is False


def test_pin_manifest_requires_full_immutable_shas():
    manifest = upstream._manifest()
    assert set(manifest["engines"]) == {"filmcraft", "effectcraft"}
    for engine in manifest["engines"].values():
        assert len(engine["qualified_sha"]) == 40
        assert len(engine["last_seen_upstream_sha"]) == 40


def test_manifest_mirrors_driver_crate_pins():
    manifest = upstream._manifest()
    pins = upstream.crate_pins()
    assert "filmcraft" in pins
    assert upstream.assert_pins_match_crates(manifest) == pins
    for name, pin in pins.items():
        assert manifest["engines"][name]["upstream_url"] == pin["upstream_url"]
        assert manifest["engines"][name]["qualified_sha"] == pin["qualified_sha"]


def test_manifest_drift_from_crate_pin_refuses():
    manifest = upstream._manifest()
    manifest["engines"]["filmcraft"]["qualified_sha"] = "0" * 40
    with pytest.raises(RuntimeError):
        upstream.assert_pins_match_crates(manifest)


def test_qualify_with_no_candidates_builds_nothing(tmp_path):
    result = upstream.qualify(str(tmp_path))
    assert result["status"] == "no_candidates"


def test_daily_poll_records_new_commit_and_release_without_moving_pin(
        monkeypatch, tmp_path):
    head = "a" * 40
    monkeypatch.setattr(upstream, "_remote_observation", lambda _url: {
        "head_sha": head, "default_branch_ref": "refs/heads/main",
        "tags": [(head, "v9.9.9")],
    })

    upstream.poll(str(tmp_path))
    state = json.loads((tmp_path / "upstream-intake.json").read_text(
        encoding="utf-8"))
    engine = state["engines"]["filmcraft"]
    assert engine["last_seen_upstream_sha"] == head
    assert engine["candidate_sha"] == head
    assert engine["observations"][0]["new_releases"] == ["v9.9.9"]
    manifest = upstream._manifest()
    assert upstream.pin_status(str(tmp_path))["filmcraft"]["qualified_sha"] == (
        manifest["engines"]["filmcraft"]["qualified_sha"])
    upstream.poll(str(tmp_path))
    state = json.loads((tmp_path / "upstream-intake.json").read_text(
        encoding="utf-8"))
    assert len(state["engines"]["filmcraft"]["observations"]) == 1


def test_expedited_request_marks_candidate_for_same_weekly_gate(
        monkeypatch, tmp_path):
    token = upstream.begin_weekly(str(tmp_path))
    upstream.finish_weekly(token, {"status": "no_candidates"}, str(tmp_path))
    head = "b" * 40
    monkeypatch.setattr(upstream, "_remote_observation", lambda _url: {
        "head_sha": head, "default_branch_ref": "refs/heads/main", "tags": [],
    })
    request = upstream.request_expedited(
        "effectcraft", "critical render corruption", str(tmp_path))
    assert request["candidate_sha"] == head
    assert upstream.due(str(tmp_path))["intake"] is True
