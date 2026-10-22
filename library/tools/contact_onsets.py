"""Contact onsets: ONE occurrence per time a hand reaches the lips.

**The defect this exists for.** The coarse candidate lane measured "the
hand is NEAR the mouth" with ONE fixed distance
(:data:`library.tools.event_spans.HAND_CANDIDATE_MAX_DISTANCE`, 1.5
face widths). On a take whose speaker holds an object near his face
throughout, that question is true almost everywhere, so the six
arrivals of the object at his lips collapsed into ONE span of 64.78
seconds - and a chain that asks "the SECOND time he brings the keys
to his lips" had nothing to name. The M0 whole-clip lane measured it
on the whole 85.82 s take of Apple 001 `IMG_1822`: 74 of the 89 coarse
frames were "near the mouth", and the class carried six spans of which
the first held every arrival the ground truth names.
`docs/benchmarks/2026-10-21-m0-e2e-whole-clip.md`.

**What this measures instead.** A CONTACT ONSET: the moment the
hand-to-lips distance crosses INTO contact, each onset being ONE
occurrence of the repeated event. The two levels that decide it are
measured, not assumed:

* the CONTACT level - the distance at which the hand is AT the lips -
  is the DECLARED number
  :data:`CONTACT_DISTANCE_FACE_WIDTHS` (0.25 face widths), measured on
  the M0 fixture (the frames in which the hand holds the car keys
  against the lips measure 0.02-0.05 and the frames of the approach
  measure 0.13-0.43) and overridable;
* the RELEASE level - the distance at which the hand has LEFT the lips
  - is MEASURED ON THE TAKE ITSELF (:func:`release_level`), because a
  fixed release distance would merge the arrivals of a speaker who
  never goes far from his face and split the arrivals of one who does.
  It is the take's own best two-way split of its measured distances.

So the split is per take: two takes of the same scene can carry
different numbers of occurrences, and each says which levels produced
it.

**What this is not.** It is not a claim that the object in the hand
reached the lips - the engine measures the HAND, and the M0 ground
truth measured the KEYS the hand holds. Two quantities that are close
and not the same; the report says which one each number came from. It
is also not a frame-exact boundary: an onset names the coarse frame
the arrival was SEEN at, and
:mod:`library.tools.frame_exact` is what names the frame it happened
on.

[why](docs/RULE_EVIDENCE.md#the-arrivals-that-were-one-span)
"""

from __future__ import annotations

from collections.abc import Sequence

CONTACT_DISTANCE_FACE_WIDTHS = 0.25
"""The hand-to-lips distance at which the hand is AT the lips.

The distance that decides "the hand is at the lips" is a DECLARED
number, so the number that decides a boundary is visible rather than
implicit. It is measured on the M0 fixture (Apple 001 `IMG_1822`, whole
85.82 s take, 30 fps): the frames in which the hand holds the car keys
against the lips measure 0.02-0.05 face widths and the frames of the
approach measure 0.13-0.43, so 0.25 separates them.

It is measured on ONE take by ONE speaker and is not calibrated on the
D-01 corpus. ``library.tools.frame_exact`` reads the SAME number from
here - one reading, one place to move it.
"""

RELEASE_MUST_EXCEED_CONTACT = True
"""Whether a take whose measured release level is not above the contact
level is treated as HOLDING the object at the lips throughout.

Such a take has no release at all: the hand never leaves the contact
level, so the object is at the lips for the whole take and there is ONE
held occurrence, not many arrivals. The record says so, because a
reader who sees one occurrence on a long take must be able to tell it
apart from a lane that failed to split.
"""


