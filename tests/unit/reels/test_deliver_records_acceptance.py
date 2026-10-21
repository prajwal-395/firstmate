"""A DELIVERED reel records its OWN acceptance.

Defect (2026-10-21): `reel_deliver.deliver_reel` recorded the delivery's
acceptance from `report["output_path"]` - a name that is not bound in
that scope. The NameError was swallowed by the block's own `except`, so
every delivery printed "could not record its acceptance" and carried on:
the generation the captain TOOK was not protected, and a later promotion
could replace the accepted look without a word from him. That is the
Reel 26 audit §2.1 defect, reopened by a rename.

The same line failed the repository's own lint gate (F821, an undefined
name), which is how the rename was found: a gate that cannot fail is
worse than no gate, and this one could.

Nothing here reaches Resolve or a real project.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from library.tools import reel_acceptance, reel_deliver, resolve_lock
from tests.resolve_double import FakeTimeline, make_project

TIMELINE_NAME = "Reel 26 - write-for-the-question-your-customer-ask"


def _project(tmp_path) -> str:
    folder = tmp_path / "project"
    folder.mkdir()
    cfg = {"name": "T", "slug": "t",
           "resolve": {"project_name": "Scratch"},
           "pipeline": {}}
    (folder / "project.yaml").write_text(yaml.safe_dump(cfg),
                                          encoding="utf-8")
    (folder / "pipeline_output" / "review").mkdir(parents=True)
    return str(folder)


@pytest.fixture()
def delivered(tmp_path, monkeypatch):
    """A delivery whose render, verification and retention are pinned."""
    folder = _project(tmp_path)
    timeline = FakeTimeline(TIMELINE_NAME, start_frame=0, end_frame=299,
                            frame_rate=30)
    project = make_project(name="Scratch", timelines=[timeline],
                           current=timeline, frame_rate=30)

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(resolve_lock, "_broker_turn", lambda *a, **k: _none())
    monkeypatch.setattr(reel_deliver, "check_render", list)
    monkeypatch.setattr(reel_deliver, "resolve_deliver_settings",
                        lambda folder: {"naming": "{timeline}",
                                        "extension": "mp4"})
    monkeypatch.setattr(reel_deliver, "timeline_name_for_reel",
                        lambda folder, reel: TIMELINE_NAME)
    monkeypatch.setattr("library.tools.reel_build._connect_resolve_project",
                        lambda name: project)
    monkeypatch.setattr(reel_deliver, "_live_timeline_for_reel",
                        lambda *a, **k: timeline)
    monkeypatch.setattr("library.tools.timeline_addressing.timeline_address",
                        lambda timeline, project_name: SimpleNamespace(
                            timeline_id="timeline-uid-1"))
    monkeypatch.setattr(reel_deliver, "overlay_staleness", lambda *a, **k: [])
    monkeypatch.setattr("library.tools.delivery_spec.duration_refusal",
                        lambda *a, **k: None)

    output = Path(tmp_path) / "exports" / f"{TIMELINE_NAME}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"\x00" * 1000)

    def fake_deliver_timeline(*args, **kwargs):
        return {"render": {"output_path": str(output)},
                "delivered": True, "verification": {"passed": True}}

    monkeypatch.setattr("library.tools.deliver_and_verify.deliver_timeline",
                        fake_deliver_timeline)
    return folder, output


def _none():
    from contextlib import contextmanager

    @contextmanager
    def nothing():
        yield None

    return nothing()


def test_a_delivery_records_its_own_acceptance(delivered):
    """The delivered file is named in the acceptance that protects it."""
    folder, output = delivered

    reel_deliver.deliver_reel(folder, 26, retention_job=False)

    acceptance = reel_acceptance.acceptance_for(folder, TIMELINE_NAME)
    assert acceptance is not None, (
        "the delivery recorded no acceptance, so nothing protects the "
        "generation the captain took")
    assert acceptance["delivered_file"] == str(output)
    assert Path(reel_acceptance.path_for(folder)).is_file()


def test_the_acceptance_survives_the_delivery_record(delivered):
    """The acceptance is on disk, and it names the file that was handed over."""
    folder, output = delivered

    reel_deliver.deliver_reel(folder, 26, retention_job=False)

    record = json.loads(
        Path(reel_acceptance.path_for(folder)).read_text(encoding="utf-8"))
    entry = (record.get("acceptances") or {}).get(TIMELINE_NAME)
    assert entry is not None, record
    assert entry["delivered_file"] == str(output)
