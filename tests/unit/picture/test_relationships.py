"""M10 relationship detectors: what they find, what they refuse, and the
coordinate frame they measure in.

Every test names a defect the detector prevents: hands that merely
share a frame reading as a hold, a one-frame proximity reading as a
sustained relationship, a fist or a hand aimed away reading as a point,
an unowned hand - or an off-frame person's hand - guessed into a named
relationship, a normalized-unit distance misread on a non-square frame,
a head turned toward a partner going unreported, a frontal or turned-
away head read as looking at someone, and a look carried through the
nearer person to whoever sits behind. No test asserts a registry,
enumeration or schema contains named entries or a count.

Every test builds its memory under `tmp_path`. No test reaches a real
project, runs a model or vision pass, or opens Resolve.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from library.tools import (
    event_spans,
    footage_analysis,
    person_entity,
    person_regions,
    relationships,
    source_memory,
    span_verification,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An isolated memory root: nothing touches the machine store."""
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


DIGEST = "digest123"
SOURCE = "/nowhere/source.MOV"
FRAME_PIXELS = (1000, 500)
"""Non-square on purpose: a normalized distance and a pixel distance
disagree here, which is the coordinate defect under test."""


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


REGION_GRID = (100, 50)
"""The M3d mask grid: the frame's aspect, coarse enough to reason about
cell by cell."""

LEFT_FACE = (0.2, 0.1, 0.4, 0.4)
RIGHT_FACE = (0.6, 0.1, 0.8, 0.4)
LEFT_BODY = (0.1, 0.05, 0.45, 1.0)
RIGHT_BODY = (0.55, 0.05, 0.9, 1.0)
"""Two seated people, each person region around their own face."""


def _face(box, yaw=None):
    return {"box": list(box), "confidence": 0.9,
            "pose": {"yaw": yaw, "pitch": None, "roll": None},
            "capture_quality": 0.5,
            "outer_lips": [], "inner_lips": []}


def _hand(joints, chirality="right", confidence=0.9):
    return {"chirality": chirality, "confidence": confidence,
            "joints": {name: [x, y, confidence]
                       for name, (x, y) in joints.items()}}


def _m3_doc(frames, frame_pixels=FRAME_PIXELS):
    return {
        "content_digest": DIGEST,
        "size_bytes": 1234,
        "source_file": SOURCE,
        "status": "measured",
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(frames),
        "frames": frames,
        "instrument": {"method": "vision-helper-v1 over M2",
                       "m2_width": 384,
                       "persistence_iou": 0.3,
                       "frame_pixels": list(frame_pixels)},
    }


def _identity_doc(face_boxes, frame_pixels=FRAME_PIXELS):
    """One M3b face track per box, each observed at the M3 frame times -
    the shape `person_entity` writes (one span per sampled instant)."""
    times = [0.0, 0.5, 1.0]
    faces = []
    for i, box in enumerate(face_boxes):
        faces.append({
            "track_id": f"face_{i + 1:03d}",
            "embedding": [0.1 + i],
            "spans": [{"start": t - 0.1, "end": t + 0.1,
                       "box": list(box), "det_score": 0.9}
                      for t in times]})
    return {
        "content_digest": DIGEST,
        "source_file": SOURCE,
        "status": "measured",
        "faces": faces,
        "voices": [],
        "speech_face_links": [],
        "instrument": {"frame_source": person_entity.FRAME_SOURCE_M2_TIMES,
                       "frame_pixels": list(frame_pixels)},
    }


def _box_mask(box):
    """A rectangular person mask on `REGION_GRID` (cell centres inside)."""
    width, height = REGION_GRID
    xs = (np.arange(width) + 0.5) / width
    ys = (np.arange(height) + 0.5) / height
    return ((xs[None, :] >= box[0]) & (xs[None, :] < box[2])
            & (ys[:, None] >= box[1]) & (ys[:, None] < box[3]))


