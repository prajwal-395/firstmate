"""Timeline inventories stay stable against concurrent Resolve writers."""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import textwrap
import threading
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from library.tools import reel_replace_guard, resolve_lock
from tests.resolve_double import FakeProject


REPO_ROOT = str(__import__("pathlib").Path(__file__).resolve().parents[3])

_EXCLUSIVE_HOLDER = textwrap.dedent("""
    import os, sys
    sys.path.insert(0, {repo!r})
    from library.tools import resolve_lock
    os.environ.pop(resolve_lock.INHERIT_ENV, None)
    os.environ.pop(resolve_lock.INHERIT_MODE_ENV, None)
    print("TRYING", flush=True)
    with resolve_lock.resolve_lease(
            "test: concurrent timeline creator", exclusive=True,
            timeout=5.0):
        print("HELD", flush=True)
        sys.stdin.readline()
""")


class _PausingProject(FakeProject):
    def __init__(self, count_read, continue_read):
        super().__init__(timelines=["Master", "Reel 01"])
        self.count_read = count_read
        self.continue_read = continue_read

    def GetTimelineCount(self):
        count = super().GetTimelineCount()
        self.count_read.set()
        if not self.continue_read.wait(timeout=5):
            raise TimeoutError("test did not release timeline inventory read")
        return count


