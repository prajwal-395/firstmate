"""M7 event spans and the structured query: the defects this task found.

Every test builds its memory and project under `tmp_path`; nothing runs
Vision, insightface or ECAPA. What the measured parts are worth (the
face-identity FAR, the diarization DER, the hand_near_mouth rule that was
NOT shipped) is in the eval directories those modules name.
"""


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


def _clock(digest, members, offset):
    source_memory.write_json(
        source_memory.source_dir(digest) / conversation_clock.SLOT_CLOCK,
        {"content_digest": digest, "group_id": "g", "group_members": members,
         "reference_digest": members[0], "offset_to_reference_seconds": offset,
         "path_to_reference": [digest], "direct_measurement": None,
         "resolve_cross_check": None, "ngram_size": 4, "built_at": "now"})


def test_a_span_on_one_angle_is_placed_on_the_items_that_play_the_other(memory_root):
    """The captain's example is a gesture seen on Craig's angle, but the
    timeline also plays Akshita's angle at that moment. A span must reach
    every item that plays THAT MOMENT of the conversation, through the M6
    clock - and nothing past an item's recorded source extent."""
    _clock("AKSHITA", ["AKSHITA", "CRAIG"], 0.0)
    _clock("CRAIG", ["AKSHITA", "CRAIG"], 3.8)  # craig_t + 3.8 == akshita_t
    items = [
        {"resolve_item_id": "item-a", "speaker": "Akshita", "source_file": "/a.mxf",
         "offset": 1000.0 - 500.0, "source_start": 500.0, "source_end": 520.0,
         "segments": [(500.0, 520.0)]},
        {"resolve_item_id": "item-c", "speaker": "Craig", "source_file": "/c.mxf",
         "offset": 2000.0 - 600.0, "source_start": 600.0, "source_end": 610.0,
         "segments": [(600.0, 610.0)]},
    ]
    files = {"/a.mxf": "AKSHITA", "/c.mxf": "CRAIG"}

    placements = event_spans.place("proj", "CRAIG", 501.2, 503.2, items, files)

    assert placements == [{
        "resolve_item_id": "item-a", "track_speaker": "Akshita",
        "via": "M6 clock", "source_file": "/a.mxf",
        "source_start": 505.0, "source_end": 507.0,
        "timeline_start": 1005.0, "timeline_end": 1007.0}]


def test_an_unshipped_predicate_is_refused_not_answered_empty(tmp_path):
    """hand_near_mouth failed its pre-registered bar. An empty answer to
    "when does Craig cover his mouth" reads as "never" - the one answer
    the measurement cannot support."""
    with pytest.raises(RenRefusal) as refused:
        event_spans.query(str(tmp_path), "Craig", "hand_near_mouth")
    assert "11/17" in str(refused.value.why)


def test_verify_refuses_a_predicate_whose_spans_are_the_whole_episode(tmp_path):
    """on_screen spans run the length of every take: verifying them is the
    whole-episode VLM pass the candidate stage exists to avoid."""
    with pytest.raises(RenRefusal) as refused:
        event_spans.verified_query(str(tmp_path), "Craig", "on_screen",
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