def _write_regions(root, times, regions, owners):
    """An M3d record + sidecar: per sample, the same person regions.

    `regions` is `[(body_box, region_track, face_index_or_None)]`;
    `owners` maps each region track to its face track (None = unowned).
    The sidecar is written by M3d's own writer, so the reader decodes
    exactly the codec the lane produces.
    """
    sdir = source_memory.source_dir(DIGEST, root)
    masks, samples = [], []
    for t in times:
        instances = []
        for box, track, face in regions:
            instances.append({"track": track, "mask": len(masks),
                              **({"face": face} if face is not None else
                                 {"unassigned": "no_face_inside"})})
            masks.append(person_regions.encode_runs(_box_mask(box)))
        samples.append({"t": t, "instances": instances})
    sidecar = person_regions.write_sidecar(sdir, masks, REGION_GRID)
    _write(sdir / source_memory.SLOT_REGIONS, {
        "content_digest": DIGEST, "status": "measured",
        "grid": list(REGION_GRID), "sidecar": sidecar,
        "frame_count": len(times), "samples": samples,
        "tracks": [{"track_id": track, "face_track": owner}
                   for track, owner in owners.items()],
        "instrument": {"method": person_regions.M3D_METHOD}})


def _write_lanes(root, m3, identity, regions=None, owners=None):
    """M3 + M3b, and M3d (one region per face by default, each owned by
    that face's track) at M3's sample times."""
    _write(root / source_memory.source_dir(DIGEST) / source_memory.SLOT_PERSONS,
           m3)
    _write(root / source_memory.source_dir(DIGEST) / source_memory.SLOT_IDENTITY,
           identity)
    if regions is None:
        regions = [(_body_of(face["box"]), f"region_{f + 1:03d}", f)
                   for f, face in enumerate(m3["frames"][0]["faces"])]
        owners = {f"region_{f + 1:03d}": track["track_id"]
                  for f, track in enumerate(identity["faces"])}
    _write_regions(root, [f["t"] for f in m3["frames"]], regions, owners)


def _body_of(face_box):
    """A person region around a face: the face widened, down to the
    frame's bottom edge."""
    x1, y1, x2, _y2 = face_box
    pad = (x2 - x1) / 2.0
    return (max(0.0, x1 - pad), max(0.0, y1 - 0.05), min(1.0, x2 + pad), 1.0)


def _assignment(m3, identity):
    return event_spans.assign_face_tracks(m3, identity)


def _regions(m3, root):
    return relationships.read_regions(DIGEST, m3, root)


# ── holding ──────────────────────────────────────────────────────────


