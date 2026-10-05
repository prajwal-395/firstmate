"""Candidate action/gesture/event predicates for M7, and the VLM
verifier that judges them.

The defect each test names: a geometric rule that proposes no span (so
the VLM never sees the moment), or a rule that proposes a span for a
motion that is not the predicate. A candidate is never an answer - the
verifier disposes - so a rule that cannot clear the bar by itself must
still propose. No test here asserts that a registry or schema contains
a named entry; each drives a generator on fixture M3 and asserts the
spans it proposes.
"""
import pytest
from library.tools import event_spans, span_verification
from library.tools.ren_refusal import RenRefusal


def _m3(frames):
    return {"instrument": {"frame_pixels": [384, 216]}, "frames": frames}


def _face(centre_y, height=0.2):
    top = centre_y - height / 2.0
    return {"box": [0.45, top, 0.55, top + height]}


def _hand(y):
    return {"chirality": "left", "confidence": 1.0,
            "joints": {"wrist": [0.5, y, 0.9], "tip": [0.5, y - 0.05, 0.9]}}


def _frames_with_hand(hand_ys, face_cy=0.3):
    """One face track and one hand at `hand_ys[i]` in frame i."""
    frames, assignment = [], []
    for i, y in enumerate(hand_ys):
        frames.append({"t": i * 0.5, "faces": [_face(face_cy)],
                       "hands": [_hand(y)]})
        assignment.append(["face_001"])
    return frames, assignment


# ── hand_raise ─────────────────────────────────────────────────────


def test_hand_raise_proposes_a_span_when_a_hand_is_lifted_above_the_face():
    """A hand lifted from the lap into the air proposes no candidate unless
    the rule looks for a rise ending above the face's centre - and the
    verifier must be shown the transition, not only the raised hand."""
    hand_ys = [0.7] * 6 + [0.08] * 6  # low, then above the face's centre
    frames, assignment = _frames_with_hand(hand_ys)
    spans = event_spans.hand_raise_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    shown = span_verification.select_frames(spans[0])
    assert [f["t"] for f in shown] == [2.0, 2.5, 3.0, 3.5]


def test_hand_raise_proposes_nothing_when_the_hand_stays_low():
    """A hand that rises a little but stays below the face's centre is a
    gesture, not a raise - proposing it would flood the VLM."""
    hand_ys = [0.7] * 6 + [0.5] * 6  # rises but stays below the centre
    frames, assignment = _frames_with_hand(hand_ys)
    assert event_spans.hand_raise_candidates(_m3(frames), assignment) == []


# ── nod ────────────────────────────────────────────────────────────


def test_nod_proposes_a_span_for_a_vertical_oscillation():
    """A head that bobs down-then-up proposes no nod candidate unless the
    rule looks for a vertical peak-to-peak displacement that reverses."""
    frames, assignment = [], []
    for i in range(12):
        cy = 0.3 + (0.06 if i % 2 else -0.06)  # oscillate vertically
        frames.append({"t": i * 0.5, "faces": [_face(cy)], "hands": []})
        assignment.append(["face_001"])
    assert len(event_spans.nod_candidates(_m3(frames), assignment)) == 1


def test_nod_proposes_nothing_for_a_horizontal_head_move():
    """A head that translates sideways carries no vertical oscillation - a
    nod rule keyed on vertical displacement must not propose it."""
    frames, assignment = [], []
    for i in range(12):
        top = 0.2
        face = {"box": [0.45 + 0.01 * i, top, 0.55 + 0.01 * i, top + 0.2]}
        frames.append({"t": i * 0.5, "faces": [face], "hands": []})
        assignment.append(["face_001"])
    assert event_spans.nod_candidates(_m3(frames), assignment) == []


# ── object_pickup ──────────────────────────────────────────────────


def test_object_pickup_proposes_a_span_when_a_hand_rises_from_below():
    """A hand lifted from below the face's bottom to above it proposes no
    pickup candidate unless the rule requires that arc."""
    hand_ys = [0.8] * 8 + [0.25] * 6  # from below the bottom (0.4) to above
    frames, assignment = _frames_with_hand(hand_ys)
    assert len(event_spans.object_pickup_candidates(_m3(frames),
                                                   assignment)) == 1


def test_object_pickup_proposes_nothing_when_the_hand_stays_low():
    """A hand that rises but never reaches the face's bottom is a gesture,
    not a pickup - proposing it would flood the VLM."""
    hand_ys = [0.8] * 8 + [0.55] * 6  # rises but stays below the bottom
    frames, assignment = _frames_with_hand(hand_ys)
    assert event_spans.object_pickup_candidates(_m3(frames),
                                                assignment) == []


# ── person_enters / person_leaves ──────────────────────────────────


def test_person_enters_proposes_a_span_at_a_track_appearance():
    """A face track that appears mid-clip proposes no enter candidate
    unless the rule looks at the run's first frame - and the span must
    lead with the frame before it, or the transition is invisible."""
    frames, assignment = [], []
    for i in range(10):
        present = i >= 4
        frames.append({"t": i * 0.5,
                       "faces": [_face(0.3)] if present else [], "hands": []})
        assignment.append(["face_001"] if present else [])
    spans = event_spans.person_enters_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][0]["t"] == 1.5  # the frame before it
    assert spans[0]["frames"][0]["d"] == 0.0


def test_person_enters_at_the_clip_start_has_no_frame_before_it():
    """A track present from the first frame proposes an enter with no
    leading frame - the person was there when the clip opened."""
    frames, assignment = [], []
    for i in range(10):
        frames.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": []})
        assignment.append(["face_001"])
    spans = event_spans.person_enters_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][0]["t"] == 0.0


def test_person_leaves_proposes_a_span_at_a_track_disappearance():
    """A face track that disappears proposes no leave candidate unless the
    rule looks at the run's last frame - and the span must trail with the
    frame after it."""
    frames, assignment = [], []
    for i in range(10):
        present = i < 6
        frames.append({"t": i * 0.5,
                       "faces": [_face(0.3)] if present else [], "hands": []})
        assignment.append(["face_001"] if present else [])
    spans = event_spans.person_leaves_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][-1]["t"] == 3.0  # the frame after it
    assert spans[0]["frames"][-1]["d"] == 0.0


def test_person_leaves_at_the_clip_end_has_no_frame_after_it():
    """A track present until the last frame proposes a leave with no
    trailing frame - the clip ends, not the person."""
    frames, assignment = [], []
    for i in range(10):
        frames.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": []})
        assignment.append(["face_001"])
    spans = event_spans.person_leaves_candidates(_m3(frames), assignment)
    assert len(spans) == 1
    assert spans[0]["frames"][-1]["t"] == 4.5


# ── the query and the verifier ─────────────────────────────────────


def test_a_candidate_predicate_is_refused_with_a_verify_pointer():
    """Asking for a candidate predicate alone reads as "never happens" -
    the refusal must name --verify, the only way to answer it."""
    with pytest.raises(RenRefusal) as refused:
        event_spans.query("/nonexistent", "SpeakerTwo", "hand_raise")
    assert "hand_raise" in refused.value.what
    assert "--verify" in refused.value.fix


def test_the_prompt_judges_a_sequence_so_an_event_description_works():
    """An event description (a person entering) is a transition no single
    frame contains - the prompt must ask about the sequence, not only
    about one frame, or the transition the frames collectively show is
    rejected."""
    prompt = span_verification.PROMPT.format(n=4, statement="enters the frame")
    assert "enters the frame" in prompt
    assert "across the sequence" in prompt
