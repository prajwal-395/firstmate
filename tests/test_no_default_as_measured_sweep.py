"""The widened default-as-measured sweep (WP1, principle 3).

`tests/test_assessment_reports_no_default_as_measured.py` sweeps exactly
one producer - `compute_deterministic_assessment`. The defect family it
names spans the codebase ("assume another exists until the sweep says
otherwise"), and the 177-site `audit_p3b.py` pass plus its classification
(`docs/WP1_P3B_CLASSIFICATION.md`) proved it: 16 sites where a swallowed
item let an unmeasured value publish as measured.

This file is the widened gate. For every tranche-1 fix it pins the
admitted absence directly against the fixed function, beiden directions
bound (AGENTS.md 10.4):

- it FAILS on the defect: revert any one fix and the matching test goes
  red (a gate that cannot fail is worse than no gate);
- it does NOT fail on legitimate optional swallows: the last two tests
  pin swallows the classification cleared, so a future "fix" that turns
  an admitted absence into a refusal - or a gate that flags every
  `except: continue` - fails here first (a gate that fails correct
  output is no more coverage than one that cannot fail).

Tranche 2 (`docs/WP1_P3B_CLASSIFICATION.md`, eleven sites in the
reel/render/captain-edits subsystems) is pinned the same way below:
one defect-direction test per site, each with the mirror proving the
measured case still reads measured.
"""

import json
import subprocess
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

numpy = pytest.importorskip(
    "numpy", reason="measurement math needs numpy; CI installs it",
)

from library.steps.step_1_04_temporal_index import step as step_1_04
from library.steps.step_3_03_review_rough_cut.step import placed_windows
from library.steps.step_6_01_render.resolve_build_timeline import (
    caption_block_offsets,
)
from library.tools import beat_grid
from library.tools import captain_edits
from library.tools import reel_deliver
from library.tools import reel_look
from library.tools.analysis import music_pipeline
from library.tools.reel_build import (
    OffsetRefused,
    ReelBuildError,
    _link_offset_unions,
    absorb_wordless_remnants,
    sweep_placed_audio,
    verify_cover_clip,
)
from library.tools.render_check import check_captions
from library.tools.subject_framing import subject_center_reading
from library.tools.timeline_layout import A_ROLL

FRAME_SIZE = 160 * 90


def _ffmpeg_result(n_frames, fill=0):
    return subprocess.CompletedProcess(
        args=["ffmpeg"], returncode=0,
        stdout=bytes([fill]) * FRAME_SIZE * n_frames, stderr=b"",
    )


# ── Anchor: step_1_04 optical-flow shift search ───────────────────────


def test_flow_where_no_candidate_survives_is_unknown_not_static(monkeypatch):
    """Every (dx, dy) raising must leave the pair unmeasured.

    Pre-fix this published magnitude 0.0 per pair and classified the
    clip "static" - a default presented as a measurement.

    Only the candidate evaluations fail here: the means the
    classifier reads afterwards keep working, so a fix that merely
    moves the crash (the function's outer handler already converts a
    *later* crash into unknown) does not pass - the zero vectors
    themselves must never publish.
    """
    real_mean = numpy.mean
    calls = {"n": 0}
    # One frame pair (two frames) x 81 shift candidates.
    FAIL_FIRST = 81

    def flaky_mean(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= FAIL_FIRST:
            raise RuntimeError("boom")
        return real_mean(*args, **kwargs)

    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _ffmpeg_result(2))
    monkeypatch.setattr(numpy, "mean", flaky_mean)
    result = step_1_04.compute_optical_flow_direction("clip.mp4")
    assert result["values"] == []
    assert result["dominant_motion"] == "unknown"


def test_genuine_stillness_still_reads_static(monkeypatch):
    """The mirror: identical frames really are still, and still say so.

    Noise, not a flat fill: on constant frames every shift ties at
    zero and the estimator keeps its first candidate, while on
    identical noise only (0, 0) compares equal and wins outright.
    """
    rng = numpy.random.RandomState(7)
    frame = rng.randint(0, 255, size=(90, 160)).astype(numpy.uint8)
    still = numpy.tile(frame, (3, 1, 1))
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=["ffmpeg"], returncode=0, stdout=still.tobytes(),
            stderr=b""),
    )
    result = step_1_04.compute_optical_flow_direction("clip.mp4")
    assert result["dominant_motion"] == "static"
    assert all(v["magnitude"] == 0.0 for v in result["values"])


