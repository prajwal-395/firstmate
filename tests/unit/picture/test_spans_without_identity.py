"""M7 event spans WITHOUT the identity lane, and a measurement CLI that
finds the project it was pointed at.

Two defects, one cause each:

1. **A licence blocked the whole event lane.** `event_spans` REFUSED to
   build without M3b (ArcFace), so a commercial project - where the
   InsightFace pack is refused for its licence - measured NO event spans
   at all. The candidate rules are geometry over M3 faces and hands and
   never needed an identity. A licence must not remove a measurement:
   the candidates are measured, and the person-scoped predicates are
   recorded as UNAVAILABLE, never as empty spans.

2. **A project slug resolved to nothing.** The measurement CLIs read
   `pipeline_data.json` relative to the string they were given, so a
   slug read as "no clips", exit 0, no error - a measurement that never
   ran, reported as a measurement that found nothing. A reference that
   resolves to no project must REFUSE by name.
"""
import json

import pytest

from library.tools import (
    event_spans,
    face_tracks,
    person_measurements,
    project_ref,
    source_memory,
)
from library.tools.ren_refusal import RenRefusal


def _m3(frames, digest="d" * 64, pixels=(384, 216)):
    return {"content_digest": digest, "size_bytes": 1,
            "source_file": "/cam/A.MOV", "status": "measured",
            "coordinates": "image-normalised, top-left origin",
            "frame_count": len(frames), "frames": frames,
            "instrument": {"method": "vision-helper-v2 over M2",
                           "frame_pixels": list(pixels)}}


def _face(centre_y=0.3, height=0.2, left=0.45):
    top = centre_y - height / 2.0
    return {"box": [left, top, left + 0.1, top + height]}


def _mouth_face():
    return {"box": [0.45, 0.2, 0.55, 0.4], "outer_lips": [[0.5, 0.4]]}


def _hand(y):
    return {"chirality": "left", "confidence": 1.0,
            "joints": {"wrist": [0.5, y, 0.9]}}


@pytest.fixture
def memory_root(tmp_path):
    """An isolated source-memory root: nothing touches the machine store."""
    return tmp_path / "memory"


def _write_m3(root, digest, record):
    path = source_memory.source_dir(digest, root) / source_memory.SLOT_PERSONS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


# ── the geometry track: a face continuing, not a person ─────────────


def test_a_face_that_continues_keeps_one_track():
    """A face that moves a little across samples is ONE track - a track
    that restarts on every sample would split every candidate rule's
    window and propose nothing."""
    frames = [{"t": i * 0.5, "faces": [_face(0.3 + 0.01 * i)], "hands": []}
              for i in range(6)]
    assignment = face_tracks.assign_tracks(_m3(frames))
    assert {t for row in assignment for t in row if t} == {"geo_001"}


def test_a_face_that_leaves_and_comes_back_is_a_new_track():
    """A track unseen for more than the gap closes: the face that comes
    back later is an entry, not a re-detection of the same presence."""
    frames = []
    for i in range(10):
        present = i < 3 or i >= 8
        frames.append({"t": i * 0.5,
                       "faces": [_face(0.3)] if present else [], "hands": []})
    assignment = face_tracks.assign_tracks(_m3(frames))
    tracks = {t for row in assignment for t in row if t}
    assert len(tracks) == 2, "the returning face must not join the closed track"


def test_two_faces_in_one_frame_are_two_tracks():
    """Two faces are two tracks: a track that swallowed both would put
    one person's hand at the other person's mouth."""
    frames = [{"t": i * 0.5,
               "faces": [_face(0.3, left=0.10), _face(0.3, left=0.70)],
               "hands": []} for i in range(4)]
    assignment = face_tracks.assign_tracks(_m3(frames))
    assert len({t for row in assignment for t in row if t}) == 2
    assert all(len(set(row)) == 2 for row in assignment)


# ── M7 builds without the identity lane ─────────────────────────────


