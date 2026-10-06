"""A caption that covers the subject's face must not ship.

The defect these tests name: `render_qa.measure_caption_obscuring` detects a
caption overlapping a detected face (render_qa.py:2172) but the finding is
report-only (`CAPTION_QUALITY_GATES = False`) and nothing moves the caption
and re-checks.  An obscuring caption passes every gate.  The correction loop
closes that: it moves the caption by the measured clearance and re-measures
until the render passes or the loop names why it cannot.
"""

from __future__ import annotations

from library.tools.caption_correction import (
    CaptionCorrectionLoop,
    compute_clearance_move,
    correct_caption_obscuring,
)
from library.tools.correction_loop import (
    VERDICT_CANNOT_CORRECT,
    VERDICT_PASS,
)
from library.tools.tight_box import placement_for_box

FRAME_H = 1920.0
# A centred caption card in the lower half of a 1080x1920 delivery frame.
CAPTION = (240.0, 1500.0, 840.0, 1560.0)


def test_overlapping_caption_is_moved_by_the_measured_clearance_and_passes():
    # A face in the lower half that the caption covers.
    face = (390.0, 1400.0, 690.0, 1600.0)

    loop = CaptionCorrectionLoop(CAPTION, [face], FRAME_H)
    result = loop.run()

    assert result.passed
    assert result.verdict == VERDICT_PASS
    # The move is measured, not a constant: the caption top must clear the
    # face bottom (1600) by the margin (8), so it moves down 108px.
    [correction] = result.corrections_applied()
    assert correction == {"dx": 0.0, "dy": 108.0}
    # The corrected box clears the face by exactly the margin.
    assert loop._caption_box[1] == 1608.0
    assert loop._caption_box[3] == 1668.0
    # The loop records the failing measurement and the passing re-measurement.
    assert result.iteration_count == 2
    assert result.iterations[0].outcome == "corrected"
    assert result.iterations[1].outcome == "pass"


def test_clear_caption_passes_on_the_first_measurement_without_a_move():
    # A face in the upper half the caption never touches.
    face = (390.0, 400.0, 690.0, 800.0)

    loop = CaptionCorrectionLoop(CAPTION, [face], FRAME_H)
    result = loop.run()

    assert result.passed
    assert result.verdict == VERDICT_PASS
    assert result.iteration_count == 1
    assert result.corrections_applied() == []
    assert result.iterations[0].outcome == "pass"


def test_a_face_spanning_the_lower_half_names_why_it_cannot_correct():
    # A face from the lower-half limit to the frame bottom: moving the
    # caption down runs off the frame, moving it up enters the upper half.
    face = (390.0, 900.0, 690.0, 1900.0)

    loop = CaptionCorrectionLoop(CAPTION, [face], FRAME_H)
    result = loop.run()

    assert not result.passed
    assert result.verdict == VERDICT_CANNOT_CORRECT
    assert result.iteration_count == 1
    assert result.corrections_applied() == []
    # The reason is named, not looped on.
    assert "frame bottom" in result.detail


def test_the_move_clears_every_face_when_several_are_detected():
    # Two faces straddling the caption: the move must clear the lowest.
    faces = [(390.0, 1400.0, 690.0, 1500.0), (390.0, 1520.0, 690.0, 1620.0)]

    loop = CaptionCorrectionLoop(CAPTION, faces, FRAME_H)
    result = loop.run()

    assert result.passed
    assert result.verdict == VERDICT_PASS
    [correction] = result.corrections_applied()
    # Clears the lowest face bottom (1620) by the margin (8): moves down 128.
    assert correction == {"dx": 0.0, "dy": 128.0}
    assert loop._caption_box[1] == 1628.0


def test_the_smaller_clearance_direction_wins():
    # A face just above the caption: moving up to sit above it is smaller
    # than moving down past it.
    face = (390.0, 1200.0, 690.0, 1480.0)

    dx, dy, blocked = compute_clearance_move(CAPTION, [face], FRAME_H)

    assert blocked is None
    assert dx == 0.0
    # Up: caption bottom (1560) to face top (1200) minus margin (8) = -368.
    # Down: face bottom (1480) + margin (8) - caption top (1500) = -12, but
    # down must be positive to be a down move, so only up is a candidate.
    assert dy == -368.0


def test_the_loop_records_its_iterations_and_verdict():
    face = (390.0, 1400.0, 690.0, 1600.0)

    loop = CaptionCorrectionLoop(CAPTION, [face], FRAME_H)
    result = loop.run()
    record = result.as_dict()

    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    assert record["iteration_count"] == 2
    assert [it["outcome"] for it in record["iterations"]] == [
        "corrected", "pass"]
    assert record["iterations"][0]["correction"] == {"dx": 0.0, "dy": 108.0}
    assert record["iterations"][1]["correction"] is None


class _Seg:
    kind = "subtitle_overlay"

    def __init__(self, segment_id, path, start, end):
        self.segment_id = segment_id
        self.path = path
        self.start = start
        self.end = end