# ── music_pipeline windowed key / chords ─────────────────────────────


def _stub_essentia(monkeypatch, seconds, sample_rate=44100):
    """An essentia that loads short or long audio and always answers."""
    fake_standard = types.ModuleType("essentia.standard")

    class _Loader:
        def __init__(self, filename, sampleRate=44100):
            pass

        def __call__(self):
            return [0.0] * int(seconds * sample_rate)

    class _Key:
        def __call__(self, chunk):
            return ("C", "major", 0.9)

    fake_standard.MonoLoader = _Loader
    fake_standard.KeyExtractor = _Key
    fake_package = types.ModuleType("essentia")
    fake_package.standard = fake_standard
    monkeypatch.setitem(sys.modules, "essentia", fake_package)
    monkeypatch.setitem(sys.modules, "essentia.standard", fake_standard)


def test_key_consistency_with_no_surviving_windows_is_none(monkeypatch):
    """Zero windowed estimates is not perfect stability.

    Pre-fix this published consistency 1.0 with the extractor's method
    beside it. A 3-second track is shorter than one 5-second window, so
    short audio alone reaches the defect without any extractor failure.
    """
    _stub_essentia(monkeypatch, seconds=3)
    result = music_pipeline.analyze_key("track.wav")
    assert result["key"] == "C"
    assert result["consistency"] is None
    assert result["key_changes"] == []
    assert result["note"] == "no windowed key estimates survived"


def test_key_consistency_measured_stable_still_reads_one(monkeypatch):
    """The mirror: windows that all agree still read 1.0."""
    _stub_essentia(monkeypatch, seconds=12)
    result = music_pipeline.analyze_key("track.wav")
    assert result["consistency"] == 1.0
    assert len(result["key_changes"]) > 0


def test_chord_count_with_no_surviving_windows_is_none(monkeypatch):
    """Zero windowed estimates is not "no chord changes".

    Pre-fix this published chord_count 0 with the windowed method
    beside it.
    """
    _stub_essentia(monkeypatch, seconds=1)
    result = music_pipeline.analyze_chord_progression("track.wav")
    assert result["chord_count"] is None
    assert result["chord_progression"] == []
    assert result["note"] == "no windowed chord estimates survived"


def test_chord_count_measured_still_counts(monkeypatch):
    """The mirror: windows that answer still produce a count."""
    _stub_essentia(monkeypatch, seconds=12)
    result = music_pipeline.analyze_chord_progression("track.wav")
    assert result["chord_count"] is not None
    assert result["chord_count"] > 0


# ── render_check caption probe geometry ──────────────────────────────


def _caption_plan(**overlay):
    base = {"fps": 30.0}
    base.update(overlay)
    return {"subtitle_overlay": {
        "fps": base.pop("fps"),
        "segments": [{
            "overlay_path": "/nonexistent/caption.mov",
            "timeline_start": 0.0,
            "timeline_end": 1.0,
            "source_in_frame": 0,
            **base,
        }],
    }}


def test_unreadable_overlay_fps_is_a_finding_not_thirty():
    """A declared-but-garbled fps must fail closed, not probe at 30."""
    findings = check_captions("render.mp4", _caption_plan(fps="fast"))
    assert any(not f.passed and "fps" in f.message for f in findings)


def test_unreadable_source_offset_is_a_finding_not_zero():
    """A garbled source offset must fail closed, not probe the head."""
    findings = check_captions(
        "render.mp4", _caption_plan(source_in_frame="somewhere"))
    assert any(not f.passed and "offset" in f.message for f in findings)


def test_missing_overlay_file_with_clean_metadata_reads_unreadable():
    """The mirror: valid plans never hit the new findings.

    Requires ffmpeg on PATH (CI installs it): without a decoder the
    probe cannot run at all, and skipping would make this a gate that
    cannot fail in that environment.
    """
    if __import__("shutil").which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH; CI installs it")
    findings = check_captions("render.mp4", _caption_plan())
    assert findings, "a missing overlay must still be reported"
    assert all(not f.passed for f in findings)
    assert all("unreadable" not in f.message or "could not be read" in f.message
               for f in findings), (
        "clean metadata must not route into the corrupt-geometry findings: "
        f"{[f.message for f in findings]}"
    )


