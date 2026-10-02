"""The face-identity study measures the frames production decodes.

The first run measured 4K stills while production ran ArcFace on 384 px
M2 thumbnails. The study now reaches production's source decode and
measured output-width cap. Nothing here runs ffmpeg or insightface.
"""

import json

import numpy as np
import pytest

from library.tools import (
    face_identity_study,
    footage_identity,
    person_entity,
    source_memory,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def test_the_study_takes_its_frames_from_productions_decode_path(tmp_path, memory_root,
                                                                  monkeypatch):
    """The study's frames come through `person_entity.frames_at` - the
    path production's faces take - and its report says what it measured
    on, so a study on stills production never decodes cannot pass."""
    media = tmp_path / "A.MXF"
    media.write_bytes(b"a" * 4096)
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root / digest / source_memory.SLOT_SOURCE,
                             {"content_digest": digest, "duration_seconds": 30.0})
    seen = []

    def frames_at(source_file, dig, timestamps, root, scratch):
        seen.append(list(timestamps))
        return [(t, f"{scratch}/f_{t}.jpg", True) for t in timestamps], "decoded-here"

    def measure(frames):
        rng = np.random.default_rng(0)
        return [person_entity.FaceObservation(t, (0, 0, 1, 1), 0.9,
                                              tuple(rng.normal(size=8)))
                for t, _p, _o in frames], [3840, 2160]

    monkeypatch.setattr(person_entity, "frames_at", frames_at)
    monkeypatch.setattr(person_entity, "measure_face_observations", measure)

    out = face_identity_study.measure_source(str(media), "craig", [12.5])

    assert seen == [sorted(set(person_entity.sample_timestamps(30.0)) | {12.5})]
    assert (out["frame_source"], out["frame_pixels"]) == ("decoded-here", [3840, 2160])
    assert len(out["faces"]) == len(seen[0])


def test_one_same_person_pair_below_the_threshold_fails_the_study():
    """The bound is FAR=0 AND FRR=0: a single weak same-person pair - the
    thumbnails' profile frames - fails it, and is named."""
    a = (1.0, 0.0, 0.0)
    faces = [("craig@1", "craig", a), ("craig@2", "craig", (0.95, 0.31, 0.0)),
             ("craig@3", "craig", (0.2, 0.0, 0.98)), ("akshita@1", "akshita", (0.0, 1.0, 0.0))]

    report = face_identity_study.pair_report(faces, threshold=0.25)

    assert not report["passes"]
    assert report["at_threshold"]["false_rejects"] == 2
    assert {key for key, _n in report["frames_in_failing_pairs"]} >= {"craig@3"}
    json.dumps(report)
