"""Contact onsets: the coarse lane must SPLIT the repeated arrivals.

**The defect each test names.** The coarse candidate lane measured
"the hand is NEAR the mouth" with ONE fixed distance, so a take whose
speaker holds an object near his face answered "near" almost
everywhere, and the six arrivals of the object at his lips collapsed
into ONE span of 64.78 seconds. A chain that asks for "the SECOND time
he brings the keys to his lips" then had nothing to name: the class
carried one span, and the second span it carried was a different
moment entirely (`docs/benchmarks/2026-10-21-m0-e2e-whole-clip.md`).

The fix measures the CONTACT ONSETS per take: the hand reached the
lips, and it was RELEASED before the next time, with the release level
measured on the take itself.

The tests here drive the generators on a fixture M3 and assert the
spans they propose - they do not assert that a registry contains a
named entry.
"""
from library.tools import contact_onsets, event_spans

LIP_Y = 0.70
FACE_WIDTH_PX = 384 * (0.55 - 0.45)
"""The fixture face's width in pixels, at the M2 thumbnail width."""


def _face():
    """A face whose outer lips carry one point, so the distance from a
    hand joint to the lips is the distance from that joint to it."""
    return {"box": [0.45, 0.50, 0.55, 0.70],
            "outer_lips": [[0.5, LIP_Y]],
            "capture_quality": 0.5}


def _hand_at(distance_face_widths):
    """A hand whose only confident joint sits `distance_face_widths`
    face widths from the lips."""
    pixels = distance_face_widths * FACE_WIDTH_PX
    return {"chirability": "right", "confidence": 1.0,
            "joints": {"wrist": [0.5, LIP_Y + pixels / 216.0, 0.9]}}


def _take(distance_series, cadence=0.5):
    """One face track, one hand, at the measured distances given."""
    frames, assignment = [], []
    for index, distance in enumerate(distance_series):
        frames.append({"t": index * cadence, "faces": [_face()],
                       "hands": [_hand_at(distance)]})
        assignment.append(["face_001"])
    return {"instrument": {"frame_pixels": [384, 216]}, "frames": frames}, \
        assignment


def _holding_then_arrivals(arrivals, held=0.8, away=1.2, cadence=0.5):
    """A take whose hand hovers NEAR the face and reaches the lips at
    the given frames - the shape the M0 fixture has: the object is near
    the face throughout, and it arrives at the lips several times.

    The "away" distance stays inside the NEAR band (1.5 face widths),
    so the near candidate cannot tell the arrivals apart - which is the
    defect this file is about."""
    series = []
    index = 0
    while index <= max(arrivals) + 4:
        series.append(held)
        index += 1
    for arrival in arrivals:
        series[arrival] = 0.02
        if arrival + 1 < len(series):
            series[arrival + 1] = held
    # The hand goes AWAY between the arrivals: that is what makes the
    # next arrival a new occurrence rather than a continuation.
    for arrival in arrivals[:-1]:
        for step in range(arrival + 2, min(arrival + 6, len(series))):
            series[step] = away
    return series


def test_the_near_candidate_merges_every_arrival_into_one_span():
    """The defect, measured on a fixture that has the M0 fixture's
    shape: the hand hovers near the face the whole take and reaches the
    lips three times, and the NEAR candidate answers with ONE span that
    holds all three arrivals. This is why the near candidate cannot
    name "the second time"."""
    series = _holding_then_arrivals([4, 12, 20])
    m3, assignment = _take(series)
    near = event_spans.hand_at_mouth_candidates(m3, assignment)
    assert len(near) == 1
    assert len(near[0]["frames"]) == len(series)


def test_the_contact_onset_candidate_yields_one_span_per_arrival():
    """The same take, measured the tighter way: the hand REACHED the
    lips three times and was RELEASED between the arrivals, so the
    class carries three spans, each with its own onset - and the
    arrivals are not merged back into one."""
    series = _holding_then_arrivals([4, 12, 20])
    m3, assignment = _take(series)
    spans = event_spans.hand_contact_onset_candidates(m3, assignment)
    assert len(spans) == 3
    onsets = sorted(span["onset_seconds"] for span in spans)
    assert onsets == [4 * 0.5, 12 * 0.5, 20 * 0.5]


