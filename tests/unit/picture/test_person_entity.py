"""The person entity store (M3b): clustering, linking and cross-source
resolution.

Every test builds its project and its memory under `tmp_path` and never
calls insightface, ffmpeg or the ECAPA encoder - the measured parts
(face-identity FAR, diarization DER) are proven in
`data/vep-person-entity-store/eval/results.md` and PR #1482; these tests
guard the LOGIC this task adds on top of those measurements: clustering,
the speech-face join, and cross-source person resolution.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import footage_identity, person_entity, source_memory


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


def _project_with_clips(tmp_path: Path, clips: list, extra_source: dict = None) -> Path:
    """`clips`: `[(clip_id, media_path, digest)]`."""
    root = tmp_path / "proj"
    catalog = [{"clip_id": cid, "filename": media.name,
               "source_file": str(media), "path": str(media),
               "duration_seconds": 10.0} for cid, media, _ in clips]
    fingerprints = {cid: {"path": str(media), "size_bytes": media.stat().st_size,
                         "content_digest": digest}
                   for cid, media, digest in clips}
    data = {"step_outputs": {"catalog": {"clip_catalog": catalog}},
           "source_fingerprints": fingerprints}
    (root).mkdir(parents=True, exist_ok=True)
    (root / "pipeline_data.json").write_text(json.dumps(data), encoding="utf-8")
    if extra_source is not None:
        (root / "project.yaml").write_text(
            "source:\n" + "\n".join(f"  {k}: {v}" for k, v in extra_source.items()),
            encoding="utf-8")
    return root


# ── sample_timestamps ─────────────────────────────────────────────────


def test_sample_timestamps_stay_inside_the_source_and_the_cap():
    """A source too short for a safe margin gets no samples, never a
    span that claims presence at a timestamp past the file's own
    length."""
    assert person_entity.sample_timestamps(0.5) == []
    assert person_entity.sample_timestamps(0.0) == []
    # A long source is capped at max_samples, inside its own length.
    out = person_entity.sample_timestamps(10000.0, interval_s=10.0,
                                          max_samples=20)
    assert len(out) <= 20
    assert out[0] > 0.0
    assert out[-1] < 10000.0


def test_extract_frame_scales_before_writing_the_jpeg(tmp_path, monkeypatch):
    output = tmp_path / "frame.jpg"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        Path(command[command.index("-q:v") + 2]).write_bytes(b"jpeg")
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(person_entity.subprocess, "run", fake_run)

    assert person_entity.extract_frame("source.MXF", 5.0, str(output))
    command = captured["command"]
    assert command[command.index("-vf") + 1] == (
        f"scale='min({person_entity.FACE_INPUT_MAX_WIDTH},iw)':-2")
    assert output.read_bytes() == b"jpeg"


# ── resolve_sample_frames (M2 reuse vs own decode) ──────────────────────


def test_a_fresh_m2_sample_gives_its_times_never_its_thumbnails(tmp_path, memory_root,
                                                                monkeypatch):
    """With a fresh M2 the frames are decoded from source at M2's I-frame
    times, then capped to the measured face input width. ArcFace on the
    384 px thumbnails themselves fails the face study; the times still
    have to be M2's so M7 can join M3b to M3."""
    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root / digest / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": media.stat().st_size,
        "status": "sampled", "frame_count": 3,
        "frames": [{"file": "frames/frame_000001.jpg", "t": 0.5},
                  {"file": "frames/frame_000002.jpg", "t": 5.0},
                  {"file": "frames/frame_000003.jpg", "t": 9.9}],
    })
    decoded = []

    def fake_extract(source_file, timestamp, out_path):
        decoded.append((source_file, timestamp))
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", fake_extract)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=4.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_M2_TIMES
    assert sorted(decoded) == [(str(media), 0.5), (str(media), 5.0), (str(media), 9.9)]
    assert [t for t, _path, _owned in frames] == [0.5, 5.0, 9.9]
    assert all(owned and "frames/frame_" not in path for _t, path, owned in frames)


