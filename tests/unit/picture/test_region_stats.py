"""Region stats (`library/tools/region_stats.py`, MU-11): the colourist
sees the subject against the set around them, not only the whole frame.

The fixture is a 32x18 M2 grid: a grey set (170) and a seated speaker
whose region M3d masks (`region_001`, owned by `face_001`), lit about a
stop and a half under the set, with skin and texture inside. On `main`
step 5.01's context carried only whole-frame numbers (`clip_exposure`),
which average the two together.
"""
import json

import numpy as np
import pytest
from PIL import Image

from library.tools import footage_identity, model_task, region_stats, source_memory
from library.tools import person_regions as pr
from library.tools.context_views import build_view
from library.tools.replay_bench import bench, trees
from library.tools.replay_bench import snapshot as snapshot_mod

GRID = (32, 18)
SET_GREY = (170, 170, 170)
#: Skin under the skin rule (R > G > B, spread 45, luma ~67, R/G 1.5).
SKIN = (90, 60, 45)
TIMES = (10.0, 10.5, 11.0, 40.0)


def _subject_mask():
    mask = np.zeros((GRID[1], GRID[0]), dtype=bool)
    mask[6:18, 10:20] = True
    return mask


def _frame(mask, extra=None):
    """The set, the subject in skin with a fine texture (so the inside
    carries detail the flat set does not), and an optional second
    person `(mask, rgb)`."""
    rgb = np.empty((GRID[1], GRID[0], 3), dtype=np.uint8)
    rgb[:] = SET_GREY
    rows, cols = np.nonzero(mask)
    for r, c in zip(rows, cols):
        bump = 6 if (r + c) % 2 else -6
        rgb[r, c] = [SKIN[0] + bump, SKIN[1] + bump, SKIN[2] + bump]
    if extra is not None:
        rgb[extra[0]] = extra[1]
    return rgb


def _write_memory(root, digest, *, second_person=False):
    """M2 thumbnails (PNG, so the pixels are exact) and M3d at TIMES; the
    t=40 sample is outside the played seconds and shows the subject
    BRIGHT, so a reading that leaks past the played span is visible."""
    sdir = source_memory.source_dir(digest, root)
    (sdir / source_memory.SLOT_FRAMES_DIR).mkdir(parents=True)
    subject = _subject_mask()
    other = np.zeros_like(subject)
    other[4:18, 24:30] = True
    frames, samples, masks = [], [], []
    for i, t in enumerate(TIMES):
        rgb = _frame(subject, (other, (250, 250, 250)) if second_person
                     else None)
        if t == 40.0:
            rgb[subject] = (230, 200, 180)
        name = f"{source_memory.SLOT_FRAMES_DIR}/frame_{i:06d}.png"
        Image.fromarray(rgb).save(sdir / name)
        frames.append({"file": name, "t": t})
        instances = [{"track": "region_001", **pr.summarize(subject),
                      "mask": len(masks), "face": 0, "containment": 0.97,
                      "face_track": "face_001"}]
        masks.append(pr.encode_runs(subject))
        if second_person:
            instances.append({"track": "region_002", **pr.summarize(other),
                              "mask": len(masks), "unassigned":
                              "no_face_inside"})
            masks.append(pr.encode_runs(other))
        samples.append({"t": t, "instances": instances})
    source_memory.write_json(sdir / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "frames": frames,
        "frame_count": len(frames)})
    sidecar = pr.write_sidecar(sdir, masks, GRID)
    tracks = [{"track_id": "region_001", "start": TIMES[0],
               "end": TIMES[-1], "samples": len(TIMES),
               "face_track": "face_001", "face_track_fraction": 1.0}]
    if second_person:
        tracks.append({"track_id": "region_002", "start": TIMES[0],
                       "end": TIMES[-1], "samples": len(TIMES),
                       "face_track": None, "unassigned": "no_face_inside"})
    source_memory.write_json(sdir / source_memory.SLOT_REGIONS, {
        "content_digest": digest, "grid": list(GRID), "sidecar": sidecar,
        "tracks": tracks, "samples": samples,
        "instrument": {"method": pr.M3D_METHOD}})


def _source(tmp_path, monkeypatch, *, second_person=False, memory=True):
    """A camera file outside the project, and the memory root the reading
    uses (the env var, so the host's own memory is never read)."""
    source = tmp_path / "LCATL0013.MXF"
    source.write_bytes(b"camera A bytes" * 64)
    digest = footage_identity.fingerprint(str(source))["content_digest"]
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    if memory:
        _write_memory(root, digest, second_person=second_person)
    return str(source), digest, root


def _clip(source):
    return {"clip_id": "clip_001", "source_file": source}


PLAYED = [[9.5, 11.5]]


def _row(record):
    view = build_view("region_exposure", {"region_stats": [record]})
    return view["region_exposure"]


# ── The reading ─────────────────────────────────────────────────────


