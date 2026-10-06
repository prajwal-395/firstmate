"""`deliver-reel` masters to the named platform's spec, not the default.

Covers the mastering half of `reel_deliver.deliver_reel`: the Resolve
render is normalized to the loudness target and true-peak ceiling of
the delivery spec the project's named platform selects - the same
resolved spec the plan (step 5.02) and the gates (step 6.02,
verify_render) read. The defect: `master_render_report` was called
with no targets, so every deliver normalized to the bare default even
when the project named a platform.
"""
from __future__ import annotations

import dataclasses

import pytest
import yaml

from library.tools import master_loudness, reel_build, reel_deliver
from library.tools import resolve_lock
from library.tools.delivery_spec import resolve_delivery_spec
from library.tools.execution import resolve_render


class _Timeline:
    def GetSetting(self, _name):
        return "30"

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 299

    def GetName(self):
        return "Reel 01 - x"


def _project(tmp_path, pipeline_block):
    cfg = {"name": "T", "slug": "t",
           "resolve": {"project_name": "Scratch"},
           "pipeline": pipeline_block}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg))
    return str(tmp_path)


def test_deliver_masters_to_the_named_platforms_targets(
        tmp_path, monkeypatch):
    """The spec's targets reach the mastering call: with a platform spec
    carrying -16 LUFS, the master is normalized to -16, not the -14
    default. (The registry rows agree today, so the test varies the
    resolved spec to pin the FORWARDING - the wiring defect - rather
    than the registry's current values.)"""
    folder = _project(tmp_path, {"delivery_platform": "master"})
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 64)

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(reel_deliver, "timeline_name_for_reel",
                        lambda folder, reel: "Reel 01 - x")
    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda name: object())
    monkeypatch.setattr(resolve_render, "_find_timeline",
                        lambda project, name: _Timeline())
    monkeypatch.setattr(reel_deliver, "overlay_staleness", lambda p, n: [])
    monkeypatch.setattr(resolve_render, "render_timeline",
                        lambda **kwargs: {"output_path": str(raw)})

    distinct = dataclasses.replace(
        resolve_delivery_spec("master"),
        loudness_target_lufs=-16.0, true_peak_ceiling_dbtp=-2.0)
    monkeypatch.setattr(
        "library.tools.delivery_spec.resolve_delivery_spec_for_project",
        lambda folder, templates_dir=None: distinct)

    seen = {}

    def fake_master(report, output_path, target_lufs, true_peak_ceiling):
        seen["target_lufs"] = target_lufs
        seen["true_peak_ceiling"] = true_peak_ceiling
        return {"output_path": output_path}

    monkeypatch.setattr(master_loudness, "master_render_report", fake_master)
    monkeypatch.setattr(reel_deliver, "verify_deliverable", lambda *a: {
        "passed": True, "duration": {"passed": True},
        "resolution": {"passed": True}, "audio": {"passed": True},
        "content_present": True})

    full = reel_deliver.deliver_reel(folder, 1,
                                     output_dir=str(tmp_path / "out"))
    assert full["delivered"] is True
    assert seen == {"target_lufs": -16.0, "true_peak_ceiling": -2.0}


def test_mastering_targets_default_to_the_registry_default(
        tmp_path, monkeypatch):
    """A project that names no platform masters to the registry default -
    naming nothing changes nothing, and the forwarding is still the
    resolved spec rather than a second constant."""
    from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC

    folder = _project(tmp_path, {})
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 64)

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(reel_deliver, "timeline_name_for_reel",
                        lambda folder, reel: "Reel 01 - x")
    monkeypatch.setattr(reel_build, "_connect_resolve_project",
                        lambda name: object())
    monkeypatch.setattr(resolve_render, "_find_timeline",
                        lambda project, name: _Timeline())
    monkeypatch.setattr(reel_deliver, "overlay_staleness", lambda p, n: [])
    monkeypatch.setattr(resolve_render, "render_timeline",
                        lambda **kwargs: {"output_path": str(raw)})

    seen = {}

    def fake_master(report, output_path, target_lufs, true_peak_ceiling):
        seen["target_lufs"] = target_lufs
        seen["true_peak_ceiling"] = true_peak_ceiling
        return {"output_path": output_path}

    monkeypatch.setattr(master_loudness, "master_render_report", fake_master)
    monkeypatch.setattr(reel_deliver, "verify_deliverable", lambda *a: {
        "passed": True, "duration": {"passed": True},
        "resolution": {"passed": True}, "audio": {"passed": True},
        "content_present": True})

    reel_deliver.deliver_reel(folder, 1, output_dir=str(tmp_path / "out"))
    assert seen == {
        "target_lufs": DEFAULT_DELIVERY_SPEC.loudness_target_lufs,
        "true_peak_ceiling": DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp}