# ── Legitimate swallows the gate must not punish ─────────────────────


def test_garbage_beats_are_dropped_and_valid_ones_kept():
    """beat_grid skips non-numeric beats: the grid narrows, nothing is
    invented. A sweep that flagged every `except: continue` would fail
    this correct behaviour."""
    beats = [float(t) for t in range(1, 11)] + ["bogus", None, "1.5x"]
    analysis = {"tempo": {"beats": beats}}
    out = beat_grid.beat_positions(analysis, None, None, None)
    assert out == [float(t) for t in range(1, 11)]


def test_garbage_face_samples_read_as_unmeasurable_not_centred():
    """subject_framing drops bad samples toward unmeasurable-None, which
    the module distinguishes from measured-centred-None. Clearing this
    swallow would collapse that distinction."""
    reading = subject_center_reading(
        {"face_center_x": ["bogus", None, "xx", 0.5, ""],
         "sample_rate_hz": 5},
        0.0, 1.0,
    )
    assert reading.position is None
    assert reading.status == "unmeasurable"


# ── Tranche 2: the eleven reel/render/captain-edits sites ──────────
#
# Same contract as tranche 1: each defect-direction test goes red when
# its fix is reverted, each mirror proves the measured case still reads
# measured.


def _a_roll_row(timeline_start, video_in=0.0, video_out=1.0):
    return {
        "spine_block_position": 1,
        "block_type": "speech",
        "timeline_start": timeline_start,
        "timeline_end": 4.0,
        "video_segments": [{
            "clip_id": "clip_001",
            "source_file": "clip_001.mp4",
            "video_in": video_in,
            "video_out": video_out,
            "frame_rate": 30.0,
        }],
    }


def test_garbled_review_timeline_start_is_unplaced_not_zero():
    """A garbled `timeline_start` must not place the window at 0.0.

    Pre-fix `placed_windows` published base 0.0, so a window whose
    placement was never measured sorted and mapped as the cut's
    opening frame.
    """
    rows = placed_windows(
        {"a_roll_assignments": [_a_roll_row("fast")],
         "b_roll_assignments": [], "b_roll_interjections": []})
    assert len(rows) == 1
    assert rows[0]["timeline_start"] is None


def test_measured_review_timeline_start_still_places():
    """The mirror: a readable start still offsets its segments."""
    rows = placed_windows(
        {"a_roll_assignments": [
            _a_roll_row(2.5, video_in=0.0, video_out=1.0)],
         "b_roll_assignments": [], "b_roll_interjections": []})
    assert [r["timeline_start"] for r in rows] == [2.5]


class _NoStart:
    def GetStart(self):
        raise AttributeError("Resolve will not answer")


def test_unreadable_placed_start_leaves_the_block_out():
    """An unreadable `GetStart` must not publish offset 0 ("aligned").

    Pre-fix the caption map fell back to the planned start, so the
    measured-minus-planned offset read exactly 0. The missing-block
    skip below already fails closed, so leaving the block out routes
    its captions there instead of misplacing them silently.
    """
    placed = _NoStart()
    offsets = caption_block_offsets(
        [{"label": "speech_3", "timeline_in_frame": 100}],
        {"speech_3": placed},
    )
    assert offsets == {}


def test_measured_placed_start_still_offsets():
    """The mirror: a start Resolve answers still yields its offset."""
    placed = SimpleNamespace(GetStart=lambda: 110)
    offsets = caption_block_offsets(
        [{"label": "speech_3", "timeline_in_frame": 100}],
        {"speech_3": placed},
    )
    assert offsets == {3: 10}


def _grade_plan(index=1):
    return {"video_tracks": [{"role": A_ROLL, "occupant": "cam",
                              "index": index}]}


