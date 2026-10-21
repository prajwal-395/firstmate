"""Identity continuity: one person resolves to one person, across angles.

Every test here names a DEFECT and builds its project under `tmp_path`.
No test reaches a real project, runs a model, or opens Resolve.

The defects:

- the person ON SCREEN is not always the person TALKING, and the roster
  used to give the other speaker's speech to whoever was on screen;
- a voice the measurement contradicts used to be force-attached to the
  person it does not belong to;
- a face that two candidates fit equally well used to be merged into the
  nearest one instead of being reported as undecidable;
- a face that fits one member of a group but not the whole group used to
  be chained into it.
"""
import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import (
    footage_identity,
    identity_continuity,
    person_entity,
    source_memory,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _unit(vec: np.ndarray) -> tuple:
    return tuple((vec / np.linalg.norm(vec)).tolist())


def _embedding(dim: int, seed: int) -> tuple:
    rng = np.random.default_rng(seed)
    return _unit(rng.normal(size=dim))


def _jitter(base: tuple, seed: int, scale: float = 0.05) -> tuple:
    rng = np.random.default_rng(seed)
    v = np.asarray(base) + rng.normal(scale=scale, size=len(base))
    return _unit(v)


def _media(tmp_path: Path, name: str, seed: bytes) -> Path:
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
    return path


def _project(tmp_path: Path, clips: list) -> Path:
    """`clips`: `[(clip_id, media_path, digest)]`."""
    root = tmp_path / "proj"
    catalog = [{"clip_id": cid, "filename": media.name,
                "source_file": str(media), "path": str(media),
                "duration_seconds": 10.0} for cid, media, _ in clips]
    fingerprints = {cid: {"path": str(media), "size_bytes": media.stat().st_size,
                          "content_digest": digest}
                    for cid, media, digest in clips}
    (root).mkdir(parents=True, exist_ok=True)
    (root / "pipeline_data.json").write_text(json.dumps({
        "step_outputs": {"catalog": {"clip_catalog": catalog}},
        "source_fingerprints": fingerprints}), encoding="utf-8")
    return root


def _write_identity(root: Path, digest: str, record: dict):
    source_memory.write_json(root / digest / source_memory.SLOT_IDENTITY,
                             record)


def _private_root(project: Path) -> Path:
    """Where a project-scoped reader looks for this project's source
    measurements: they are private to the project that measured them."""
    return source_memory.project_memory_root(str(project))


def _link(voice_track: str) -> dict:
    return {"t": 1.1, "face_track": "face_001", "voice_track": voice_track,
            "basis": "co-occurrence: voice span + face span overlap"}


def _spans_of(person: dict, digest: str) -> list:
    return sorted((span["start"], span["end"])
                  for span in person.get("voice_spans") or []
                  if span["content_digest"] == digest)


def test_the_other_speakers_voice_on_this_camera_is_not_given_to_the_person_on_screen(
        tmp_path, memory_root):
    """THE DEFECT. Two cameras, one conversation. On camera A the person
    on screen is P, but the voice the camera picked up is Q's - Q is
    talking off-camera. The roster used to hand Q's speech to P, so a
    reference to Q resolved to P on camera A and to Q on camera B: the
    same speaker, two people, depending on the angle.

    The measured voice identity decides: Q's voice on camera A and Q's
    voice on camera B are one measured voice, and its owner is the person
    the co-occurrence agrees on for the MAJORITY of its speech.
    """
    face_p, face_q = _embedding(512, 1), _embedding(512, 2)
    voice_q = _embedding(192, 7)
    media_a = _media(tmp_path, "A.MXF", b"source-a")
    media_b = _media(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project(tmp_path, [("clip_001", media_a, digest_a),
                                  ("clip_002", media_b, digest_b)])

    # Camera A shows P and hears Q (21 s). Camera B shows Q and hears Q
    # (60 s). The measured component is one voice; Q's own camera holds
    # the majority of its speech, so Q owns it.
    _write_identity(_private_root(project), digest_a, {
        "content_digest": digest_a, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_p),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [{"track_id": "voice_001", "embedding": list(voice_q),
                    "spans": [[0.0, 21.0]]}],
        "speech_face_links": [_link("voice_001")]})
    _write_identity(_private_root(project), digest_b, {
        "content_digest": digest_b, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_q),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [{"track_id": "voice_001",
                    "embedding": list(_jitter(voice_q, 2)),
                    "spans": [[0.0, 60.0]]}],
        "speech_face_links": [_link("voice_001")]})

    roster = person_entity.resolve_person_tracks(str(project))

    on_camera_a = [p for p in roster["persons"]
                   if any(t["content_digest"] == digest_a
                          for t in p["face_tracks"])]
    on_camera_b = [p for p in roster["persons"]
                   if any(t["content_digest"] == digest_b
                          for t in p["face_tracks"])]
    assert len(on_camera_a) == 1 and len(on_camera_b) == 1
    person_p, person_q = on_camera_a[0], on_camera_b[0]
    assert person_p["person_id"] != person_q["person_id"]

    # P does not speak on camera A: Q's voice is Q's.
    assert _spans_of(person_p, digest_a) == []
    # Q's voice is Q's, on BOTH angles.
    assert _spans_of(person_q, digest_a) == [(0.0, 21.0)]
    assert _spans_of(person_q, digest_b) == [(0.0, 60.0)]
    # The contradiction is filed, not swallowed.
    assert [row["voice_track"] for row in person_q["voice_reassignments"]] \
        == ["voice_001"]