def _obscuring_value(segment, caption_box, face_boxes, frame_h=FRAME_H):
    # The shape production passes: measure_caption_obscuring's
    # RenderQAResult.value, whose obscuring rows are keyed by the
    # overlay file's basename and carry the face detections.
    return {
        "segments": 1,
        "measured": [],
        "obscuring": [{
            "segment": segment,
            "caption_box": list(caption_box),
            "face_boxes": [list(f) for f in face_boxes],
            "overlapping_faces": len(face_boxes),
            "frame_h": frame_h,
        }],
    }


def _probe_vertical(monkeypatch):
    # Ren's reels are vertical (1080x1920). The loop must place for the
    # probed frame, never a shape literal.
    from library.tools import render_qa
    monkeypatch.setattr(
        render_qa, "_probe_video_size", lambda _path: (1080, 1920))


def test_correct_caption_obscuring_closes_the_loop_on_the_obscuring_segment(
        monkeypatch):
    # Two segments: one obscuring (the touch made it so), one clear. The
    # bridge runs the loop only on the obscuring one and returns its
    # corrected box as the placement target. The overlay files do not
    # exist, so no pixel re-measure runs - and the record says so.
    _probe_vertical(monkeypatch)
    obscuring_seg = _Seg("sub_block_10", "/tmp/a.mov", 1.0, 3.0)
    clear_seg = _Seg("sub_block_11", "/tmp/b.mov", 4.0, 6.0)
    face = (390.0, 1400.0, 690.0, 1600.0)

    def detect(seg, _video_path):
        if seg is obscuring_seg:
            return {
                "passed": False,
                "caption_box": list(CAPTION),
                "face_boxes": [list(face)],
                "frame_h": FRAME_H,
                "overlapping_faces": 1,
            }
        return None

    records = correct_caption_obscuring(
        "unused.mov", [obscuring_seg, clear_seg], detect=detect)

    assert len(records) == 1
    [record] = records
    assert record["segment"] == "sub_block_10"
    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    # The corrected box clears the face by the margin.
    assert record["corrected_box"][1] == 1608.0
    assert record["iterations"][0]["correction"] == {"dx": 0.0, "dy": 108.0}
    # No re-render was possible, so this pass is named as geometry -
    # never as a pixel re-measure that never ran.
    assert record["remeasured"] is False
    assert "geometry" in record["detail"]


def test_correct_caption_obscuring_names_the_segment_it_cannot_correct(
        monkeypatch):
    _probe_vertical(monkeypatch)
    stuck_seg = _Seg("sub_block_12", "/tmp/c.mov", 7.0, 9.0)
    # A face spanning the lower half: no vertical move clears it.
    face = (390.0, 900.0, 690.0, 1900.0)

    def detect(seg, _video_path):
        return {
            "passed": False,
            "caption_box": list(CAPTION),
            "face_boxes": [list(face)],
            "frame_h": FRAME_H,
            "overlapping_faces": 1,
        }

    records = correct_caption_obscuring(
        "unused.mov", [stuck_seg], detect=detect)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is False
    assert record["verdict"] == VERDICT_CANNOT_CORRECT
    assert "frame bottom" in record["detail"]
    assert record["corrected_placement"] is None
    assert record["remeasured"] is False


def test_correct_caption_obscuring_writes_placement_where_the_build_reads_it(
        monkeypatch):
    # The defect this names: the corrected placement was computed in-memory
    # but never written to the manifest segment's tight_box.placement, so
    # the build (which refuses an overlay with no tight_box placement -
    # resolve_build_timeline.py) never saw the correction and shipped the
    # obscuring caption. The write_placement callback must land the
    # placement on the manifest segment the build reads.
    _probe_vertical(monkeypatch)
    seg = _Seg("sub_block_13", "/tmp/d.mov", 10.0, 12.0)
    face = (390.0, 1400.0, 690.0, 1600.0)
    manifest = {
        "subtitle_overlay": {
            "segments": [{
                "overlay_path": "/tmp/d.mov",
                "timeline_start": 10.0,
                "timeline_end": 12.0,
            }],
        },
    }

    obscuring_result = _obscuring_value("d.mov", CAPTION, [face])

    def detect(seg, _video_path):
        raise AssertionError("reused detections must reach every segment")

    written = []

    def write_placement(seg, placement):
        written.append((seg, placement))
        for manifest_seg in manifest["subtitle_overlay"]["segments"]:
            if manifest_seg.get("overlay_path") == seg.path:
                manifest_seg.setdefault("tight_box", {})["placement"] = placement

    records = correct_caption_obscuring(
        "unused.mov", [seg], obscuring_result=obscuring_result,
        detect=detect, write_placement=write_placement)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is True
    # The placement was written to the manifest segment the build reads.
    assert len(written) == 1
    [(written_seg, written_placement)] = written
    assert written_seg is seg
    manifest_placement = manifest["subtitle_overlay"]["segments"][0][
        "tight_box"]["placement"]
    assert manifest_placement == written_placement
    assert manifest_placement == record["corrected_placement"]