def test_m7_builds_its_candidates_without_the_identity_lane(memory_root):
    """Without M3b the event lane still measures: the candidate rules
    are geometry over M3, and a licence-blocked identity model must not
    remove them. The record says which basis the tracks came from."""
    digest = "a" * 64
    frames = []
    for i in range(8):
        y = 0.8 if i < 4 else 0.25
        frames.append({"t": i * 0.5, "faces": [_face(0.3)], "hands": [_hand(y)]})
    _write_m3(memory_root, digest, _m3(frames, digest))

    account = event_spans.build_source_events(digest, "/cam/A.MOV", memory_root)

    assert account["identity"] == "geometry"
    assert account["object_pickup_candidates"] >= 1
    record = json.loads((source_memory.source_dir(digest, memory_root)
                         / source_memory.SLOT_EVENTS).read_text("utf-8"))
    assert record["candidates"]["object_pickup"]["spans"]
    assert record["identity"]["available"] is False
    assert "licence" in record["identity"]["unavailable_reason"]


def test_m7_without_identity_does_not_record_a_predicate_it_never_measured(
        memory_root):
    """`speaking` and `on_screen` are PERSON-scoped: with no identity
    they were not measured. Recording them as empty spans would read as
    "this person never spoke and was never on screen" - a false negative
    out of a licence. They are recorded as unavailable, with the
    reason."""
    digest = "b" * 64
    frames = [{"t": i * 0.5, "faces": [_face(0.3)], "hands": []}
              for i in range(4)]
    _write_m3(memory_root, digest, _m3(frames, digest))

    event_spans.build_source_events(digest, "/cam/A.MOV", memory_root)
    record = json.loads((source_memory.source_dir(digest, memory_root)
                         / source_memory.SLOT_EVENTS).read_text("utf-8"))

    assert record["predicates"] == {}
    for predicate in ("speaking", "on_screen"):
        assert predicate in record["predicates_unavailable"]
        assert record["predicates_unavailable"][predicate].strip()


def test_an_identity_record_with_no_face_track_falls_back_to_geometry(
        memory_root):
    """M3b can be PRESENT and carry no face track - no faces detected, or
    the identity model unavailable for this purpose. That is not a
    licence block and it is not a measurement that found no faces: the
    candidates are still measured, on geometry tracks, and the record
    says which case it was."""
    digest = "d" * 64
    frames = [{"t": i * 0.5, "faces": [_face(0.3)], "hands": []}
              for i in range(4)]
    _write_m3(memory_root, digest, _m3(frames, digest))
    identity = {"status": "insightface-unavailable", "faces": [], "voices": [],
                "instrument": {"face_unavailable_reason":
                               "the model pack is not authorised"}}
    path = (source_memory.source_dir(digest, memory_root)
            / source_memory.SLOT_IDENTITY)
    path.write_text(json.dumps(identity), encoding="utf-8")

    account = event_spans.build_source_events(digest, "/cam/A.MOV", memory_root)

    assert account["identity"] == "geometry"
    record = json.loads((source_memory.source_dir(digest, memory_root)
                         / source_memory.SLOT_EVENTS).read_text("utf-8"))
    assert "insightface-unavailable" in record["identity"]["unavailable_reason"]
    assert record["predicates"] == {}


def test_a_measured_empty_predicate_is_still_recorded_as_measured(
        memory_root, monkeypatch):
    """The absence of a measurement and the measurement of an absence
    stay apart: an M7 that HAS the predicate records its (possibly
    empty) spans, and a reader can tell the two records apart."""
    digest = "c" * 64
    frames = [{"t": i * 0.5, "faces": [_face(0.3)], "hands": []}
              for i in range(4)]
    _write_m3(memory_root, digest, _m3(frames, digest))
    identity = {"instrument": {"frame_source": "M2-times-source-decode",
                               "frame_pixels": [384, 216]},
                "faces": [{"track_id": "face_001",
                           "spans": [{"box": [172, 43, 211, 91],
                                      "start": 0.0, "end": 2.0}]}],
                "voices": []}
    path = source_memory.source_dir(digest, memory_root) / source_memory.SLOT_IDENTITY
    path.write_text(json.dumps(identity), encoding="utf-8")

    event_spans.build_source_events(digest, "/cam/A.MOV", memory_root)
    record = json.loads((source_memory.source_dir(digest, memory_root)
                         / source_memory.SLOT_EVENTS).read_text("utf-8"))

    assert record["identity"]["basis"] == "m3b"
    assert "speaking" in record["predicates"]
    assert record["predicates_unavailable"] == {}


