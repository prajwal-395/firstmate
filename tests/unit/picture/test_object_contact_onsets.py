"""Object contact: the class must measure the OBJECT, not the HAND.

**The defect each test names.** The coarse candidate lane measures the
hand at the lips, and a hand that reaches the lips with the object a
centimetre below is not an arrival of the object. On the M0 fixture
the class's ordinal was therefore the ordinal of the HAND's contacts:
the truth's second arrival of the car keys at the lips (33.40 s,
frame 1002) was the class's FIFTH occurrence, and the class's second
occurrence was the truth's FIRST arrival
(`docs/evidence/m0-object-contact-occurrences.md`).

The fix measures the object's OWN box against the measured lips, on
the frames the object was measured on, with the object identity that
says which physical object the box is.

The tests here drive the generators on a fixture M3 and a fixture
object record - they do not assert that a registry contains a named
entry.
"""
import pytest

from library.tools import contact_onsets, event_spans, object_contact

LIP_X = 0.50
LIP_Y = 0.70
FACE_WIDTH = 0.10
"""The fixture face's width, in the frame's normalised units."""

CADENCE = 0.5


def _face():
    """A face whose outer lips carry one point, so the distance from an
    object's box to the lips is the distance from that box to it."""
    return {"box": [0.45, 0.50, 0.55, 0.70],
            "outer_lips": [[LIP_X, LIP_Y]],
            "capture_quality": 0.5}


def _hand_at(distance_face_widths):
    """A hand whose only confident joint sits `distance_face_widths`
    face widths from the lips."""
    return {"chirability": "right", "confidence": 1.0,
            "joints": {"wrist": [LIP_X, LIP_Y + distance_face_widths * FACE_WIDTH,
                                 0.9]}}


def _object_box(distance_face_widths):
    """The box of an object `distance_face_widths` face widths BELOW the
    lips. A distance of zero is a box that covers the lips."""
    top = LIP_Y + distance_face_widths * FACE_WIDTH
    return [LIP_X - 0.01, top, LIP_X + 0.01, top + 0.02]


def _take(hand_distances, object_distances, *, entity="obj-0001",
          phrase="car keys", cadence=CADENCE):
    """One take: a face, a hand at the given distances, and an object
    measured at the given distances.

    The two series are SEPARATE on purpose: the defect this file is
    about is exactly the case where the hand is at the lips and the
    object is not.
    """
    frames, assignment, observations = [], [], []
    for index, (hand, obj) in enumerate(zip(hand_distances,
                                            object_distances)):
        frames.append({"t": index * cadence, "frame_index": index * 15,
                       "faces": [_face()],
                       "hands": [_hand_at(hand)]})
        assignment.append(["face_001"])
        if obj is not None:
            observations.append({
                "frame_id": f"frame-{index * 15:06d}",
                "frame_index": index * 15,
                "t": index * cadence,
                "entity_id": entity,
                "phrase": phrase,
                "status": "observed",
                "state": "visible",
                "box": _object_box(obj),
                "score": 0.4,
            })
    m3 = {"instrument": {"frame_pixels": [384, 216]}, "frames": frames}
    objects = {"content_digest": "d" * 64, "observations": observations}
    return m3, assignment, objects


def test_a_hand_at_the_lips_is_not_the_object_at_the_lips():
    """The defect, on a fixture that has the M0 fixture's shape: the
    hand reaches the lips and the object is a centimetre below, so the
    HAND class has a contact and the OBJECT class has none. The hand's
    contact is not the object's arrival, and a class that measured the
    hand could not name the object's second arrival."""
    hand = [0.8, 0.02, 0.02, 0.8, 0.8, 0.8, 0.8]
    obj = [0.8, 0.60, 0.60, 0.8, 0.8, 0.8, 0.8]
    m3, assignment, _objects = _take(hand, obj)
    assert event_spans.hand_contact_onset_candidates(m3, assignment)
    assert object_contact.contact_occurrences(
        object_contact.lips_series({}, m3)) == []