def test_correct_caption_obscuring_reuses_face_detections_from_measure(
        monkeypatch):
    # The defect this names: the correction loop ran Haar face detection
    # over the same spans measure_caption_obscuring had already measured,
    # doubling the expensive part. When obscuring_result is passed, the
    # detections are reused and detect is never called.
    _probe_vertical(monkeypatch)
    seg = _Seg("sub_block_14", "/tmp/e.mov", 13.0, 15.0)
    face = (390.0, 1400.0, 690.0, 1600.0)
    obscuring_result = _obscuring_value("e.mov", CAPTION, [face])

    def detect(seg, _video_path):
        raise AssertionError("detect must not be called when obscuring_result is passed")

    records = correct_caption_obscuring(
        "unused.mov", [seg], obscuring_result=obscuring_result,
        detect=detect)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is True
    assert record["segment"] == "sub_block_14"


def test_correct_caption_obscuring_uses_probed_frame_size_not_hardcoded(
        monkeypatch):
    # The defect this names: the placement was computed for a hardcoded
    # 1920x1080 canvas. Ren's reels are vertical (1080x1920), so the
    # correction landed on the wrong transform. The placement must equal
    # tight_box.placement_for_box for the PROBED frame size.
    _probe_vertical(monkeypatch)
    seg = _Seg("sub_block_15", "/tmp/f.mov", 16.0, 18.0)
    face = (390.0, 1400.0, 690.0, 1600.0)
    obscuring_result = _obscuring_value("f.mov", CAPTION, [face])

    def detect(seg, _video_path):
        raise AssertionError("detect must not be called when obscuring_result is passed")

    records = correct_caption_obscuring(
        "unused.mov", [seg], obscuring_result=obscuring_result,
        detect=detect)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is True
    # Corrected box (240,1608,840,1668): 600x60 at centre (540,1638).
    expected = placement_for_box(600.0, 60.0, 540.0, 1638.0, 1080, 1920)
    assert record["corrected_placement"] == expected
    assert record["corrected_placement"] != placement_for_box(
        600.0, 60.0, 540.0, 1638.0, 1920, 1080)


def test_correct_caption_obscuring_remeasures_on_rerendered_pixels(
        monkeypatch, tmp_path):
    # The defect this names: the loop's pass verdict came from a box moved
    # in memory, which the gate's pixel measurement never confirmed. The
    # loop must re-render the overlay with the correction applied and run
    # the production measurement chain on those pixels.
    from PIL import Image, ImageDraw

    from library.tools import render_qa

    _probe_vertical(monkeypatch)
    overlay_dir = tmp_path / "seg15"
    overlay_dir.mkdir()
    # A frame-sized overlay (no sidecar, so full-canvas) whose ink is the
    # caption card: drawn [240,1500,839,1559], its alpha bbox is CAPTION.
    frame = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    ImageDraw.Draw(frame).rectangle(
        [240, 1500, 839, 1559], fill=(255, 255, 255, 255))
    frame.save(str(overlay_dir / "frame_00001.png"))

    seg = _Seg("sub_block_15", str(overlay_dir), 16.0, 18.0)
    face = (390.0, 1400.0, 690.0, 1600.0)
    obscuring_result = _obscuring_value("seg15", CAPTION, [face])

    def detect(seg, _video_path):
        raise AssertionError("detect must not be called when obscuring_result is passed")

    records = correct_caption_obscuring(
        "unused.mov", [seg], obscuring_result=obscuring_result,
        detect=detect)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    # A real re-render ran and the verdict is its verdict.
    assert record["remeasured"] is True
    assert render_qa._probe_overlay_ink(record["rerendered_path"]) is not None
    assert record["remeasured_overlapping_faces"] == 0
    # The re-derived ink box is the corrected box, read off pixels.
    assert record["remeasured_box"] == record["corrected_box"]


def test_correct_caption_obscuring_refuses_an_unprobed_frame(monkeypatch):
    # The companion defect: with no measured frame size the loop must not
    # correct against a guessed frame. It refuses, naming the refusal.
    from library.tools import render_qa
    monkeypatch.setattr(
        render_qa, "_probe_video_size", lambda _path: None)
    seg = _Seg("sub_block_16", "/tmp/g.mov", 19.0, 21.0)
    face = (390.0, 1400.0, 690.0, 1600.0)
    obscuring_result = _obscuring_value("g.mov", CAPTION, [face])

    records = correct_caption_obscuring(
        "unused.mov", [seg], obscuring_result=obscuring_result)

    assert len(records) == 1
    [record] = records
    assert record["passed"] is False
    assert record["verdict"] == VERDICT_CANNOT_CORRECT
    assert "probed" in record["detail"]
    assert record["corrected_placement"] is None
