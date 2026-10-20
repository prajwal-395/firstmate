"""Keep-clear (`library/tools/overlay_keep_clear.py`, MU-13): the overlay
sweep reports which placed overlays sit over a person region (M3d) or a
salient box (M3e), read back off the held transforms, and moves nothing.
The M3e lane (`library/tools/attention_saliency.py`) is its saliency
input."""
import json

import numpy as np

from library.tools import attention_saliency, footage_identity, source_memory
from library.tools import person_regions as pr
from library.tools.overlay_verify import sweep_reel_overlays
from library.tools.resolve_transform import pan_tilt_for_centre
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeProject,
    FakeTimeline,
    FakeTimelineItem,
)

FRAME = (1080, 1920)
FPS = 24
GRID = (32, 18)
#: The zoom that makes a 3840x2160 source FILL a 1080x1920 frame: the
#: picture is drawn 3413 px wide, so the frame shows source x
#: 0.342..0.658 and all of source y.
FILL_ZOOM = 1920 / (2160 * 1080 / 3840)
LOWER_THIRD = (904, 480)


def _person_mask():
    """Craig, seated: a body in source x 0.41..0.59, y 0.44..1.0."""
    mask = np.zeros((GRID[1], GRID[0]), dtype=bool)
    mask[8:18, 13:19] = True
    return mask


def _write_memory(root, digest, *, saliency=True):
    """M3d (one region track owned by `face_001`) and M3e (one salient box
    on the head and shoulders, y 0.3..0.55) at three M2 samples,
    t = 10.0 / 10.5 / 11.0."""
    mask = _person_mask()
    runs = pr.encode_runs(mask)
    sdir = source_memory.source_dir(digest, root)
    sidecar = pr.write_sidecar(sdir, [runs, runs, runs], GRID)
    samples = [{"t": t, "instances": [{
        "track": "region_001", **pr.summarize(mask), "mask": i,
        "face": 0, "containment": 0.98, "face_track": "face_001"}]}
        for i, t in enumerate((10.0, 10.5, 11.0))]
    source_memory.write_json(sdir / source_memory.SLOT_REGIONS, {
        "content_digest": digest, "grid": list(GRID), "sidecar": sidecar,
        "tracks": [{"track_id": "region_001", "start": 10.0, "end": 11.0,
                    "samples": 3, "face_track": "face_001",
                    "face_track_fraction": 1.0}],
        "samples": samples,
        "instrument": {"method": pr.M3D_METHOD}})
    source_memory.write_json(sdir / source_memory.SLOT_IDENTITY, {
        "content_digest": digest,
        "faces": [{"track_id": "face_001", "embedding": [1.0, 0.0, 0.0],
                   "spans": [{"start": 10.0, "end": 11.0}]}]})
    if saliency:
        source_memory.write_json(sdir / source_memory.SLOT_SALIENCY, {
            "content_digest": digest,
            "samples": [{"t": t, "boxes": [{"bbox": [0.38, 0.3, 0.62, 0.55],
                                            "confidence": 0.5}]}
                        for t in (10.0, 10.5, 11.0)],
            "instrument": {"method": attention_saliency.M3E_METHOD}})


def _project(tmp_path, monkeypatch, *, catalog=True, saliency=True):
    """A project whose catalog names one camera file, the memory for it,
    and the memory root the sweep reads (the env, cleared of the host's)."""
    project = tmp_path / "proj"
    project.mkdir()
    source = tmp_path / "LCATL0013.MXF"
    source.write_bytes(b"camera A bytes" * 64)
    digest = footage_identity.fingerprint(str(source))["content_digest"]
    clips = ([{"clip_id": "clip_001", "source_file": str(source)}]
             if catalog else [])
    (project / "pipeline_data.json").write_text(json.dumps({
        "capability_outputs": {"footage.catalog": {"clip_catalog": clips}}}))
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    # A project's measurements are private to it, so the roster the
    # keep-clear report reads lives under THIS project's scope.
    _write_memory(source_memory.project_memory_root(str(project)), digest,
                  saliency=saliency)
    return str(project), str(source)


def _reel(source_file, overlay_centre_y):
    """A reel: V1 the camera filling the frame from source t = 10 s, V2 a
    904x480 lower-third canvas placed at `overlay_centre_y` for 3 s."""
    timeline = FakeTimeline(name="Reel 30", frame_rate=FPS)
    pool = FakeMediaPoolItem("LCATL0013.MXF")
    pool.SetClipProperty("File Path", source_file)
    pool.SetClipProperty("FPS", "24")
    picture = FakeTimelineItem("LCATL0013.MXF", timeline, start=0,
                               duration=240, pool_item=pool,
                               source_start_frame=240)
    picture.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": FILL_ZOOM,
                          "ZoomY": FILL_ZOOM}
    timeline.add_item("video", 1, picture)
    pan, tilt = pan_tilt_for_centre(*LOWER_THIRD, *FRAME, 540.0,
                                    overlay_centre_y)
    placement = {"scaling": 1.0, "pan": pan, "tilt": tilt}
    overlay = FakeTimelineItem("lower_third.mov", timeline, start=12,
                               duration=72)
    overlay.properties = {"Scaling": 1.0, "Pan": pan, "Tilt": tilt,
                          "ZoomX": 1.0, "ZoomY": 1.0}
    timeline.add_item("video", 2, overlay)
    entry = {"label": "lower_third at 0.50s", "kind": "lower_third",
             "segment_id": "lt_001", "track_index": 2, "record_frame": 12,
             "canvas_wh": LOWER_THIRD, "placement": placement}
    return timeline, FakeProject(timelines=[timeline], current=timeline), entry


