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
from library.tools import (
    conversation_clock,
    event_spans,
    person_entity,
    person_measurements,
    shared_environment,
    single_track_diarization,
    source_memory,
)
from library.tools.ren_refusal import RenRefusal


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
    assert "ren setup --with panns" in check.fix


# --------------------------------------------------------------------------
# From test_event_spans.py
#
# M7 event spans and the structured query: the defects this task found.
#
# Every test builds its memory and project under `tmp_path`; nothing runs
# Vision, insightface or ECAPA. What the measured parts are worth (the
# face-identity FAR, the diarization DER, the hand_near_mouth rule that was
# NOT shipped) is in the eval directories those modules name.

@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def test_lips_are_mapped_out_of_their_face_box_into_image_space():
    """Vision's landmark points are relative to the FACE BOX; hand joints
    are relative to the IMAGE. The Vision lane's hand-over-mouth eval
    compared the two raw and read FP 99/265 (23/265 once mapped). M3
    stores lips in image space so no reader can repeat that."""
    box = [0.4, 0.2, 0.6, 0.5]  # x1, y1, x2, y2 in the image
    assert person_measurements.lips_to_image([[0.5, 0.5], [0.0, 1.0]], box) == [
        [0.5, 0.35], [0.4, 0.5]]


def _m3(frames):
    return {"instrument": {"frame_pixels": [384, 216]}, "frames": frames}


def _face(opening):
    """A face box with inner lips `opening` of its height apart."""
    return {"box": [0.4, 0.2, 0.5, 0.4],
            "inner_lips": [[0.45, 0.35], [0.45, 0.35 + opening * 0.2]]}


def test_a_voice_is_linked_to_the_face_whose_lips_move_with_it():
    """On a single-person angle the listener's voice co-occurs with the
    only face on screen as much as the speaker's does, so co-occurrence
    (M3b's `speech_face_links`) attaches BOTH voices to that face. The
    link must come from the lips: the face moves its mouth during its own
    voice's turns and holds still during the other's."""
    frames, assignment = [], []
    for i in range(200):
        t = i * 0.5
        talking = t < 50.0  # voice_A owns the first 50 s
        opening = (0.02 if i % 2 else 0.12) if talking else 0.05
        frames.append({"t": t, "faces": [_face(opening)], "hands": []})
        assignment.append(["face_001"])
    identity = {"voices": [{"track_id": "voice_A", "spans": [[0.0, 49.9]]},
                           {"track_id": "voice_B", "spans": [[50.0, 99.9]]}]}

    links = {link["voice_track"]: link["face_track"]
             for link in event_spans.link_voices_to_faces(_m3(frames), assignment, identity)}

    assert links == {"voice_A": "face_001", "voice_B": None}


def test_a_source_resolution_face_box_is_joined_at_its_own_scale():
    """M3b draws its boxes on source-resolution frames at M2's times, not
    on the 384x216 thumbnails M3 read. Scaled by M3's `frame_pixels`, a
    4K box lands ten frames off-screen and no Vision face is ever
    assigned a track."""
    m3 = _m3([{"t": 10.0, "faces": [{"box": [0.4, 0.2, 0.5, 0.4]}], "hands": []}])
    identity = {
        "instrument": {"frame_source": person_entity.FRAME_SOURCE_M2_TIMES,
                       "frame_pixels": [3840, 2160]},
        "faces": [{"track_id": "face_001", "spans": [
            {"start": 9.9, "end": 10.1, "box": [1536.0, 432.0, 1920.0, 864.0]}]}]}

    assert event_spans.assign_face_tracks(m3, identity) == [["face_001"]]


def _clock(digest, members, offset):
    source_memory.write_json(
        source_memory.source_dir(digest) / conversation_clock.SLOT_CLOCK,
        {"content_digest": digest, "group_id": "g", "group_members": members,
         "reference_digest": members[0], "offset_to_reference_seconds": offset,
         "path_to_reference": [digest], "direct_measurement": None,
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})


def test_a_span_on_one_angle_is_placed_on_the_items_that_play_the_other(memory_root):
    """The captain's example is a gesture seen on SpeakerTwo's angle, but the
    timeline also plays SpeakerOne's angle at that moment. A span must reach
    every item that plays THAT MOMENT of the conversation, through the M6
    clock - and nothing past an item's recorded source extent."""
    _clock("SPEAKERONE", ["SPEAKERONE", "SPEAKERTWO"], 0.0)
    _clock("SPEAKERTWO", ["SPEAKERONE", "SPEAKERTWO"], 3.8)  # speakertwo_t + 3.8 == speakerone_t
    items = [
        {"resolve_item_id": "item-a", "speaker": "SpeakerOne", "source_file": "/a.mxf",
         "offset": 1000.0 - 500.0, "source_start": 500.0, "source_end": 520.0,
         "segments": [(500.0, 520.0)]},
        {"resolve_item_id": "item-c", "speaker": "SpeakerTwo", "source_file": "/c.mxf",
         "offset": 2000.0 - 600.0, "source_start": 600.0, "source_end": 610.0,
         "segments": [(600.0, 610.0)]},
    ]
    files = {"/a.mxf": "SPEAKERONE", "/c.mxf": "SPEAKERTWO"}

    placements = event_spans.place("proj", "SPEAKERTWO", 501.2, 503.2, items, files)

    assert placements == [{
        "resolve_item_id": "item-a", "track_speaker": "SpeakerOne",
        "via": "M6 clock", "source_file": "/a.mxf",
        "source_start": 505.0, "source_end": 507.0,
        "timeline_start": 1005.0, "timeline_end": 1007.0}]


def test_a_predicate_the_measurement_cannot_answer_is_refused(tmp_path):
    """hand_near_mouth failed its pre-registered bar: an empty answer to
    "when does SpeakerTwo cover his mouth" reads as "never". on_screen spans
    run the length of every take: verifying them is the whole-episode VLM
    pass the candidate stage exists to avoid."""
    with pytest.raises(RenRefusal) as refused:
        event_spans.query(str(tmp_path), "SpeakerTwo", "hand_near_mouth")
    assert "11/17" in str(refused.value.why)
    with pytest.raises(RenRefusal) as refused:
        event_spans.verified_query(str(tmp_path), "SpeakerTwo", "on_screen",
                                   "covers his mouth with his hand")
    assert "whole episode" in str(refused.value.why)


def test_insightface_is_unavailable_when_this_interpreter_cannot_import_it(
        tmp_path, monkeypatch):
    """Weights on disk without the package answered True, and every clip of
    a geo-podcast M3b build then died on a bare ModuleNotFoundError."""
    pack = tmp_path / shared_environment.INSIGHTFACE_PACK_NAME
    pack.mkdir()
    for name in shared_environment.INSIGHTFACE_REQUIRED_FILES:
        (pack / name).write_bytes(b"x")
    import importlib.util
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "insightface" else real(name, *a))
    monkeypatch.setattr(shared_environment, "_insightface_pack_dir", lambda explicit=None: pack)

    usable, detail = shared_environment.insightface_available()

    assert usable is False and "insightface" in detail


def test_a_voice_track_that_could_not_be_measured_says_why(monkeypatch):
    """`voices: []` with no reason read exactly like a silent source when
    speechbrain was simply missing."""
    def refuse(*_a, **_k):
        raise single_track_diarization.DiarizationUnavailable("speechbrain is not installed")
    monkeypatch.setattr(single_track_diarization, "diarize_track", refuse)

    voices, reason = person_entity.measure_voice_tracks("unused.wav")

    assert voices == [] and "speechbrain" in reason
