"""M10 relationship detectors: what they find, what they refuse, and the
coordinate frame they measure in.

Every test names a defect the detector prevents: hands that merely
share a frame reading as a hold, a one-frame proximity reading as a
sustained relationship, a fist or a hand aimed away reading as a point,
an unowned hand guessed into a named relationship, a normalized-unit
distance misread on a non-square frame, and looking-at answered from
box aspect when M3 carries no orientation. No test asserts a registry,
enumeration or schema contains named entries or a count.

Every test builds its memory under `tmp_path`. No test reaches a real
project, runs a model or vision pass, or opens Resolve.
"""
import json
from pathlib import Path
import pytest
from library.tools import event_spans, person_entity, relationships
from library.tools import source_memory


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


def _face(box):
    return {"box": list(box), "confidence": 0.9,
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


def _write_lanes(root, m3, identity):
    _write(root / source_memory.source_dir(DIGEST) / source_memory.SLOT_PERSONS,
           m3)
    _write(root / source_memory.source_dir(DIGEST) / source_memory.SLOT_IDENTITY,
           identity)


def _assignment(m3, identity):
    return event_spans.assign_face_tracks(m3, identity)


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
    spans = relationships.holding_candidates(m3, _assignment(m3, identity))
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
    assert relationships.holding_candidates(m3, _assignment(m3, identity)) == []


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
                      _hand({"wrist": (0.47 if close else 0.75, 0.5),
                             "indexTIP": (0.48 if close else 0.76, 0.45)},
                            chirality="left")]})
    m3 = _m3_doc(frames)
    identity = _identity_doc([(400, 50, 600, 200)])
    _write_lanes(memory_root, m3, identity)
    assert relationships.holding_candidates(m3, _assignment(m3, identity)) == []


def test_holding_skips_a_hand_no_track_claims(memory_root):
    """A hand with no tracked face is left out of named relationships,
    not guessed into one: with the only face unassigned, two close
    hands produce no span."""
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
    assignment = [[None] for _ in frames]
    assert relationships.holding_candidates(m3, assignment) == []


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
    spans = relationships.pointing_candidates(m3, _assignment(m3, identity))
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
    assert relationships.pointing_candidates(m3, _assignment(m3, identity)) == []


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
    assert relationships.pointing_candidates(m3, _assignment(m3, identity)) == []


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
    assert relationships.pointing_candidates(m3, _assignment(m3, identity)) == []


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
    assert relationships.holding_candidates(m3, _assignment(m3, identity)) == []


# ── looking-at is refused, not guessed ───────────────────────────────


def test_looking_at_is_refused_with_its_reason(memory_root, tmp_path):
    """A narrow face box beside another face is exactly what a naive
    aspect-ratio gaze heuristic would call 'looking at'. M3 carries no
    orientation, so the build records the refusal and no looking_at
    span - an empty list would read as 'never looks at anyone'."""
    frames = [{
        "t": 0.0,
        "faces": [_face((0.2, 0.1, 0.32, 0.4)),
                  _face((0.6, 0.1, 0.8, 0.4))],
        "hands": []}]
    m3 = _m3_doc(frames)
    identity = _identity_doc([(200, 50, 320, 200), (600, 50, 800, 200)])
    _write_lanes(memory_root, m3, identity)
    account = relationships.build_source_relationships(DIGEST, SOURCE,
                                                       root=memory_root)
    assert account["looking_at"].startswith("refused")
    record = source_memory.read_relationships(DIGEST, root=memory_root)
    assert "looking_at" not in record["relationships"]
    assert record["unmeasured"]["looking_at"] == (
        relationships.LOOKING_AT_UNMEASURED)
    assert "orientation" in record["unmeasured"]["looking_at"]


# ── building M10 ──────────────────────────────────────────────────────


def test_build_refuses_without_m3_or_m3b(memory_root):
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


def test_build_writes_the_slot_and_read_relationships_round_trips(memory_root):
    """The build writes relationships.json with both candidate types
    and the refusal; read_relationships serves it back, and None when never
    built."""
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
    record = source_memory.read_relationships(DIGEST, root=memory_root)
    assert record["status"] == relationships.STATUS_BUILT
    assert record["content_digest"] == DIGEST
    assert len(record["relationships"]["holding"]["spans"]) == 1
    assert record["relationships"]["pointing"]["spans"] == []
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