def test_resolve_sample_frames_falls_back_when_m2_is_stale_or_absent(tmp_path, memory_root,
                                                            monkeypatch):
    """A stale M2 record (size/digest disagree with the live file) must
    never be served - the fallback path (own decode) must be taken
    instead, the same freshness rule M1 already follows."""
    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root / digest / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": media.stat().st_size + 1,  # stale
        "status": "sampled", "frame_count": 1,
        "frames": [{"file": "frames/frame_000001.jpg", "t": 0.5}],
    })

    calls = []

    def fake_extract(source_file, timestamp, out_path):
        calls.append(timestamp)
        Path(out_path).write_bytes(b"\xff\xd8\xff")  # not a real jpeg, just non-empty
        return True

    monkeypatch.setattr(person_entity, "extract_frame", fake_extract)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=5.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_OWN_DECODE
    assert calls  # ffmpeg extraction was actually invoked
    assert all(owned is True for _t, _path, owned in frames)

    # With no M2 record at all, the same fallback.
    media = _media(tmp_path, "B.MXF", b"source-b")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    monkeypatch.setattr(person_entity, "extract_frame",
                        lambda source_file, timestamp, out_path:
                        Path(out_path).write_bytes(b"x") or True)
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=5.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_OWN_DECODE
    assert frames


# ── frames_at parallelism ───────────────────────────────────────────


def test_frames_at_returns_timestamp_order_whatever_finishes_first(
        tmp_path, memory_root, monkeypatch):
    """Parallel seeks complete out of order - the returned frames must
    still be timestamp-sorted. The defect this guards is completion
    order leaking into `measure_face_observations`, whose cluster spans
    assume time order."""
    import time as _time

    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]

    def slow_first(source_file, timestamp, out_path):
        _time.sleep(max(0.0, 0.05 - timestamp * 0.01))
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", slow_first)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, _source = person_entity.frames_at(
        str(media), digest, [5.0, 1.0, 3.0, 2.0, 4.0], None, str(scratch))

    assert [t for t, _path, _owned in frames] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # One corrupt seek must not fail the lane - the frame is omitted
    # and the surviving frames still arrive sorted.
    def flaky(source_file, timestamp, out_path):
        if timestamp == 3.0:
            return False
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", flaky)
    frames, _source = person_entity.frames_at(
        str(media), digest, [1.0, 2.0, 3.0, 4.0], None, str(scratch))

    assert [t for t, _path, _owned in frames] == [1.0, 2.0, 4.0]

    # No timestamps means no work - in particular no zero-worker pool,
    # which `ThreadPoolExecutor` refuses.
    def boom(source_file, timestamp, out_path):  # pragma: no cover
        raise AssertionError("must not decode")

    monkeypatch.setattr(person_entity, "extract_frame", boom)
    frames, source = person_entity.frames_at(
        str(media), digest, [], None, str(scratch))
    assert frames == []
    assert source == person_entity.FRAME_SOURCE_OWN_DECODE


def test_nearest_m2_times_deduplicates_shared_nearest_frame():
    """Two planned timestamps landing on the same nearest M2 frame must
    contribute it once, not twice - a regression here would double-count
    one frame's face toward the cluster."""
    m2_frames = [{"file": "frames/frame_000001.jpg", "t": 1.0},
                {"file": "frames/frame_000002.jpg", "t": 20.0}]
    assert person_entity._nearest_m2_times(m2_frames, [0.9, 1.1, 19.0]) == [1.0, 20.0]


def test_face_cache_key_tracks_the_sample_plan_and_input_width(monkeypatch):
    args = ("digest", person_entity.FRAME_SOURCE_OWN_DECODE, [1.0, 2.0],
            person_entity.FRAME_SAMPLE_INTERVAL_S, person_entity.MAX_FRAME_SAMPLES)
    original = person_entity._face_cache_key(*args)
    changed_times = person_entity._face_cache_key(
        args[0], args[1], [1.0, 2.5], args[3], args[4])

    monkeypatch.setattr(person_entity, "FACE_INPUT_MAX_WIDTH", 640)
    changed_width = person_entity._face_cache_key(*args)

    assert original != changed_times
    assert original != changed_width