def release_level(distances: Sequence[float]) -> float | None:
    """The take's OWN release level: the distance at which the hand is
    AWAY from the lips.

    Measured as the take's own best TWO-WAY split of its measured
    distances (Otsu): the level that separates the take's distances
    into an "at the lips" group and an "away" group with the largest
    between-group variance, taken as the midpoint between the two
    values the split falls between. A take whose speaker never goes
    far from his face therefore measures a LOW release level - the
    hand leaves the lips often - and a take whose speaker holds the
    object near his face measures a HIGH one.

    ``None`` when the take measured no distance at all: a take that
    measured nothing has no level, and a lane that invents one is
    reporting a default as though it were measured.
    """
    values = sorted(float(d) for d in distances if d is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    best_variance = -1.0
    best_split = 1
    count = len(values)
    for index in range(1, count):
        low = values[:index]
        high = values[index:]
        weight_low = len(low) / count
        weight_high = len(high) / count
        mean_low = sum(low) / len(low)
        mean_high = sum(high) / len(high)
        variance = weight_low * weight_high * (mean_low - mean_high) ** 2
        if variance > best_variance:
            best_variance = variance
            best_split = index
    return (values[best_split - 1] + values[best_split]) / 2.0


def contact_onsets(measurements: Sequence[tuple[int, float, float]],
                   *, contact_distance: float = CONTACT_DISTANCE_FACE_WIDTHS,
                   release_distance: float | None = None,
                   release: float | None = None) -> list[dict]:
    """The contact onsets of ONE measured series, in the order they happened.

    ``measurements`` are ``(frame index, seconds, distance)`` rows in
    time order; a row whose distance could not be measured is simply
    absent (the caller drops it), because an unmeasured frame is not a
    frame at the lips and not a frame away from them.

    ``release_distance`` is the take's own measured release level
    (:func:`release_level`); when it is ``None`` the caller has not
    measured one and every measured release is used, which is the
    conservative reading (the hand was away whenever it was measured
    away).

    An OCCURRENCE is a maximal stretch of the take during which the
    hand was never released, and it counts only when the hand reached
    CONTACT inside it. The occurrence carries:

    * ``onset_index`` / ``onset_seconds`` - the FIRST frame at which
      the hand was at the lips, which is the coarse frame the arrival
      was SEEN at;
    * ``closest_index`` / ``closest_seconds`` / ``closest_distance`` -
      the frame the hand was deepest at, which is a different moment
      and is reported next to the onset so a reader cannot confuse them
      (``library.tools.frame_exact`` has the same two moments);
    * ``contact_frames`` - the frames that measured contact;
    * the two levels the occurrence was measured at.
    """
    rows = [(int(i), float(t), float(d)) for i, t, d in measurements
            if d is not None]
    if not rows:
        return []
    level = release if release is not None else release_distance
    if level is None:
        level = max((d for _i, _t, d in rows), default=contact_distance)
    level = float(level)
    holding = (RELEASE_MUST_EXCEED_CONTACT and level <= contact_distance)

    occurrences: list[dict] = []
    current: list[tuple[int, float, float]] = []
    for row in rows:
        if not holding and row[2] > level:
            # The hand was away: whatever contact it reached before this
            # frame is one occurrence, and what follows is another.
            if current:
                occurrences.append(current)
                current = []
            continue
        current.append(row)
    if current:
        occurrences.append(current)

    out: list[dict] = []
    for episode in occurrences:
        contacts = [row for row in episode if row[2] <= contact_distance]
        if not contacts:
            continue
        onset = contacts[0]
        closest = min(contacts, key=lambda row: row[2])
        out.append({
            "onset_index": onset[0],
            "onset_seconds": onset[1],
            "closest_index": closest[0],
            "closest_seconds": closest[1],
            "closest_distance": round(closest[2], 4),
            "contact_frames": [{"index": row[0], "t": row[1],
                                "distance": round(row[2], 4)}
                               for row in contacts],
            "contact_distance": contact_distance,
            "release_distance": level,
        })
    return out


def occurrences_by_track(series: dict[str, list[tuple[int, float, float]]],
                         *, contact_distance: float = CONTACT_DISTANCE_FACE_WIDTHS,
                         release_distance: float | None = None
                         ) -> dict[str, list[dict]]:
    """The contact onsets of every track in one take.

    The RELEASE level is measured ON THE TAKE - over every track's
    measured distances together - so one take gets one level and every
    track in it is split the same way. A take with two faces therefore
    does not get two different answers to "when was the hand away".
    """
    every = [d for rows in series.values() for (_i, _t, d) in rows]
    level = release_distance if release_distance is not None else (
        release_level(every))
    return {track: contact_onsets(rows,
                                  contact_distance=contact_distance,
                                  release_distance=level)
            for track, rows in series.items()}