def test_the_objects_arrivals_are_the_occurrences_and_the_second_one_is_second():
    """The object reaches the lips twice and is away in between, so the
    class carries two occurrences and the SECOND occurrence is the
    second arrival - the ordinal the chain is asked for."""
    hand = [0.8] * 14
    obj = [0.8, 0.8, 0.8, 0.8, 0.02, 0.02, 0.8, 0.8, 0.8, 0.8, 0.8, 0.02,
           0.02, 0.8]
    m3, assignment, objects = _take(hand, obj)
    spans = event_spans.object_contact_onset_candidates(m3, assignment,
                                                        objects)
    assert len(spans) == 2
    onsets = sorted(span["onset_seconds"] for span in spans)
    assert onsets == [4 * CADENCE, 11 * CADENCE]


def test_a_frame_the_object_was_not_measured_in_is_not_a_frame_away():
    """An unmeasured frame carries no row: the object was not seen
    there, which is not the same as the object being away from the
    lips. The occurrence therefore survives the gap instead of being
    split by it."""
    hand = [0.8] * 8
    obj = [0.8, 0.02, None, None, 0.02, 0.8, 0.8, 0.8]
    m3, assignment, objects = _take(hand, obj)
    rows = object_contact.lips_series(objects, m3)
    assert [row["index"] for row in rows] == [0, 1, 4, 5, 6, 7]
    spans = event_spans.object_contact_onset_candidates(m3, assignment,
                                                        objects)
    assert len(spans) == 1
    assert spans[0]["onset_index"] == 1


def test_the_identity_names_the_object_that_reached_the_lips():
    """Two objects of the same class in one frame are two objects: the
    frame's measurement is the one NEAREST the face, and the identity
    of the object that made it travels with the row - so a reader can
    tell this object's arrivals from another object's."""
    m3, assignment, objects = _take([0.8] * 4, [0.02] * 4)
    objects["observations"].insert(1, {
        "frame_id": "frame-000015", "frame_index": 15, "t": CADENCE,
        "entity_id": "obj-0002", "phrase": "car keys",
        "status": "observed", "state": "visible",
        "box": _object_box(2.0), "score": 0.3,
    })
    rows = object_contact.lips_series(objects, m3)
    assert len(rows) == 4
    assert {row["entity_id"] for row in rows} == {"obj-0001"}
    spans = event_spans.object_contact_onset_candidates(m3, assignment,
                                                        objects)
    assert spans and spans[0]["object_entities"] == ["obj-0001"]


def test_the_release_level_is_measured_on_the_take_itself():
    """The object's release level is the take's own split of its own
    measured distances, read from the same place the hand lane reads
    its own: a take whose object never goes far from the face measures
    a low level, and a take whose object does measures a high one."""
    close = object_contact.release_level(
        [{"distance": 0.02}, {"distance": 0.05}, {"distance": 0.10}])
    wide = object_contact.release_level(
        [{"distance": 0.02}, {"distance": 2.0}, {"distance": 3.0}])
    assert close < wide
    assert close is not None


def test_a_box_that_covers_the_lips_measures_zero():
    """The geometry: the distance is measured from the LIPS to the
    box, so a box that covers the lips measures zero. A distance from
    the box's centre would measure how big the object is as well as
    where it is."""
    face = _face()
    assert object_contact.box_to_lips_distance(_object_box(0.0),
                                               face) == 0.0
    assert object_contact.box_to_lips_distance(_object_box(0.6),
                                               face) == pytest.approx(0.6)


def test_a_class_with_no_object_measurement_is_refused_not_answered_as_empty():
    """A source with no object measurement has NO object-contact
    measurement, and the record says so: the class is absent and the
    reason is recorded, so a query refuses by name instead of
    answering "the object never reached the lips"."""
    m3, _assignment, _objects = _take([0.8] * 4, [0.02] * 4)
    rows = object_contact.lips_series({}, m3)
    assert rows == []
    assert event_spans.OBJECT_CONTACT_ABSENT_REASON
    assert (contact_onsets.CONTACT_DISTANCE_FACE_WIDTHS
            == object_contact.CONTACT_DISTANCE_FACE_WIDTHS)