def test_face_cache_rejects_a_record_with_pixels_above_the_width_cap():
    cache_key = person_entity._face_cache_key(
        "digest", person_entity.FRAME_SOURCE_OWN_DECODE, [5.0], 10.0,
        person_entity.MAX_FRAME_SAMPLES)
    identity = {"status": person_entity.STATUS_MEASURED,
                "face_match_threshold": person_entity.FACE_MATCH_THRESHOLD,
                "instrument": {
                    "face_cache_key": cache_key,
                    "sample_count": 1,
                    "face_input_max_width": person_entity.FACE_INPUT_MAX_WIDTH,
                    "face_model_pack": person_entity.shared_environment.INSIGHTFACE_PACK_NAME,
                    "frame_pixels": [3840, 2160],
                }}

    assert not person_entity._cached_face_record(identity, cache_key, 1)


def test_build_source_identity_reuses_a_matching_face_measurement(
        tmp_path, memory_root, monkeypatch):
    from library.tools import source_primitives

    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    m0 = {"video_streams": [{"width": 3840, "height": 2160}],
          "duration_seconds": 10.0,
          "program_track": {"basis": "no-audio-streams"}}
    primitives = {"content_digest": digest, "m0": m0,
                  "program_wav": None}
    monkeypatch.setattr(source_primitives, "ensure",
                        lambda *_args, **_kwargs: primitives)

    timestamps = person_entity.sample_timestamps(10.0)
    cache_key = person_entity._face_cache_key(
        digest, person_entity.FRAME_SOURCE_OWN_DECODE, timestamps,
        person_entity.FRAME_SAMPLE_INTERVAL_S, person_entity.MAX_FRAME_SAMPLES)
    faces = [{"track_id": "face_001", "embedding": [1.0, 0.0],
              "spans": [{"start": 4.9, "end": 5.1,
                         "box": [0.0, 0.0, 1.0, 1.0], "det_score": 0.9}]}]
    source_memory.write_json(source_memory.source_dir(digest, memory_root)
                             / source_memory.SLOT_IDENTITY, {
        "content_digest": digest, "status": person_entity.STATUS_MEASURED,
        "face_match_threshold": person_entity.FACE_MATCH_THRESHOLD,
        "faces": faces, "instrument": {
            "face_cache_key": cache_key,
            "sample_count": len(timestamps),
            "face_input_max_width": person_entity.FACE_INPUT_MAX_WIDTH,
            "face_model_pack": person_entity.shared_environment.INSIGHTFACE_PACK_NAME,
            "frame_source": person_entity.FRAME_SOURCE_OWN_DECODE,
            "frame_pixels": [person_entity.FACE_INPUT_MAX_WIDTH, 288],
        },
    })

    def unexpected(*_args, **_kwargs):
        pytest.fail("a valid face cache must bypass decode and embedding")

    monkeypatch.setattr(person_entity, "resolve_sample_frames", unexpected)
    monkeypatch.setattr(person_entity, "measure_face_observations", unexpected)

    result = person_entity.build_source_identity(str(media), root=memory_root)

    assert result["face_measurement_reused"] is True
    assert result["faces"] == 1
    assert person_entity.read_identity(digest, memory_root)["faces"] == faces


def test_voice_cache_key_tracks_audio_device_parameters_and_model(monkeypatch):
    from library.tools import shared_environment
    from library.tools import single_track_diarization as std

    original = person_entity._voice_cache_key("pcm", 2, "mps")
    changed = {
        "pcm": person_entity._voice_cache_key("other-pcm", 2, "mps"),
        "speakers": person_entity._voice_cache_key("pcm", None, "mps"),
        "device": person_entity._voice_cache_key("pcm", 2, "cpu"),
    }
    monkeypatch.setattr(std, "VAD_DB_BELOW_PEAK", std.VAD_DB_BELOW_PEAK + 1)
    changed["vad"] = person_entity._voice_cache_key("pcm", 2, "mps")
    monkeypatch.undo()
    monkeypatch.setattr(shared_environment, "ECAPA_REVISION", "another")
    changed["model"] = person_entity._voice_cache_key("pcm", 2, "mps")

    assert all(key != original for key in changed.values()), changed
    assert person_entity._voice_cache_key(None, 2, "mps") is None
    assert person_entity._voice_cache_key("pcm", 2, None) is None