def test_a_voice_the_measurement_contradicts_is_left_unresolved_not_attached(
        tmp_path, memory_root):
    """THE DEFECT. One source, two diarized voices, one face: the
    co-occurrence says the person on screen spoke both. The measurement
    says the short one is not that person's voice - it matches none of
    their other voices. Forcing it on put another voice in their speech
    history; the fix leaves it unresolved and files the refutation.
    """
    face_p = _embedding(512, 1)
    voice_p = _embedding(192, 7)
    stranger = _embedding(192, 99)
    media_a = _media(tmp_path, "A.MXF", b"source-a")
    media_b = _media(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project(tmp_path, [("clip_001", media_a, digest_a),
                                  ("clip_002", media_b, digest_b)])

    _write_identity(_private_root(project), digest_a, {
        "content_digest": digest_a, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_p),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [{"track_id": "voice_001", "embedding": list(voice_p),
                    "spans": [[0.0, 30.0]]},
                   {"track_id": "voice_002", "embedding": list(stranger),
                    "spans": [[40.0, 42.5]]}],
        "speech_face_links": [_link("voice_001"), _link("voice_002")]})
    _write_identity(_private_root(project), digest_b, {
        "content_digest": digest_b, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_p),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [{"track_id": "voice_001",
                    "embedding": list(_jitter(voice_p, 3)),
                    "spans": [[0.0, 40.0]]}],
        "speech_face_links": [_link("voice_001")]})

    roster = person_entity.resolve_person_tracks(str(project))
    person = next(p for p in roster["persons"]
                  if any(t["content_digest"] == digest_a
                         for t in p["face_tracks"]))

    # The stranger's 2.5 s is nobody's speech.
    assert _spans_of(person, digest_a) == [(0.0, 30.0)]
    assert [row["voice_track"] for row in roster["voice_unresolved"]] \
        == ["voice_002"]
    assert roster["voice_unresolved"][0]["claimed_person"] == person["person_id"]
    assert any(conflict.get("kind") == "within-source-contradiction"
               for conflict in roster["voice_match_conflicts"])


def test_a_face_two_candidates_fit_equally_well_starts_its_own_person():
    """THE DEFECT. A face that fits two candidate people equally well is
    not decidable by what was measured. It used to be merged into the
    nearest one; now it stands alone and says why.

    The geometry is the region the face study did NOT measure (it had
    two people, and they were 0.124 apart at most): two candidates 0.30
    apart, and a face 0.45 from one and 0.42 from the other. Nothing
    separates them, so nothing is merged.
    """
    import numpy as np

    a = np.array([1.0, 0.0, 0.0])
    b = np.array([0.20, 0.9799, 0.0])
    middle = np.array([0.45, 0.3369, 0.8270])

    clustering = person_entity.cluster_face_tracks(
        [_unit(a), _unit(b), _unit(middle)])
    groups = clustering["groups"]
    assert len(groups) == 3
    assert clustering["uncertain"], "the undecidable face was not reported"
    row = clustering["uncertain"][0]
    assert row["reason"] == "ambiguous-between-persons"
    assert row["best"] - row["runner_up"] < person_entity.AMBIGUITY_MARGIN


def test_a_face_that_fits_only_part_of_a_group_is_not_chained_into_it():
    """THE DEFECT. The old clustering ran a CENTROID, so a face that fit
    one member of a group drifted into the group with the other. The
    match is complete-linkage now: every member has to fit.
    """
    base = _embedding(512, 11)
    close = _jitter(base, 12)
    far = _embedding(512, 13)

    clustering = person_entity.cluster_face_tracks([base, close, far])
    groups = clustering["groups"]
    assert [len(group) for group in groups] == [2, 1]
    assert far not in groups[0]


def test_the_roster_is_project_local_and_says_what_it_could_not_measure(
        tmp_path, memory_root):
    """The roster is built from THIS project's catalog, and a source it
    has no identity measurement for is reported rather than dropped."""
    face_p = _embedding(512, 1)
    media_a = _media(tmp_path, "A.MXF", b"source-a")
    media_b = _media(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project(tmp_path, [("clip_001", media_a, digest_a),
                                  ("clip_002", media_b, digest_b)])
    _write_identity(_private_root(project), digest_a, {
        "content_digest": digest_a, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_p),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [], "speech_face_links": []})

    roster = person_entity.resolve_person_tracks(str(project))
    assert roster["identity_scope"] == "project"
    assert [row["content_digest"] for row in
            roster["sources_without_identity_measurement"]] == [digest_b]


def test_the_measurement_reports_its_denominators(tmp_path, memory_root):
    """A measurement without its denominators is a score, not a
    measurement: every count states what it is a count OF."""
    face_p = _embedding(512, 1)
    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    project = _project(tmp_path, [("clip_001", media, digest)])
    _write_identity(_private_root(project), digest, {
        "content_digest": digest, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(face_p),
                   "spans": [{"start": 1.0, "end": 1.2}]}],
        "voices": [{"track_id": "voice_001",
                    "embedding": list(_embedding(192, 7)),
                    "spans": [[0.0, 30.0]]}],
        "speech_face_links": [_link("voice_001")]})

    manifest = {"sources": [{"content_digest": digest, "person": "P",
                             "voices": {"voice_001": "P"}}]}
    report = identity_continuity.run_study(str(project), manifest)

    denominators = report["denominators"]
    assert denominators["roster_persons"] == 1
    assert denominators["labelled_sources"] == 1
    assert denominators["labelled_voice_tracks"] == 1
    resolution = report["same_speaker_resolution"]["P"]
    assert resolution["voice_tracks_on_own_person"] == 1
    assert resolution["voice_tracks_labelled"] == 1
    assert resolution["identity_splits_across_persons"] is False
    assert report["false_merges"]["count"] == 0
    assert report["false_merges"]["denominator_persons"] == 1
