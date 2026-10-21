"""The picture-only split adapter: an exact cut, and an untouched audio.

The defect this names: `clip.split` had no AXI writer at all - the only
split was a staged rebuild that refused a LINKED item, so a cut on a
linked picture could not be placed, and nothing measured whether the
audio beside the picture survived it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from library.tools import edit_execution_adapters as adapters


def _video_clip():
    return {
        "unique_id": "clip-17", "record_in": 20, "record_out": 80,
        "source_in_frame": 5, "source_out_frame": 64,
        "duration": 60, "source_file": "/media/a.mov",
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0},
        "enabled": True, "clip_color": "", "flags": [],
        "track_type": "video", "track_index": 2,
        "linked_unique_ids": ["audio-9"],
    }


def _audio_clip(record_in=20, record_out=80):
    return {
        "unique_id": "audio-9", "record_in": record_in,
        "record_out": record_out, "source_in_frame": 5,
        "source_out_frame": 64, "source_file": "/media/a.mov",
        "volume": 0.0, "enabled": True,
        "linked_unique_ids": ["clip-17"],
    }


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot={"reported_fps": 24.0, "tracks": [
            {"type": "video", "index": 2, "clips": [_video_clip()]},
            {"type": "audio", "index": 1, "clips": [_audio_clip()]},
        ]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


def _after():
    """The read-back after the split: two halves, the audio untouched."""
    return {"reported_fps": 24.0, "tracks": [
        {"type": "video", "index": 2, "clips": [
            {**_video_clip(), "record_in": 20, "record_out": 44,
             "source_in_frame": 5, "source_out_frame": 29,
             "unique_id": "clip-17a", "linked_unique_ids": ["audio-9"]},
            {**_video_clip(), "record_in": 44, "record_out": 80,
             "source_in_frame": 29, "source_out_frame": 64,
             "unique_id": "clip-17b", "linked_unique_ids": ["audio-9"]},
        ]},
        {"type": "audio", "index": 1, "clips": [_audio_clip()]},
    ]}


def _wire(monkeypatch, after):
    from library.tools import resolve_axi_core

    calls = []
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        calls.append(command) or SimpleNamespace(
            ok=True, stdout="journal:\n  entry: journal-17", stderr="")))
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda *_args: after)
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: "/tmp/project")
    return calls


def test_the_split_adapter_asks_for_a_source_frame_and_reads_the_cut_back(
        monkeypatch):
    calls = _wire(monkeypatch, _after())

    receipt = adapters._split(_context({
        "op": "clip.split", "unique_id": "clip-17", "at_frame": 44,
    }))

    assert calls == [[
        "edit", "split", "--track", "V2", "--index", "0",
        "--at-source-frame", "29",
        "--target-unique-id", "clip-17",
        "--expected-source-file", "/media/a.mov",
        "--expected-source-in-frame", "5",
        "--expected-source-out-frame", "64",
        "--expected-record-in-frame", "20",
        "--expected-record-out-frame", "80",
        "--project-folder", "/tmp/project", "--require-recovery",
        "--apply",
        "--project", "project-1", "--timeline", "Reel 01 - opening",
    ]]
    results = {row["check"]: row
               for row in receipt["acceptance_contracts"][0]["results"]}
    assert results["exact_cut_frame"]["passed"] is True
    assert results["linked_audio_preserved"]["passed"] is True
    assert receipt["acceptance_contracts"][0]["passed"] is True


def test_the_split_adapter_refuses_when_the_audio_moved(monkeypatch):
    after = _after()
    after["tracks"][1]["clips"][0]["record_out"] = 44
    _wire(monkeypatch, after)

    with pytest.raises(RuntimeError,
                       match="read-back acceptance failed for clip.split"):
        adapters._split(_context({
            "op": "clip.split", "unique_id": "clip-17", "at_frame": 44,
        }))