def _sweep(project_folder, timeline, project, entry):
    from library.tools.overlay_verify import judge_reel_overlays
    from library.tools.overlay_verify import read_reel_overlays

    reads = read_reel_overlays(timeline, [entry], resolve_project=project,
                               picture_tracks=[1])
    return judge_reel_overlays(reads, intent={}, full_wh=FRAME,
                               project_folder=project_folder, fps=FPS)


def test_a_lower_third_over_a_person_is_reported_with_the_person_and_fraction(
        tmp_path, monkeypatch):
    """MU-13's acceptance. Would catch: the sweep judging an overlay only
    against its own intent, so a lower third across the speaker's body
    passes every gate - which is all `main` can do (it reads no picture
    and reports no keep-clear at all)."""
    project_folder, source = _project(tmp_path, monkeypatch)
    timeline, project, entry = _reel(source, overlay_centre_y=1540.0)

    report = _sweep(project_folder, timeline, project, entry)

    assert report["passed"], "keep-clear REPORTS; it never fails the sweep"
    keep = report["keep_clear"]
    assert keep["checked"] == 1 and keep["skipped"] == []
    (finding,) = keep["findings"]
    assert finding["label"] == "lower_third at 0.50s"
    person = next(o for o in finding["overlaps"] if o["kind"] == "person")
    assert person["region_track"] == "region_001"
    assert person["face_track"] == "face_001"
    assert person["person_id"] == "person_001"
    # The canvas draws at frame x 88..992, y 1300..1780: source x
    # 0.368..0.632, y 0.677..0.927 through the fill crop. On the 32x18
    # grid that is columns 12..19 and rows 12..16, and the body
    # (columns 13..18, rows 8..17) fills 6 of the 8 columns.
    assert person["fraction_of_overlay"] == 0.75
    assert person["samples_overlapping"] == person["samples_read"] == 2
    # The salient box ends at y 0.55, above the lower third.
    assert all(o["kind"] != "salient" for o in finding["overlaps"])


def test_the_overlay_span_maps_to_the_source_seconds_it_plays_over(
        tmp_path, monkeypatch):
    """Would catch: reading the memory at timeline seconds instead of the
    picture's source seconds - frames 12..84 at 24 fps over a clip
    starting on source frame 240 are source 10.5..13.5 s."""
    project_folder, source = _project(tmp_path, monkeypatch)
    timeline, project, entry = _reel(source, overlay_centre_y=1540.0)

    finding = _sweep(project_folder, timeline, project,
                     entry)["keep_clear"]["findings"][0]

    person = finding["overlaps"][0]
    assert person["source_seconds"] == [10.5, 13.5]
    assert person["sampled_at"] == [10.5, 11.0]


def test_a_title_over_the_face_reports_the_salient_box_too(
        tmp_path, monkeypatch):
    project_folder, source = _project(tmp_path, monkeypatch)
    timeline, project, entry = _reel(source, overlay_centre_y=700.0)

    finding = _sweep(project_folder, timeline, project,
                     entry)["keep_clear"]["findings"][0]

    kinds = {o["kind"] for o in finding["overlaps"]}
    assert kinds == {"person", "salient"}


def test_an_overlay_over_the_background_is_clear(tmp_path, monkeypatch):
    """Would catch: a check that reports every overlay over a picture
    with a person anywhere in it."""
    project_folder, source = _project(tmp_path, monkeypatch)
    timeline, project, entry = _reel(source, overlay_centre_y=300.0)

    keep = _sweep(project_folder, timeline, project, entry)["keep_clear"]

    assert keep["findings"] == []
    assert keep["clear"] == ["lower_third at 0.50s"]


def test_a_source_the_check_cannot_read_is_skipped_not_cleared(
        tmp_path, monkeypatch):
    """Would catch: an unreadable source passing as clear - a gate that
    cannot see an overlay must not read as covering it."""
    project_folder, source = _project(tmp_path, monkeypatch, catalog=False)
    timeline, project, entry = _reel(source, overlay_centre_y=1540.0)

    keep = _sweep(project_folder, timeline, project, entry)["keep_clear"]

    assert keep["checked"] == 0 and keep["clear"] == []
    assert "no content digest" in keep["skipped"][0]["reason"]


def test_a_missing_saliency_slot_is_said_on_the_finding(
        tmp_path, monkeypatch):
    project_folder, source = _project(tmp_path, monkeypatch, saliency=False)
    timeline, project, entry = _reel(source, overlay_centre_y=1540.0)

    finding = _sweep(project_folder, timeline, project,
                     entry)["keep_clear"]["findings"][0]

    assert any("M3e" in note["unread"] for note in finding["notes"])


def test_the_sweep_without_picture_rows_is_unchanged(tmp_path, monkeypatch):
    """The callers that pass no project keep the old report exactly."""
    project_folder, source = _project(tmp_path, monkeypatch)
    timeline, project, entry = _reel(source, overlay_centre_y=1540.0)

    report = sweep_reel_overlays(timeline, [entry], resolve_project=project,
                                 intent={}, full_wh=FRAME)

    assert report["passed"] and "keep_clear" not in report


def test_an_unread_saliency_sample_is_none_not_empty():
    """Would catch: a frame the helper could not read stored as `[]`,
    which reads as "measured, nothing salient"."""
    samples = attention_saliency.assemble(
        [0.0, 0.5, 1.0],
        [{"boxes": []}, {"error": "unreadable"}, None])
    assert samples[0]["boxes"] == []
    assert samples[1]["boxes"] is None and samples[2]["boxes"] is None