def test_build_source_identity_reuses_voices_but_never_a_refusal(
        tmp_path, memory_root, monkeypatch):
    from library.tools import shared_environment, source_primitives
    from library.tools import single_track_diarization as std

    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    m0 = {"video_streams": [{"width": 3840, "height": 2160}],
          "duration_seconds": 10.0, "program_track": {"channel": 1}}
    primitives = {"content_digest": digest, "m0": m0,
                  "program_wav": str(tmp_path / "program.16k.wav"),
                  "program_pcm_sha256": "pcm"}
    monkeypatch.setattr(source_primitives, "ensure",
                        lambda *_args, **_kwargs: primitives)

    def no_insightface():
        raise shared_environment.InsightfaceEnvironmentMissing("absent")

    monkeypatch.setattr(shared_environment, "require_insightface",
                        no_insightface)
    monkeypatch.setattr(std, "embedding_device", lambda: "cpu")
    voices = [{"track_id": "voice_001", "embedding": [1.0, 0.0],
               "spans": [[0.0, 4.0]]}]
    answers = [([], "speechbrain is not installed"), (voices, None)]
    calls = []

    def measure(*args):
        calls.append(args)
        return answers[len(calls) - 1]

    monkeypatch.setattr(person_entity, "measure_voice_tracks", measure)

    refused = person_entity.build_source_identity(str(media), root=memory_root)
    measured = person_entity.build_source_identity(str(media), root=memory_root)
    reused = person_entity.build_source_identity(str(media), root=memory_root)

    assert [refused["voice_measurement_reused"],
            measured["voice_measurement_reused"],
            reused["voice_measurement_reused"]] == [False, False, True]
    assert len(calls) == 2
    record = person_entity.read_identity(digest, memory_root)
    assert record["voices"] == voices
    assert record["instrument"]["voice_device"] == "cpu"


# ── cluster_face_observations ─────────────────────────────────────────


def test_cluster_face_observations_groups_same_person_apart_from_different():
    """Two people's jittered embeddings must land in two tracks, not one -
    the defect this guards is a threshold applied so loosely (or
    inverted) that every face collapses into a single track."""
    base_a = _embedding(512, 1)
    base_b = _embedding(512, 2)
    obs = [person_entity.FaceObservation(t, (0, 0, 10, 10), 0.9,
                                         _jitter(base_a, seed=10 + i))
          for i, t in enumerate([1.0, 2.0, 3.0])]
    obs += [person_entity.FaceObservation(t, (0, 0, 10, 10), 0.9,
                                          _jitter(base_b, seed=20 + i))
           for i, t in enumerate([10.0, 11.0])]

    tracks = person_entity.cluster_face_observations(obs)

    assert len(tracks) == 2
    assert {len(t["spans"]) for t in tracks} == {3, 2}


# ── link_speech_to_face ────────────────────────────────────────────────


def _face_track(track_id: str, spans: list) -> dict:
    return {"track_id": track_id, "embedding": [0.0], "spans": spans}


def test_link_speech_to_face_only_links_overlapping_spans():
    """A face span with no covering voice turn gets no link - the
    defect this guards is a 'nearest turn' fallback that would invent
    an attribution past what the spans actually overlap (the module
    docstring states this is never invented)."""
    faces = [_face_track("face_001", [{"start": 1.0, "end": 1.2}]),
            _face_track("face_002", [{"start": 50.0, "end": 50.2}])]
    voices = [{"track_id": "voice_001", "embedding": [0.0],
              "spans": [[0.5, 3.5]]}]
    links = person_entity.link_speech_to_face(faces, voices)
    assert len(links) == 1
    assert links[0]["face_track"] == "face_001"
    assert links[0]["voice_track"] == "voice_001"
    # No voice tracks at all (ECAPA unreachable, a silent source) gives
    # zero links, never a fabricated one.
    assert person_entity.link_speech_to_face(faces, []) == []


