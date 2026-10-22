"""Object contact: when a NAMED OBJECT reaches the lips.

**The defect this exists for.** The hand-at-lips candidate measures the
HAND. On a take whose speaker holds an object at his face throughout,
the hand's contact episodes are not the object's arrivals: the hand
touches the lips with the object a centimetre below, the hand is at
the brow, and the coarse sample catches a geometry artefact. The
ordinal of the class is then the ordinal of the HAND's contacts, and a
chain that asks "the SECOND time he brings the keys to his lips" is
answered with the wrong moment.

**What this measures instead.** The OBJECT's own contact: the distance
between the object's measured BOX and the measured lips, in face
widths, per frame, with the identity that says which physical object
the box is
(:mod:`library.tools.object_measurements`, whose identity half is
:mod:`library.tools.analysis.object_identity`).

The two levels that decide a contact episode are the ones the hand
lane already uses, read from ONE place
(:mod:`library.tools.contact_onsets`): the CONTACT level is the
declared distance at which the object is AT the lips, and the RELEASE
level is MEASURED ON THE TAKE, because a speaker who never goes far
from his face measures a different release level from one who does.

**The identity earns its place.** Two objects of the same class in one
frame are two objects, and a class that counted their contacts
together would count a second person's keys as the first person's
second arrival. The identity names which physical object each frame's
box is, so a reader can see whether the series is one object or
several.

**What this is not.** It is not a frame-exact boundary: an occurrence
names the coarse frame the contact was SEEN at, and
:mod:`library.tools.frame_exact` is what names the frame it happened
on. It is also not an answer on its own - it is a CANDIDATE, put to
the local VLM like every other candidate in the M7 record.

[why](docs/evidence/m0-object-contact-occurrences.md)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from library.tools import contact_onsets

CONTACT_DISTANCE_FACE_WIDTHS = contact_onsets.CONTACT_DISTANCE_FACE_WIDTHS
"""The object-to-lips distance at which the object is AT the lips.