def _start_exclusive_holder(lock_dir):
    env = os.environ.copy()
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    # The child is an independent Resolve client, not a child operation
    # inheriting the reader's lease contract.
    env.pop(resolve_lock.INHERIT_ENV, None)
    env.pop(resolve_lock.INHERIT_MODE_ENV, None)
    return subprocess.Popen(
        [sys.executable, "-c", _EXCLUSIVE_HOLDER.format(repo=REPO_ROOT)],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")


def _release_holder(holder):
    if holder is None:
        return
    if holder.poll() is None:
        try:
            holder.stdin.write("release\n")
            holder.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
    try:
        holder.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        holder.kill()
        holder.communicate(timeout=5)


def test_inventory_holds_shared_lease_while_count_and_indexes_are_read(
        tmp_path, monkeypatch):
    """An exclusive creator waits until a count/index inventory completes."""
    lock_dir = tmp_path / "resolve-lock"
    lock_dir.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    count_read = threading.Event()
    continue_read = threading.Event()
    project = _PausingProject(count_read, continue_read)
    # Match the observed Resolve response when a deletion invalidates an
    # index after GetTimelineCount but before GetTimelineByIndex.
    project.GetTimelineByIndex = lambda index: (
        project._timelines[index - 1]
        if 1 <= index <= len(project._timelines) else None)

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(reel_replace_guard.timeline_inventory, project)
    holder = None
    try:
        assert count_read.wait(timeout=5), "inventory never read the timeline count"
        holder = _start_exclusive_holder(lock_dir)
        assert select.select([holder.stdout], [], [], 5)[0], (
            "concurrent writer did not report its lease attempt")
        line = holder.stdout.readline().strip()
        assert line == "TRYING"
        writer_entered_during_inventory = bool(
            select.select([holder.stdout], [], [], 0.5)[0])
        if writer_entered_during_inventory:
            assert holder.stdout.readline().strip() == "HELD"
            # This is the observed count-then-delete-then-index interleaving.
            project.GetMediaPool().DeleteTimelines(
                [project._timelines[-1]])
            continue_read.set()
            with pytest.raises(reel_replace_guard.ReplaceGuardUnreadable,
                               match="timeline 2 returned no object"):
                future.result(timeout=5)
            pytest.fail(
                "reproduced the concurrent timeline inventory race: an "
                "exclusive timeline writer entered between count and index "
                "reads")

        # The writer is queued behind the shared inventory lease. Let the
        # indexed reads finish, then confirm the writer enters afterward.
        continue_read.set()
        entries = future.result(timeout=5)
        assert [entry["name"] for entry in entries] == ["Master", "Reel 01"]
        assert select.select([holder.stdout], [], [], 5)[0], (
            "writer did not proceed after the inventory released its lease")
        assert holder.stdout.readline().strip() == "HELD"
    finally:
        continue_read.set()
        _release_holder(holder)
        executor.shutdown(wait=True)


def test_full_snapshot_records_the_project_transform_unit(monkeypatch):
    from library.tools import marker_feedback, reel_read

    project = FakeProject(timelines=["Reel 09"])
    project.SetSettings({"timelineResolutionWidth": 3840,
                         "timelineResolutionHeight": 2160})
    timeline = project.GetTimelineByIndex(1)
    monkeypatch.setattr(
        resolve_lock, "cursor_excursion",
        lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(reel_read, "read_tracks",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr(marker_feedback, "read_notes",
                        lambda *_args, **_kwargs: [])

    snapshot = reel_replace_guard.full_timeline_snapshot(timeline, project)

    assert snapshot["metadata"]["transform_unit_resolution"] == [3840, 2160]


def test_subtitle_row_merge_requires_sidecar_word_preservation(
        tmp_path, monkeypatch):
    from types import SimpleNamespace

    from library.tools import reel_read

    def caption_asset(stem, text):
        media = tmp_path / f"{stem}.mov"
        media.with_name(f"{media.stem}_props.json").write_text(
            json.dumps({"subtitles": [{"text": text}]}),
            encoding="utf-8")
        return str(media)

    old_assets = [
        caption_asset("old_a", "the link's bio."),
        caption_asset("old_b", "you should go check it out."),
    ]
    merged_asset = caption_asset(
        "merged", "the link's in our bio, you should go check it out.")
    lossy_asset = caption_asset(
        "lossy", "the link's in our bio, you should go check it.")
    unreadable_asset = tmp_path / "unreadable.mov"

    def track_for(assets):
        clips = []
        cursor = 0
        for index, path in enumerate(assets):
            duration = (180 if len(assets) == 1
                        else 100 if index == 0 else 80)
            clips.append({
                "name": Path(path).name,
                "source_file": path,
                "record_in": cursor,
                "record_out": cursor + duration,
                "duration": duration,
                "enabled": True,
            })
            cursor += duration
        return [{"type": "video", "index": 4, "name": "Subtitles",
                 "clips": clips}]

    tracks = {
        "retiring": track_for(old_assets),
        "merged": track_for([merged_asset]),
        "lossy": track_for([lossy_asset]),
        "unreadable": track_for([str(unreadable_asset)]),
    }
    monkeypatch.setattr(
        reel_read, "read_tracks", lambda timeline: tracks[timeline.GetName()])

    def snapshot(name):
        timeline = SimpleNamespace(GetName=lambda: name)
        return reel_replace_guard.snapshot_timeline(timeline, name)

    old = snapshot("retiring")
    merged = snapshot("merged")
    lossy = snapshot("lossy")
    unreadable = snapshot("unreadable")
    assert old["video:Subtitles"]["caption_tokens"][0]

    accepted = reel_replace_guard.check_replacement(
        "final", "merged", old, merged)
    assert accepted["joined"] == ["video:Subtitles"]

    with pytest.raises(reel_replace_guard.ReplaceGuardRefused,
                       match="video:Subtitles"):
        reel_replace_guard.check_replacement("final", "lossy", old, lossy)
    with pytest.raises(reel_replace_guard.ReplaceGuardRefused,
                       match="video:Subtitles"):
        reel_replace_guard.check_replacement(
            "final", "unreadable", old, unreadable)


def test_shrinking_subtitle_row_requires_exact_timed_words_and_year_merge(
        tmp_path, monkeypatch):
    from types import SimpleNamespace

    from library.tools import reel_read

    def caption_asset(stem, text, words):
        media = tmp_path / f"{stem}.mov"
        media.with_name(f"{media.stem}_props.json").write_text(
            json.dumps({
                "_source_in_frame": 0,
                "subtitles": [{"text": text, "words": words}],
            }),
            encoding="utf-8")
        return str(media)

    old_a = caption_asset("old_a", "from 20", [
        {"word": "from", "startFrame": 0, "endFrame": 5},
        {"word": "20", "startFrame": 10, "endFrame": 14},
    ])
    old_b = caption_asset("old_b", "21 had said different.", [
        {"word": "21", "startFrame": 0, "endFrame": 4},
        {"word": "had", "startFrame": 4, "endFrame": 9},
        {"word": "said", "startFrame": 9, "endFrame": 14},
        {"word": "different.", "startFrame": 14, "endFrame": 19},
    ])
    merged = caption_asset("merged", "from 2021 had said different.", [
        {"word": "from", "startFrame": 0, "endFrame": 5},
        {"word": "2021", "startFrame": 10, "endFrame": 18},
        {"word": "had", "startFrame": 18, "endFrame": 23},
        {"word": "said", "startFrame": 23, "endFrame": 28},
        {"word": "different.", "startFrame": 28, "endFrame": 33},
    ])
    dropped = caption_asset("dropped", "from 2021 had said", [
        {"word": "from", "startFrame": 0, "endFrame": 5},
        {"word": "2021", "startFrame": 10, "endFrame": 18},
        {"word": "had", "startFrame": 18, "endFrame": 23},
        {"word": "said", "startFrame": 23, "endFrame": 28},
    ])
    moved = caption_asset("moved", "from 2021 had said different.", [
        {"word": "from", "startFrame": 0, "endFrame": 5},
        {"word": "2021", "startFrame": 13, "endFrame": 21},
        {"word": "had", "startFrame": 21, "endFrame": 26},
        {"word": "said", "startFrame": 26, "endFrame": 31},
        {"word": "different.", "startFrame": 31, "endFrame": 36},
    ])
    unreadable = caption_asset("unreadable",
                               "from 2021 had said different.", [])

    def track_for(assets, starts, durations):
        clips = []
        for path, start, duration in zip(assets, starts, durations):
            clips.append({
                "name": Path(path).name,
                "source_file": path,
                "record_in": start,
                "record_out": start + duration,
                "source_in_frame": 0,
                "duration": duration,
                "enabled": True,
            })
        return [{"type": "video", "index": 4, "name": "Subtitles",
                 "clips": clips}]

    tracks = {
        "retiring": track_for([old_a, old_b], [0, 14], [30, 39]),
        "merged": track_for([merged], [0], [56]),
        "dropped": track_for([dropped], [0], [56]),
        "moved": track_for([moved], [0], [56]),
        "unreadable": track_for([unreadable], [0], [56]),
    }
    monkeypatch.setattr(reel_read, "read_tracks",
                        lambda timeline: tracks[timeline.GetName()])

    def snapshot(name):
        timeline = SimpleNamespace(GetName=lambda: name)
        return reel_replace_guard.snapshot_timeline(timeline, name)

    old = snapshot("retiring")
    accepted = snapshot("merged")
    lost_word = snapshot("dropped")
    moved_word = snapshot("moved")
    missing_timing = snapshot("unreadable")

    assert accepted["video:Subtitles"]["frames"] < old["video:Subtitles"]["frames"]
    assert reel_replace_guard.check_replacement(
        "final", "merged", old, accepted)["joined"] == ["video:Subtitles"]
    for incoming in (lost_word, moved_word, missing_timing):
        with pytest.raises(reel_replace_guard.ReplaceGuardRefused,
                           match="video:Subtitles"):
            reel_replace_guard.check_replacement("final", "staging", old,
                                                 incoming)


def test_empty_node_lut_shapes_do_not_make_a_color_edit():
    item = {
        "track_type": "video",
        "track_index": 1,
        "track_name": "Speaker",
        "name": "speaker.mov",
        "source_identity": "file:/media/speaker.mov",
        "source_in_frame": 0,
        "source_out_frame": 100,
        "record_in": 0,
        "record_out": 100,
        "duration": 100,
        "enabled": True,
        "transform": {},
        "composite": {},
        "fusion": {},
        "color": {"cdl": {}, "color_group": ""},
        "clip_color": "",
        "flags": [],
        "markers": [],
    }

    for empty_luts in (None, []):
        reread = {**item, "color": {**item["color"], "luts": empty_luts}}
        assert (
            reel_replace_guard.snapshot_diff({"items": [item]}, {"items": [reread]})
            == []
        )


def test_legacy_empty_lut_pending_change_is_normalized_before_promotion(tmp_path):
    from library.tools import plan_provenance

    final = "Reel 01 - legacy no-op LUT change"
    review_dir = tmp_path / "pipeline_output" / "review"
    item = {
        "track_type": "video",
        "track_index": 1,
        "track_name": "Speaker",
        "name": "speaker.mov",
        "source_identity": "file:/media/speaker.mov",
        "source_in_frame": 0,
        "source_out_frame": 100,
        "record_in": 0,
        "record_out": 100,
        "duration": 100,
        "enabled": True,
        "transform": {},
        "composite": {},
        "fusion": {},
        "color": {"cdl": {}, "color_group": ""},
        "clip_color": "",
        "flags": [],
        "markers": [],
    }
    reread = {**item, "color": {**item["color"], "luts": []}}
    before = {"items": [item]}
    after = {"items": [reread]}
    identity = reel_replace_guard._stable_item_key(item)
    legacy_change = {
        "kind": "item_changed",
        "identity": identity,
        "before": item,
        "after": reread,
        "changed": {"color": {"before": item["color"], "after": reread["color"]}},
    }
    record = {
        "id": "legacy-empty-lut",
        "timeline": final,
        "before_snapshot": before,
        "after_snapshot": after,
        "changes": [legacy_change],
        "status": "pending",
    }
    plan_provenance.record_timeline_snapshot(
        str(review_dir), final, before, action="test baseline"
    )
    plan_provenance.record_editor_changes(str(review_dir), final, [record])

    detection = reel_replace_guard.detect_editor_changes(
        str(tmp_path), final, after, before
    )

    assert detection["detected"] == []
    assert detection["pending"][0]["id"] == "legacy-empty-lut"
    assert detection["pending"][0]["changes"] == []
    promotion = reel_replace_guard.protect_editor_changes(
        str(tmp_path), final, after, before, after, detection=detection
    )
    assert promotion["carried"] == ["legacy-empty-lut"]
