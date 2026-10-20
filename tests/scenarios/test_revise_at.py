"""`ren revise --at`: a chat note anchored to a delivered file's clock.

T11 (Chat-and-time notes, without Resolve): `ren revise <p> --at 0:12
[--reel N] "<note>"` maps the delivered-file time onto the timeline
through the delivery record (`.deliver.json`) and stores the note in
the same note store with a stable note id, so markers and chat notes
share one ledger (`feedback_ledger.py`).

Acceptance: a note at 0:12 on a fixture delivered reel with a known
timeline start resolves to the correct timeline frame and source clip,
and re-asking the same words is recognized as a re-ask by note id.

The project, its sidecar, its shadow generation and its records all
live under `tmp_path`. No Resolve, no broker, no render.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REEL = "Reel 02 - pricing"
QUALIFICATION = "Ren Qualification"
FPS = 30.0
START = 108000


def _caption(uid: str, start: int, end: int) -> dict:
    return {"unique_id": uid, "name": f"{uid}.mov", "record_in": START + start,
            "record_out": START + end, "duration": end - start,
            "enabled": True, "transform": {"ZoomX": 1.0, "ZoomY": 1.0},
            "markers": []}


SNAPSHOT = {
    "timeline": REEL, "project": QUALIFICATION, "fps": FPS,
    "start_frame": START, "end_frame": START + 900,
    "tracks": [
        {"type": "video", "index": 1, "name": "A-roll", "clips": [{
            "unique_id": "aroll-1", "name": "host.mov", "record_in": START,
            "record_out": START + 900, "duration": 900, "enabled": True,
            "transform": {"ZoomX": 1.0, "ZoomY": 1.0}, "markers": []}]},
        {"type": "video", "index": 5, "name": "Subtitles", "clips": [
            _caption("cap-1", 30, 90), _caption("cap-2", 90, 150),
            _caption("cap-3", 600, 660)]},
    ],
    "markers": {"timeline": [], "notes": []},
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    from library.tools import timeline_shadow
    from library.tools.project_layout import Area, ProjectLayout

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    # Hermetic under a chat harness: nothing here reads the host, and a
    # harness marker must not change what the anchor resolves to.
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\n'
        f'project_format_version: 1\nresolve:\n  project_name: "{QUALIFICATION}"\n',
        encoding="utf-8")
    layout = ProjectLayout(str(root))
    layout.ensure()
    timeline_shadow.ShadowStore().record(
        project=QUALIFICATION, timeline_id="tl-02", timeline_name=REEL,
        snapshot=SNAPSHOT, source=timeline_shadow.OBSERVED, expected_head=0)
    # The delivery record: reel 2 rendered to a file, carrying the
    # timeline rate and start the file time maps through.
    exports = layout.write_dir(Area.EXPORTS)
    video = exports / "Reel 02 - pricing.mp4"
    video.write_bytes(b"\x00" * 16)
    sidecar = exports / "Reel 02 - pricing.deliver.json"
    sidecar.write_text(json.dumps({
        "timeline_name": REEL, "reel": 2, "delivered": True,
        "timeline_fps": FPS, "timeline_start_frame": START,
        "timeline_end_frame": START + 900, "timeline_seconds": 30.0,
        "render": {"output_path": str(video)},
        "verification": {"output_path": str(video)},
    }), encoding="utf-8")
    return root


def test_file_time_resolves_to_timeline_frame_and_source_clip(project):
    """Catches: 0:12 landing anywhere but START + 360, or on no clip."""
    from library.tools import delivered_note_anchor as anchor

    resolved = anchor.resolve(project, "2", anchor.parse_at("0:12"))

    assert resolved["timeline_name"] == REEL
    assert resolved["timeline_frame"] == START + 360
    assert resolved["timeline_seconds"] == 12.0
    assert resolved["source_clip"]["unique_id"] == "aroll-1"


def test_revise_at_stores_one_ledger_note_and_reask_keeps_its_id(project):
    """Catches: the anchored note landing outside the shared ledger, or a
    re-asked note minting a new identity instead of rejoining its own."""
    from library.tools import delivered_note_anchor as anchor
    from library.tools import feedback_ledger, revise

    first = revise.start(project, "the cut is early", reel="2", at="0:12")
    assert first["status"] == revise.STATUS_AWAITING
    assert first["anchor"]["timeline_frame"] == START + 360
    assert first["anchor"]["source_clip"]["unique_id"] == "aroll-1"
    assert first["source"] == "chat-at"
    assert first["reel"] == REEL

    document = feedback_ledger.build(str(project))
    assert len(document["entries"]) == 1
    [entry] = document["entries"]
    assert entry["reel"] == REEL
    assert entry["frames"] == [START + 360]
    assert entry["note_id"] == anchor.anchored_identity(
        REEL, "the cut is early", 12.0)

    second = revise.start(project, "the cut is early", reel="2", at="0:12")
    assert second["note_id"] == first["note_id"]

    again = feedback_ledger.build(str(project))
    assert len(again["entries"]) == 1
    assert again["entries"][0]["pulls"] == 2

    other = revise.start(project, "the cut is early", reel="2", at="0:13")
    assert other["note_id"] != first["note_id"]


def test_revise_at_refuses_past_the_end_and_without_a_delivery(
        project, tmp_path, monkeypatch):
    """Catches: a file time past the delivered duration being clamped or
    guessed, and a project with no delivery anchoring to nothing."""
    from library.tools import delivered_note_anchor as anchor
    from library.tools import revise
    from library.tools.ren_refusal import RenRefusal

    with pytest.raises(RenRefusal, match="past the end"):
        revise.start(project, "the cut is early", reel="2", at="1:00")

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "other.sqlite3"))
    bare = tmp_path / "bare-show"
    bare.mkdir()
    (bare / "project.yaml").write_text(
        'name: "Bare"\nslug: bare-show\nproject_format_version: 1\n'
        f'resolve:\n  project_name: "{QUALIFICATION}"\n', encoding="utf-8")
    with pytest.raises(anchor.AnchorRefused, match="no reel .* delivered"):
        anchor.resolve(bare, "", anchor.parse_at("0:12"))


def test_at_clock_shapes_and_reel_names(project):
    """Catches: `--at 12` or `--at 1:02:03` refusing, or a timeline name
    resolving to a different reel's file."""
    from library.tools import delivered_note_anchor as anchor

    assert anchor.parse_at("12") == 12.0
    assert anchor.parse_at("0:12.5") == 12.5
    assert anchor.parse_at("1:02:03") == 3723.0
    with pytest.raises(anchor.AnchorRefused):
        anchor.parse_at("twelve")

    by_name = anchor.resolve(project, REEL, anchor.parse_at("0:12"))
    assert by_name["timeline_frame"] == START + 360
