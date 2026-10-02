"""Timed non-speech sound events reach the planners, and only those.

Fidelity rung 5e: the soundtrack carried no timed events - plans could
land a hit on a word, a beat or a frame, but never on the laugh or
impact the footage actually holds. Step 1.04 now banks PANNs AudioSet
spans per clip (`sound_events` + `sound_event_method`); these tests
drive the readers on fixture summaries shaped like the measured ones
(Music spans on the 10 s clip_10-20, a Cough burst on the multicam
audio) and assert the addressing and the refusal half: a label no clip
measured refuses with what the clip carries, never coined.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis import sound_event_pipeline as sep
from library.tools.context_views import build_view
from library.tools.frame_utils import seconds_to_frame
from library.tools.sound_events import (
    events,
    events_in_block,
    find_events,
    measured,
    present_labels,
)
from library.tools.sub_block_anchor import AnchorRefused, resolve_anchor

FPS = 30.0
METHOD = "panns-cnn14-decisionlevelmax"


def _summary(events_list=None, method=METHOD):
    return {
        "clip_id": "clip_001",
        "sound_events": (
            events_list if events_list is not None else [
                {"label": "Music", "start": 0.32, "end": 1.92,
                 "confidence": 0.717},
                {"label": "Music", "start": 3.52, "end": 7.67,
                 "confidence": 0.668},
                {"label": "Vehicle", "start": 2.56, "end": 3.2,
                 "confidence": 0.232},
            ]),
        "sound_event_method": method,
    }


def _block(clip_id="clip_001", source_start=0.0, source_end=10.0,
           tl_start=0.0, tl_end=10.0, position=3):
    return {
        "position": position,
        "clip_id": clip_id,
        "source_start": source_start,
        "source_end": source_end,
        "timeline_start": tl_start,
        "timeline_end": tl_end,
    }


# ── the reader ───────────────────────────────────────────────────────

def test_events_read_measured_spans_in_order_and_nothing_else():
    """Measured spans come back in time order; malformed rows are
    skipped rather than served; an unmeasured summary is empty, not an
    error."""
    rows = events(_summary())
    assert [(r["label"], r["start_seconds"], r["end_seconds"])
            for r in rows] == [
        ("Music", 0.32, 1.92),
        ("Vehicle", 2.56, 3.2),
        ("Music", 3.52, 7.67),
    ]
    assert present_labels(_summary()) == ["Music", "Vehicle"]

    summary = _summary(events_list=[
        {"label": "Music", "start": 1.0, "end": 2.0, "confidence": 0.5},
        {"label": "", "start": 1.0, "end": 2.0, "confidence": 0.5},
        {"label": "Cough", "start": "x", "end": 2.0, "confidence": 0.5},
        {"label": "Sneeze", "start": 3.0, "end": 2.0, "confidence": 0.5},
        "not-a-dict",
    ])
    assert [(r["label"], r["start_seconds"]) for r in events(summary)] == [
        ("Music", 1.0)]

    assert not measured({"clip_id": "c"})
    assert not measured({"clip_id": "c", "sound_event_method":
                         "unmeasured: no checkpoint", "sound_events": []})
    assert events({"clip_id": "c"}) == []
    assert present_labels({"clip_id": "c"}) == []


def test_find_events_matches_case_insensitively_keeps_verbatim():
    matches, present = find_events(_summary(), "music")
    assert [m["label"] for m in matches] == ["Music", "Music"]
    assert present == ["Music", "Vehicle"]
    matches, present = find_events(_summary(), "Laughter")
    assert matches == [] and present == ["Music", "Vehicle"]


def test_events_in_block_tolerates_the_cut_edge():
    rows = events_in_block(_summary(), 1.92, 10.0)
    assert [r["label"] for r in rows] == ["Music", "Vehicle", "Music"]
    assert events_in_block(_summary(), 7.8, 10.0) == []


# ── the anchor ───────────────────────────────────────────────────────

def _anchor(anchor, block=None, summaries=None, index=0):
    return resolve_anchor(
        anchor, block=block or _block(),
        temporal_indices=[_summary()] if summaries is None else summaries,
        frame_rate=FPS, step="plan_sfx", plan="sfx_creative", index=index)


def test_event_anchor_resolves_to_the_addressed_frame():
    for anchor, seconds in (
            ({"event": "Music"}, 0.32),
            ({"event": "Music", "occurrence": 2}, 3.52),
            ({"event": "Music", "edge": "end"}, 1.92)):
        hit = _anchor(anchor)
        assert hit["frame"] == seconds_to_frame(seconds, FPS), anchor
    assert "Music" in _anchor({"event": "Music"})["method"]


# (anchor, block, summaries, what the refusal says, what it lists)
UNRESOLVABLE = [
    ({"event": "Laughter"}, None, None, "not measured inside block",
     "Music, Vehicle"),
    ({"event": "Music", "occurrence": 5}, None, None,
     "occurrence 5 was asked for", None),
    ({"event": "Music", "edge": "middle"}, None, None,
     "is not 'start' or 'end'", None),
    ({"event": "Music", "grid": "detected"}, None, None,
     "grid applies to beat anchors", None),
    ({"event": ""}, None, None, "names no event", None),
    ({"event": "Music", "word": "quit"}, None, None, "names 2 addresses",
     None),
    ({"event": "Music"}, _block(source_start=4.0, source_end=10.0), None,
     "Music", None),
    ({"event": "Music"}, None, [_summary(method="unmeasured: no checkpoint")],
     "no measured sound events", None),
    ({"event": "Music"}, None, [], "no sound-event measurement is routed",
     None),
    ({"event": "Music"}, _block(clip_id=None), None, "names no source clip",
     None),
]


def test_unresolvable_event_anchors_refuse_with_the_fix():
    """A label no clip measured refuses with what the clip carries,
    never coined; every other unanswerable address refuses by name."""
    for anchor, block, summaries, why, lists in UNRESOLVABLE:
        with pytest.raises(AnchorRefused) as exc:
            _anchor(anchor, block=block, summaries=summaries, index=7)
        assert why in str(exc.value.what), anchor
        assert "entry 7" in str(exc.value.why), anchor
        if lists is not None:
            assert lists in str(exc.value.what), anchor


# ── the view ─────────────────────────────────────────────────────────

def _view_data(blocks, summaries):
    return {"temporal_event_indices": summaries,
            "timed_spine": {"structure": blocks}}


def test_soundevents_view_carries_spans_and_names_the_unmeasured():
    data = _view_data([_block(), _block(position=4, clip_id="clip_002")],
                      [_summary()])
    view = build_view("soundevents", data)["soundevents"]
    assert view["blocks_measured"] == 1
    row = view["blocks"][0]
    assert row["block_position"] == 3
    assert [(e["label"], e["start_seconds"], e["end_seconds"])
            for e in row["events"]] == [
        ("Music", 0.32, 1.92),
        ("Vehicle", 2.56, 3.2),
        ("Music", 3.52, 7.67),
    ]
    assert "legend" in view
    assert "4" in view["not_measured"]
    assert build_view("soundevents", {}) == {}


# ── the producer ─────────────────────────────────────────────────────

def test_runs_merge_gaps_and_drop_blips():
    # Gaps under MERGE_GAP_SECONDS close (the 0.1 s and 0.2 s gaps
    # both merge); a lone 0.1 s blip drops under MIN_DURATION_SECONDS.
    assert sep._runs([False, True, True, False, True, True, True,
                      False, False, True, False], 10.0) == [
        (0.1, 1.0),
    ]
    assert sep._runs([True] + [False] * 20, 10.0) == []


def test_missing_checkpoint_is_unmeasured_never_a_guess(tmp_path):
    # The checkpoint check runs before any audio is read, so the
    # source path never needs to decode. Without the ML stack the
    # same call raises the same type naming the missing import -
    # either way the clip is unmeasured, never guessed.
    missing = str(tmp_path / "nope.pth")
    with pytest.raises(sep.SoundEventsUnavailable) as exc:
        sep.measure_sound_events(__file__, checkpoint_path=missing)
    assert ("not on this machine" in str(exc.value)
            or "is not installed" in str(exc.value))


# ── ren doctor reports the checkpoint ────────────────────────────────

def _stub_doctor_models(monkeypatch, tmp_path, panns_ok):
    from ren import doctor
    import library.tools.shared_environment as se

    monkeypatch.setattr(doctor, "hf_hub_cache",
                        lambda: tmp_path / "hf")
    monkeypatch.setattr(doctor, "torch_checkpoints",
                        lambda: tmp_path / "torch")
    monkeypatch.setattr(doctor, "resolve_interpreter",
                        lambda: (None, ""))
    monkeypatch.setattr(se, "mfa_available", lambda: (False, ""))
    monkeypatch.setattr(se, "panns_available", lambda: (panns_ok, ""))
    monkeypatch.setattr(se, "panns_checkpoint",
                        lambda *a: tmp_path / "x.pth")
    return doctor


def test_doctor_reports_panns_and_its_absence_never_fails(monkeypatch,
                                                         tmp_path):
    doctor = _stub_doctor_models(monkeypatch, tmp_path, True)
    (tmp_path / "x.pth").write_bytes(b"0" * 64)
    check = next(c for c in doctor.model_checks()
                 if c.name == "model PANNs Cnn14-DLM")
    assert check.ok

    doctor = _stub_doctor_models(monkeypatch, tmp_path, False)
    check = next(c for c in doctor.model_checks()
                 if c.name == "model PANNs Cnn14-DLM")
    assert not check.ok and "not downloaded" in check.detail
    assert doctor.required_failures([check]) == []
    assert "scripts/install_panns.sh" in check.fix
