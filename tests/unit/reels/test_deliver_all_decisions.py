"""T16, unit half: the skip is fail-closed, platforms share one master,
and the declaration is recorded once.

Each test names the defect it pins: a skip that fires without a
positive match (a reel silently dropped), a second render per
identical-spec platform (the pmk defect), and a preset/naming answer
asked on every delivery instead of recorded once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _bare_project(tmp_path, pipeline_block=""):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        "name: Test\nslug: test\nproject_format_version: 1\n"
        f"{pipeline_block}",
        encoding="utf-8")
    return str(root)


def _identity(**overrides):
    base = {"timeline_name": "Reel 01 - pricing",
            "settings": {"format": "mp4", "codec": "H264",
                         "width": 1080, "height": 1920,
                         "naming": "{timeline}.{ext}", "extension": "mp4",
                         "output_transform": "Rec.709",
                         "preset_declared": False,
                         "naming_declared": False},
            "spec": {"platform": "tiktok", "width": 1080, "height": 1920},
            "proposal_digest": "plan-sha",
            "assignment_reel": {"number": 1, "name": "Reel 01 - pricing",
                                "built_at": "2026-10-11T00:00:00"}}
    base.update(overrides)
    return base


def _recorded_ledger(project: str, timeline: str, output: Path) -> dict:
    from library.tools import deliver_all

    signature = deliver_all.delivery_signature(**_identity())
    return {timeline: {"signature": signature, "output_path": str(output),
                       "size_bytes": output.stat().st_size,
                       "mtime_ns": output.stat().st_mtime_ns,
                       "reel": 1, "assignment_id": "reels-x",
                       "qc_passed": True}}


# ── The skip is fail-closed ──────────────────────────────────────

def test_no_ledger_means_render(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    render, why = deliver_all.needs_render(folder, **_identity())
    assert render is True
    assert "no delivery signature" in why


def test_untracked_work_always_renders(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    render, why = deliver_all.needs_render(
        folder, **_identity(assignment_reel=None, ledger=ledger))
    assert render is True
    assert "untracked" in why


def test_matching_signature_and_file_skips(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    render, why = deliver_all.needs_render(
        folder, **_identity(ledger=ledger))
    assert render is False
    assert "match" in why


def test_changed_settings_render(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    changed = _identity(ledger=ledger)
    changed["settings"] = {**changed["settings"], "codec": "ProRes"}
    render, _ = deliver_all.needs_render(folder, **changed)
    assert render is True


def test_rebuilt_reel_renders(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    rebuilt = _identity(ledger=ledger)
    rebuilt["assignment_reel"] = {**rebuilt["assignment_reel"],
                                  "built_at": "2026-10-12T00:00:00"}
    render, _ = deliver_all.needs_render(folder, **rebuilt)
    assert render is True


def test_delivery_itself_does_not_invalidate_the_signature(tmp_path):
    """The status move signed_off -> delivered must not re-render.

    The signature carries the build identity, not the delivery
    outcome: otherwise every second run rendered, and the acceptance
    check ("a second invocation re-renders nothing") failed.
    """
    from library.tools import deliver_all

    before = deliver_all.delivery_signature(**_identity(
        assignment_reel={"number": 1, "name": "Reel 01 - pricing",
                         "status": "signed_off",
                         "built_at": "2026-10-11T00:00:00"}))
    after = deliver_all.delivery_signature(**_identity(
        assignment_reel={"number": 1, "name": "Reel 01 - pricing",
                         "status": "delivered",
                         "built_at": "2026-10-11T00:00:00",
                         "delivered_at": "2026-10-11T00:01:00",
                         "delivered_approved": True}))
    assert before == after


def test_gone_file_renders(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    output.unlink()
    render, why = deliver_all.needs_render(
        folder, **_identity(ledger=ledger))
    assert render is True
    assert "gone" in why


def test_replaced_file_renders(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"bytes")
    ledger = _recorded_ledger(folder, "Reel 01 - pricing", output)
    output.write_bytes(b"different bytes, same name")
    render, _ = deliver_all.needs_render(
        folder, **_identity(ledger=ledger))
    assert render is True


# ── Platforms ────────────────────────────────────────────────────

def test_default_platform_comes_from_the_project(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    assert deliver_all.resolve_platforms(folder) == ["tiktok"]


def test_project_platform_declaration_wins(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(
        tmp_path, "pipeline:\n  delivery_platform: master\n")
    assert deliver_all.resolve_platforms(folder) == ["master"]


def test_unknown_platform_refuses(tmp_path):
    from library.tools import deliver_all
    from library.tools.reel_deliver import DeliverRefused

    folder = _bare_project(tmp_path)
    with pytest.raises(DeliverRefused, match="unknown delivery"):
        deliver_all.resolve_platforms(folder, extra=("vimeo",))


def test_identical_specs_share_one_job_without_the_set(tmp_path):
    """pmk: three vertical platforms are ONE master, not three renders."""
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    jobs = deliver_all.delivery_jobs(
        ["tiktok", "instagram_reels", "youtube_shorts"])
    assert len(jobs) == 1
    assert list(jobs[0]["serves"]) == ["tiktok", "instagram_reels",
                                      "youtube_shorts"]


def test_differing_specs_plan_the_set(tmp_path):
    from library.tools import deliver_all

    folder = _bare_project(tmp_path)
    jobs = deliver_all.delivery_jobs(["tiktok", "master"])
    assert len(jobs) == 2


# ── Record once ──────────────────────────────────────────────────

def test_answered_preset_and_naming_land_in_project_yaml(tmp_path):
    from library.tools import reel_deliver

    folder = _bare_project(tmp_path)
    recorded = reel_deliver.record_deliver_declaration(
        folder, preset={"format": "mp4", "codec": "H264"},
        naming="{timeline}.{ext}")
    assert recorded == {"preset_recorded": True, "naming_recorded": True}
    declaration = reel_deliver.read_deliver_declaration(folder)
    assert declaration == {"preset": {"format": "mp4", "codec": "H264"},
                           "naming": "{timeline}.{ext}"}
    settings = reel_deliver.resolve_deliver_settings(folder)
    assert settings["preset_declared"] is True
    assert settings["naming_declared"] is True
    assert settings["needs_captain_word"] == []


def test_nothing_to_record_records_nothing(tmp_path):
    from library.tools import reel_deliver

    folder = _bare_project(tmp_path)
    assert reel_deliver.record_deliver_declaration(folder) == {
        "preset_recorded": False, "naming_recorded": False}


def test_unreadable_preset_choice_is_refused_not_recorded(tmp_path):
    from library.tools import reel_deliver

    folder = _bare_project(tmp_path)
    with pytest.raises(reel_deliver.DeliverRefused):
        reel_deliver.record_deliver_declaration(
            folder, preset={"format": "mp4", "container": "mp4"})
    assert reel_deliver.read_deliver_declaration(folder) == {
        "preset": {}, "naming": ""}


def test_reveal_opts_out_by_environment(tmp_path, monkeypatch):
    from library.tools import deliver_all

    monkeypatch.setenv("REN_DELIVER_REVEAL", "0")
    revealed, reason = deliver_all.reveal_in_finder(str(tmp_path))
    assert revealed is False
    assert "opted out" in reason