def test_unlistable_grade_row_is_recorded_not_empty():
    """A row Resolve will not list is not an empty row.

    Pre-fix `_footage_picture_items` skipped it silently, and the
    record read "nothing on this row needed grading". The module's own
    rule - everything skipped is RECORDED - names the row instead.
    """
    timeline = MagicMock()
    timeline.GetItemListInTrack.side_effect = RuntimeError("boom")
    record = {"applied": [], "skipped": [], "warnings": []}
    assert list(reel_look._footage_picture_items(
        timeline, _grade_plan(), ("/footage/a.mp4",),
        record, "no CDL")) == []
    assert record["skipped"] == ["V1: unreadable - no CDL"]


def test_listed_non_footage_still_records_per_clip():
    """The mirror: a row that lists still skips per clip, not per row."""
    pool = MagicMock()
    pool.GetClipProperty.side_effect = lambda key: {
        "File Path": "/other/x.mp4", "File Name": "x.mp4"}[key]
    item = MagicMock()
    item.GetMediaPoolItem.return_value = pool
    item.GetName.return_value = "x"
    timeline = MagicMock()
    timeline.GetItemListInTrack.return_value = [item]
    record = {"applied": [], "skipped": [], "warnings": []}
    assert list(reel_look._footage_picture_items(
        timeline, _grade_plan(), ("/footage/a.mp4",),
        record, "no CDL")) == []
    assert record["skipped"] == ["x.mp4: not placed footage - no CDL"]


def _sweep_timeline(items_by_row, fail_rows=()):
    timeline = MagicMock()
    def _listed(media_type, index):
        if index in fail_rows:
            raise RuntimeError("boom")
        return list(items_by_row.get(index, ()))
    timeline.GetItemListInTrack.side_effect = _listed
    return timeline


def _sweep_plan(*indices):
    rows = [SimpleNamespace(index=i) for i in indices]
    return SimpleNamespace(speech_rows=lambda: list(rows))


def _placed_item(uid, channels):
    item = MagicMock()
    item.GetUniqueId.return_value = uid
    item.GetSourceAudioChannelMapping.return_value = json.dumps(
        {"track_mapping": {"1": {"channel_idx": list(channels)}}})
    return item


def test_sweep_skips_a_row_its_before_snapshot_never_saw():
    """A failed BEFORE inventory must not read as empty.

    Pre-fix the row inventoried as `[]`, so every pre-existing item on
    it looked ADDED and the sweep DELETED speech this placement never
    added. Now enforcement skips the unknown row and reports it.
    """
    item = _placed_item("u1", [2])
    timeline = _sweep_timeline({1: [item]})
    kept, deleted, unverified = sweep_placed_audio(
        timeline, _sweep_plan(1), {1: None}, 1, 1, "A1 clip @0")
    assert deleted == []
    assert timeline.DeleteClips.call_count == 0
    assert kept == [item]
    assert any("A1" in u and "before" in u for u in unverified)


def test_sweep_reports_a_row_its_after_inventory_would_not_list():
    """A failed AFTER inventory must not read as none-added.

    Pre-fix the row inventoried as `[]`, so a spill there was never
    seen and "no spill found" published. Now the row reports
    spill-unchecked.
    """
    timeline = _sweep_timeline({}, fail_rows=(1,))
    kept, deleted, unverified = sweep_placed_audio(
        timeline, _sweep_plan(1), {1: set()}, 1, 1, "A1 clip @0")
    assert (kept, deleted) == ([], [])
    assert any("A1" in u and "spill unchecked" in u
               for u in unverified)


def test_sweep_still_deletes_a_stray_it_can_prove():
    """The mirror: a stray on a fully inventoried row is still swept."""
    item = _placed_item("u1", [2])
    timeline = _sweep_timeline({1: [item]})
    kept, deleted, unverified = sweep_placed_audio(
        timeline, _sweep_plan(1), {1: set()}, 1, 1, "A1 clip @0")
    assert kept == []
    assert len(deleted) == 1 and deleted[0]["row"] == 1
    assert unverified == []


def _aroll_plan(*names):
    rows = [SimpleNamespace(index=i + 1, name=n)
            for i, n in enumerate(names)]
    return SimpleNamespace(aroll_rows=lambda: list(rows))


