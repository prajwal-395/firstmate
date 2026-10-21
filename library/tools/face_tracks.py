"""Face tracks by GEOMETRY alone: which M3 face is the face in the next
sample.

**This is not identity.** A track here says "this is the same face
continuing across the picture", never "this is Craig". Identity is M3b
(ArcFace), and identity is an ENRICHMENT of the event lane, not its
precondition: the candidate rules in `event_spans` are geometry over M3
faces and hands, so a project whose identity model is licence-blocked
(InsightFace Buffalo-L is non-commercial research only) can still
measure them.

**Why this module exists.** `event_spans.build_source_events` used to
REFUSE without M3b, so the licence gate on the identity model removed
the whole event lane: on a commercial project there were no event spans
at all - a false negative caused by a licence, not by the footage. The
measured defect is in `docs/RULE_EVIDENCE.md`.

**What a track is.** A greedy nearest-neighbour continuation over the
M3 face boxes: a face in one sample continues the open track whose last
face is nearest, within `TRACK_MAX_CENTER_SHIFT` face widths, whose size
is within `TRACK_MAX_SIZE_RATIO` of it, and which was seen no more than
`TRACK_MAX_GAP_FRAMES` samples ago. A face no open track claims starts
a new track; a track unseen for longer than that is CLOSED, so a face
that comes back later is a new track (an entry is an entry, not a
re-detection of the same presence).

The ids are `geo_001`, `geo_002`, ... - namespaced away from M3b's
`face_001`, so a record can say which basis a track came from and no
reader can mistake one for the other.

    python3 -m library.tools.face_tracks <m3-record.json>
"""

from __future__ import annotations

import argparse
import json
import sys

TRACK_PREFIX = "geo_"
"""The namespace of a geometry track's id. M3b's ArcFace tracks are
`face_001`; a reader can tell the two bases apart by the id alone."""

TRACK_MAX_CENTER_SHIFT = 1.0
"""How far a face may move, in face widths, and still be the same face
continuing. A geometric prior, not a measured identity bar: the two
detectors that M3b joins (Vision and ArcFace) are matched at 0.75 face
widths (`event_spans.FACE_TRACK_MAX_CENTER_SHIFT`), and a face's own
continuation across half-second samples is the same kind of question."""

TRACK_MAX_SIZE_RATIO = 2.0
"""How much a face's width may change between samples and still be the
same face continuing. A face that doubles in size is a different face
near the camera, not the same one closer."""

TRACK_MAX_GAP_FRAMES = 3
"""How many samples a track survives without being seen. A face missed
for one or two samples (a detection dropout) keeps its track; a longer
absence closes it, so the face that comes back is a new track and its
appearance is an entry."""


def _aspect(m3: dict) -> float:
    """The frame's height/width, for measuring a distance in face widths.

    A distance in image-normalised units is anisotropic - a 16:9 frame's
    y is squeezed against its x - so the vertical distance is scaled by
    the frame's aspect before it is compared with a face width. A record
    with no `frame_pixels` is measured as square, which is stated rather
    than guessed at.
    """
    pixels = (m3.get("instrument") or {}).get("frame_pixels")
    if isinstance(pixels, (list, tuple)) and len(pixels) == 2:
        try:
            width, height = float(pixels[0]), float(pixels[1])
        except (TypeError, ValueError):
            return 1.0
        if width > 0 and height > 0:
            return height / width
    return 1.0


def _face_geometry(face: dict) -> tuple[float, float, float] | None:
    """(centre x, centre y, width) of a face box, image-normalised."""
    box = face.get("box") or []
    if len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    width = x2 - x1
    if width <= 0:
        return None
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, width


def _shift_in_face_widths(cx: float, cy: float, width: float,
                          state: dict, aspect: float) -> float:
    """How far a face is from a track's last face, in face widths."""
    dx = (cx - state["cx"]) / width
    dy = (cy - state["cy"]) * aspect / width
    return (dx * dx + dy * dy) ** 0.5


def assign_tracks(m3: dict) -> list[list[str | None]]:
    """Per M3 frame, per face: the geometry track it continues, or None.

    The shape is the one `event_spans` already consumes for an M3b
    assignment (`assign_face_tracks`), so every candidate rule runs on
    either basis without knowing which one it has.

    Greedy and per frame: each face in a frame takes the nearest open
    track that claims it, and a track is claimed by at most one face in
    a frame.
    """
    frames = m3.get("frames") or []
    aspect = _aspect(m3)
    open_tracks: dict[str, dict] = {}
    out: list[list[str | None]] = []
    number = 0
    for index, frame in enumerate(frames):
        closed = [track_id for track_id, state in open_tracks.items()
                  if index - state["last_index"] > TRACK_MAX_GAP_FRAMES]
        for track_id in closed:
            del open_tracks[track_id]
        row: list[str | None] = []
        claimed: set[str] = set()
        for face in frame.get("faces") or []:
            geometry = _face_geometry(face)
            if geometry is None:
                row.append(None)
                continue
            cx, cy, width = geometry
            best: tuple[float, str] | None = None
            for track_id, state in open_tracks.items():
                if track_id in claimed:
                    continue
                ratio = width / state["width"] if state["width"] > 0 else 0.0
                if ratio > TRACK_MAX_SIZE_RATIO or ratio < 1.0 / TRACK_MAX_SIZE_RATIO:
                    continue
                shift = _shift_in_face_widths(cx, cy, width, state, aspect)
                if shift > TRACK_MAX_CENTER_SHIFT:
                    continue
                if best is None or shift < best[0]:
                    best = (shift, track_id)
            if best is None:
                number += 1
                track_id = f"{TRACK_PREFIX}{number:03d}"
            else:
                track_id = best[1]
            state = open_tracks.get(track_id)
            if state is None:
                open_tracks[track_id] = {"cx": cx, "cy": cy, "width": width,
                                         "last_index": index}
            else:
                state.update({"cx": cx, "cy": cy, "width": width,
                              "last_index": index})
            claimed.add(track_id)
            row.append(track_id)
        out.append(row)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="face_tracks",
        description="Geometry face tracks for an M3 record: which face "
                    "continues across the picture. Not identity.")
    parser.add_argument("m3", help="an M3 record (persons.json)")
    args = parser.parse_args(argv)
    with open(args.m3, encoding="utf-8") as handle:
        m3 = json.load(handle)
    assignment = assign_tracks(m3)
    print(json.dumps({"tracks": len({t for row in assignment for t in row
                                     if t}),
                      "assignment": assignment}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
