"""One batched footage placement pass commits what N per-insert patches did.

The reel builder used to place every footage clip through its own
`apply_live_patch` plus its own `sweep_placed_audio`: each patch
re-read the whole timeline (observe, pre/post read-back, handle walk)
and re-loaded the Project Model twice, so a one-reel build paid
O(n^2) Resolve calls and O(n) model loads - measured 2026-10-17 at
46x for a 10x scale probe and ~10 s per model load. The builder now
collects the pass into ONE patch plus ONE `sweep_placed_audio_many`,
and `edit_patch` no longer re-walks the rows after an insert no later
op addresses by handle.

These tests pin the replacement from the cheap side (the fake Resolve
double, no live connection): the batched end state matches the
sequential one item by item, strays are still deleted on the spot, an
insert-only batch makes a constant number of live row reads, and a
pinned model revision skips the per-patch reloads.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from library.tools import edit_patch
from library.tools import timeline_shadow as shadow
from library.tools.reel_build import (
    _placed_channel,
    _speech_row_uids,
    _timeline_span,
    commit_footage_placements,
    sweep_placed_audio,
    sweep_placed_audio_many,
)
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeTimeline,
    make_pool_clip,
    make_project,
)


def _world(audio_channels=(1, 2)):
    project = make_project("Bench")
    timeline = project.adopt(FakeTimeline("Reel 26", project=project))
    timeline._ensure_track("video", 1)
    timeline._ensure_track("audio", 1)
    timeline._ensure_track("audio", 2)
    project.SetCurrentTimeline(timeline)
    pool = project.GetMediaPool()
    pool.audio_channels = tuple(audio_channels)
    clips = []
    for name in ("a1-take1.MXF", "a2-take1.MXF", "a1-take2.MXF",
                 "a2-take2.MXF"):
        clip = make_pool_clip(name, path=f"/m/{name}")
        pool.GetRootFolder()._clips.append(clip)
        clips.append(clip)
    return project, timeline, pool, clips


def _plan():
    """Three picture/speech pairs, both mics hot on the middle one."""
    return [
        # (clip_index, track_type, row, record_in)
        (0, "video", 1, 0),
        (0, "audio", 1, 0),
        (1, "audio", 2, 0),
        (2, "video", 1, 100),
        (2, "audio", 1, 100),
        (3, "audio", 2, 100),
    ]


def _op(name, clips, entry, duration=48):
    clip_index, track_type, row, record_in = entry
    return {
        "op": "clip.insert",
        "unique_id": f"footage:{name}:{track_type}:clip{clip_index}:{record_in}",
        "track_type": track_type,
        "track_index": row,
        "record_in": record_in,
        "record_out": record_in + duration,
        "media_pool_item": str(clips[clip_index].GetUniqueId()),
        "source_in": 0,
        "source_out": duration,
    }


def _exp(clips, entry):
    clip_index, track_type, row, record_in = entry
    pool_item = clips[clip_index]
    return {
        "target_row": row,
        "expected_channel": 1,
        "label": f"A{row} clip{clip_index} @{record_in}",
        "record_in": record_in,
        "source_file": pool_item.GetClipProperty("File Path"),
    }


def _inventory(timeline):
    """Per-row (span, source, channel) for every speech item, in order."""
    out = {}
    for row in (1, 2):
        items = timeline.GetItemListInTrack("audio", row) or []
        rows = []
        for item in items:
            pool_item = item.GetMediaPoolItem()
            rows.append((
                _timeline_span(item),
                pool_item.GetClipProperty("File Path"),
                _placed_channel(item),
            ))
        out[row] = rows
    return out


def _sequential(project, timeline, pool, clips, store, entries):
    """The old semantics: one patch plus one sweep per placement."""
    labels = []
    plan = SimpleNamespace(
        speech_rows=lambda: [SimpleNamespace(index=1),
                             SimpleNamespace(index=2)])
    for entry in entries:
        op = _op("Reel 26", clips, entry)
        before = _speech_row_uids(timeline, plan)
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[op], conflict_domains=("timeline_structure",),
            idempotency_key=f"seq:{op['unique_id']}", store=store)
        assert receipt["status"] == "committed", receipt
        if entry[1] != "video":
            exp = _exp(clips, entry)
            kept, deleted, unverified = sweep_placed_audio(
                timeline, plan, before, exp["target_row"],
                exp["expected_channel"], exp["label"])
            labels.append((exp["label"], len(kept), len(deleted),
                           len(unverified)))
    return labels


def test_batched_pass_matches_sequential_patches_item_by_item(tmp_path):
    """The batch is the same timeline the per-insert loop built.

    Both mics are hot on the middle span and every audio append spills
    its non-program stream onto the neighbour row (the fake pool's
    `audio_channels=(1, 2)` shape); the sequential run deletes each
    spill in its own sweep and the batch must delete the same ones.
    """
    name = "Reel 26"
    entries = _plan()

    project, timeline, pool, clips = _world()
    store = shadow.ShadowStore(tmp_path / "seq.db")
    with assume_sole_writer("batched-vs-sequential"):
        shadow.observe(project, timeline, store)
        _sequential(project, timeline, pool, clips, store, entries)
    seq_inventory = _inventory(timeline)

    project2, timeline2, pool2, clips2 = _world()
    store2 = shadow.ShadowStore(tmp_path / "batched.db")
    with assume_sole_writer("batched-vs-sequential"):
        shadow.observe(project2, timeline2, store2)
        ops = [_op(name, clips2, entry) for entry in entries]
        video_ops = [op for op in ops if op["track_type"] == "video"]
        pairs = [(op, _exp(clips2, entry)) for op, entry in
                 zip(ops, entries) if entry[1] != "video"]
        record = commit_footage_placements(
            project2, timeline2,
            SimpleNamespace(
                speech_rows=lambda: [SimpleNamespace(index=1),
                                     SimpleNamespace(index=2)]),
            name, video_ops, pairs, project_folder=None,
            model_revision=None)
    assert record["unverified"] == []
    assert len(record["deleted"]) == 4
    assert record["batches"] == 3, record  # video + two audio layers
    assert _inventory(timeline2) == seq_inventory


def test_insert_only_batch_makes_constant_live_row_reads(tmp_path):
    """An insert-only batch walks the rows once, not once per insert.

    Before the fix, every `timeline_structure` op re-walked
    `_live_handles` even when no later op addresses an item by handle,
    so even one batched patch paid O(n) walks. Twelve inserts must
    stay under a constant bound that per-insert walks would blow past.
    """
    project, timeline, pool, clips = _world(audio_channels=(1,))
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    calls = {"reads": 0}
    original = FakeTimeline.GetItemListInTrack

    def _counted(self, kind, index):
        calls["reads"] += 1
        return original(self, kind, index)

    timeline.GetItemListInTrack = _counted.__get__(timeline, FakeTimeline)
    counts = {}
    with assume_sole_writer("constant-reads"):
        shadow.observe(project, timeline, store)
        for batch, base in ((6, 10000), (12, 20000)):
            calls["reads"] = 0
            ops = []
            for i in range(batch):
                ops.append({
                    "op": "clip.insert",
                    "unique_id": f"footage:Reel 26:video:clip{i}:{base + i * 50}",
                    "track_type": "video",
                    "track_index": 1,
                    "record_in": base + i * 50,
                    "record_out": base + i * 50 + 48,
                    "media_pool_item": str(clips[i % len(clips)].GetUniqueId()),
                    "source_in": 0,
                    "source_out": 48,
                })
            receipt = edit_patch.apply_live_patch(
                project=project, timeline=timeline, capability="reel.build",
                operations=ops, conflict_domains=("timeline_structure",),
                idempotency_key=f"batched:constant-reads:{batch}",
                store=store)
            assert receipt["status"] == "committed", receipt
            # Three full-timeline reads (observe excluded above: pre,
            # post plus the patch's own planning read) over the rows,
            # plus verification from the recorded snapshot - and nothing
            # per insert. A per-insert handle walk would read 6x/12x that.
            counts[batch] = calls["reads"]
    assert counts[6] == counts[12], counts
    assert counts[12] <= 60, counts


def test_mixed_batch_still_addresses_just_placed_clips(tmp_path):
    """Incremental handles carry across inserts: a later op addresses a
    clip an earlier op of the SAME patch placed, by its client id."""
    project, timeline, pool, clips = _world(audio_channels=(1,))
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("mixed-batch"):
        shadow.observe(project, timeline, store)
        operations = []
        for i in range(3):
            operations.append({
                "op": "clip.insert",
                "unique_id": f"caption:{i}",
                "track_type": "video",
                "track_index": 1,
                "record_in": 10 + i * 50,
                "record_out": 40 + i * 50,
                "media_pool_item": str(clips[i % len(clips)].GetUniqueId()),
                "source_in": 0,
                "source_out": 30,
            })
            operations.append({
                "op": "clip.set_property",
                "unique_id": f"caption:{i}",
                "key": "Tilt",
                "value": -100.0 - i,
            })
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="render.build",
            operations=operations,
            conflict_domains=("timeline_structure", "picture_transform"),
            idempotency_key="mixed:insert-then-address",
            store=store,
            project_folder=str(tmp_path))
    assert receipt["status"] == "committed", receipt
    head = store.head("Bench", receipt["readback"]["timeline_id"])
    placed = [c for c in shadow.clips(store.snapshot(head))
              if c["record_in"] in (10, 60, 110)]
    assert len(placed) == 3
    assert sorted(p["transform"]["Tilt"] for p in placed) == [-102.0, -101.0,
                                                             -100.0]


def test_pinned_model_revision_skips_per_patch_reloads(tmp_path, monkeypatch):
    """A caller placing many patches loads the model once, not per patch."""
    import json as _json
    from pathlib import Path as _Path

    from library.tools import project_model_editorial, project_model_store

    calls = {"loads": 0}
    original = project_model_store.load_project_model

    def _counted(project):
        calls["loads"] += 1
        return original(project)

    monkeypatch.setattr(project_model_store, "load_project_model", _counted)
    # The readback commit is a different concern (and would advance the
    # revision under the pin); neutralize it for this counting test.
    monkeypatch.setattr(project_model_editorial, "commit_edit_readback",
                        lambda *a, **k: 0)
    model = _json.loads(_Path(
        "native/ren-protocol/fixtures/project-model.json"
    ).read_text(encoding="utf-8"))
    project_model_store.commit_project_model(
        tmp_path, model, expected_revision=0)
    project, timeline, pool, clips = _world(audio_channels=(1,))
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    pool_clip = clips[0]
    operation = {
        "op": "clip.insert",
        "unique_id": "footage:Reel 26:video:pinned",
        "track_type": "video",
        "track_index": 1,
        "record_in": 0,
        "record_out": 48,
        "media_pool_item": str(pool_clip.GetUniqueId()),
        "source_in": 0,
        "source_out": 48,
    }
    with assume_sole_writer("pinned-revision"):
        shadow.observe(project, timeline, store)
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[dict(operation)],
            conflict_domains=("timeline_structure",),
            idempotency_key="pinned:one",
            store=store, project_folder=str(tmp_path),
            model_revision=1)
        assert receipt["status"] == "committed", receipt
        assert calls["loads"] == 0
        before = calls["loads"]
        receipt = edit_patch.apply_live_patch(
            project=project, timeline=timeline, capability="reel.build",
            operations=[dict(operation, unique_id="footage:Reel 26:video:other",
                             record_in=100, record_out=148)],
            conflict_domains=("timeline_structure",),
            idempotency_key="unpinned:one",
            store=store, project_folder=str(tmp_path))
        assert receipt["status"] == "committed", receipt
        # Both forms use the validated revision pointer, without loading
        # unused model data. Actual readback commits remain separate.
        assert calls["loads"] == before, calls


def test_sweep_many_reports_unmatched_additions_without_deleting(tmp_path):
    """An added item no placement of the pass owns is foreign evidence:
    kept and reported, never deleted."""
    from unittest.mock import MagicMock

    item = MagicMock()
    item.GetUniqueId.return_value = "foreign"
    item.GetStart.return_value = 999
    item.GetEnd.return_value = 1047
    item.GetSourceAudioChannelMapping.return_value = json.dumps(
        {"track_mapping": {"1": {"channel_idx": [2]}}})
    pool_item = MagicMock()
    pool_item.GetClipProperty.return_value = "/m/foreign.MXF"
    item.GetMediaPoolItem.return_value = pool_item
    timeline = MagicMock()
    timeline.GetItemListInTrack.side_effect = lambda kind, index: (
        [item] if (kind, index) == ("audio", 1) else [])
    timeline.DeleteClips.return_value = True
    plan = SimpleNamespace(
        speech_rows=lambda: [SimpleNamespace(index=1)])
    kept_by, kept_all, deleted, unverified = sweep_placed_audio_many(
        timeline, plan, {1: set()},
        [{"target_row": 1, "expected_channel": 1,
          "label": "A1 known @0", "record_in": 0,
          "source_file": "/m/known.MXF"}])
    assert deleted == []
    assert timeline.DeleteClips.call_count == 0
    assert kept_all == [item]
    assert any("matches no placement" in line for line in unverified)


def test_batch_reads_the_revision_advanced_by_its_previous_readback(tmp_path, monkeypatch):
    """Real model commits, unlike a neutralized readback, advance the pin."""
    from pathlib import Path
    from library.tools import project_model_store

    model = json.loads(Path(
        "native/ren-protocol/fixtures/project-model.json"
    ).read_text(encoding="utf-8"))
    project_model_store.commit_project_model(tmp_path, model, expected_revision=0)
    project, timeline, pool, clips = _world(audio_channels=(1, 2))
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    original = edit_patch.apply_live_patch
    def apply(**kwargs):
        return original(**kwargs, store=store)
    monkeypatch.setattr(edit_patch, "apply_live_patch", apply)
    plan = SimpleNamespace(speech_rows=lambda: [
        SimpleNamespace(index=1), SimpleNamespace(index=2)])
    entries = _plan()
    video_ops = [_op("Reel 26", clips, entry) for entry in entries
                 if entry[1] == "video"]
    audio_pairs = [(_op("Reel 26", clips, entry), _exp(clips, entry))
                   for entry in entries if entry[1] == "audio"]
    with assume_sole_writer("real-model-batch"):
        result = commit_footage_placements(
            project, timeline, plan, "Reel 26", video_ops, audio_pairs,
            project_folder=str(tmp_path), model_revision=1)
    assert result["batches"] == 3
    revision, model = project_model_store.load_project_model(tmp_path)
    assert revision == 4
    assert len(model["edits"]) == 4
    assert result["unverified"] == []