def test_unreadable_census_row_refuses_instead_of_placing():
    """A census row that will not list is not a row with nothing unlinked.

    Pre-fix it contributed no items, so "none unlinked" published and
    the build placed over an unchecked row. Now the row joins
    `unlinked`, and the existing `OffsetRefused` fires.
    """
    timeline = MagicMock()
    timeline.GetItemListInTrack.side_effect = RuntimeError("boom")
    record = {"link_groups": [], "caption_links": [],
              "warnings": ["legacy"]}
    with pytest.raises(OffsetRefused, match="unreadable"):
        _link_offset_unions(timeline, _aroll_plan("V1"), record, [], [],
                            [], set(), ())


def test_clean_census_still_links_nothing_and_passes():
    """The mirror: rows that list empty pass without refusal."""
    timeline = MagicMock()
    timeline.GetItemListInTrack.return_value = []
    record = {"link_groups": [], "caption_links": [],
              "warnings": ["legacy"]}
    assert _link_offset_unions(timeline, _aroll_plan("V1"), record,
                               [], [], [], set(), ()) is None
    assert record["warnings"] == []


def _strike():
    # Kept ranges AFTER the subtraction: one 0.2s remnant where the
    # strike ends at 9.8s, under the 0.5s absorb floor.
    return [(9.8, 10.0)], [(0.0, 9.8, "strike-1")]


def test_malformed_timed_word_blocks_the_absorb():
    """A timed word with no readable span counts as spoken.

    Pre-fix it was skipped, so `spoken` read None and the remnant was
    ABSORBED - deleted - on the evidence of a parse failure. Now the
    existing REFUSING error fires.
    """
    ranges, intervals = _strike()
    transcript = {"segments": [{"words": [
        {"timed": True, "word": "hello"}]}]}
    with pytest.raises(ReelBuildError, match="hello"):
        absorb_wordless_remnants(ranges, intervals, transcript)


def test_silent_remnant_still_absorbs():
    """The mirror: a remnant carrying no timed words still folds."""
    ranges, intervals = _strike()
    transcript = {"segments": [{"words": [
        {"timed": True, "word": "hi", "start": 1.0, "end": 1.5}]}]}
    out, grown = absorb_wordless_remnants(ranges, intervals, transcript)
    assert out == []
    assert grown[0][:2] == (0.0, 10.0)


def _cover_neighbour(source_file):
    return SimpleNamespace(
        track_type="video", source_file=source_file,
        source_in=5.0, source_out=7.0,
        timeline_start=50.0, timeline_end=52.0,
        speaker="Craig")


def _probe_result(stdout):
    return subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0, stdout=stdout, stderr="")


def test_cover_over_an_unparseable_word_refuses():
    """A timed word with no readable span cannot be shown outside the cover.

    Pre-fix it was skipped, so no overlap was found and the cover
    placed over possible speech. Now the cover refuses, like the
    unreadable picture size just below it.
    """
    import os as _os
    import tempfile as _tempfile

    from library.tools import reel_build as _reel_build

    with _tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
        neighbour = _cover_neighbour(_os.path.abspath(tmp.name))
        transcript = {"segments": [{"speaker": "Craig", "words": [
            {"word": "well", "start": "soon"}]}]}
        with patch.object(
                subprocess, "run",
                return_value=_probe_result(
                    "duration=10.0\nwidth=640\nheight=480\n")):
            with pytest.raises(OffsetRefused, match="no readable span"):
                verify_cover_clip(tmp.name, 1.0, 2.0, [neighbour],
                                  transcript, 30.0, require_face=False)


def test_cover_over_silence_still_places():
    """The mirror: a cover whose span carries no speech still places."""
    import os as _os
    import tempfile as _tempfile

    from library.tools import reel_build as _reel_build

    with _tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
        neighbour = _cover_neighbour(_os.path.abspath(tmp.name))
        transcript = {"segments": [{"speaker": "Craig", "words": [
            {"word": "hi", "start": 5.5, "end": 5.9}]}]}
        frames = {"mean_luma": 100.0, "rim_diff": 0.0,
                  "center_diff": 0.0, "face_shift": None,
                  "face_note": ""}
        with patch.object(
                subprocess, "run",
                return_value=_probe_result(
                    "duration=10.0\nwidth=640\nheight=480\n")), \
            patch.object(_reel_build, "_cover_frames_static",
                         return_value=frames):
            clip = verify_cover_clip(tmp.name, 1.0, 2.0, [neighbour],
                                     transcript, 30.0, require_face=False)
    assert (clip.source_in, clip.source_out) == (1.0, 2.0)