def test_an_occurrence_carries_its_onset_and_its_closest_approach():
    """An occurrence names the frame the arrival was SEEN at and the
    frame the hand was DEEPEST at, and the two are different moments:
    a held gesture has an arrival at its edge and a peak inside it, and
    a reader that cannot tell them apart reads a hold as an arrival
    (`library/tools/frame_exact` carries the same two moments)."""
    series = [0.9, 0.4, 0.05, 0.02, 0.03, 0.2, 0.9, 2.0, 0.9, 0.9]
    m3, assignment = _take(series)
    spans = event_spans.hand_contact_onset_candidates(m3, assignment)
    assert len(spans) == 1
    span = spans[0]
    assert span["onset_index"] == 2
    assert span["closest_index"] == 3
    assert span["closest_distance"] < 0.05


def test_the_release_level_is_measured_on_the_take_itself():
    """Two takes of the same gesture measure DIFFERENT release levels,
    because the level is read off the take's own distances: a take
    whose hand never leaves the lips measures a low level, and a take
    whose hand stays near the face measures a high one. Each span says
    which level produced it."""
    near_series = [0.8] * 10 + [0.02] * 3 + [0.8] * 10
    far_series = [1.8] * 10 + [0.02] * 3 + [1.8] * 10
    near_m3, near_assignment = _take(near_series)
    far_m3, far_assignment = _take(far_series)
    near = event_spans.hand_contact_onset_candidates(near_m3, near_assignment)
    far = event_spans.hand_contact_onset_candidates(far_m3, far_assignment)
    assert near and far
    assert near[0]["release_distance"] < far[0]["release_distance"]


def test_a_take_that_measured_no_distance_has_no_release_level():
    """A take with nothing measured reports NO level, rather than a
    default that reads as a measurement."""
    assert contact_onsets.release_level([]) is None
    assert contact_onsets.release_level([None, None]) is None


def test_the_release_level_is_the_take_s_own_best_split():
    """The level is measured ON THE TAKE: a take whose distances are
    mostly far measures a far release level, and a take whose distances
    are mostly near measures a near one. The split falls BETWEEN two
    measured values, so a frame at the level is never mistaken for a
    release."""
    close = contact_onsets.release_level([0.02, 0.05, 0.10, 0.12, 0.20])
    wide = contact_onsets.release_level([0.02, 0.05, 2.00, 2.50, 3.00])
    assert close < wide
    assert close not in (0.02, 0.05, 0.10, 0.12, 0.20)


def test_a_release_level_that_never_exceeds_the_contact_level_holds():
    """A take whose own measured release level is not above the contact
    level has NO release at all: the object is at the lips throughout,
    so there is ONE held occurrence and the record says the take held
    it."""
    series = [0.05] * 8
    m3, assignment = _take(series)
    spans = event_spans.hand_contact_onset_candidates(m3, assignment)
    assert len(spans) == 1
    assert spans[0]["release_distance"] <= contact_onsets.CONTACT_DISTANCE_FACE_WIDTHS


def test_two_takes_measure_two_release_levels():
    """The level is a property of the TAKE, not of the scene: a take
    with wide excursions measures a higher release level than a take
    whose hand stays close."""
    close = contact_onsets.release_level([0.1, 0.2, 0.3, 0.4, 0.5])
    wide = contact_onsets.release_level([0.1, 0.2, 2.0, 2.5, 3.0])
    assert close < wide


def test_the_contact_onsets_of_a_series_are_the_contact_episodes():
    """The unit the generator is built on: a measured series becomes
    one onset per stretch during which the hand was never released, and
    a stretch that never reached contact is not an occurrence."""
    series = [(0, 0.0, 2.0), (1, 0.5, 0.02), (2, 1.0, 0.03),
              (3, 1.5, 2.0), (4, 2.0, 0.5), (5, 2.5, 0.02)]
    found = contact_onsets.contact_onsets(series, release_distance=1.0)
    assert [o["onset_index"] for o in found] == [1, 5]
    assert found[0]["closest_index"] == 1
    assert found[1]["closest_index"] == 5