# ── declared_person_names ─────────────────────────────────────────────


def test_declared_person_names_reads_yaml_and_drops_non_string_values(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "source:\n"
        "  person_names:\n"
        "    person_001: Craig\n"
        "    person_002: 7\n",
        encoding="utf-8")
    names = person_entity.declared_person_names(str(project))
    assert names == {"person_001": "Craig"}
    (project / "project.yaml").write_text("name: x\n", encoding="utf-8")
    assert person_entity.declared_person_names(str(project)) == {}


# ── resolve_person_tracks / find_person ────────────────────────────────


def _write_identity(root: Path, digest: str, record: dict):
    source_memory.write_json(root / digest / source_memory.SLOT_IDENTITY,
                             record)


def test_resolve_person_tracks_merges_same_person_across_sources(
        tmp_path, memory_root):
    """The same face embedding appearing in two different source digests
    must resolve to ONE person, with spans from both clips - this is the
    cross-video identity the report asked for."""
    base = _embedding(512, 42)
    media_a = _media(tmp_path, "A.MXF", b"source-a")
    media_b = _media(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media_a, digest_a),
                  ("clip_002", media_b, digest_b)])

    _write_identity(memory_root, digest_a, {
        "content_digest": digest_a, "status": "measured",
        "faces": [{"track_id": "face_001",
                  "embedding": list(_jitter(base, seed=1)),
                  "spans": [{"start": 1.0, "end": 1.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})
    _write_identity(memory_root, digest_b, {
        "content_digest": digest_b, "status": "measured",
        "faces": [{"track_id": "face_001",
                  "embedding": list(_jitter(base, seed=2)),
                  "spans": [{"start": 5.0, "end": 5.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})

    roster = person_entity.resolve_person_tracks(str(project))
    assert len(roster["persons"]) == 1
    person = roster["persons"][0]
    clip_ids = {span["clip_id"] for span in person["face_spans"]}
    assert clip_ids == {"clip_001", "clip_002"}


def test_resolve_person_tracks_never_merges_on_voice_alone(
        tmp_path, memory_root):
    """Two sources whose VOICE embeddings are identical but whose FACE
    embeddings are unrelated must stay two different people - the
    module's stated design decision (voice never merges cross-source
    identity, only face does, because only face has a measured FAR).
    A regression that started matching on voice similarity would merge
    these into one person silently."""
    identical_voice = list(_embedding(192, 99))
    base_a = _embedding(512, 1)
    base_b = _embedding(512, 2)
    media_a = _media(tmp_path, "A.MXF", b"source-a")
    media_b = _media(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media_a, digest_a),
                  ("clip_002", media_b, digest_b)])

    for digest, base in ((digest_a, base_a), (digest_b, base_b)):
        _write_identity(memory_root, digest, {
            "content_digest": digest, "status": "measured",
            "faces": [{"track_id": "face_001", "embedding": list(base),
                      "spans": [{"start": 1.0, "end": 1.2,
                                "box": [0, 0, 1, 1], "det_score": 0.9}]}],
            "voices": [{"track_id": "voice_001",
                       "embedding": identical_voice,
                       "spans": [[0.5, 2.0]]}],
            "speech_face_links": [{"t": 1.1, "face_track": "face_001",
                                   "voice_track": "voice_001",
                                   "basis": "co-occurrence: voice span + "
                                           "face span overlap"}]})

    roster = person_entity.resolve_person_tracks(str(project))
    assert len(roster["persons"]) == 2


def test_find_person_resolves_declared_name_and_reports_none_unmatched(
        tmp_path, memory_root):
    base = _embedding(512, 1)
    media = _media(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media, digest)],
        extra_source={"person_names": "{person_001: Craig}"})

    _write_identity(memory_root, digest, {
        "content_digest": digest, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(base),
                  "spans": [{"start": 1.0, "end": 1.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})

    found = person_entity.find_person(str(project), "craig")
    assert found is not None
    assert found["person_id"] == "person_001"

    assert person_entity.find_person(str(project), "nobody") is None