def test_the_view_reports_a_subject_darker_than_the_set(tmp_path,
                                                        monkeypatch):
    source, _, _ = _source(tmp_path, monkeypatch)
    record = region_stats.clip_region_stats("", _clip(source), PLAYED)
    view = _row(record)
    [row] = view["rows"]
    assert row["basis_id"] == "clip_001/region_001"
    assert row["owner"] == "face_001"
    assert row["samples"] == 3  # 40.0 s is not played
    assert row["outside_luma_p50"] == pytest.approx(170.0, abs=0.5)
    assert row["subject_luma_p50"] < 75
    assert row["subject_minus_outside_stops"] == pytest.approx(
        np.log2(row["subject_luma_p50"] / row["outside_luma_p50"]), abs=1e-3)
    assert row["subject_minus_outside_stops"] < -1.0
    assert row["subject_skin_rgb"] is not None
    # The textured subject is sharper than the flat set, and the
    # silhouette edge - the strongest edge in the frame - is in neither.
    assert row["subject_sharpness"] > 0
    assert row["outside_sharpness"] == 0.0
    assert "legend" in view and "not_read" not in view


def test_outside_is_the_set_never_another_person(tmp_path, monkeypatch):
    """A brighter second person (an unowned region) is not averaged into
    the speaker's surround."""
    source, _, _ = _source(tmp_path, monkeypatch, second_person=True)
    record = region_stats.clip_region_stats("", _clip(source), PLAYED)
    rows = {r["region_track"]: r for r in _row(record)["rows"]}
    assert rows["region_001"]["outside_luma_p50"] == pytest.approx(
        170.0, abs=0.5)
    assert rows["region_002"]["owner"] == "unassigned (no_face_inside)"
    assert rows["region_002"]["subject_luma_p50"] == pytest.approx(
        250.0, abs=0.5)


def test_a_clip_without_m3d_is_one_line_with_the_reason(tmp_path,
                                                        monkeypatch):
    source, _, _ = _source(tmp_path, monkeypatch, memory=False)
    record = region_stats.clip_region_stats("", _clip(source), PLAYED)
    view = _row(record)
    assert view["rows"] == []
    assert view["not_read"] == [
        ("clip_001: no M3d person regions for this source "
         "(`ren analyze <project> --lanes regions`)")]


def test_a_span_between_samples_is_read_at_the_nearest_and_says_so(
        tmp_path, monkeypatch):
    source, _, _ = _source(tmp_path, monkeypatch)
    record = region_stats.clip_region_stats("", _clip(source),
                                            [[10.1, 10.2]])
    assert record["samples_read"] == 1
    assert record["scope_notes"] == [
        ("10.1-10.2s holds no M2 sample; read at 10s, the one nearest "
         "its middle")]


def test_the_lane_slot_and_the_in_memory_reading_agree(tmp_path,
                                                       monkeypatch):
    source, digest, root = _source(tmp_path, monkeypatch)
    now = region_stats.clip_region_stats("", _clip(source), PLAYED)
    assert now["served_from"] == "measured_now"
    region_stats.build_source_region_stats(source, digest, root)
    slot = region_stats.clip_region_stats("", _clip(source), PLAYED)
    assert slot["served_from"] == "m3f"
    assert slot["regions"] == now["regions"]


def test_region_stats_become_project_model_observations(tmp_path,
                                                        monkeypatch):
    from library.tools.project_model_evidence import migrate_project_evidence

    source, digest, root = _source(tmp_path, monkeypatch)
    region_stats.build_source_region_stats(source, digest, root)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {"footage.catalog": {
            "clip_catalog": [_clip(source)]}}}))
    model, _ = migrate_project_evidence(str(project), memory_root=root)
    stats = [o for o in model["observations"]
             if o["modality"] == "region_stats"]
    assert len(stats) == len(TIMES)
    first = stats[0]["payload"]
    assert first["region_track"] == "region_001"
    assert first["track_id"] == f"track-region-{digest}-region_001"
    assert first["inside"]["luma_p50"] < first["outside"]["luma_p50"]


# ── The prompt and the answer ───────────────────────────────────────


def test_a_bridge_table_a_view_reads_is_not_restored_beside_it():
    inputs = {"region_stats": [{"clip_id": "clip_001", "status": "absent",
                                "absent_because": "no M3d"}],
              "clip_exposure": [{"clip_id": "clip_001"}]}
    projected = model_task.project_context(
        inputs, ["view:region_exposure"],
        bridge_supplied={"region_stats", "clip_exposure"})
    assert "region_stats" not in projected
    assert projected["region_exposure"]["not_read"] == [
        "clip_001: no M3d"]
    # Every other bridge table is still restored by name.
    assert projected["clip_exposure"] == inputs["clip_exposure"]


def _subject_grade(**extra):
    return {"clip_id": "clip_001", "scope": "subject-only",
            "target": {"kind": "person", "role": "speaker"},
            "grade": {"gain": 1.4}, **extra}


def test_a_subject_grade_must_cite_its_measured_row(tmp_path, monkeypatch):
    source, _, _ = _source(tmp_path, monkeypatch)
    stats = [region_stats.clip_region_stats("", _clip(source), PLAYED)]
    for bad in (None, "clip_002/region_001", "clip_001/region_009"):
        with pytest.raises(region_stats.UnsourcedSubjectGrade):
            region_stats.resolve_measured_basis(
                [_subject_grade(measured_basis=bad)], stats)
    [entry] = region_stats.resolve_measured_basis(
        [_subject_grade(measured_basis="clip_001/region_001")], stats)
    assert entry["measured_basis"]["subject_minus_outside_stops"] < -1.0


