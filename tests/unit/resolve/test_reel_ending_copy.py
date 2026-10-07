"""The typed broker path copies a TV-off tail onto one named reel."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools.resolved import jobs, reel_ending_copy
from library.tools.resolved.jobs import JobRefused
from library.tools import still_overlays
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeProject,
    FakeResolve,
    FakeTimeline,
    FakeTimelineItem,
)


def _params(**overrides):
    return {
        "project": reel_ending_copy.PROJECT_NAME,
        "project_slug": reel_ending_copy.PROJECT_SLUG,
        "source_timeline": "Reel 26 - reference",
        "target_timeline": "Reel 27 - target",
        "target_picture_uid": "target-picture",
        "record_frame": 1013,
        **overrides,
    }


def test_prepare_is_a_closed_exclusive_mutation_for_named_reels():
    from library.tools.resolved.scheduler import EXCLUSIVE

    shape = jobs.prepare(reel_ending_copy.KIND, _params())

    assert shape["mode"] == EXCLUSIVE
    assert shape["priority"] == "mutation"
    assert shape["executed"] is True
    assert shape["qualification"] is False
    assert shape["project"] == reel_ending_copy.PROJECT_NAME
    assert shape["timeline"] == "Reel 27 - target"
    assert shape["locality"] == "timeline"
    assert shape["coalesce_key"] is None


def test_prepare_accepts_exact_timeline_id_and_picture_row_selector():
    shape = jobs.prepare(reel_ending_copy.KIND, _params(
        target_timeline_id="timeline-27",
        target_picture_uid=None,
        target_picture_track="Akshita",
    ))

    assert shape["timeline"] == "Reel 27 - target"


@pytest.mark.parametrize("changes, message", [
    ({"project": "Other project"}, "may only touch"),
    ({"project_slug": "other"}, "may only use project slug"),
    ({"source_timeline": "Reel 22 - target"}, "accepts Reel 10 or 26"),
    ({"target_timeline": "Reel 10 - reference"}, "accepts Reel 10 or 26"),
    ({"project_folder": "/tmp/project"}, "caller-supplied paths"),
    ({"record_frame": True}, "non-negative integer"),
    ({"target_picture_uid": None}, "exactly one of"),
    ({"target_picture_track": "Akshita"}, "exactly one of"),
])
def test_prepare_refuses_out_of_scope_or_open_ended_requests(changes, message):
    with pytest.raises(JobRefused, match=message):
        jobs.prepare(reel_ending_copy.KIND, _params(**changes))


def test_exact_timeline_id_disambiguates_duplicate_names():
    first = FakeTimeline("Reel 22 - target", uid="old-reel-22")
    second = FakeTimeline("Reel 22 - target", uid="live-reel-22")
    project = FakeProject("Podcast (field test)", [first, second])

    assert reel_ending_copy._exact_timeline(
        project, "Reel 22 - target", "live-reel-22") is second
    with pytest.raises(JobRefused, match="expected one timeline"):
        reel_ending_copy._exact_timeline(project, "Reel 22 - target")


@pytest.mark.parametrize("media_type", ["Video", "Video + Audio"])
def test_picture_row_selector_finds_one_video_ending_at_requested_frame(
        media_type):
    pool = FakeMediaPoolItem("body.mov", uid="body-pool")
    pool.SetClipProperty("Type", media_type)
    picture = FakeTimelineItem(
        "body.mov", None, start=0, duration=1013,
        pool_item=pool, uid="body-item")
    target = FakeTimeline(
        "Reel 22 - target", video=[("Akshita", [picture]), ("Frame", [])])

    assert reel_ending_copy._item_by_track_end(
        target, "Akshita", 1013) == (1, picture)
    with pytest.raises(JobRefused, match="found 0"):
        reel_ending_copy._item_by_track_end(target, "Akshita", 1012)


def test_tv_signature_reads_the_three_animation_splines():
    from library.tools.fusion.comp_builder import build_effect_comp

    comp = build_effect_comp(
        {"tv_power_tail": True, "source_in_frame": 0,
         "source_out_frame": 18},
        19,
        (1920, 1080),
    )

    signature = reel_ending_copy._fusion_signature(comp)

    assert set(signature["nodes"]) == {
        "band", "mask", "merge", "dot", "decay"}
    assert set(signature["splines"]) == {"band", "dot", "decay"}
    assert all(signature["splines"][role]["keyframes"]
               for role in ("band", "dot", "decay"))
    with pytest.raises(JobRefused, match="expected one TV-off graph"):
        reel_ending_copy._fusion_signature(
            build_effect_comp({}, 19, (1920, 1080)))


def test_append_misplacement_is_removed_and_reported_to_inverse(monkeypatch):
    target = FakeTimeline(
        "Reel 27 - target", video=[("Picture", []), ("Logo", [])])
    project = FakeProject("Podcast (field test)", [target], current=target)
    pool = project.GetMediaPool()
    media = FakeMediaPoolItem("logo.mov", uid="logo-pool")
    original_append = pool.AppendToTimeline

    def append_then_raise(specs):
        original_append(specs)
        raise RuntimeError("failure after Resolve placed the item")

    monkeypatch.setattr(pool, "AppendToTimeline", append_then_raise)
    orphaned = []

    with pytest.raises(JobRefused, match="raised after creating 1 logo"):
        reel_ending_copy._append_item(
            pool, target, media_pool_item=media, row=1,
            record_frame=100, source_start=0, duration=20,
            label="logo", orphaned_items=orphaned)

    assert len(orphaned) == 1
    assert target.GetItemListInTrack("video", 1) == []
    assert target.GetItemListInTrack("video", 2) == []


def test_inverse_inventory_comparison_allows_resolve_renewed_uids():
    before = {
        "rows": {"V1 Picture": [{
            "uid": "original", "name": "body.mov", "record": [0, 100],
            "src_off": 0, "duration": 100, "enabled": True,
            "props": {}, "fusion_comps": 0, "mpi_path": "/body.mov",
            "pool_uid": "body-pool", "clip_markers": {},
        }]},
        "end": [0, 100], "timeline_markers": {},
    }
    after = {
        "rows": {"V1 Picture": [{**before["rows"]["V1 Picture"][0],
                                  "uid": "renewed"}]},
        "end": [0, 100], "timeline_markers": {},
    }

    assert reel_ending_copy._inventory_matches(before, after)
    after["rows"]["V1 Picture"][0]["record"] = [0, 99]
    assert not reel_ending_copy._inventory_matches(before, after)


def test_partial_post_header_restore_keeps_original_pool_media():
    from tests.resolve_double import FakeFolder

    original_path = "/media/post-header.mov"
    original_pool_item = FakeMediaPoolItem(
        "post-header.mov", uid="original-post-header")
    original_pool_item.SetClipProperty("File Path", original_path)
    root_folder = FakeFolder("Master")
    root_folder.add_clip(original_pool_item)
    target = FakeTimeline(
        "Reel 22 - target", video=[("Post Header", [])])
    project = FakeProject(
        reel_ending_copy.PROJECT_NAME, [target], current=target)
    pool = project.GetMediaPool()
    pool._root = root_folder
    partial_pool_item = FakeMediaPoolItem("partial.png", uid="partial-png")
    partial_pool_item.SetClipProperty("File Path", "/media/partial.png")
    partial = FakeTimelineItem(
        "partial.png", target, start=0, duration=50,
        pool_item=partial_pool_item, uid="partial-overlay")
    target.add_item("video", 1, partial)

    still_overlays._restore_swaps(
        None, project, pool, target, 30.0,
        [{"label": "Post Header card.mov@0", "track_index": 1,
          "item_uid": "deleted-original", "record": [0, 100],
          "file_path": original_path, "source_in": 0, "props": {},
          "markers": {}, "keep_still": False}],
        [], "/tmp/project", target.GetName())

    restored = target.GetItemListInTrack("video", 1)
    assert [(item.GetStart(), item.GetEnd()) for item in restored] == [(0, 100)]
    assert restored[0].GetMediaPoolItem() is original_pool_item
    assert original_pool_item in root_folder.GetClipList()


def test_reel_22_post_header_is_planned_to_stop_at_the_ending(
        tmp_path):
    header_path = tmp_path / "post_header.png"
    header_path.write_bytes(b"still")
    header_pool = FakeMediaPoolItem("post_header.png")
    header_pool.SetClipProperty("File Path", str(header_path))
    header_pool.SetClipProperty("Type", "Still")
    header = FakeTimelineItem(
        "post_header.png", None, start=0, duration=1032,
        pool_item=header_pool, uid="post-header")
    target = FakeTimeline(
        "Reel 22 - target", end_frame=1032,
        video=[("Picture", []), ("Video 2", []), ("Post Header", [header])],
    )

    plan = reel_ending_copy._plan_post_header(target, 1013, 30.0)

    assert len(plan) == 1
    assert plan[0]["item_uid"] == "post-header"
    assert plan[0]["record"] == [0, 1032]
    assert plan[0]["want_end"] == 1013


def test_run_archives_then_adds_the_target_freeze_comp_and_logo(
        monkeypatch, tmp_path):
    from library.tools.fusion.comp_builder import build_effect_comp

    root = tmp_path / "project"
    scratch = root / "pipeline_output" / "scratch"
    scratch.mkdir(parents=True)
    media = tmp_path / "media"
    media.mkdir()
    picture_path = media / "body.mov"
    reference_freeze_path = media / "reel_freeze_reference.mov"
    logo_path = media / "logo_bulb_lines_safe_23976.mov"
    for path in (picture_path, reference_freeze_path, logo_path):
        path.write_bytes(b"fixture")

    def pool_item(name, path, *, frames=2000, fps="30", clip_type="Video"):
        item = FakeMediaPoolItem(name)
        item.SetClipProperty("File Path", str(path))
        item.SetClipProperty("Frames", str(frames))
        item.SetClipProperty("FPS", fps)
        item.SetClipProperty("Type", clip_type)
        return item

    tail_comp = build_effect_comp(
        {"tv_power_tail": True, "source_in_frame": 0,
         "source_out_frame": 18},
        19,
        (1920, 1080),
    )
    reference_freeze = FakeTimelineItem(
        "reel_freeze_reference.mov", None, start=100, duration=19,
        pool_item=pool_item("reel_freeze_reference.mov",
                            reference_freeze_path, frames=19),
        fusion_comps=[tail_comp], uid="reference-freeze")
    reference_logo = FakeTimelineItem(
        "logo_bulb_lines_safe_23976.mov", None, start=119, duration=72,
        pool_item=pool_item("logo_bulb_lines_safe_23976.mov", logo_path),
        nodes=2, uid="reference-logo")
    source = FakeTimeline(
        "Reel 26 - reference",
        video=[("Picture", [reference_freeze]), ("Video 2", []),
               ("Video 3", []), ("Video 4", []), ("Video 5", []),
               ("Logo", [reference_logo])],
    )
    target_picture = FakeTimelineItem(
        "body.mov", None, start=0, duration=1013,
        pool_item=pool_item("body.mov", picture_path),
        uid="target-picture")
    target = FakeTimeline(
        "Reel 27 - target",
        video=[("Picture", [target_picture]), ("Video 2", []),
               ("Video 3", []), ("Video 4", []), ("Video 5", []),
               ("Logo", [])],
    )
    project = FakeProject(
        reel_ending_copy.PROJECT_NAME, [source, target], current=target)
    resolve = FakeResolve(project)
    config = SimpleNamespace(
        slug=reel_ending_copy.PROJECT_SLUG,
        project_root=root,
        resolve=SimpleNamespace(project_name=reel_ending_copy.PROJECT_NAME),
    )
    order = []
    archive_path = root / "recovery.drt"

    def archive(*_args, **_kwargs):
        order.append("archive")
        return {"archived": True, "path": str(archive_path)}

    def snapshot(timeline):
        order.append("inventory")
        return original_snapshot(timeline)

    original_snapshot = still_overlays.inventory_snapshot

    @contextmanager
    def cursor_excursion(open_project, timeline, _purpose):
        previous = open_project.GetCurrentTimeline()
        open_project.SetCurrentTimeline(timeline)
        try:
            yield
        finally:
            open_project.SetCurrentTimeline(previous)

    def render_freeze(freeze, out_dir, _fps):
        order.append("render")
        output = Path(out_dir) / "reel_freeze_target.mov"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered target frame")
        return replace(freeze, rendered_path=str(output))

    def promote(_source, _project_root, _area):
        output = root / "pipeline_output" / "reel_cards" / "reel_freeze_target.mov"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"durable target frame")
        return str(output)

    def import_pool_item(pool, filepath, *_args):
        item = pool_item(Path(filepath).name, filepath, frames=19)
        pool.GetRootFolder().add_clip(item)
        return item

    monkeypatch.setattr("library.tools.project_registry.get_project",
                        lambda _slug: config)
    monkeypatch.setattr(
        "library.tools.versions.store.export_timeline_recovery_archive",
        archive)
    monkeypatch.setattr("library.tools.resolve_lock.cursor_excursion",
                        cursor_excursion)
    monkeypatch.setattr("library.tools.still_overlays.inventory_snapshot",
                        snapshot)
    monkeypatch.setattr("library.tools.reel_ending.render_freeze",
                        render_freeze)
    monkeypatch.setattr("library.tools.reel_placed_assets.promote_to_durable",
                        promote)
    monkeypatch.setattr("library.tools.reel_build.import_pool_item",
                        import_pool_item)
    monkeypatch.setattr("library.tools.reel_build.overlay_import_bin",
                        lambda *_args: project.GetMediaPool().GetRootFolder())

    target_params = _params(
        target_timeline_id=target.GetUniqueId(),
        target_picture_uid=None,
        target_picture_track="Picture",
    )
    result = reel_ending_copy.run(
        {"id": "test-ending", "params": target_params}, resolve)

    assert result["changed"] is True
    assert result["drt_backup"] == str(archive_path)
    assert result["freeze"]["record"] == [1013, 1032]
    assert result["logo"]["record"] == [1032, 1104]
    assert result["freeze"]["comp"]["signature"] == \
        reel_ending_copy._fusion_signature(tail_comp)
    assert order[:3] == ["archive", "inventory", "render"]
    assert project.GetCurrentTimeline() is target
    assert [item.GetName() for item in target.GetItemListInTrack(
        "video", 1)] == ["body.mov", "reel_freeze_target.mov"]
    assert [item.GetName() for item in target.GetItemListInTrack(
        "video", 6)] == ["logo_bulb_lines_safe_23976.mov"]

    before_repeat = still_overlays.inventory_snapshot(target)
    repeated = reel_ending_copy.run(
        {"id": "test-ending-repeat", "params": target_params}, resolve)
    after_repeat = still_overlays.inventory_snapshot(target)

    assert repeated["changed"] is False
    assert repeated["already_applied"] is True
    assert before_repeat == after_repeat
