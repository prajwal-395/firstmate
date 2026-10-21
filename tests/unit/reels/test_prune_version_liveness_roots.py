"""A pool prune may not delete media a RECORDED VERSION still names.

Defect (Reel 26 audit §2.2): the pool prune removed files a recorded
version referenced - two motion-graphics `.mov` files the v22 undo
journal still pointed at. The prune proved that no file was on a live
timeline and deleted it; the version that could still be restored had
already lost its media.

Two shapes of the same blindness:

1. the undo journal and the reel read name a clip's media under
   `source_file`, and the prune's liveness-root collector never looked
   for that key - so a recorded version's own journal said which file
   it played and the prune did not hear it;
2. a version's recovery receipt carries an ASSET MANIFEST, and each
   asset spells its file `path` - a key the collector did not read
   either.

`tests/unit/resolve/test_prune_orphans.py` holds the prune's own
tests; these name the defect.
"""

from __future__ import annotations

import json

import pytest

from library.tools.execution import prune_orphans


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review" / "undo" / "entry-1").mkdir(
        parents=True)
    return project


def test_a_recorded_versions_journal_media_is_a_liveness_root(tmp_path):
    """The undo journal names the media under `source_file`."""
    project = _project(tmp_path)
    media = tmp_path / "mg_geo-podcast_70306651.mov"
    media.write_bytes(b"mov")
    journal = {
        "format": "undo_journal/1",
        "before": {"tracks": [{"clips": [{
            "name": "mg_geo-podcast_70306651.mov",
            "source_file": str(media),
            "record_in": 0,
        }]}]},
    }
    (project / "pipeline_output" / "review" / "undo" / "entry-1"
     / "entry.json").write_text(json.dumps(journal), encoding="utf-8")

    referenced = prune_orphans.retained_recovery_paths(str(project))

    assert str(media) in referenced


def test_a_recovery_receipts_asset_manifest_is_a_liveness_root(tmp_path):
    """The receipt's asset manifest spells each media file `path`."""
    project = _project(tmp_path)
    media = tmp_path / "logo_bulb_lines_safe_23976.mov"
    media.write_bytes(b"mov")
    ledger = {
        "format": "reel_versions/1",
        "reels": {"Reel 26 - x": [{
            "version": 23,
            "kind": "build",
            "rows": {},
            "rows_digest": "",
            "recovery_archive": {
                "timeline_id": "a0c4856c",
                "archive_path": "/archives/reel26.drt",
                "content_id": "sha256:deadbeef",
                "assets": [{"path": str(media), "bytes": 3,
                            "sha256": "0" * 64}],
            },
        }]},
    }
    (project / "pipeline_output" / "review" / "reel_versions.json").write_text(
        json.dumps(ledger), encoding="utf-8")

    referenced = prune_orphans.retained_recovery_paths(str(project))

    assert str(media) in referenced


def test_the_prune_refuses_to_delete_a_file_a_recorded_version_names(
        tmp_path):
    """The end of the story: the file is not deleted."""
    project = _project(tmp_path)
    media = tmp_path / "mg_geo-podcast_70306651.mov"
    media.write_bytes(b"mov")
    journal = {
        "format": "undo_journal/1",
        "before": {"tracks": [{"clips": [{
            "name": media.name,
            "source_file": str(media),
            "record_in": 0,
        }]}]},
    }
    (project / "pipeline_output" / "review" / "undo" / "entry-1"
     / "entry.json").write_text(json.dumps(journal), encoding="utf-8")

    referenced = prune_orphans.retained_recovery_paths(str(project))
    journal_path = str(
        project / "pipeline_output" / "review" / "prune.jsonl")

    # `delete_files` re-proves every path against the union of the
    # instruments, so a referenced file STOPS the run rather than being
    # skipped quietly.
    from library.tools.orphan_removal import RemovalRefused

    with pytest.raises(RemovalRefused):
        prune_orphans.delete_files(
            [str(media)], referenced, journal_path)

    assert media.is_file()