def test_a_clip_with_no_reading_cites_nothing_and_says_why(tmp_path,
                                                           monkeypatch):
    source, _, _ = _source(tmp_path, monkeypatch, memory=False)
    stats = [region_stats.clip_region_stats("", _clip(source), PLAYED)]
    with pytest.raises(region_stats.UnsourcedSubjectGrade):
        region_stats.resolve_measured_basis(
            [_subject_grade(measured_basis="clip_001/region_001")], stats)
    [entry] = region_stats.resolve_measured_basis([_subject_grade()], stats)
    assert entry["measured_basis"] is None
    assert "no M3d" in entry["measured_basis_absent_because"]


def test_the_spec_records_what_the_subject_grade_was_sourced_from(
        tmp_path, monkeypatch):
    from library.steps.step_5_01_color_grade.post_bridge import (
        resolve_color_grade,
    )

    source, _, _ = _source(tmp_path, monkeypatch)
    stats = [region_stats.clip_region_stats("", _clip(source), PLAYED)]
    spec = resolve_color_grade({
        "a_roll_assignments": [{"timeline_start": 0.0, "video_segments": [
            {"clip_id": "clip_001", "source_file": source,
             "video_in": 9.5, "video_out": 11.5}]}],
        "clip_exposure": [{"clip_id": "clip_001", "luma": 120.0,
                           "luma_method": "ffprobe_signalstats_yavg",
                           "luma_samples": 10}],
        "color_correction": [], "grade_assessment": "speaker sits low",
        "region_stats": stats,
        "subject_grades": [_subject_grade(
            measured_basis="clip_001/region_001")],
    })["color_grade_spec"]
    [kept] = spec["subject_grades"]
    assert kept["measured_basis"]["basis_id"] == "clip_001/region_001"
    assert kept["measured_basis"]["subject_minus_outside_stops"] < -1.0


# ── Acceptance: a replay of 5.01 off frozen state ───────────────────


def _project(tmp_path, source):
    """A project whose state satisfies color_grade's edges: one A-roll
    placement of clip_001, source seconds 9.5-11.5."""
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: region stats fixture\nslug: region-fixture\n",
        encoding="utf-8")
    catalog = [{"clip_id": "clip_001", "filename": "LCATL0013.MXF",
                "source_file": source, "duration_seconds": 60.0,
                "clip_type": "a_roll"}]
    state = {
        "project_folder": str(project),
        "preflight_completed": ["scan", "catalog", "semantic_analysis"],
        "edit_completed": ["creative_direction", "assign_aroll",
                           "select_broll"],
        "step_outputs": {
            "catalog": {"clip_catalog": catalog},
            "semantic_analysis": {"semantic_analysis_documents": [
                {"clip_id": "clip_001", "filename": "LCATL0013.MXF",
                 "analysis": {"scene": "a speaker at a desk, grey set"}}],
                "total_clips_analyzed": 1},
            "creative_direction": {"creative_direction": {
                "mood": "warm", "theme": "conversation"}},
            "assign_aroll": {"a_roll_assignments": [{
                "spine_block_position": 0, "block_type": "speech",
                "timeline_start": 0.0, "timeline_end": 2.0,
                "video_segments": [{"clip_id": "clip_001",
                                    "source_file": source,
                                    "video_in": 9.5, "video_out": 11.5}]}]},
            "select_broll": {"b_roll_assignments": [],
                             "b_roll_interjections": []},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8")
    return project


def test_a_replay_of_color_grade_sources_the_subject_reading(tmp_path,
                                                             monkeypatch):
    """MU-11 acceptance. The colourist's reconstructed context carries the
    subject-against-set reading - and only the reading - and a subject
    grade citing its row resolves to exactly the numbers that context
    shows. On `main` the same replay carries `clip_exposure` and no
    region reading at all."""
    source, _, _ = _source(tmp_path, monkeypatch)
    project = _project(tmp_path, source)
    store = tmp_path / "snapshots"
    snapshot_mod.capture(str(project), snapshot_id="mu11", store=store)
    result = bench.replay("mu11", "color_grade", rev=trees.WORKTREE,
                          store=store)

    keys = set(result["top_level_keys"])
    assert {"clip_exposure", "region_exposure"} <= keys
    assert "region_stats" not in keys
    context = result["context"]
    assert "clip_001/region_001" in context
    assert "measured_basis" in result["expected_schema"]

    stats = [region_stats.clip_region_stats("", _clip(source), PLAYED)]
    [entry] = region_stats.resolve_measured_basis(
        [_subject_grade(measured_basis="clip_001/region_001")], stats)
    basis = entry["measured_basis"]
    assert basis["subject_minus_outside_stops"] < -1.0
    for column in ("subject_luma_p50", "outside_luma_p50",
                   "subject_minus_outside_stops"):
        assert f"{basis[column]:g}" in context, column
