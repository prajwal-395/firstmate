"""VC-14: per-reel component manifests attach to revisions and diff them.

Hermetic: every project lives under `tmp_path`, every record is
hand-made. No Resolve, no models, no real projects.
"""

from __future__ import annotations

import json

import pytest

from library.tools import reel_component_manifest as manifests
from library.tools.versions import reel_versions

FINAL = "Reel 07 - the-captain-says-ship-it"
ROWS = {"video:Semantic": [{"clip": "a.mov", "start": 0, "duration": 48}]}


def _write(path, document) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _assembly(final: str) -> dict:
    clip = {
        "source_file": "/footage/a.mov",
        "timeline_in": 0.0,
        "timeline_out": 2.0,
        "reel": final,
    }
    segment = {
        "overlay_path": "pipeline_output/renders/cap_07.mov",
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "reel": final,
    }
    return {
        "tracks": {"V1": {"clips": [dict(clip)]}, "A1": {"clips": [dict(clip)]}},
        "subtitle_overlay": {"segments": [dict(segment)]},
        "motion_graphics_overlay": {"segments": [dict(segment)]},
        "timed_text_overlay": {"segments": []},
        "generator_overlays": [],
    }


def _project_with_assembly(tmp_path, final: str = FINAL) -> object:
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(str(tmp_path))
    _write(
        layout.read_path(Area.ASSEMBLY_MANIFEST, "assembly_manifest.json"),
        _assembly(final),
    )
    return layout


def test_build_records_all_six_components_with_stable_ids(tmp_path):
    _project_with_assembly(tmp_path)
    manifest = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    assert manifest["format"] == manifests.FORMAT
    assert manifest["reel_key"] == "reel-07"
    assert manifest["resolve_timeline"] == FINAL
    assert set(manifest["components"]) == set(manifests.COMPONENTS)
    for name in manifests.COMPONENTS:
        component = manifest["components"][name]
        assert component["id"] == f"reel-07:{name}"
        assert component["status"] == manifests.RECORDED
        assert component["digest"]
    assert manifest["manifest_digest"]


def test_identity_survives_a_resolve_rename(tmp_path):
    _project_with_assembly(tmp_path, FINAL)
    before = manifests.build_manifest(str(tmp_path), FINAL, ROWS)
    renamed = "Reel 07 - a-better-title"
    _project_with_assembly(tmp_path, renamed)
    after = manifests.build_manifest(str(tmp_path), renamed, ROWS)

    assert after["reel_key"] == before["reel_key"] == "reel-07"
    assert after["components"]["aroll"]["id"] == before["components"]["aroll"]["id"]
    assert after["resolve_timeline"] == renamed


def test_missing_records_are_absent_never_invented(tmp_path):
    manifest = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    assert manifest["components"]["timeline"]["status"] == manifests.RECORDED
    for name in ("aroll", "captions", "motion_graphics", "audio"):
        assert manifest["components"][name]["status"] == manifests.ABSENT
    assert manifest["components"]["fusion"]["status"] == manifests.ABSENT


def test_assembly_without_this_reel_is_unkeyed_not_empty(tmp_path):
    _project_with_assembly(tmp_path, "Reel 03 - another-reel")
    manifest = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    assert manifest["components"]["aroll"]["status"] == manifests.UNKEYED
    # The timeline component comes from the revision's own rows.
    assert manifest["components"]["timeline"]["status"] == manifests.RECORDED


def test_diff_names_only_the_changed_component(tmp_path):
    _project_with_assembly(tmp_path)
    before = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    changed = _assembly(FINAL)
    changed["subtitle_overlay"]["segments"][0]["timeline_end"] = 3.0
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(str(tmp_path))
    _write(layout.read_path(Area.ASSEMBLY_MANIFEST, "assembly_manifest.json"), changed)
    after = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    diff = manifests.diff_manifests(before, after)
    assert diff["changed"] == ["captions"]
    assert set(diff["unchanged"]) == set(manifests.COMPONENTS) - {"captions"}
    assert diff["detail"]["captions"]["fields"]


def test_diff_with_a_predate_revision_is_unknown_never_unchanged(tmp_path):
    _project_with_assembly(tmp_path)
    current = manifests.build_manifest(str(tmp_path), FINAL, ROWS)

    diff = manifests.diff_manifests(None, current)
    assert set(diff["changed"]) == set(manifests.COMPONENTS)
    assert diff["unchanged"] == []
    assert all(value["changed"] == "unknown" for value in diff["detail"].values())


def test_attach_and_reader_round_trip(tmp_path):
    _project_with_assembly(tmp_path)
    entry = reel_versions.record(
        str(tmp_path), FINAL, kind=reel_versions.KIND_BUILD, rows=ROWS
    )

    assert manifests.manifest_of_version(str(tmp_path), FINAL, entry["version"]) is None
    manifest = manifests.build_manifest(str(tmp_path), FINAL, ROWS)
    manifests.attach_to_version(str(tmp_path), FINAL, entry["version"], manifest)

    assert (
        manifests.manifest_of_version(str(tmp_path), FINAL, entry["version"])[
            "manifest_digest"
        ]
        == manifest["manifest_digest"]
    )
    assert (
        manifests.latest_manifest(str(tmp_path), FINAL)["manifest_digest"]
        == manifest["manifest_digest"]
    )


def test_corrupt_manifest_is_refused_not_emptied(tmp_path):
    entry = reel_versions.record(
        str(tmp_path), FINAL, kind=reel_versions.KIND_BUILD, rows=ROWS
    )
    with pytest.raises(manifests.ManifestUnreadable):
        manifests.attach_to_version(
            str(tmp_path), FINAL, entry["version"], {"format": "nope"}
        )
    document = reel_versions.read(str(tmp_path))
    document["reels"][FINAL][0]["component_manifest"] = {"format": "nope"}
    from library.tools.versions import store

    store.write_record(
        reel_versions.path_for(str(tmp_path)), document, prefix=".reel-versions-"
    )
    with pytest.raises(manifests.ManifestUnreadable):
        manifests.latest_manifest(str(tmp_path), FINAL)


def test_backfill_marks_staleness_and_skips_rowless(tmp_path):
    _project_with_assembly(tmp_path)
    with_rows = reel_versions.record(
        str(tmp_path), FINAL, kind=reel_versions.KIND_BUILD, rows=ROWS
    )
    rowless = reel_versions.record(
        str(tmp_path), FINAL, kind=reel_versions.KIND_BUILD, rows={}
    )

    report = manifests.backfill_project(str(tmp_path))

    assert report["attached"] == [{"final": FINAL, "version": with_rows["version"]}]
    assert [skip["version"] for skip in report["skipped"]] == [rowless["version"]]
    backfilled = manifests.manifest_of_version(
        str(tmp_path), FINAL, with_rows["version"]
    )
    assert backfilled["backfilled"] is True
    assert "postdate" in backfilled["backfill_note"]
    # A second run changes nothing: the backfilled entry is complete.
    assert manifests.backfill_project(str(tmp_path)) == {
        "attached": [],
        "skipped": [
            {
                "final": FINAL,
                "version": rowless["version"],
                "reason": "no rows snapshot on the entry",
            }
        ],
    }
