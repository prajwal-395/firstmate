"""`ren notes import`: Frame.io comments into the shared note store.

T18 (Frame.io comments into the note store): `ren notes import <p>
<export.csv|fiojson|edl> --reel N` ingests a comment export, keeping
author, comment id, timestamp/range and annotation, and mapping file
time to timeline time through the delivery record. Unmappable comments
are listed, never guessed.

Acceptance: a fixture FIOJSON with 3 comments (one with a range, one
with no timestamp) yields 2 anchored notes and 1 general note with
ids, and `ren revise` can consume them.

The project, its sidecar, its shadow generation and its records all
live under `tmp_path`. No Resolve, no broker, no render, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REEL = "Reel 02 - pricing"
QUALIFICATION = "Ren Qualification"
FPS = 30.0
START = 108000

SNAPSHOT = {
    "timeline": REEL, "project": QUALIFICATION, "fps": FPS,
    "start_frame": START, "end_frame": START + 900,
    "tracks": [
        {"type": "video", "index": 1, "name": "A-roll", "clips": [{
            "unique_id": "aroll-1", "name": "host.mov", "record_in": START,
            "record_out": START + 900, "duration": 900, "enabled": True,
            "transform": {"ZoomX": 1.0, "ZoomY": 1.0}, "markers": []}]},
    ],
    "markers": {"timeline": [], "notes": []},
}

#: The acceptance fixture: one point comment, one ranged comment, one
#: comment with no timestamp.
FIOJSON_COMMENTS = {
    "comments": [
        {"id": "fio-c1", "author": "Lucie",
         "text": "the cut is early",
         "timestamp": 12.0,
         "annotation": {"x": 0.5, "y": 0.4}},
        {"id": "fio-c2", "author": "Lucie",
         "text": "hold this reaction longer",
         "timestamp": "0:20", "timestamp_end": "0:22.5"},
        {"id": "fio-c3", "author": "Marco",
         "text": "overall the pacing feels right"},
    ],
}


@pytest.fixture
def project(tmp_path, monkeypatch):
    from library.tools import timeline_shadow
    from library.tools.project_layout import Area, ProjectLayout

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    # Hermetic under a chat harness: nothing here reads the host, and a
    # harness marker must not change what the import resolves to.
    monkeypatch.delenv("REN_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\n'
        f'project_format_version: 1\nresolve:\n  project: "{QUALIFICATION}"\n',
        encoding="utf-8")
    layout = ProjectLayout(str(root))
    layout.ensure()
    timeline_shadow.ShadowStore().record(
        project=QUALIFICATION, timeline_id="tl-02", timeline_name=REEL,
        snapshot=SNAPSHOT, source=timeline_shadow.OBSERVED, expected_head=0)
    # The delivery record: reel 2 rendered to a file, carrying the
    # timeline rate and start the file times map through.
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


@pytest.fixture
def fiojson_export(tmp_path):
    path = tmp_path / "review-comments.fiojson"
    path.write_text(json.dumps(FIOJSON_COMMENTS), encoding="utf-8")
    return path


def test_three_comments_yield_two_anchored_and_one_general(
        project, fiojson_export):
    """Catches: a ranged or timeless comment landing nowhere, or two
    imports of the same export filing different ids."""
    from library.tools import frameio_import, marker_routing

    result = frameio_import.import_comments(
        str(project), str(fiojson_export), reel="2")

    assert result["parsed"] == 3
    assert result["anchored"] == 2
    assert result["general"] == 1
    assert result["unmapped"] == []
    assert len(result["note_ids"]) == 3
    assert len(set(result["note_ids"])) == 3
    assert all(note_id.startswith("fio:") for note_id in result["note_ids"])
    assert Path(result["pull_path"]).is_file()

    routed = {note.stable_note_id: note
              for note in marker_routing.route_project(str(project))}
    assert set(routed) == set(result["note_ids"])
    point = routed["fio:fio-c1"]
    assert point.frame == START + 360
    assert point.timeline == REEL
    ranged = routed["fio:fio-c2"]
    assert ranged.frame == START + 600
    assert ranged.duration_frames == 75
    general = routed["fio:fio-c3"]
    assert general.frame is None
    assert general.timeline == REEL
    assert "no timestamp" in (general.unplaced_reason or "")


def test_imported_notes_keep_author_id_and_annotation(
        project, fiojson_export):
    """Catches: the author, comment id or annotation being dropped on
    the way into the note store."""
    import json as _json

    from library.tools import frameio_import

    result = frameio_import.import_comments(
        str(project), str(fiojson_export), reel="2")
    payload = _json.loads(Path(result["pull_path"]).read_text(
        encoding="utf-8"))
    kept = {note["note_id"]: note["custom_data"]["frameio"]
            for note in payload["notes"]}
    assert kept["fio:fio-c1"]["author"] == "Lucie"
    assert kept["fio:fio-c1"]["comment_id"] == "fio-c1"
    assert kept["fio:fio-c1"]["annotation"] == {"x": 0.5, "y": 0.4}
    assert kept["fio:fio-c3"]["author"] == "Marco"


def test_revise_consumes_imported_notes_and_reimport_reasks(
        project, fiojson_export):
    """Catches: an imported note `ren revise` cannot find by id, and a
    re-import minting new identities instead of rejoining its own."""
    from library.tools import feedback_ledger, frameio_import, marker_routing, revise

    first = frameio_import.import_comments(
        str(project), str(fiojson_export), reel="2")
    routed = marker_routing.route_project(str(project))
    revision = revise.start(str(project), note_id=routed[0].edit_link_id)
    assert revision["status"] == revise.STATUS_AWAITING
    assert revision["note_id"] == routed[0].edit_link_id

    document = feedback_ledger.build(str(project))
    assert len(document["entries"]) == 3

    second = frameio_import.import_comments(
        str(project), str(fiojson_export), reel="2")
    assert second["note_ids"] == first["note_ids"]
    again = feedback_ledger.build(str(project))
    assert len(again["entries"]) == 3
    assert all(entry["pulls"] == 2 for entry in again["entries"])


def test_unmappable_comments_are_listed_never_guessed(
        project, tmp_path):
    """Catches: a past-the-end timestamp being clamped onto the reel,
    or a garbled timestamp landing at zero."""
    from library.tools import frameio_import, marker_routing

    export = tmp_path / "bad-comments.fiojson"
    export.write_text(json.dumps({"comments": [
        {"id": "fio-past", "author": "Lucie",
         "text": "trim the very end", "timestamp": 60.0},
        {"id": "fio-garbled", "author": "Lucie",
         "text": "what is this frame", "timestamp": "twelve"},
    ]}), encoding="utf-8")

    result = frameio_import.import_comments(
        str(project), str(export), reel="2")

    assert result["anchored"] == 0
    assert result["general"] == 0
    assert result["pull_path"] is None
    assert len(result["unmapped"]) == 2
    reasons = " ".join(entry["reason"] for entry in result["unmapped"])
    assert "past the end" in reasons
    assert "not a file moment" in reasons
    assert marker_routing.route_project(str(project)) == []


def test_csv_and_edl_exports_parse_to_the_same_store(
        project, tmp_path):
    """Catches: either of the other two accepted suffixes refusing, or
    mapping through a different frame than the fiojson path."""
    from library.tools import frameio_import, marker_routing

    csv_export = tmp_path / "review-comments.csv"
    csv_export.write_text(
        "comment_id,author,comment,time,end\n"
        "fio-csv1,Lucie,the cut is early,0:12,\n"
        "fio-csv2,Lucie,overall pacing note,,\n",
        encoding="utf-8")
    csv_result = frameio_import.import_comments(
        str(project), str(csv_export), reel="2")
    assert (csv_result["anchored"], csv_result["general"]) == (1, 1)

    edl_export = tmp_path / "review-comments.edl"
    edl_export.write_text(
        "TITLE: GEO EP12 REVIEW\n"
        "001  CLIP01   V     C        00:00:12:00 00:00:12:01 "
        "00:00:00:00 00:00:00:01\n"
        "* COMMENT: Lucie | the cut is early\n",
        encoding="utf-8")
    edl_result = frameio_import.import_comments(
        str(project), str(edl_export), reel="2")
    assert (edl_result["anchored"], edl_result["general"]) == (1, 0)

    frames = sorted(note.frame for note in marker_routing.route_project(
        str(project)) if note.frame is not None)
    # Both exports say the same words at the same frame, so the router
    # reads one note - while the ledger keeps both imports' identities.
    assert frames == [START + 360]
    from library.tools import feedback_ledger
    document = feedback_ledger.build(str(project))
    assert len(document["entries"]) == 3


def test_notes_import_verb_reaches_the_import(
        project, fiojson_export, tmp_path, monkeypatch):
    """Catches: `ren notes import` parsing somewhere other than the
    import (the verb wiring, not the module)."""
    import os

    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ,
               PIPELINE_PROJECTS_ROOT=str(tmp_path),
               REN_SHADOW_DB=str(tmp_path / "shadow.sqlite3"))
    env.pop("REN_HOST", None)
    env.pop("CLAUDECODE", None)
    completed = subprocess.run(
        [sys.executable, "manage_project.py", "notes", "import",
         str(project), str(fiojson_export), "--reel", "2"],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8",
        env=env, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "anchored: 2" in completed.stdout
    assert "general: 1" in completed.stdout