def _deliver_pool(timeline_name, subs=(), clips=()):
    """A fake pool: `subs` are (name_or_exc, child_folders, clips)."""
    def _folder(name_or_exc, children, items):
        folder = MagicMock()
        if isinstance(name_or_exc, Exception):
            folder.GetName.side_effect = name_or_exc
        else:
            folder.GetName.return_value = name_or_exc
        folder.GetClipList.return_value = list(items)
        folder.GetSubFolderList.return_value = list(children)
        return folder

    def _item(name, path, fail_path=False):
        item = MagicMock()

        def _prop(key):
            if key == "File Path" and fail_path:
                raise RuntimeError("boom")
            return {"Clip Name": name, "File Path": path}[key]

        item.GetClipProperty.side_effect = _prop
        return item

    reel = _folder(timeline_name, [],
                   [_item(n, p, fail_path=fp) for n, p, fp in clips])
    root = MagicMock()
    root.GetSubFolderList.return_value = [
        _folder(name, children, []) for name, children in subs] + [reel]
    project = MagicMock()
    project.GetMediaPool.return_value.GetRootFolder.return_value = root
    return project


def test_unnameable_bin_is_recorded_not_clean():
    """A bin Resolve will not name cannot match - and neither can its
    subtree, so it is unchecked rather than clean.

    Pre-fix it was skipped, and `overlay_staleness` returning [] read
    "every overlay decodes". Now it is a stale entry, which refuses
    the deliver.
    """
    project = _deliver_pool("Reel 03 - hook",
                            subs=[(RuntimeError("boom"), [])])
    found = reel_deliver.overlay_staleness(project, "Reel 03 - hook")
    assert len(found) == 1
    assert found[0].get("unreadable") is True
    assert "unchecked" in found[0].get("reason", "")
    refusal = reel_deliver._refuse_stale_overlays(found)
    assert "unchecked" in refusal


def test_unreadable_pool_item_is_recorded_not_clean():
    """A pool item whose file path will not read is unchecked, not clean.

    Pre-fix it was skipped with the same "every overlay decodes"
    claim. Now it is a stale entry naming the clip.
    """
    project = _deliver_pool("Reel 03 - hook",
                            clips=[("sub_x.mov", "/exports/sub_x.mov",
                                    True)])
    found = reel_deliver.overlay_staleness(project, "Reel 03 - hook")
    assert len(found) == 1
    assert found[0].get("unreadable") is True
    assert found[0]["clip"] == "sub_x.mov"


def test_fully_readable_overlays_still_deliver_clean(tmp_path):
    """The mirror: readable overlays that agree with disk stay clean."""
    path = str(tmp_path / "sub_ok.mov")
    open(path, "wb").write(b"\x00")
    project = _deliver_pool("Reel 03 - hook",
                            clips=[("sub_ok.mov", path, False)])
    with patch.object(reel_deliver, "_disk_resolution",
                      return_value=(904, 480)), \
        patch.object(reel_deliver, "_pool_resolution",
                     return_value=(904, 480)):
        assert reel_deliver.overlay_staleness(
            project, "Reel 03 - hook") == []


def test_malformed_timed_word_unproves_the_edge():
    """A timed word with no readable span cannot testify about an edge.

    Pre-fix it was skipped, so an opening that word might contain read
    as a clean edge and the redraw APPLIED. Now the edge reads
    unproven - False - and the caller takes its CANNOT APPLY path.
    """
    transcript = {"segments": [{"words": [
        {"timed": True, "word": "well", "start": 1.0, "end": 1.2},
        {"timed": True, "word": "actually"},
    ]}]}
    assert captain_edits._opens_on_word_edge(1.0, transcript) is False


def test_clean_edge_still_opens_and_midword_still_refuses():
    """The mirror: a clean edge lands, a mid-word boundary does not."""
    transcript = {"segments": [{"words": [
        {"timed": True, "word": "well", "start": 1.0, "end": 1.2},
        {"timed": True, "word": "actually", "start": 1.2, "end": 1.8},
    ]}]}
    assert captain_edits._opens_on_word_edge(1.0, transcript) is True
    assert captain_edits._opens_on_word_edge(1.1, transcript) is False