def test_holding_finds_two_hands_that_meet(memory_root):
    """Two hands whose joints come within the candidate distance for
    three frames are one holding span naming both owners, each frame
    carrying its measured distance as verification evidence."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.45, 0.5), "indexTIP": (0.46, 0.45)}),
                      _hand({"wrist": (0.47, 0.5), "indexTIP": (0.48, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    spans = relationships.holding_candidates(m3, _regions(m3, memory_root))
    assert len(spans) == 1
    span = spans[0]
    assert span["owners"] == ["face_001", "face_001"]
    assert span["start"] == 0.0 and span["end"] == 1.25
    assert [f["d"] for f in span["frames"]] == [0.1, 0.1, 0.1]
    assert span["frames"][0]["hands"] == ["right", "left"]


def test_holding_ignores_hands_that_stay_apart(memory_root):
    """Two people sitting side by side are not holding anything: hands
    a full face width apart produce no span."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.42, 0.5), "indexTIP": (0.43, 0.45)}),
                      _hand({"wrist": (0.62, 0.5), "indexTIP": (0.63, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.holding_candidates(m3, _regions(m3, memory_root)) == []


def test_holding_needs_sustained_proximity(memory_root):
    """A single frame of proximity among distant frames is not a hold:
    the defect is one 2 Hz sample (~0.5 s) reading as a sustained
    relationship."""
    frames = []
    for t in (0.0, 0.5, 1.0, 1.5):
        close = t == 1.0
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.45, 0.5), "indexTIP": (0.46, 0.45)}),
                      _hand({"wrist": (0.47 if close else 0.65, 0.5),
                             "indexTIP": (0.48 if close else 0.66, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.holding_candidates(m3, _regions(m3, memory_root)) == []


def test_holding_skips_a_hand_no_track_claims(memory_root):
    """A hand in a region no face track owns is left out of named
    relationships, not guessed into one: with the only region unowned,
    two close hands produce no span."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.45, 0.5), "indexTIP": (0.46, 0.45)}),
                      _hand({"wrist": (0.47, 0.5), "indexTIP": (0.48, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity,
                 regions=[((0.3, 0.05, 0.7, 1.0), "region_001", 0)],
                 owners={"region_001": None})
    assert relationships.holding_candidates(m3, _regions(m3, memory_root)) == []


def test_holding_does_not_hand_an_off_frame_hand_to_the_nearest_face(
        memory_root):
    """The off-frame hand case M3d measured (LCATL0013 t~60-90): a hand
    whose owner is out of frame reaches in beside the on-screen person.
    Owning hands by the NEAREST FACE gave both hands to that person and
    called it a two-handed hold; by region, the reaching hand is in its
    own faceless region, so nobody is named."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.2, 0.1, 0.4, 0.4))],
            "hands": [_hand({"wrist": (0.42, 0.6), "indexTIP": (0.44, 0.58)}),
                      _hand({"wrist": (0.48, 0.6), "indexTIP": (0.46, 0.58)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 400, 200)])
    _write_lanes(memory_root, m3, identity,
                 regions=[((0.1, 0.05, 0.45, 1.0), "region_001", 0),
                          ((0.45, 0.45, 1.0, 0.8), "region_002", None)],
                 owners={"region_001": "face_001", "region_002": None})
    regions = _regions(m3, memory_root)
    assert relationships.holding_candidates(m3, regions) == []
    owners = relationships._hand_owners(m3, regions)
    assert owners[0] == ["face_001", None]


# ── pointing ─────────────────────────────────────────────────────────


def test_pointing_finds_extended_hand_aimed_at_a_face(memory_root):
    """A hand whose wrist->fingertip ray lands within the angle cutoff
    of another tracked face's centre, extended past the fist cutoff,
    is one pointing span naming pointer and target."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.2, 0.1, 0.4, 0.4)),
                      _face((0.6, 0.1, 0.8, 0.4))],
            "hands": [_hand({"wrist": (0.35, 0.3),
                             "indexTIP": (0.43, 0.285),
                             "middleTIP": (0.43, 0.285)})]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 400, 200), (600, 50, 800, 200)])
    _write_lanes(memory_root, m3, identity)
    spans = relationships.pointing_candidates(
        m3, _assignment(m3, identity), _regions(m3, memory_root))
    assert len(spans) == 1
    span = spans[0]
    assert span["owners"] == ["face_001", "face_002"]
    assert span["start"] == 0.25 and span["end"] == 1.25
    assert span["frames"][0]["angle"] <= relationships.POINTING_MAX_ANGLE_DEG
    assert span["frames"][0]["extension"] >= (
        relationships.POINTING_MIN_HAND_EXTENSION)


def test_pointing_ignores_a_fist(memory_root):
    """A fist's fingertips sit near the wrist: an unextended hand
    produces no pointing span, however well it is aimed."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.2, 0.1, 0.4, 0.4)),
                      _face((0.6, 0.1, 0.8, 0.4))],
            "hands": [_hand({"wrist": (0.35, 0.3),
                             "indexTIP": (0.36, 0.29)})]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 400, 200), (600, 50, 800, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.pointing_candidates(
        m3, _assignment(m3, identity), _regions(m3, memory_root)) == []


def test_pointing_ignores_a_hand_aimed_away(memory_root):
    """An extended hand aimed at nothing in the frame is not a point:
    the ray must land near another tracked face's centre."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.2, 0.1, 0.4, 0.4)),
                      _face((0.6, 0.1, 0.8, 0.4))],
            "hands": [_hand({"wrist": (0.35, 0.3),
                             "indexTIP": (0.35, 0.6)})]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 400, 200), (600, 50, 800, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.pointing_candidates(
        m3, _assignment(m3, identity), _regions(m3, memory_root)) == []


def test_pointing_needs_sustained_aim(memory_root):
    """One frame of aim among frames aimed away is not a point - the
    same sustain rule as holding."""
    frames = []
    for t in (0.0, 0.5, 1.0, 1.5):
        aimed = t == 1.0
        tip = (0.43, 0.285) if aimed else (0.35, 0.6)
        frames.append({
            "t": t,
            "faces": [_face((0.2, 0.1, 0.4, 0.4)),
                      _face((0.6, 0.1, 0.8, 0.4))],
            "hands": [_hand({"wrist": (0.35, 0.3), "indexTIP": tip})]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 400, 200), (600, 50, 800, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.pointing_candidates(
        m3, _assignment(m3, identity), _regions(m3, memory_root)) == []


# ── the coordinate frame ─────────────────────────────────────────────


def test_distances_are_measured_in_pixels_not_normalised_units(memory_root):
    """The dominant bug class `person_measurements` documents: M3 is
    image-normalised, so a distance is only meaningful after x scales
    by the frame width and y by its height. On this 1000x500 frame the
    two joints below are 0.179 apart in normalized units but 113 px
    apart - over the 100 px (0.5 face width) holding cutoff. A detector
    that skipped the scaling would call this a hold."""
    frames = [{
        "t": 0.0,
        "faces": [_face((0.4, 0.1, 0.6, 0.4))],
        "hands": [_hand({"wrist": (0.50, 0.50), "indexTIP": (0.50, 0.50)}),
                  _hand({"wrist": (0.58, 0.66), "indexTIP": (0.58, 0.66)},
                        chirality="left")]}]
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.holding_candidates(m3, _regions(m3, memory_root)) == []


# ── looking at (head-pose ray meets a region) ──────────────────────


def _two_shot(left_yaw, right_yaw, times=(0.0, 0.5, 1.0)):
    """Two seated people, left and right, each head at its own yaw."""
    return _m3_doc([{"t": t,
                     "faces": [_face(LEFT_FACE, left_yaw),
                               _face(RIGHT_FACE, right_yaw)],
                     "hands": []} for t in times])


TWO_SHOT_IDENTITY = [(200, 50, 400, 200), (600, 50, 800, 200)]


def _looking_at(m3, root):
    identity = _identity_doc(TWO_SHOT_IDENTITY)
    return relationships.looking_at_candidates(
        m3, _assignment(m3, identity), _regions(m3, root))


def test_two_people_facing_each_other_are_looking_at_candidates(memory_root):
    """MU-19's acceptance: A on the left turned toward image right
    (positive yaw, the measured convention), B on the right turned
    back toward A. Each is a looking_at candidate naming looker and
    target, every frame carrying its yaw and the `d` M8 ranks by. On
    main the build refused looking_at by name and measured nothing."""
    m3 = _two_shot(0.9, -0.9)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    spans = _looking_at(m3, memory_root)
    assert sorted(span["owners"] for span in spans) == [
        ["face_001", "face_002"], ["face_002", "face_001"]]
    a_at_b = next(s for s in spans if s["owners"] == ["face_001", "face_002"])
    assert a_at_b["start"] == 0.0 and a_at_b["end"] == 1.25
    frame = a_at_b["frames"][0]
    assert frame["yaw"] == 0.9
    assert frame["d"] == round(math.pi / 2 - 0.9, 3)
    assert frame["region"] == "region_002"
    # A's face centre is x 0.3; the ray meets B's region in its first
    # grid cell (centre x 0.505): 205 px on a 1000 px frame, in 200 px
    # face widths.
    assert frame["reach"] == 1.025
    account = relationships.build_source_relationships(DIGEST, SOURCE,
                                                       root=memory_root)
    assert account["looking_at_spans"] == 2
    record = source_memory.read_relationships(DIGEST, root=memory_root)
    assert sorted(s["owners"] for s in
                  record["relationships"]["looking_at"]["spans"]) == [
        ["face_001", "face_002"], ["face_002", "face_001"]]


def test_a_head_facing_the_camera_is_not_looking_at_anyone(memory_root):
    """A near-frontal face addresses the lens: a yaw under the cutoff
    casts no ray, whatever side it leans to. Without the cutoff every
    talking head in a two-shot would 'look at' its neighbour."""
    m3 = _two_shot(0.05, -0.05)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    assert _looking_at(m3, memory_root) == []


def test_a_head_turned_away_is_not_looking_at_the_partner(memory_root):
    """The ray follows the yaw's SIGN: both heads turned out toward the
    frame edges meet nobody. A rule that read only |yaw| would call two
    people turned away from each other a mutual look."""
    m3 = _two_shot(-0.9, 0.9)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    assert _looking_at(m3, memory_root) == []


def test_an_unmeasured_pose_casts_no_ray(memory_root):
    """A face with no yaw (an older helper, or a pose Vision left nil)
    is not a look in any direction - absent, not defaulted to frontal or
    to turned."""
    m3 = _two_shot(None, None)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    assert _looking_at(m3, memory_root) == []


def test_the_look_stops_at_the_first_person_it_meets(memory_root):
    """A third, unnamed person sits between A and B. A's ray meets them
    first: the look is at THAT person, so no named relationship - never
    carried through them to B behind."""
    m3 = _two_shot(0.9, None)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY),
                 regions=[(LEFT_BODY, "region_001", 0),
                          ((0.46, 0.0, 0.54, 1.0), "region_003", None),
                          (RIGHT_BODY, "region_002", 1)],
                 owners={"region_001": "face_001", "region_002": "face_002",
                         "region_003": None})
    assert _looking_at(m3, memory_root) == []


def test_a_glance_is_not_a_look(memory_root):
    """One sample of a turned head among frontal ones is a glance past -
    the same sustain rule as holding and pointing."""
    frames = [{"t": t,
               "faces": [_face(LEFT_FACE, 0.9 if t == 0.5 else 0.0),
                         _face(RIGHT_FACE, 0.0)],
               "hands": []} for t in (0.0, 0.5, 1.0)]
    m3 = _m3_doc(frames)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    assert _looking_at(m3, memory_root) == []


def test_a_looking_at_span_is_rankable_by_the_m8_verifier(memory_root):
    """Looking-at is a CANDIDATE for M8: `span_verification` shows a
    long span through the frames its `d` ranks closest. A span whose
    frames had no `d` would raise there instead of being verified; this
    one shows the most squarely turned frames, in time order."""
    yaws = [0.5, 1.4, 0.6, 1.3, 0.7, 1.2]
    frames = [{"t": 0.5 * n,
               "faces": [_face(LEFT_FACE, yaw), _face(RIGHT_FACE, None)],
               "hands": []} for n, yaw in enumerate(yaws)]
    m3 = _m3_doc(frames)
    identity = _identity_doc(TWO_SHOT_IDENTITY)
    identity["faces"] = [
        {**track, "spans": [{"start": f["t"] - 0.1, "end": f["t"] + 0.1,
                             "box": track["spans"][0]["box"],
                             "det_score": 0.9} for f in frames]}
        for track in identity["faces"]]
    _write_lanes(memory_root, m3, identity)
    [span] = relationships.looking_at_candidates(
        m3, _assignment(m3, identity), _regions(m3, memory_root))
    shown = span_verification.select_frames(span)
    assert [f["yaw"] for f in shown] == [1.4, 1.3, 0.7, 1.2]


# ── building M10 ──────────────────────────────────────────────────────


def test_build_refuses_without_m3_m3b_or_m3d(memory_root):
    """An unbuilt source would read as 'no relationships here': the
    build refuses by name instead, like M7's build does."""
    identity = _identity_doc([(400, 50, 600, 200)])
    _write(memory_root / source_memory.source_dir(DIGEST)
           / source_memory.SLOT_IDENTITY, identity)
    with pytest.raises(RuntimeError, match="M3"):
        relationships.build_source_relationships(DIGEST, SOURCE,
                                                 root=memory_root)
    (memory_root / source_memory.source_dir(DIGEST)
     / source_memory.SLOT_IDENTITY).unlink()
    m3 = _m3_doc([{"t": 0.0, "faces": [_face((0.4, 0.1, 0.6, 0.4))],
                   "hands": []}])
    _write(memory_root / source_memory.source_dir(DIGEST)
           / source_memory.SLOT_PERSONS, m3)
    with pytest.raises(RuntimeError, match="M3b"):
        relationships.build_source_relationships(DIGEST, SOURCE,
                                                 root=memory_root)
    _write(memory_root / source_memory.source_dir(DIGEST)
           / source_memory.SLOT_IDENTITY, identity)
    with pytest.raises(RuntimeError, match="M3d"):
        relationships.build_source_relationships(DIGEST, SOURCE,
                                                 root=memory_root)


def test_build_refuses_m3d_measured_on_another_sample(memory_root):
    """Frame `i` must be the same instant in M3 and M3d: regions from a
    re-sampled M2 would put each hand and ray against another moment's
    people. The build refuses rather than index across the two."""
    m3 = _two_shot(0.9, -0.9)
    identity = _identity_doc(TWO_SHOT_IDENTITY)
    _write_lanes(memory_root, m3, identity)
    _write_regions(memory_root, [0.0, 0.4, 0.8],
                   [(LEFT_BODY, "region_001", 0)],
                   {"region_001": "face_001"})
    with pytest.raises(RuntimeError, match="another|this M3"):
        relationships.build_source_relationships(DIGEST, SOURCE,
                                                 root=memory_root)


def test_build_writes_the_slot_and_read_relationships_round_trips(memory_root):
    """The build writes relationships.json with all three candidate
    types and its slot version; read_relationships serves it back, and
    None when never built."""
    assert source_memory.read_relationships(DIGEST, root=memory_root) is None
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.45, 0.5), "indexTIP": (0.46, 0.45)}),
                      _hand({"wrist": (0.47, 0.5), "indexTIP": (0.48, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    account = relationships.build_source_relationships(DIGEST, SOURCE,
                                                       root=memory_root)
    assert account["holding_spans"] == 1
    assert account["pointing_spans"] == 0
    assert account["looking_at_spans"] == 0
    record = source_memory.read_relationships(DIGEST, root=memory_root)
    assert record["status"] == relationships.STATUS_BUILT
    assert record["content_digest"] == DIGEST
    assert record["instrument"]["method"] == relationships.M10_METHOD
    assert len(record["relationships"]["holding"]["spans"]) == 1
    assert record["relationships"]["pointing"]["spans"] == []
    assert record["relationships"]["looking_at"]["spans"] == []
    on_disk = json.loads(
        (memory_root / source_memory.source_dir(DIGEST)
         / source_memory.SLOT_RELATIONSHIPS).read_text(encoding="utf-8"))
    assert on_disk["content_digest"] == DIGEST


def test_build_project_reports_each_catalog_source(memory_root, tmp_path):
    """The project build walks the catalog the way M7's does: a source
    with its lanes built is reported with its span counts, and the
    media-offline clip is reported, not failed."""
    frames = []
    for t in (0.0, 0.5, 1.0):
        frames.append({
            "t": t,
            "faces": [_face((0.4, 0.1, 0.6, 0.4))],
            "hands": [_hand({"wrist": (0.45, 0.5), "indexTIP": (0.46, 0.45)}),
                      _hand({"wrist": (0.47, 0.5), "indexTIP": (0.48, 0.45)},
                            chirality="left")]})
    _write_lanes(memory_root, _m3_doc(frames),
                 _identity_doc([(400, 50, 600, 200)]))
    project = tmp_path / "project"
    project.mkdir()
    state = {
        "project_folder": str(project),
        "source_fingerprints": {
            "clip_001": {"content_digest": DIGEST, "size_bytes": 1234}},
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": SOURCE}]}},
    }
    _write(project / "pipeline_data.json", state)
    report = relationships.build_project_relationships(
        str(project), root=memory_root)
    assert report["failed"] == []
    assert len(report["clips"]) == 1
    assert report["clips"][0]["clip_id"] == "clip_001"
    assert report["clips"][0]["holding_spans"] == 1


# ── the ren analyze lane ─────────────────────────────────────────────


def test_m10_is_a_lane_that_rebuilds_when_its_inputs_move(memory_root,
                                                          tmp_path):
    """`ren analyze` runs M10 as a lane: built over the catalog, then
    reused while fresh. A v1 record (hands by nearest face, looking-at
    refused) is stale under the slot version, and so is one older than
    the M3d it was measured from - a re-measured region track would
    otherwise keep serving the old owners."""
    import os
    m3 = _two_shot(0.9, -0.9)
    _write_lanes(memory_root, m3, _identity_doc(TWO_SHOT_IDENTITY))
    project = tmp_path / "project"
    project.mkdir()
    _write(project / "pipeline_data.json", {
        "project_folder": str(project),
        "source_fingerprints": {
            "clip_001": {"content_digest": DIGEST, "size_bytes": 1234}},
        "capability_outputs": {
            "footage.catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "source_file": SOURCE}]}}})
    lane = next(item for item in footage_analysis.LANES
                if item.name == "relationships")
    sources = [{"clip_id": "clip_001", "digest": DIGEST,
                "duration_seconds": 1.5}]
    built = footage_analysis.run_lane(lane, str(project), sources, memory_root)
    assert built["sources"]["clip_001"] == {"status": footage_analysis.BUILT}
    record = source_memory.read_relationships(DIGEST, root=memory_root)
    assert len(record["relationships"]["looking_at"]["spans"]) == 2
    again = footage_analysis.run_lane(lane, str(project), sources, memory_root)
    assert again["sources"]["clip_001"] == {"status": footage_analysis.REUSED}

    slot = source_memory.source_dir(DIGEST, memory_root) / \
        source_memory.SLOT_RELATIONSHIPS
    v1 = {**record, "instrument": None}
    _write(slot, v1)
    assert not footage_analysis.slot_is_fresh(lane, DIGEST, memory_root)
    _write(slot, record)
    assert footage_analysis.slot_is_fresh(lane, DIGEST, memory_root)
    regions = source_memory.source_dir(DIGEST, memory_root) / \
        source_memory.SLOT_REGIONS
    later = slot.stat().st_mtime + 10
    os.utime(regions, (later, later))
    assert not footage_analysis.slot_is_fresh(lane, DIGEST, memory_root)