The SAME declared number the hand lane uses, read from ONE place: the
contact level decides a boundary, so it is one number and it is
visible. It is measured on one take by one speaker and is not
calibrated on a corpus.
"""


def point_to_box_distance(point: Sequence[float],
                          box: Sequence[float]) -> float:
    """The distance from a point to a box; zero when the point is in it."""
    px, py = float(point[0]), float(point[1])
    x0, y0, x1, y1 = (float(value) for value in box)
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return (dx * dx + dy * dy) ** 0.5


def face_width(face: dict[str, Any]) -> float | None:
    """The width of a measured face, in the frame's normalised units.

    The face's own box when it carries one, and the extent of its lip
    landmarks when it does not: a distance between two normalised
    points is a distance only once the frame's size is known, and the
    face's width is the unit the hand lane measures in.
    """
    box = face.get("box") if isinstance(face, dict) else None
    if isinstance(box, (list, tuple)) and len(box) == 4:
        width = float(box[2]) - float(box[0])
        if width > 0:
            return width
    lips = face.get("outer_lips") if isinstance(face, dict) else None
    if isinstance(lips, (list, tuple)) and lips:
        xs = [float(point[0]) for point in lips]
        width = max(xs) - min(xs)
        if width > 0:
            return width
    return None


def box_to_lips_distance(box: Sequence[float],
                          face: dict[str, Any]) -> float | None:
    """The distance from an object's box to a face's lips, in face widths.

    The distance is measured from the LIPS to the box, so a box that
    covers the lips measures zero: the object is at the lips, and a
    distance from the box's CENTRE would measure how big the object is
    as well as where it is.
    """
    lips = face.get("outer_lips") if isinstance(face, dict) else None
    if not isinstance(lips, (list, tuple)) or not lips:
        return None
    unit = face_width(face)
    if not unit:
        return None
    nearest = min(point_to_box_distance(point, box) for point in lips)
    return nearest / unit


def lips_series(objects: dict[str, Any], m3: dict[str, Any],
                *, assignment: Sequence[Sequence[str | None]] | None = None
                ) -> list[dict[str, Any]]:
    """The object-to-lips distance of every frame that measured one.

    One row per inspected frame that carried a detection: the frame's
    CLOSEST approach of the named object to the lips (the object a
    reader is asking about is the one nearest the face), the identity
    of the object that made it, and the frame's own index so the row
    joins the M3 frames by position.

    A frame with no detection carries NO row: an unmeasured frame is
    not a frame at the lips and not a frame away from them.
    """
    frames_by_source_index = {}
    for position, frame in enumerate(m3.get("frames") or []):
        index = frame.get("frame_index")
        if index is None:
            continue
        frames_by_source_index[int(index)] = (position, frame)
    rows: list[dict[str, Any]] = []
    for observation in objects.get("observations") or []:
        if observation.get("status") != "observed":
            continue
        box = observation.get("box")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        found = frames_by_source_index.get(observation.get("frame_index"))
        if found is None:
            continue
        position, frame = found
        best = None
        for face_position, face in enumerate(frame.get("faces") or []):
            distance = box_to_lips_distance(box, face)
            if distance is None:
                continue
            track = None
            if assignment is not None and position < len(assignment):
                row = assignment[position]
                if face_position < len(row):
                    track = row[face_position]
            if best is None or distance < best["distance"]:
                best = {"distance": distance, "face_track": track,
                        "face_position": face_position}
        if best is None:
            continue
        row = {
            "index": position,
            "source_frame_index": observation.get("frame_index"),
            "t": frame.get("t"),
            "distance": round(float(best["distance"]), 4),
            "entity_id": observation.get("entity_id"),
            "phrase": observation.get("phrase"),
            "state": observation.get("state"),
            "score": observation.get("score"),
            "box": [float(value) for value in box],
            "face_track": best["face_track"],
        }
        # ONE row per frame: the frame's measurement is the named
        # object's CLOSEST approach to the lips in that frame, and the
        # identity that made it travels with the row. Two objects of the
        # same class in one frame are two objects, and the frame's
        # answer is about the one nearest the face.
        existing = next((found for found in rows
                         if found["index"] == position), None)
        if existing is None or row["distance"] < existing["distance"]:
            if existing is not None:
                rows.remove(existing)
            rows.append(row)
    rows.sort(key=lambda row: (row["index"], row["entity_id"] or ""))
    return rows


def release_level(rows: Sequence[dict[str, Any]]) -> float | None:
    """The take's OWN release level for the object's distances.

    Measured as the take's best two-way split of the measured
    distances, exactly as the hand lane measures its own: a speaker
    who never goes far from his face measures a LOW release level, and
    one who does measures a high one. ``None`` when nothing was
    measured: a take that measured nothing has no level.
    """
    return contact_onsets.release_level([row["distance"] for row in rows])


def contact_occurrences(rows: Sequence[dict[str, Any]], *,
                        contact_distance: float = CONTACT_DISTANCE_FACE_WIDTHS,
                        release_distance: float | None = None
                        ) -> list[dict[str, Any]]:
    """The object's contact episodes: one occurrence per arrival.

    An occurrence is a maximal stretch of the take during which the
    object was never RELEASED (it passed the take's own release level),
    and it counts only when the object reached CONTACT inside it. The
    split is the hand lane's split, read from ONE place.

    Each occurrence carries the object identities it saw, so a reader
    can tell one object's arrivals from two objects' arrivals.
    """
    measured = [(int(row["index"]), float(row.get("t") or 0.0),
                 float(row["distance"])) for row in rows]
    level = release_distance
    if level is None:
        level = release_level(rows)
    occurrences = contact_onsets.contact_onsets(
        measured, contact_distance=contact_distance,
        release_distance=level)
    by_index = {int(row["index"]): row for row in rows}
    out: list[dict[str, Any]] = []
    for occurrence in occurrences:
        frames = []
        for contact in occurrence["contact_frames"]:
            row = by_index.get(int(contact["index"]))
            if row is None:
                continue
            frames.append({**row, "distance": contact["distance"]})
        if not frames:
            continue
        entities = sorted({str(row.get("entity_id")) for row in frames
                           if row.get("entity_id")})
        phrases = sorted({str(row.get("phrase")) for row in frames
                          if row.get("phrase")})
        out.append({
            **occurrence,
            "contact_frames": frames,
            "entity_ids": entities,
            "phrases": phrases,
        })
    return out