def test_the_canonical_model_does_not_turn_an_unmeasured_predicate_into_a_measured_empty_one(
        memory_root, monkeypatch):
    """The migration is where an absent predicate would become a claim:
    an empty `speaking` with COMPLETE coverage reads as "this person
    never spoke". An identity-free M7 must migrate to no `speaking`
    measurement at all, and a note that says why."""
    from library.tools import project_model_evidence

    digest = "e" * 64
    frames = [{"t": i * 0.5, "faces": [_face(0.3)], "hands": []}
              for i in range(4)]
    _write_m3(memory_root, digest, _m3(frames, digest))
    event_spans.build_source_events(digest, "/cam/A.MOV", memory_root)

    builder = project_model_evidence._Builder("p", memory_root)
    project_model_evidence._migrate_events(
        builder, digest, "asset-1", "clock-1", 2.0, memory_root, [])

    spoken = [o for o in builder.model["observations"]
              if o.get("payload", {}).get("predicate") == "speaking"]
    assert spoken == [], "an unmeasured predicate became a measurement"
    assert any("not measured" in note for note in builder.notes)


# ── a measurement that cannot find its project refuses ──────────────


def test_a_project_slug_resolves_to_its_folder(tmp_path, monkeypatch):
    """The measurement CLIs read `pipeline_data.json` relative to what
    they were given: a slug resolved to nothing and the report read
    like 'there was nothing to measure'. A slug must resolve."""
    root = tmp_path / "projects"
    project = root / "m0-pickup"
    project.mkdir(parents=True)
    (project / "pipeline_data.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(root))

    assert project_ref.resolve_project_folder("m0-pickup") == project.resolve()


def test_an_unknown_slug_refuses_instead_of_reporting_an_empty_catalog(
        tmp_path, monkeypatch):
    """A slug that names no project must REFUSE by name: an empty
    catalog with exit 0 is a measurement that never ran, reported as a
    measurement that found nothing."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(root))

    with pytest.raises(RenRefusal) as refused:
        project_ref.resolve_project_folder("no-such-project")
    assert "no-such-project" in refused.value.what
    assert refused.value.fix.strip()


def test_a_folder_that_is_not_a_project_refuses(tmp_path, monkeypatch):
    """A path with no `pipeline_data.json` and no `project.yaml` has no
    catalog to measure; that is a refusal, not an empty result."""
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    folder = tmp_path / "not-a-project"
    folder.mkdir()

    with pytest.raises(RenRefusal):
        project_ref.resolve_project_folder(str(folder))
    with pytest.raises(RenRefusal):
        project_ref.require_project_state(folder)


def test_a_project_scanned_nothing_yet_says_so(tmp_path, monkeypatch):
    """A project with a `project.yaml` but no scan has no catalog yet:
    the fix is to scan it, not to report an empty measurement."""
    project = tmp_path / "p"
    project.mkdir()
    (project / "project.yaml").write_text("slug: p\n", encoding="utf-8")

    with pytest.raises(RenRefusal) as refused:
        project_ref.require_project_state(project)
    assert "scan" in refused.value.fix


def test_the_measurement_cli_refuses_a_slug_it_cannot_resolve(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    """The refusal reaches the caller as a refusal (exit 4), not as an
    empty report: `python3 -m library.tools.person_measurements build
    <slug>` is how the defect was found."""
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path / "projects"))
    with pytest.raises(RenRefusal):
        person_measurements.main(["build", "not-a-project"])
