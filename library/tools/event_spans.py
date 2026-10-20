"""Derived event spans per person: M7 (`events.json`) of the per-source
footage memory, and the structured query `ren search --predicate` reads.

Scout basis: `data/vep-long-footage-memory-scout/report.md` §3.2-3.3 and
§5 item 5 (in firstmate's home). Retrieval is STRUCTURED FIRST: "person
x predicate x time" is answered from spans precomputed here, joined
across camera angles on the M6 conversation clock, and placed on the
timeline through the source/timeline binding `timeline_transcript`
already records. No model runs at query time.

**What each predicate is made of** (one source file at a time):

- `on_screen` - a face track's M3 frames (Apple Vision faces at the M2
  cadence, ~2 Hz) joined into spans. WHICH person a Vision face is comes
  from M3b: the face takes the ArcFace track of the time-adjacent M3b
  observation whose face sits where it sits (`assign_face_tracks`). A
  face no observation vouches for is left unassigned, never guessed.
- `speaking` - a diarized voice turn (M3b `voices`, the ECAPA clusters
  of PR #1482) attributed to a face by MOUTH MOTION: a voice is that
  face's when the face's lips move more during that voice's turns than
  outside them (`link_voices_to_faces`). Co-occurrence alone - M3b's own
  `speech_face_links` basis - attaches EVERY voice to the only face on a
  single-person angle, because the listener is on screen during the
  speaker's turns too. A voice no face on an angle speaks with is
  off-screen there; it is still that person's speech on the angle that
  shows them, and reaches every other angle through the clock.

**`hand_near_mouth` is NOT answered alone, by measurement.** The
pre-registered span rule was evaluated on every 2 Hz frame of the
captain's four Craig angles (10,873 frames, 17 labelled events):
event recall 11/17 (bar 0.80), precision 0.50 - and the frame rule with
corrected coordinates beat it on recall (14/17) at 0.43 precision, so
neither cleared the bar fixed before measuring. Rule, labels and numbers:
`data/vep-structured-footage-query/eval/` in firstmate's home. A
predicate the engine cannot measure is REFUSED by name (`UNSHIPPED`),
never answered with an empty list that reads as "he never did".
It is recorded as a CANDIDATE generator instead (`CANDIDATE_PREDICATES`,
a looser cutoff), and `verified_query` / `ren search --verify` puts each
candidate span to the local VLM (`span_verification`): measured 14/17
events at span precision 14/15 for "covers his mouth with his hand",
`data/vep-query-hit-verification/eval/` in firstmate's home.

**Action, gesture and event CANDIDATES.** `hand_raise`, `nod`,
`object_pickup`, `person_enters` and `person_leaves` are candidate
generators too: a cheap geometric rule over M3 hand/face geometry or
M3b face-track runs proposes spans for the same VLM verifier. None is
answered alone - a rule that cannot clear the bar by itself proposes, and
the verifier disposes. The thresholds are geometric priors calibrated
on the four Craig angles' geo-podcast M3
(`data/vep-structured-footage-query/eval/diag_720_m3.json` in
firstmate's home), not a measured recall bar: hand_raise 33 spans,
object_pickup 81 and nod 33 per 1.51 h, against hand_near_mouth's 257.
Those counts predate merging (same-track spans within `MERGE_MAX_GAP_S`
now merge) and boundary tightening; the thresholds are unchanged.

**Tiering: cheap filters before any VLM call.** Every candidate span
carries `tier` (0 = strong evidence, 1 = marginal) and `score`
(higher = stronger), computed from the same measured geometry that
proposed it - no model, no decode. `verified_query` verifies tier 0
first and reports per-tier costs, so a caller can budget VLM calls
(`max_tier`) without re-running the geometry. Motion spans are
tightened to the motion's measured onset and last moving frame rather
than the full detector window, so EVAL-03 IoU is measured against the
motion, not its window.

**Names.** A person is named by the project's declaration
(`source.person_names`, `person_entity.declared_person_names`) when it
makes one; otherwise by the speaker label the saved timeline transcript
gives the speech that person's attributed voice turns overlap
(`derived_person_names`): the timeline's own per-speaker tracks, with the
measured agreement recorded beside the name. Never by appearance.

**Placement on the timeline.** A span on source S, and the same moment on
every other angle of S's multicam group (`conversation_clock.map_time`),
is looked up against the timeline transcript's items: each item has a
constant source-to-timeline offset and a RECORDED source extent (its
first to last word). A span outside every recorded extent is reported
`unplaced`, not "not on the timeline": the transcript records where
speech is, and an item can run past it.

**Face capture quality orders, never filters.** Every `on_screen` and
`speaking` span carries the median M3 face capture quality of its
frames (`capture_quality`, None when no face in the span measured
one). `query` answers best face first; an unmeasured span keeps time
order behind every measured one rather than being dropped.

    python3 -m library.tools.event_spans build <project>
    python3 -m library.tools.event_spans status <project>
    ren search <project> --person Craig --predicate speaking
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from library.tools import (
    conversation_clock,
    person_entity,
    person_measurements,
    source_memory,
)
from library.tools.ren_refusal import RenRefusal

PREDICATES = ("speaking", "on_screen")

UNSHIPPED = {
    "hand_near_mouth": (
        "measured and NOT shipped: on every 2 Hz frame of the four Craig "
        "angles (10,873 frames, 17 labelled hand-at-mouth events) the "
        "pre-registered span rule recalled 11/17 events at precision 0.50, "
        "under the 0.80 recall bar fixed before measuring; a single finger "
        "on the lips can carry no detected hand at all. It is answered "
        "ONLY as candidates the local VLM verifies: add --verify "
        "\"<what you see>\" (14/17 events at precision 14/15, "
        "data/vep-query-hit-verification/eval/results.md in firstmate's "
        "home)"),
}
"""Predicates asked for by name that the engine refuses, and why. A
silent empty answer would read as "this never happens in the footage"."""

CANDIDATE_PREDICATES = {
    "hand_near_mouth": "M3 hand joint within HAND_CANDIDATE_MAX_DISTANCE "
                       "face widths of the lips (a candidate, not an answer)",
    "hand_raise": "M3 hand joint risen HAND_RAISE_MIN_RISE face heights "
                  "within HAND_RAISE_WINDOW frames, ending above the face's "
                  "centre (a candidate, not an answer)",
    "nod": "M3 face centre's vertical peak-to-peak displacement of "
           "NOD_MIN_DISPLACEMENT face heights within NOD_WINDOW frames, "
           "reversing direction (a candidate, not an answer)",
    "object_pickup": "M3 hand joint risen PICKUP_MIN_RISE face heights "
                     "within PICKUP_WINDOW frames, from below the face's "
                     "bottom to above it (a candidate, not an answer)",
    "person_enters": "an M3b face track's first frames, with the frame "
                     "before them (a candidate, not an answer)",
    "person_leaves": "an M3b face track's last frames, with the frame "
                     "after them (a candidate, not an answer)",
}
"""Predicates M7 records as CANDIDATES: too imprecise to answer alone,
cheap enough to propose spans that `--verify` puts to the local VLM."""

SPAN_GAP_FRAMES = 1
"""A run of hit frames survives this many missing frames inside it -
one dropped detection at the M2 cadence (~0.5 s) is not a new event."""

FACE_TRACK_MAX_CENTER_SHIFT = 0.75
"""A Vision face belongs to an ArcFace track when its centre lies within
this many face widths of a time-adjacent ArcFace observation's centre.
Centres, not IoU: the two detectors draw different boxes around the same
face, so an IoU floor would measure the detectors, not the person."""

VOICE_FACE_MIN_MOTION_RATIO = 1.5
"""A voice is a face's when that face's mean frame-to-frame lip motion
inside the voice's turns is at least this multiple of its motion outside
them. Checked against the timeline transcript's per-speaker ISO tracks
on geo-podcast (results.md in the eval directory named above)."""

VOICE_FACE_MIN_FRAMES = 20
"""Frames inside a voice's turns before its ratio is trusted (~10 s of
the face at the M2 cadence)."""

NAME_MIN_AGREEMENT = 0.6
"""A derived name needs this share of the person's attributed speech
seconds that overlap ANY timeline speaker label to overlap ONE label."""

HAND_JOINT_MIN_CONFIDENCE = 0.1
"""Hand joints below this Vision confidence are not measured from (the
floor every hand-at-mouth evaluation so far has used)."""

HAND_CANDIDATE_MAX_DISTANCE = 1.5
"""A frame is a hand-at-mouth CANDIDATE when a hand joint is under this
many face widths from the lips. Set from the measured candidate table on
the four Craig angles (17 labelled events): <= 1.25 reaches 14/17
events, 1.5 reaches 16/17 at 257 spans, 2.0 adds none - a looser cutoff
than any rule that answers alone, because the verifier throws the false
ones away. `data/vep-query-hit-verification/eval/` (firstmate's home)."""

HAND_RAISE_MIN_RISE = 0.25
"""A hand_raise CANDIDATE: the highest confident hand joint rises at
least this many face heights within HAND_RAISE_WINDOW frames and ends
above the face's vertical centre - a hand lifted into the air, not one
that merely moved. A geometric prior calibrated on the four Craig
angles' geo-podcast M3 (33 spans per 1.51 h,
`data/vep-structured-footage-query/eval/diag_720_m3.json` in firstmate's
home), not a measured recall bar - the verifier throws the false ones."""

HAND_RAISE_WINDOW = 4
"""Frames a hand_raise spans at the M2 cadence (~2 s)."""

NOD_MIN_DISPLACEMENT = 0.4
"""A nod CANDIDATE: the face centre's vertical peak-to-peak displacement
within NOD_WINDOW consecutive frames of the same track reaches this
many face heights, with the motion reversing direction - a nod is
down-then-up, not a one-way lean. 33 spans per 1.51 h on the Craig
angles."""

NOD_WINDOW = 4
"""Frames a nod spans at the M2 cadence."""

PICKUP_MIN_RISE = 0.45
"""An object_pickup CANDIDATE: a hand rises at least this many face
heights within PICKUP_WINDOW frames, from below the face's bottom to
above it - the arc of lifting something up from a table or lap. 81
spans per 1.51 h on the Craig angles."""

PICKUP_WINDOW = 6
"""Frames an object_pickup spans at the M2 cadence (~3 s)."""

ENTER_LEAVE_FRAMES = 3
"""Run frames a person_enters/person_leaves span shows the VLM, beside
the frame on the far side of the boundary - the transition it judges."""

MERGE_MAX_GAP_S = 1.0
"""Same-track spans of one candidate predicate separated by at most
this long merge into one span before verification. Intermittent M3
detection fragments one hold into several spans that the VLM would
judge identically; merging cuts one VLM call per fragment without
dropping coverage (the union still covers every hit frame). Distinct
track runs (enters/leaves) never merge: each boundary is its own event."""

STILLNESS_FRACTION = 0.25
"""Per-frame stillness floor as a fraction of a motion predicate's
threshold over its window. A run's trailing frames whose signal moves
less than this ended after the motion stopped: the detector window
still looks back over the motion, but nothing moves any more, so the
span ends at the last moving frame."""

HAND_MOUTH_TIER0_DISTANCE = 1.25
"""A hand-at-mouth candidate whose closest approach is within this many
face widths verifies in tier 0. The measured candidate table on the
four Craig angles reaches 14/17 labelled events at this cutoff; the
1.25-1.5 band adds the remaining 2 at the cost of most of the 257
spans, so it verifies in tier 1."""

STRONG_RISE_MULTIPLE = 1.5
"""A hand-raise / object-pickup candidate whose completing rise is at
least this multiple of its threshold verifies in tier 0; a bare
threshold crossing (slow drift, jitter) verifies in tier 1."""

STRONG_NOD_MULTIPLE = 1.5
"""A nod candidate whose completing peak-to-peak displacement is at
least this multiple of its threshold verifies in tier 0."""

TIER_STRONG = 0
TIER_MARGINAL = 1

STATUS_BUILT = "built"


# ── spans out of per-frame hits ─────────────────────────────────────


def frame_spacing(times: Sequence[float]) -> float:
    """The median spacing of a frame sequence (the M2 cadence)."""
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    return statistics.median(gaps) if gaps else 0.5


def runs(hits: Sequence[bool], gap: int = SPAN_GAP_FRAMES,
         min_hits: int = 1) -> List[Tuple[int, int, int]]:
    """`[(first_index, last_index, hit_count)]` of runs of True frames,
    bridging at most `gap` False frames, keeping runs with at least
    `min_hits` True frames."""
    out: List[Tuple[int, int, int]] = []
    start = last = None
    count = 0
    for i, hit in enumerate(hits):
        if not hit:
            continue
        if start is not None and i - last - 1 <= gap:
            last, count = i, count + 1
            continue
        if start is not None and count >= min_hits:
            out.append((start, last, count))
        start, last, count = i, i, 1
    if start is not None and count >= min_hits:
        out.append((start, last, count))
    return out


def span_bounds(times: Sequence[float], first: int, last: int) -> Tuple[float, float]:
    """A run's time extent: half a frame either side of its end frames -
    a sample stands for the interval around it, no further."""
    half = frame_spacing(times) / 2.0
    return round(max(0.0, times[first] - half), 3), round(times[last] + half, 3)


# ── who a Vision face is (M3 x M3b) ─────────────────────────────────


def _m3b_frame_dims(identity: dict, m3: dict) -> Tuple[float, float]:
    """Pixel size of the frames M3b drew its boxes on.

    M3b extracts from source at the M2 I-frame times
    (`FRAME_SOURCE_M2_TIMES`), the same instants M3 measured, and records
    its actual output `frame_pixels`. A record from before that measured
    ON the M2 thumbnails (`FRAME_SOURCE_M2`) shares M3's `frame_pixels`.
    An own-decode record is at times M3 never sampled; it cannot be
    joined and raises rather than guessing.
    """
    instrument = identity.get("instrument") or {}
    source = instrument.get("frame_source")
    if source == person_entity.FRAME_SOURCE_M2_TIMES:
        pixels = instrument.get("frame_pixels")
    elif source == person_entity.FRAME_SOURCE_M2:
        pixels = (m3.get("instrument") or {}).get("frame_pixels")
    else:
        pixels = None
    if not pixels:
        raise ValueError(
            f"M3b frame source {source!r} is not at the M2 times M3 "
            f"read; rebuild M3b after `source_memory frames`")
    return float(pixels[0]), float(pixels[1])


def assign_face_tracks(m3: dict, identity: dict) -> List[List[Optional[str]]]:
    """Per M3 frame, per face: the M3b face track it is, or None.

    The ArcFace observations immediately before and after the frame in
    time (any track) are the candidates; the face takes the track of the
    nearest-in-time candidate whose centre lies within
    `FACE_TRACK_MAX_CENTER_SHIFT` of its own face width. Pure geometry
    over two measurements - no embedding is computed here.
    """
    width, height = _m3b_frame_dims(identity, m3)
    observations = []  # (t, track_id, cx, cy) normalised
    for track in identity.get("faces") or []:
        for span in track.get("spans") or []:
            x1, y1, x2, y2 = span["box"]
            observations.append(((span["start"] + span["end"]) / 2.0,
                                 track["track_id"],
                                 (x1 + x2) / 2.0 / width,
                                 (y1 + y2) / 2.0 / height))
    observations.sort()
    times = [o[0] for o in observations]
    out: List[List[Optional[str]]] = []
    for frame in m3["frames"]:
        idx = bisect.bisect_left(times, frame["t"])
        neighbours = sorted((observations[j] for j in (idx - 1, idx)
                             if 0 <= j < len(observations)),
                            key=lambda o: abs(o[0] - frame["t"]))
        row: List[Optional[str]] = []
        for face in frame["faces"]:
            x1, y1, x2, y2 = face["box"]
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            limit = FACE_TRACK_MAX_CENTER_SHIFT * (x2 - x1) * width
            row.append(next((track_id for _t, track_id, ox, oy in neighbours
                             if math.hypot((cx - ox) * width, (cy - oy) * height)
                             <= limit), None))
        out.append(row)
    return out


def _track_runs(assignment: List[List[Optional[str]]]
                ) -> Dict[str, List[Tuple[int, int, int]]]:
    """{track_id: [(first_index, last_index, hit_count), ...]} - the runs
    of frames each track is present, bridging SPAN_GAP_FRAMES."""
    out: Dict[str, List[Tuple[int, int, int]]] = {}
    for track_id in _all_tracks(assignment):
        hits = [track_id in row for row in assignment]
        out[track_id] = runs(hits)
    return out


def _median_quality(values: Sequence[float]) -> Optional[float]:
    """The median face capture quality of a span, or None unmeasured.

    An ORDERING, never a threshold: a span whose faces carry no quality
    (pre-v2 M3, or a voice turn with no face on screen) keeps its place
    behind every measured span instead of being dropped. A span is never
    refused for looking bad.
    """
    measured = [float(v) for v in values
                if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not measured:
        return None
    return round(statistics.median(measured), 3)


def _track_frame_qualities(m3: dict, assignment: List[List[Optional[str]]],
                           track_id: str, first: int, last: int) -> List[float]:
    """Capture qualities of `track_id`'s faces over frames first..last."""
    out = []
    for i in range(first, last + 1):
        for face, assigned in zip(m3["frames"][i]["faces"], assignment[i]):
            if assigned == track_id:
                quality = face.get("capture_quality")
                if isinstance(quality, (int, float)) \
                        and not isinstance(quality, bool):
                    out.append(float(quality))
    return out


def _turn_qualities(m3: dict, assignment: List[List[Optional[str]]],
                    track_id: str, start: float, end: float) -> List[float]:
    """Capture qualities of `track_id`'s faces inside [start, end]."""
    out = []
    for frame, row in zip(m3["frames"], assignment):
        if not _inside(frame["t"], [(start, end)]):
            continue
        for face, assigned in zip(frame["faces"], row):
            if assigned != track_id:
                continue
            quality = face.get("capture_quality")
            if isinstance(quality, (int, float)) \
                    and not isinstance(quality, bool):
                out.append(float(quality))
    return out


def on_screen_spans(m3: dict, assignment: List[List[Optional[str]]]) -> List[dict]:
    """Per face track, the runs of frames that track's face is in.

    Each span carries the MEDIAN face capture quality of its frames'
    faces (`capture_quality`, None when no face in the span measured
    one): the query orders a person's spans by it, best face first.
    """
    times = [f["t"] for f in m3["frames"]]
    spans = []
    for track_id, track_runs in _track_runs(assignment).items():
        for first, last, count in track_runs:
            start, end = span_bounds(times, first, last)
            spans.append({"face_track": track_id, "start": start, "end": end,
                           "frames": count,
                           "capture_quality": _median_quality(
                               _track_frame_qualities(m3, assignment,
                                                      track_id, first, last))})
    return spans


def person_enters_candidates(m3: dict, assignment: List[List[Optional[str]]]
                              ) -> List[dict]:
    """Per face track, a CANDIDATE at each run's first frame: the track
    appears, so the person may have entered. The span shows the VLM the
    frame before the run (the scene without them) plus the run's first
    ENTER_LEAVE_FRAMES frames - the transition it judges. Never an
    answer: a re-detection after occlusion or a cut proposes the same
    span, and the VLM disposes."""
    times = [f["t"] for f in m3["frames"]]
    half = frame_spacing(times) / 2.0
    spans = []
    for track_id, track_runs in _track_runs(assignment).items():
        for first, last, count in track_runs:
            shown = ([first - 1] if first > 0 else [])
            shown += list(range(first, min(first + ENTER_LEAVE_FRAMES, last + 1)))
            frames = []
            for d, i in enumerate(shown):
                face = _face_of_track(m3["frames"][i], assignment[i], track_id)
                frames.append({"index": i, "t": times[i], "d": float(d),
                               "box": face["box"] if face else None})
            # A track present from the first frame shows no entry
            # transition (there is no frame before it); the VLM cannot
            # judge an entry it cannot see, so the clip edge verifies
            # in tier 1, after every mid-clip appearance.
            edge = first == 0
            spans.append({"face_track": track_id,
                          "start": round(max(0.0, times[shown[0]] - half), 3),
                          "end": round(times[shown[-1]] + half, 3),
                          "frames": frames,
                          "tier": TIER_MARGINAL if edge else TIER_STRONG,
                          "score": float(count),
                          "tier_reason": (
                              "track present at clip open: no entry "
                              "transition visible" if edge else
                              "mid-clip appearance with a visible "
                              "before-frame")})
    spans.sort(key=lambda s: (s["tier"], -s["score"], s["start"]))
    return spans


def person_leaves_candidates(m3: dict, assignment: List[List[Optional[str]]]
                             ) -> List[dict]:
    """Per face track, a CANDIDATE at each run's last frame: the track
    disappears, so the person may have left. The span shows the VLM the
    run's last ENTER_LEAVE_FRAMES frames plus the frame after them (the
    scene without them) - the transition it judges. Never an answer: a
    brief occlusion or a cut proposes the same span, and the VLM
    disposes."""
    times = [f["t"] for f in m3["frames"]]
    half = frame_spacing(times) / 2.0
    last_index = len(times) - 1
    spans = []
    for track_id, track_runs in _track_runs(assignment).items():
        for first, last, count in track_runs:
            shown = list(range(max(first, last - ENTER_LEAVE_FRAMES + 1),
                               last + 1))
            if last < last_index:
                shown.append(last + 1)
            frames = []
            for i in shown:
                face = _face_of_track(m3["frames"][i], assignment[i], track_id)
                d = 0.0 if i == last + 1 else float(last - i + 1)
                frames.append({"index": i, "t": times[i], "d": d,
                               "box": face["box"] if face else None})
            # A track present until the last frame shows no exit
            # transition (there is no frame after it); the clip end
            # verifies in tier 1, after every mid-clip disappearance.
            edge = last == last_index
            spans.append({"face_track": track_id,
                          "start": round(max(0.0, times[shown[0]] - half), 3),
                          "end": round(times[shown[-1]] + half, 3),
                          "frames": frames,
                          "tier": TIER_MARGINAL if edge else TIER_STRONG,
                          "score": float(count),
                          "tier_reason": (
                              "track present at clip end: no exit "
                              "transition visible" if edge else
                              "mid-clip disappearance with a visible "
                              "after-frame")})
    spans.sort(key=lambda s: (s["tier"], -s["score"], s["start"]))
    return spans


# ── who is speaking (M3b voices x M3 lips) ──────────────────────────


def lip_opening(face: dict) -> Optional[float]:
    """Inner-lip vertical extent over face-box height, or None."""
    lips = face.get("inner_lips")
    height = face["box"][3] - face["box"][1]
    if not lips or height <= 0:
        return None
    ys = [p[1] for p in lips]
    return (max(ys) - min(ys)) / height


def _inside(t: float, turns: Sequence[Sequence[float]]) -> bool:
    return any(start <= t <= end for start, end in turns)


def lip_motion(m3: dict, assignment: List[List[Optional[str]]]
               ) -> Dict[str, List[Tuple[float, float]]]:
    """`{face_track: [(t, |opening(t) - opening(previous frame)|)]}`,
    only across CONSECUTIVE frames of the same track."""
    times = [f["t"] for f in m3["frames"]]
    motion: Dict[str, List[Tuple[float, float]]] = {}
    previous: Dict[str, Tuple[int, float]] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        for face, track_id in zip(frame["faces"], row):
            opening = lip_opening(face) if track_id else None
            if opening is None:
                continue
            before = previous.get(track_id)
            if before is not None and before[0] == i - 1:
                motion.setdefault(track_id, []).append(
                    (times[i], abs(opening - before[1])))
            previous[track_id] = (i, opening)
    return motion


def link_voices_to_faces(m3: dict, assignment: List[List[Optional[str]]],
                         identity: dict) -> List[dict]:
    """Each voice track's lip-motion evidence against each face track, and
    the face it is linked to (None: no face on this angle speaks with it).

    The contrast is inside-this-voice's-turns against outside them: the
    listener's mouth is mostly still while the other person talks, the
    speaker's moves. Every ratio is recorded, linked or not.
    """
    motion = lip_motion(m3, assignment)
    links = []
    for voice in identity.get("voices") or []:
        turns = voice.get("spans") or []
        evidence = []
        for face_track, samples in sorted(motion.items()):
            inside = [m for t, m in samples if _inside(t, turns)]
            outside = [m for t, m in samples if not _inside(t, turns)]
            if len(inside) < VOICE_FACE_MIN_FRAMES or not outside:
                continue
            mean_out = statistics.fmean(outside)
            evidence.append({
                "face_track": face_track, "frames_inside": len(inside),
                "frames_outside": len(outside),
                "motion_ratio": (round(statistics.fmean(inside) / mean_out, 3)
                                 if mean_out > 0 else None)})
        scored = [e for e in evidence if e["motion_ratio"] is not None]
        best = max(scored, key=lambda e: e["motion_ratio"], default=None)
        linked = (best["face_track"] if best is not None
                  and best["motion_ratio"] >= VOICE_FACE_MIN_MOTION_RATIO else None)
        links.append({"voice_track": voice["track_id"], "face_track": linked,
                      "evidence": evidence})
    return links


def speaking_spans(identity: dict, links: List[dict], m3: Optional[dict] = None,
                   assignment: Optional[List[List[Optional[str]]]] = None
                   ) -> List[dict]:
    """Every voice turn, carrying the face track it was linked to.

    Each span also carries the MEDIAN face capture quality of its
    linked track's faces inside the turn (`capture_quality`): the face
    the query orders by. None when the turn's track has no face on
    screen (a voice with no linked track, or M3/assignment not passed,
    as in readers that only need the link).
    """
    face_of = {link["voice_track"]: link["face_track"] for link in links}
    out = []
    for voice in identity.get("voices") or []:
        for start, end in voice.get("spans") or []:
            track_id = face_of.get(voice["track_id"])
            quality = None
            if m3 is not None and assignment is not None \
                    and track_id is not None:
                quality = _median_quality(
                    _turn_qualities(m3, assignment, track_id, start, end))
            out.append({"voice_track": voice["track_id"],
                        "face_track": track_id,
                        "start": round(start, 3), "end": round(end, 3),
                        "capture_quality": quality})
    return out


# ── hand-at-mouth CANDIDATES (never an answer on their own) ─────────


def hand_mouth_distance(face: dict, hands: Sequence[dict],
                        width: float, height: float) -> Optional[float]:
    """Min pixel distance from a confident hand joint to an outer-lip
    point, over the face's width in pixels. Both are image-normalised in
    M3, so x scales by `width` and y by `height` before measuring (the
    coordinate mistake `person_measurements` documents). None with no
    lips or no confident joint."""
    lips = face.get("outer_lips")
    face_width = (face["box"][2] - face["box"][0]) * width
    if not lips or face_width <= 0:
        return None
    best = None
    for hand in hands:
        for x, y, confidence in hand["joints"].values():
            if confidence < HAND_JOINT_MIN_CONFIDENCE:
                continue
            for lx, ly in lips:
                d = math.hypot((x - lx) * width, (y - ly) * height) / face_width
                best = d if best is None or d < best else best
    return best


def _spans_from_hits(per_track: Dict[str, Dict[int, dict]],
                     times: Sequence[float]) -> List[dict]:
    """Candidate spans out of per-track hit frames: runs of frames (see
    `runs`), each carrying its per-frame evidence (`d`, `box`)."""
    spans = []
    for track_id, frames in sorted(per_track.items()):
        hits = [i in frames for i in range(len(times))]
        for first, last, _count in runs(hits):
            start, end = span_bounds(times, first, last)
            spans.append({"face_track": track_id, "start": start, "end": end,
                          "frames": [frames[i] for i in range(first, last + 1)
                                     if i in frames]})
    return spans


def _face_of_track(frame: dict, row: List[Optional[str]],
                   track_id: str) -> Optional[dict]:
    """The face a track is assigned to in one frame, or None."""
    for face, assigned in zip(frame["faces"], row):
        if assigned == track_id:
            return face
    return None


def _face_verticals(face: dict) -> Tuple[float, float, float]:
    """(top, centre, bottom) of a face box, image-normalised."""
    x1, y1, x2, y2 = face["box"]
    return y1, (y1 + y2) / 2.0, y2


def _hand_top_y(frame: dict) -> Optional[float]:
    """The highest confident hand joint's y (smallest y is highest), or
    None with no confident joint. M3 does not link hands to faces, so
    every hand in the frame is evidence for every face track in it - the
    verifier throws the false ones away."""
    ys = [y for hand in frame["hands"] for x, y, c in hand["joints"].values()
          if c >= HAND_JOINT_MIN_CONFIDENCE]
    return min(ys) if ys else None


def _hand_rises(m3: dict, window: int) -> List[Optional[float]]:
    """Per frame index, the rise of the highest hand joint over the
    previous `window` frames (positive is up), None without a confident
    hand at both ends."""
    frames = m3["frames"]
    rises: List[Optional[float]] = [None] * len(frames)
    for i in range(window, len(frames)):
        before = _hand_top_y(frames[i - window])
        now = _hand_top_y(frames[i])
        if before is not None and now is not None:
            rises[i] = before - now
    return rises


def _tighten_rise_run(sig: Sequence[Optional[float]], lo: int,
                      first: int, last: int, window: int,
                      need: float) -> Tuple[int, int]:
    """Tighten a rise-type run to its measured core.

    The hit at `first` means the signal rose `need` over
    `[first-window, first]`; the rise's core is the SHORTEST trailing
    sub-interval that still clears `need`, so an abrupt lift starts the
    span one frame before its step instead of a full window back, while
    a gradual lift keeps the whole window. The tail ends at the last
    frame that still moves (per-frame floor `STILLNESS_FRACTION` of the
    threshold rate): later hits only repeat the window over a hand that
    has already stopped. Falls back to `[lo, max(first, ...)]` whenever
    the signal is missing, so tightening never drops the hit frame."""
    floor = max(lo, first - window)
    end_val = sig[first] if 0 <= first < len(sig) else None
    tight_lo = lo
    if end_val is not None:
        # Largest k first: the shortest trailing sub-interval that
        # still clears `need` is the motion's core. An abrupt step
        # clears it in one frame (onset one frame back); a gradual
        # lift needs the whole window (onset stays at `lo`).
        for k in range(first, floor, -1):
            before = sig[k - 1] if 0 <= k - 1 < len(sig) else None
            if before is None:
                tight_lo = k
                break
            if before - end_val >= need:
                tight_lo = k - 1
                break
    eps = STILLNESS_FRACTION * need / window if window > 0 else 0.0
    tight_last = first
    for e in range(first, last + 1):
        if not (0 < e < len(sig)):
            continue
        now, prev = sig[e], sig[e - 1]
        if now is None or prev is None:
            continue
        if abs(now - prev) >= eps:
            tight_last = e
    return max(lo, tight_lo), max(first, tight_last)


def _tighten_oscillation_run(sig: Sequence[Optional[float]], lo: int,
                             first: int, last: int, window: int,
                             need: float) -> Tuple[int, int]:
    """Tighten an oscillation-type run to its measured core.

    The hit at `first` means the window ending there displaced `need`
    peak-to-peak with a direction reversal; the core is the shortest
    trailing slice (at least 3 frames, the minimum that can reverse)
    that still does. The tail ends at the last moving frame, as in
    `_tighten_rise_run`."""
    floor = max(lo, first - window)
    tight_lo = lo
    # Largest k first, as in `_tighten_rise_run`: the shortest
    # trailing slice (at least 3 frames, the minimum that can reverse)
    # that still displaces `need` with a reversal is the oscillation.
    for k in range(first, floor - 1, -1):
        if first - k + 1 < 3:
            continue
        vals = sig[k:first + 1]
        if any(v is None for v in vals):
            tight_lo = k
            break
        if max(vals) - min(vals) >= need:
            steps = [b - a for a, b in zip(vals, vals[1:])]
            if any(s > 0 for s in steps) and any(s < 0 for s in steps):
                tight_lo = k - 1 if k - 1 >= lo else lo
                break
    eps = STILLNESS_FRACTION * need / window if window > 0 else 0.0
    tight_last = first
    for e in range(first, last + 1):
        if not (0 < e < len(sig)):
            continue
        now, prev = sig[e], sig[e - 1]
        if now is None or prev is None:
            continue
        if abs(now - prev) >= eps:
            tight_last = e
    return max(lo, tight_lo), max(first, tight_last)


def _motion_spans(m3: dict, assignment: List[List[Optional[str]]],
                  hit_flags: Dict[str, List[bool]], window: int, *,
                  signals: Optional[Dict[str, List[Optional[float]]]] = None,
                  evidence: Optional[Dict[Tuple[str, int], dict]] = None,
                  signal_kind: str = "rise",
                  strong_multiple: float = STRONG_RISE_MULTIPLE) -> List[dict]:
    """Candidate spans for a motion predicate. `hit_flags[track]` marks
    the frames where the motion completed; each run of hits becomes a
    span tightened to the motion's measured onset through its last
    moving frame (see `_tighten_rise_run` / `_tighten_oscillation_run`),
    every frame carrying `d = |i - first_hit|` so the verifier is shown
    the transition the motion is, not only its end state.

    `signals[track]` is the per-frame motion signal (hand-top-y for
    lifts, face-centre-y for nods) and `evidence[(track, first_hit)]`
    carries the absolute `need` the hit cleared and its `multiple` of
    the threshold. Spans without evidence keep the full window and
    verify in the marginal tier. Every span carries `tier`, `score`
    (higher is stronger) and `tier_reason`, from geometry alone."""
    tighten = (_tighten_rise_run if signal_kind == "rise"
               else _tighten_oscillation_run)
    times = [f["t"] for f in m3["frames"]]
    spans = []
    for track_id in sorted(hit_flags):
        flags = hit_flags[track_id]
        sig = (signals or {}).get(track_id)
        for first, last, _count in runs(flags):
            lo = max(0, first - window)
            tight_lo, tight_last = lo, last
            multiple = None
            if evidence is not None and sig is not None:
                info = evidence.get((track_id, first)) or {}
                if info.get("need"):
                    multiple = info.get("multiple")
                    tight_lo, tight_last = tighten(
                        sig, lo, first, last, window, info["need"])
            frames = []
            for i in range(tight_lo, tight_last + 1):
                face = _face_of_track(m3["frames"][i], assignment[i], track_id)
                frames.append({"index": i, "t": times[i],
                               "d": float(abs(i - first)),
                               "box": face["box"] if face else None})
            start, end = span_bounds(times, tight_lo, tight_last)
            if multiple is not None and multiple >= strong_multiple:
                tier, reason = TIER_STRONG, (
                    f"motion {multiple:.2f}x the geometric threshold")
            else:
                tier, reason = TIER_MARGINAL, (
                    "bare threshold crossing" if multiple is not None
                    else "no measured evidence strength")
            spans.append({"face_track": track_id, "start": start, "end": end,
                          "frames": frames, "tier": tier,
                          "score": round(multiple, 3) if multiple is not None
                          else 0.0,
                          "tier_reason": reason})
    return spans


def finalize_candidates(spans: List[dict],
                        max_gap_s: float = MERGE_MAX_GAP_S) -> List[dict]:
    """Merge same-track, same-tier spans at most `max_gap_s` apart, then
    order strongest-tier first.

    Fragments of one episode (intermittent M3 detection splits a run the
    gap bridge does not reunite) would cost one VLM call each for the
    same judgement; the union still covers every hit frame. Merging
    stops at a tier boundary so a marginal hover never stretches a
    strong span's measured extent. One limit: two distinct events
    inside one merged span score one match under one-to-one EVAL-03
    matching, so `max_gap_s` stays at one second. Spans without
    tier/score (pre-tier M7 records) verify as tier 0, preserving old
    order."""
    by_track: Dict[str, List[dict]] = {}
    for span in spans:
        by_track.setdefault(
            (span.get("face_track"), span.get("tier", TIER_STRONG)),
            []).append(span)
    merged = []
    for track_spans in by_track.values():
        ordered = sorted(track_spans, key=lambda s: s["start"])
        current = None
        for span in ordered:
            if current is not None and span["start"] - current["end"] <= max_gap_s:
                current["end"] = max(current["end"], span["end"])
                seen = {f["index"] for f in current["frames"]}
                current["frames"] = sorted(
                    current["frames"] + [f for f in span["frames"]
                                         if f["index"] not in seen],
                    key=lambda f: f["t"])
                if span.get("tier", TIER_STRONG) < current.get(
                        "tier", TIER_STRONG):
                    current["tier"] = span["tier"]
                    current["tier_reason"] = span.get("tier_reason", "")
                current["score"] = max(current.get("score", 0.0),
                                       span.get("score", 0.0))
                current["tier_reason"] = (
                    f"merged fragments: {current.get('tier_reason', '')}")
            else:
                current = dict(span)
                current["frames"] = list(span["frames"])
                merged.append(current)
    merged.sort(key=lambda s: (s.get("tier", TIER_STRONG),
                               -(s.get("score", 0.0) or 0.0), s["start"]))
    return merged


def _all_tracks(assignment: List[List[Optional[str]]]) -> List[str]:
    return sorted({t for row in assignment for t in row if t})


def hand_at_mouth_candidates(m3: dict, assignment: List[List[Optional[str]]]
                              ) -> List[dict]:
    """Per face track, runs of frames whose hand-to-lips distance is under
    `HAND_CANDIDATE_MAX_DISTANCE`, each frame carrying its distance and
    face box so a verifier can choose and crop frames without re-reading
    M3. Tier 0 is the measured high-recall band (closest approach within
    `HAND_MOUTH_TIER0_DISTANCE`); the looser band verifies in tier 1.
    Band flicker (an isolated single frame against time-adjacent hits in
    the other band) is smoothed before runs form, so shrapnel does not
    multiply spans; genuine dwells still split by band."""
    width, height = (m3.get("instrument") or {})["frame_pixels"]
    times = [f["t"] for f in m3["frames"]]
    per_track: Dict[str, Dict[int, dict]] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        for face, track_id in zip(frame["faces"], row):
            if track_id is None:
                continue
            d = hand_mouth_distance(face, frame["hands"], width, height)
            if d is None or d >= HAND_CANDIDATE_MAX_DISTANCE:
                continue
            held = per_track.setdefault(track_id, {}).get(i)
            if held is None or d < held["d"]:
                per_track[track_id][i] = {"index": i, "t": times[i],
                                          "d": round(d, 3), "box": face["box"]}
    spans = []
    # Runs form per tier band, so a marginal hover adjacent to a firm
    # hold never stretches the strong span's measured extent: each
    # band's spans cover only that band's frames. Before banding, an
    # isolated single frame whose time-adjacent hits (within a second
    # either side) both sit in the other band takes their band: a
    # half-second band touch is detection flicker, not an episode.
    # Frames keep their measured distance; only the band - the tier -
    # is smoothed, and a lone touch with no near neighbour never flips.
    band_of: Dict[str, Dict[int, int]] = {}
    for track_id, frames in per_track.items():
        band_of[track_id] = {
            i: (TIER_STRONG if frame["d"] <= HAND_MOUTH_TIER0_DISTANCE
                else TIER_MARGINAL)
            for i, frame in frames.items()}
    for track_id, frames in per_track.items():
        bands = band_of[track_id]
        order = sorted(frames)
        for pos, i in enumerate(order):
            before = next((order[k] for k in range(pos - 1, -1, -1)
                           if times[order[k]] >= times[i] - 1.0), None)
            after = next((order[k] for k in range(pos + 1, len(order))
                          if times[order[k]] <= times[i] + 1.0), None)
            if (before is not None and after is not None
                    and bands[before] == bands[after] != bands[i]):
                bands[i] = bands[before]
    bands: Dict[int, Dict[str, Dict[int, dict]]] = {
        TIER_STRONG: {}, TIER_MARGINAL: {}}
    for track_id, frames in per_track.items():
        for i, frame in frames.items():
            bands[band_of[track_id][i]].setdefault(track_id, {})[i] = frame
    for band, subset in bands.items():
        for span in _spans_from_hits(subset, times):
            closest = min(f["d"] for f in span["frames"])
            span["tier"] = band
            span["tier_reason"] = (
                f"closest approach {closest:.2f} face widths" if band == TIER_STRONG
                else f"closest approach {closest:.2f} face widths, "
                     "loose band only")
            span["score"] = round(-closest, 3)
            spans.append(span)
    return finalize_candidates(spans)


def hand_raise_candidates(m3: dict, assignment: List[List[Optional[str]]]
                          ) -> List[dict]:
    """Per face track, runs of frames where a hand rose
    `HAND_RAISE_MIN_RISE` face heights within `HAND_RAISE_WINDOW` frames
    and ended above the face's vertical centre - a hand lifted into the
    air, not one that merely moved. A CANDIDATE for `--verify`, never an
    answer: the rule proposes, the VLM disposes."""
    times = [f["t"] for f in m3["frames"]]
    rises = _hand_rises(m3, HAND_RAISE_WINDOW)
    tops = [_hand_top_y(frame) for frame in m3["frames"]]
    hit_flags: Dict[str, List[bool]] = {t: [False] * len(times)
                                        for t in _all_tracks(assignment)}
    evidence: Dict[Tuple[str, int], dict] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        rise = rises[i]
        if rise is None:
            continue
        top_now = _hand_top_y(frame)
        for face, track_id in zip(frame["faces"], row):
            if track_id is None:
                continue
            top, centre, bottom = _face_verticals(face)
            face_height = bottom - top
            if face_height <= 0 or rise < HAND_RAISE_MIN_RISE * face_height:
                continue
            if top_now >= centre:
                continue
            hit_flags[track_id][i] = True
            need = HAND_RAISE_MIN_RISE * face_height
            evidence.setdefault((track_id, i), {
                "need": need, "multiple": round(rise / need, 3)})
    signals = {track: list(tops) for track in hit_flags}
    return finalize_candidates(_motion_spans(
        m3, assignment, hit_flags, HAND_RAISE_WINDOW,
        signals=signals, evidence=evidence, signal_kind="rise",
        strong_multiple=STRONG_RISE_MULTIPLE))


def nod_candidates(m3: dict, assignment: List[List[Optional[str]]]
                   ) -> List[dict]:
    """Per face track, runs of frames where the face centre's vertical
    peak-to-peak displacement within `NOD_WINDOW` consecutive frames of
    the same track reaches `NOD_MIN_DISPLACEMENT` face heights, with the
    motion reversing direction - a nod is down-then-up, not a one-way
    lean. A CANDIDATE for `--verify`, never an answer."""
    times = [f["t"] for f in m3["frames"]]
    per_track: Dict[str, Dict[int, dict]] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        for face, track_id in zip(frame["faces"], row):
            if track_id is None:
                continue
            top, centre, bottom = _face_verticals(face)
            per_track.setdefault(track_id, {})[i] = {
                "cy": centre, "h": bottom - top, "box": face["box"]}
    hit_flags: Dict[str, List[bool]] = {t: [False] * len(times)
                                        for t in _all_tracks(assignment)}
    evidence: Dict[Tuple[str, int], dict] = {}
    for track_id, frames in per_track.items():
        for i in sorted(frames):
            window = list(range(i - NOD_WINDOW, i + 1))
            if any(j not in frames for j in window):
                continue
            values = [frames[j]["cy"] for j in window]
            mean_h = sum(frames[j]["h"] for j in window) / len(window)
            if mean_h <= 0:
                continue
            p2p = max(values) - min(values)
            if p2p < NOD_MIN_DISPLACEMENT * mean_h:
                continue
            steps = [values[k + 1] - values[k] for k in range(len(values) - 1)]
            if not (any(s > 0 for s in steps) and any(s < 0 for s in steps)):
                continue
            hit_flags[track_id][i] = True
            need = NOD_MIN_DISPLACEMENT * mean_h
            evidence.setdefault((track_id, i), {
                "need": need, "multiple": round(p2p / need, 3)})
    signals = {track: [frames.get(i, {}).get("cy")
                       for i in range(len(times))]
               for track, frames in per_track.items()}
    for track in hit_flags:
        signals.setdefault(track, [None] * len(times))
    return finalize_candidates(_motion_spans(
        m3, assignment, hit_flags, NOD_WINDOW,
        signals=signals, evidence=evidence, signal_kind="oscillation",
        strong_multiple=STRONG_NOD_MULTIPLE))


def object_pickup_candidates(m3: dict, assignment: List[List[Optional[str]]]
                             ) -> List[dict]:
    """Per face track, runs of frames where a hand rose
    `PICKUP_MIN_RISE` face heights within `PICKUP_WINDOW` frames, from
    below the face's bottom to above it - the arc of lifting something
    up from a table or lap. A CANDIDATE for `--verify`, never an answer:
    the rule proposes, the VLM disposes."""
    times = [f["t"] for f in m3["frames"]]
    rises = _hand_rises(m3, PICKUP_WINDOW)
    tops = [_hand_top_y(frame) for frame in m3["frames"]]
    hit_flags: Dict[str, List[bool]] = {t: [False] * len(times)
                                        for t in _all_tracks(assignment)}
    evidence: Dict[Tuple[str, int], dict] = {}
    for i, (frame, row) in enumerate(zip(m3["frames"], assignment)):
        rise = rises[i]
        if rise is None:
            continue
        before = m3["frames"][i - PICKUP_WINDOW]
        top_before = _hand_top_y(before)
        top_now = _hand_top_y(frame)
        for face, track_id in zip(frame["faces"], row):
            if track_id is None:
                continue
            top, centre, bottom = _face_verticals(face)
            face_height = bottom - top
            if face_height <= 0 or rise < PICKUP_MIN_RISE * face_height:
                continue
            face_before = _face_of_track(before, assignment[i - PICKUP_WINDOW],
                                         track_id)
            if face_before is None:
                continue
            before_bottom = _face_verticals(face_before)[2]
            if top_before <= before_bottom or top_now >= bottom:
                continue
            hit_flags[track_id][i] = True
            need = PICKUP_MIN_RISE * face_height
            evidence.setdefault((track_id, i), {
                "need": need, "multiple": round(rise / need, 3)})
    signals = {track: list(tops) for track in hit_flags}
    return finalize_candidates(_motion_spans(
        m3, assignment, hit_flags, PICKUP_WINDOW,
        signals=signals, evidence=evidence, signal_kind="rise",
        strong_multiple=STRONG_RISE_MULTIPLE))


# ── building M7 ─────────────────────────────────────────────────────


def build_source_events(content_digest: str, source_file: str,
                        root: Optional[Path] = None) -> dict:
    """M7 for one source from its M3 + M3b. Light: no decode, no model."""
    m3 = person_measurements.read_m3(content_digest, root)
    identity = person_entity.read_identity(content_digest, root)
    if m3 is None:
        raise RuntimeError("no M3 (persons.json); run "
                           "`python3 -m library.tools.person_measurements build`")
    if identity is None:
        raise RuntimeError("no M3b (identity.json); run "
                           "`python3 -m library.tools.person_entity build`")
    assignment = assign_face_tracks(m3, identity)
    links = link_voices_to_faces(m3, assignment, identity)
    faces_total = sum(len(row) for row in assignment)
    faces_assigned = sum(1 for row in assignment for t in row if t)
    record = {
        "content_digest": content_digest,
        "size_bytes": m3.get("size_bytes"),
        "source_file": source_file,
        "status": STATUS_BUILT,
        "frame_count": len(m3["frames"]),
        "faces_assigned": faces_assigned,
        "faces_unassigned": faces_total - faces_assigned,
        "predicates": {
            "on_screen": {
                "basis": "M3 Vision faces at the M2 cadence, assigned to M3b "
                         "ArcFace tracks by position",
                "spans": on_screen_spans(m3, assignment)},
            "speaking": {
                "basis": "M3b diarized voice turns, attributed to a face by "
                          "lip motion (M3)",
                "voice_face_links": links,
                "voice_unavailable_reason": (identity.get("instrument") or {}
                                              ).get("voice_unavailable_reason"),
                "spans": speaking_spans(identity, links, m3, assignment)},
        },
        "candidates": {
            "hand_near_mouth": {
                "basis": f"M3 hand joint within {HAND_CANDIDATE_MAX_DISTANCE} "
                         f"face widths of the lips; a CANDIDATE for "
                         f"`--verify`, never an answer",
                "spans": hand_at_mouth_candidates(m3, assignment),
            },
            "hand_raise": {
                "basis": CANDIDATE_PREDICATES["hand_raise"],
                "spans": hand_raise_candidates(m3, assignment),
            },
            "nod": {
                "basis": CANDIDATE_PREDICATES["nod"],
                "spans": nod_candidates(m3, assignment),
            },
            "object_pickup": {
                "basis": CANDIDATE_PREDICATES["object_pickup"],
                "spans": object_pickup_candidates(m3, assignment),
            },
            "person_enters": {
                "basis": CANDIDATE_PREDICATES["person_enters"],
                "spans": person_enters_candidates(m3, assignment),
            },
            "person_leaves": {
                "basis": CANDIDATE_PREDICATES["person_leaves"],
                "spans": person_leaves_candidates(m3, assignment),
            },
        },
        "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"),
    }
    source_memory.write_json(
        source_memory.source_dir(content_digest, root) / source_memory.SLOT_EVENTS,
        record)
    return {"content_digest": content_digest, "source_file": source_file,
            "faces_assigned": faces_assigned,
            "faces_unassigned": faces_total - faces_assigned,
            **{name: len(body["spans"])
               for name, body in record["predicates"].items()},
            **{f"{name}_candidates": len(body["spans"])
               for name, body in record["candidates"].items()},
            "voice_face_links": {link["voice_track"]: link["face_track"]
                                 for link in links}}


def read_m7(content_digest: str, root: Optional[Path] = None) -> Optional[dict]:
    """A digest's M7 record, or None when never built."""
    path = source_memory.source_dir(content_digest, root) / source_memory.SLOT_EVENTS
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return None
    return doc


def catalog_sources(project_folder: str) -> List[Tuple[str, str, str]]:
    """`[(clip_id, digest, source_file)]`, one per distinct digest."""
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    seen, out = set(), []
    for clip in source_memory.load_catalog(project_folder):
        digest, _ = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None or digest in seen:
            continue
        seen.add(digest)
        out.append((clip.get("clip_id"), digest,
                    clip.get("source_file") or clip.get("path") or ""))
    return out


@source_memory._project_scoped
def build_project_events(project_folder: str,
                         root: Optional[Path] = None) -> dict:
    """M7 for every catalog source that carries M3 and M3b.

    One lane per distinct digest: catalog_sources dedups serially up
    front, and each unit is pure compute over its M3/M3b records plus
    one atomic slot write - no shared model, no lock, no warm-up. Input
    order is restored, so the report is identical to serial.
    """
    from collections import deque

    from library.tools.concurrency_routing import run_lanes

    def _one(job):
        clip_id, digest, path = job
        try:
            account = build_source_events(digest, path, root)
        except Exception as exc:
            return ({"clip_id": clip_id, "content_digest": digest,
                     "failed": f"{type(exc).__name__}: {exc}"}, clip_id)
        return ({"clip_id": clip_id, **account}, None)

    results, failed = [], []
    measured = {}
    units = catalog_sources(project_folder)
    for row, failed_id in run_lanes(_one, units):
        measured.setdefault(row["clip_id"], deque()).append(row)
        if failed_id is not None:
            failed.append(failed_id)
    for clip_id, _digest, _path in units:
        queue = measured.get(clip_id)
        if queue:
            results.append(queue.popleft())
    return {"project_folder": str(Path(project_folder).resolve()),
            "clips": results, "failed": failed}


# ── the timeline side: speaker labels and placement ─────────────────


def timeline_items(project_folder: str) -> List[dict]:
    """The saved timeline transcript's items: per `resolve_item_id`, its
    source file, speaker track, constant source->timeline offset and the
    source extent its words RECORD. `[]` with no saved transcript."""
    from library.tools import timeline_transcript

    try:
        with open(timeline_transcript.transcript_path(project_folder),
                  encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return []
    items: Dict[str, dict] = {}
    for seg in doc.get("segments") or []:
        item_id = seg.get("resolve_item_id")
        if not item_id or seg.get("source_start") is None:
            continue
        item = items.setdefault(item_id, {
            "resolve_item_id": item_id, "speaker": seg.get("speaker"),
            "source_file": seg["source_file"],
            "offset": seg["timeline_start"] - seg["source_start"],
            "source_start": seg["source_start"], "source_end": seg["source_end"],
            "segments": []})
        item["source_start"] = min(item["source_start"], seg["source_start"])
        item["source_end"] = max(item["source_end"], seg["source_end"])
        item["segments"].append((seg["source_start"], seg["source_end"]))
    return list(items.values())


def _digest_of_files(project_folder: str) -> Dict[str, str]:
    return {path: digest for _c, digest, path in catalog_sources(project_folder)}


def spans_on_every_angle(digest: str, start: float, end: float,
                         root: Optional[Path] = None) -> Dict[str, Tuple[float, float]]:
    """The span on its own source plus the same moment on every other
    member of its multicam group (M6). A source in no group maps to
    itself only - never to a zero offset on another file."""
    out = {digest: (start, end)}
    starts = conversation_clock.map_time(digest, start, root)
    ends = conversation_clock.map_time(digest, end, root)
    for other, other_start in starts.items():
        if other in ends:
            out[other] = (other_start, ends[other])
    return out


def place(project_folder: str, digest: str, start: float, end: float,
          items: List[dict], files: Dict[str, str],
          root: Optional[Path] = None) -> List[dict]:
    """Timeline placements of one source span, across every angle."""
    out = []
    for angle, (a, b) in spans_on_every_angle(digest, start, end, root).items():
        for item in items:
            if files.get(item["source_file"]) != angle:
                continue
            lo, hi = max(a, item["source_start"]), min(b, item["source_end"])
            if hi <= lo:
                continue
            out.append({"resolve_item_id": item["resolve_item_id"],
                        "track_speaker": item["speaker"],
                        "via": "same source" if angle == digest else "M6 clock",
                        "source_file": item["source_file"],
                        "source_start": round(lo, 3), "source_end": round(hi, 3),
                        "timeline_start": round(lo + item["offset"], 3),
                        "timeline_end": round(hi + item["offset"], 3)})
    return out


def _overlap(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def derived_person_names(project_folder: str, roster: List[dict],
                         root: Optional[Path] = None) -> Dict[str, dict]:
    """`{person_id: {name, agreement, seconds}}` from the timeline's own
    speaker labels: the person's attributed speech (their linked voices'
    turns on their own angles, carried to every angle by M6) overlapped
    with the transcript's labelled speech on the same source. A person
    whose speech does not reach `NAME_MIN_AGREEMENT` on one label gets no
    derived name."""
    items = timeline_items(project_folder)
    files = _digest_of_files(project_folder)
    labelled: Dict[str, List[Tuple[float, float, str]]] = {}
    for item in items:
        digest = files.get(item["source_file"])
        if digest and item["speaker"]:
            for a, b in item["segments"]:
                labelled.setdefault(digest, []).append((a, b, item["speaker"]))
    out = {}
    for person in roster:
        by_label: Dict[str, float] = {}
        for span in person_speaking(person, root):
            for angle, (a, b) in spans_on_every_angle(
                    span["content_digest"], span["start"], span["end"], root).items():
                for la, lb, label in labelled.get(angle, ()):
                    by_label[label] = by_label.get(label, 0.0) + _overlap((a, b), (la, lb))
        total = sum(by_label.values())
        if total <= 0:
            continue
        label, seconds = max(by_label.items(), key=lambda kv: kv[1])
        if seconds / total >= NAME_MIN_AGREEMENT:
            out[person["person_id"]] = {
                "name": label, "agreement": round(seconds / total, 3),
                "seconds": round(total, 1),
                "basis": "attributed speech vs the timeline transcript's "
                         "per-speaker tracks"}
    return out


# ── the query ───────────────────────────────────────────────────────


def _tracks_of(person: dict) -> Dict[str, set]:
    out: Dict[str, set] = {}
    for track in person.get("face_tracks") or []:
        out.setdefault(track["content_digest"], set()).add(track["track_id"])
    return out


def _predicate_spans(person: dict, predicate: str,
                     root: Optional[Path] = None) -> List[dict]:
    spans = []
    for digest, tracks in _tracks_of(person).items():
        record = read_m7(digest, root)
        if record is None:
            continue
        for span in record["predicates"][predicate]["spans"]:
            if span.get("face_track") in tracks:
                spans.append({"content_digest": digest,
                              "source_file": record["source_file"], **span})
    return sorted(spans, key=lambda s: (s["source_file"], s["start"]))


def person_speaking(person: dict, root: Optional[Path] = None) -> List[dict]:
    return _predicate_spans(person, "speaking", root)


def resolve_person(project_folder: str, query: str,
                   root: Optional[Path] = None) -> Tuple[dict, dict]:
    """`(person, naming)` by person_id, declared name or derived name,
    case-insensitive. Refuses with the roster when nothing resolves."""
    roster = person_entity.resolve_person_tracks(project_folder, root)["persons"]
    derived = derived_person_names(project_folder, roster, root)
    wanted = query.strip().lower()
    for person in roster:
        pid = person["person_id"]
        if pid.lower() == wanted:
            return person, {"by": "person_id"}
        if person.get("name") and person["name"].strip().lower() == wanted:
            return person, {"by": "declared name (source.person_names)"}
        if pid in derived and derived[pid]["name"].strip().lower() == wanted:
            return person, {"by": "derived name", **derived[pid]}
    known = ", ".join(
        f"{p['person_id']}"
        + (f" ({p['name']}, declared)" if p.get("name") else "")
        + (f" ({derived[p['person_id']]['name']}, derived)"
           if p["person_id"] in derived else "")
        for p in roster) or "none"
    raise RenRefusal(
        f"no person matching {query!r}",
        f"the project's measured roster is: {known}",
        "build M3b and M7 first (`person_entity build`, `event_spans build`), "
        "or declare `source.person_names` in project.yaml")


def _candidate_spans(person: dict, predicate: str,
                     root: Optional[Path] = None) -> List[dict]:
    spans = []
    for digest, tracks in _tracks_of(person).items():
        record = read_m7(digest, root)
        body = ((record or {}).get("candidates") or {}).get(predicate)
        if body is None:
            raise RenRefusal(
                f"M7 on {digest[:12]} carries no {predicate!r} candidates",
                "it was built before candidates were recorded; an unbuilt "
                "source would read as 'never happens there'",
                "python3 -m library.tools.event_spans build <project>")
        for span in body["spans"]:
            if span.get("face_track") in tracks:
                # Older M7 records predate tiering. Hand-to-mouth frames
                # already carry the measured face-width distance, so apply
                # the same cheap 1.25-width strong-tier cutoff at read time
                # without rewriting shared source memory.
                if predicate == "hand_near_mouth" and "tier" not in span:
                    distances = [frame.get("d") for frame in span["frames"]
                                 if frame.get("d") is not None]
                    span_tier = (TIER_STRONG if distances and
                                 min(distances) <= HAND_MOUTH_TIER0_DISTANCE
                                 else TIER_MARGINAL)
                    span = {**span, "tier": span_tier}
                spans.append({"content_digest": digest,
                              "source_file": record["source_file"], **span})
    return sorted(spans, key=lambda s: (s["source_file"], s["start"]))


@source_memory._project_scoped
def verified_query(project_folder: str, person_query: str, predicate: str,
                   statement: str, root: Optional[Path] = None,
                   model=None, max_tier: Optional[int] = None) -> dict:
    """person x candidate predicate -> the local VLM judges `statement`
    on each candidate span's frames -> the YES spans, placed like any
    other hit. Every verdict (yes, no or unparsed) is returned with its
    reason; only YES spans are hits.

    Spans verify strongest-tier first (tier 0 before tier 1), so the
    VLM sees the best-measured geometry before the marginal band.
    `max_tier` budgets VLM calls: spans above it are reported under
    `skipped_by_tier` and never sent to the verifier. Hand-near-mouth
    defaults to tier 0; pass `max_tier=1` to include its marginal candidates.
    Other predicates keep their existing all-tier default."""
    from library.tools import span_verification

    if predicate not in CANDIDATE_PREDICATES:
        raise RenRefusal(
            f"--verify cannot judge {predicate!r}",
            "the VLM looks at candidate spans only, never a whole episode; "
            f"{predicate!r} spans are not candidates (on_screen runs the "
            "length of every take)",
            f"candidate predicates: {', '.join(CANDIDATE_PREDICATES)}")
    if not statement.strip():
        raise RenRefusal("--verify needs a statement", "nothing to judge",
                         "--verify \"covers his mouth with his hand\"")
    person, naming = resolve_person(project_folder, person_query, root)
    missing = [digest for digest in _tracks_of(person)
               if read_m7(digest, root) is None]
    if missing:
        raise RenRefusal(
            f"M7 is missing for {len(missing)} of this person's sources",
            "an unbuilt source would read as 'never happens there'",
            f"python3 -m library.tools.event_spans build '{project_folder}'")
    candidates = _candidate_spans(person, predicate, root)
    ordered = sorted(candidates, key=lambda s: (s.get("tier", TIER_STRONG),
                                                -(s.get("score", 0.0) or 0.0),
                                                s["start"]))
    if max_tier is None and predicate == "hand_near_mouth":
        max_tier = TIER_STRONG
    skipped_by_tier = 0
    if max_tier is not None:
        kept = [s for s in ordered if s.get("tier", TIER_STRONG) <= max_tier]
        skipped_by_tier = len(ordered) - len(kept)
        ordered = kept
    items = timeline_items(project_folder)
    files = _digest_of_files(project_folder)
    by_digest: Dict[str, List[dict]] = {}
    for span in ordered:
        by_digest.setdefault(span["content_digest"], []).append(span)
    hits, rejected = [], []
    calls = cached = 0
    vlm_seconds = 0.0
    tier_cost: Dict[int, Dict[str, float]] = {}
    for digest, spans in by_digest.items():
        verdicts = span_verification.verify_spans(digest, spans, statement,
                                                  root=root, model=model)
        for span, verdict in zip(spans, verdicts):
            tier = span.get("tier", TIER_STRONG)
            row = tier_cost.setdefault(tier, {"candidates": 0, "calls": 0})
            row["candidates"] += 1
            cached += verdict["cached"]
            if not verdict["cached"]:
                calls += 1
                row["calls"] += 1
                vlm_seconds += verdict["seconds"]
            entry = {"source_file": span["source_file"], "start": span["start"],
                     "end": span["end"],
                     "verification": {k: verdict[k] for k in (
                         "answer", "reason", "frames", "frame_t", "model",
                         "cached")}}
            if verdict["answer"] != span_verification.ANSWER_YES:
                rejected.append(entry)
                continue
            entry["placements"] = place(project_folder, digest, span["start"],
                                        span["end"], items, files, root)
            hits.append(entry)
    placed = [h for h in hits if h["placements"]]
    return {
        "person": person["person_id"], "naming": naming,
        "predicate": predicate, "verify": statement,
        "signals": {
            "person": "M3b ArcFace face tracks, unified across sources",
            "candidates": CANDIDATE_PREDICATES[predicate],
            "verification": f"{span_verification.MAX_FRAMES_PER_SPAN} M2 "
                            f"frames per span (whole frame), "
                            f"judged by the local VLM",
            "join": "M6 conversation clock",
            "placement": "timeline_transcript items (resolve_item_id, "
                         "constant offset, recorded source extent)"},
        "timeline_transcript": bool(items),
        "candidates": len(candidates),
        "verified_candidates": len(ordered),
        "skipped_by_tier": skipped_by_tier,
        "cost": {"vlm_calls": calls, "cached_verdicts": cached,
                 "vlm_seconds": round(vlm_seconds, 1),
                 "by_tier": {str(tier): row
                             for tier, row in sorted(tier_cost.items())}},
        "spans": len(hits), "spans_placed": len(placed),
        "source_seconds": round(sum(h["end"] - h["start"] for h in hits), 1),
        "hits": hits, "rejected": rejected,
    }


@source_memory._project_scoped
def query(project_folder: str, person_query: str, predicate: str,
          root: Optional[Path] = None) -> dict:
    """person x predicate -> source spans -> every angle -> timeline items.

    Hits carry their span's median face capture quality and come back
    best face first: measured spans order by quality descending, spans
    with no measured quality keep time order behind them. An ordering,
    never a threshold - a span is never dropped for looking bad.
    """
    if predicate in UNSHIPPED:
        raise RenRefusal(f"predicate {predicate!r} is not answered alone",
                         UNSHIPPED[predicate],
                         f"ren search <project> --person <name> --predicate "
                         f"{predicate} --verify \"<what you see>\"; or "
                         f"answerable alone: {', '.join(PREDICATES)}")
    if predicate in CANDIDATE_PREDICATES:
        raise RenRefusal(
            f"predicate {predicate!r} is a candidate, not an answer",
            CANDIDATE_PREDICATES[predicate],
            f"ren search <project> --person <name> --predicate "
            f"{predicate} --verify \"<what you see>\"; answerable alone: "
            f"{', '.join(PREDICATES)}")
    if predicate not in PREDICATES:
        raise RenRefusal(f"unknown predicate {predicate!r}",
                         f"M7 measures: {', '.join(PREDICATES)}",
                         f"use --predicate {' / --predicate '.join(PREDICATES)}")
    person, naming = resolve_person(project_folder, person_query, root)
    missing = [digest for digest in _tracks_of(person)
               if read_m7(digest, root) is None]
    if missing:
        raise RenRefusal(
            f"M7 is missing for {len(missing)} of this person's sources",
            "an unbuilt source would read as 'never happens there'",
            f"python3 -m library.tools.event_spans build '{project_folder}'")
    items = timeline_items(project_folder)
    files = _digest_of_files(project_folder)
    hits = []
    for span in _predicate_spans(person, predicate, root):
        hits.append({**{k: span[k] for k in ("source_file", "start", "end")},
                     "capture_quality": span.get("capture_quality"),
                     "placements": place(project_folder, span["content_digest"],
                                         span["start"], span["end"], items,
                                         files, root)})
    # Best face first; unmeasured spans keep their time order behind
    # every measured one (the sort is stable, and _predicate_spans is
    # in time order). No span is dropped for its quality.
    hits.sort(key=lambda h: (h["capture_quality"] is None,
                             -(h["capture_quality"] or 0.0)))
    placed = [h for h in hits if h["placements"]]
    return {
        "person": person["person_id"], "naming": naming,
        "predicate": predicate,
        "signals": {
            "person": "M3b ArcFace face tracks, unified across sources",
            "predicate": {"speaking": "M3b ECAPA voice turns, linked to the "
                                      "face by M3 lip motion",
                          "on_screen": "M3 Apple Vision faces at the M2 "
                                       "cadence, assigned to M3b tracks"}[predicate],
            "join": "M6 conversation clock",
            "placement": "timeline_transcript items (resolve_item_id, "
                         "constant offset, recorded source extent)"},
        "timeline_transcript": bool(items),
        "spans": len(hits), "spans_placed": len(placed),
        "source_seconds": round(sum(h["end"] - h["start"] for h in hits), 1),
        "hits": hits,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="event_spans",
        description="M7: derived per-person event spans (speaking, "
                    "on_screen) from M3 + M3b. Light: no decode, no model.")
    sub = parser.add_subparsers(dest="command")
    p_build = sub.add_parser("build", help="M7 for a project's catalog")
    p_build.add_argument("project")
    p_names = sub.add_parser("names", help="derived person names and evidence")
    p_names.add_argument("project")
    p_query = sub.add_parser("query", help="person x predicate -> timeline")
    p_query.add_argument("project")
    p_query.add_argument("--person", required=True)
    p_query.add_argument("--predicate", required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        report = build_project_events(args.project)
        print(json.dumps(report, indent=2))
        return 1 if report["failed"] else 0
    if args.command == "names":
        roster = person_entity.resolve_person_tracks(args.project)["persons"]
        print(json.dumps(derived_person_names(args.project, roster), indent=2))
        return 0
    if args.command == "query":
        print(json.dumps(query(args.project, args.person, args.predicate), indent=2))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import REFUSAL_EXIT_CODE
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
