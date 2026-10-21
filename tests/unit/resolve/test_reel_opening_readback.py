"""The opening's own read-back follows the clip it tightened.

A `reel.opening` tightens the opening through the structural edits it
carries, and a trim RE-PLACES the clip it tightens: the trimmed clip is
a NEW item with a NEW unique id, and the original is gone.

Measured 2026-10-21 on the product-format scratch (the live
reel.opening qualification): the trim landed - the opening clip read
back at record 0..72 with the source window the decision named - and
the operation still refused its own success, because its read-back
addressed the target by the id it no longer had:

    AXI read-back acceptance failed for reel.opening: missing evidence
    [none], failed checks [target_identity, requested_opening_span,
    approved_edits_preserved]

The clip.trim adapter's own read-back already followed the target to
its new identity (PR 3028); the OPENING's aggregate read-back did not,
so the operation refused a write that landed.

These tests name the defect and hold the fix in place.
"""

from __future__ import annotations

from types import SimpleNamespace

from library.tools import edit_execution_adapters as adapters


def _clip(unique_id, *, record_in, record_out, source_in, source_out,
          **overrides):
    clip = {
        "unique_id": unique_id, "record_in": record_in,
        "record_out": record_out, "source_in_frame": source_in,
        "source_out_frame": source_out,
        "duration": record_out - record_in,
        "source_file": "/media/opening.mov",
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0},
        "enabled": True, "speed": 100.0, "name": "opening.mov",
        "track_type": "video", "track_index": 1,
    }
    clip.update(overrides)
    return clip


def _snapshot(clips):
    return {"reported_fps": 23.976, "tracks": [
        {"type": "video", "index": 1, "clips": list(clips)}]}


BEFORE = _snapshot([_clip("clip-17", record_in=0, record_out=95,
                           source_in=0, source_out=95),
                    _clip("clip-18", record_in=96, record_out=143,
                          source_in=0, source_out=47,
                          source_file="/media/next.mov", name="next.mov")])
AFTER = _snapshot([_clip("clip-19", record_in=0, record_out=72,
                          source_in=0, source_out=72),
                   _clip("clip-18", record_in=96, record_out=143,
                         source_in=0, source_out=47,
                         source_file="/media/next.mov", name="next.mov")])

#: The trim that produced AFTER: the opening clip, re-placed.
EDIT = {"op": "clip.trim", "unique_id": "clip-17",
        "in_frame": 0, "out_frame": 72}
#: Which id the target became.
ID_MAP = {"clip-17": "clip-19"}


def test_a_replaced_opening_is_read_as_the_same_target():
    """Catches: the opening's read-back judging the re-placed clip as a
    clip that appeared and the original as a clip that was removed (the
    live reel.opening qualification: the trim landed and the opening
    read `approved_edits_preserved` as failed)."""
    assert adapters._opening_preserved(
        BEFORE, AFTER, [EDIT], id_map=ID_MAP) is True


def test_the_same_read_back_without_the_identity_fails():
    """The defect is the missing identity, not the state."""
    assert adapters._opening_preserved(BEFORE, AFTER, [EDIT]) is False


def test_a_clip_that_appeared_is_still_refused():
    """Catches: the identity read-back waving a clip through that the
    opening never decided."""
    appeared = _snapshot(list(AFTER["tracks"][0]["clips"]) + [
        _clip("clip-20", record_in=200, record_out=240, source_in=0,
              source_out=40, source_file="/media/other.mov",
              name="other.mov")])
    assert adapters._opening_preserved(
        BEFORE, appeared, [EDIT], id_map=ID_MAP) is False


def test_the_replaced_opening_must_keep_its_approved_state():
    """Catches: the re-placed clip read as the target without its
    approved picture state being checked."""
    moved = _snapshot([
        _clip("clip-19", record_in=0, record_out=72, source_in=0,
              source_out=72, transform={"Pan": -40.0, "Tilt": 0.0,
                                        "ZoomX": 1.0}),
        BEFORE["tracks"][0]["clips"][1]])
    assert adapters._opening_preserved(
        BEFORE, moved, [EDIT], id_map=ID_MAP) is False


def test_the_trim_receipt_carries_the_id_the_target_became(monkeypatch):
    """Catches: the opening's aggregate read-back left without the
    target's new identity, so it could not follow the clip."""
    from library.tools import resolve_axi_core

    after = {"reported_fps": 23.976, "tracks": [{
        "type": "video", "index": 2, "clips": [{
            "unique_id": "clip-18", "record_in": 24, "record_out": 76,
            "source_in_frame": 9, "source_out_frame": 60,
            "duration": 52, "source_file": "/media/music.wav",
            "transform": {"Pan": -24.0, "Tilt": 0.0, "ZoomX": 1.0},
            "enabled": True, "clip_color": "", "flags": [],
            "track_type": "video", "track_index": 2,
            "linked_unique_ids": [],
        }],
    }]}
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        SimpleNamespace(
            ok=True,
            stdout="command completed\njournal:\n  entry: journal-17",
            stderr="")))
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda *_args: after)
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: "/tmp/project")

    context = SimpleNamespace(
        operation={"op": "clip.trim", "unique_id": "clip-17",
                   "in_frame": 24, "out_frame": 76},
        snapshot={"reported_fps": 30.0, "tracks": [{
            "type": "video", "index": 2,
            "clips": [{
                "unique_id": "clip-17", "record_in": 20, "record_out": 80,
                "source_in_frame": 5, "source_out_frame": 64,
                "duration": 60, "source_file": "/media/music.wav",
                "transform": {"Pan": -24.0, "Tilt": 0.0, "ZoomX": 1.0},
                "enabled": True, "clip_color": "", "flags": [],
                "track_type": "video", "track_index": 2,
                "linked_unique_ids": [],
            }],
        }]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening"),
    )
    receipt = adapters._trim(context)

    assert receipt["readback"]["id_map"] == {"clip-17": "clip-18"}
