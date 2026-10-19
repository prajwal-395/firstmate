from __future__ import annotations

import subprocess
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from library.tools import overlay_verify
from library.tools.layout_model import GeometryDescriptor


class _Item:
    def __init__(self, clip_name="asset.png"):
        self.enabled = True
        self.clip_name = clip_name

    def GetMediaPoolItem(self):
        return SimpleNamespace(GetName=lambda: self.clip_name)

    def GetStart(self):
        return 20

    def GetEnd(self):
        return 40

    def SetClipEnabled(self, enabled):
        self.enabled = enabled
        return True

    def GetClipEnabled(self):
        return self.enabled


class _Timeline:
    def __init__(self, item, item_track=3):
        self.item = item
        self.item_track = item_track

    def GetItemListInTrack(self, media_type, track):
        assert media_type == "video"
        assert 1 <= track <= 4
        return [self.item] if track == self.item_track else []

    def GetTrackCount(self, media_type):
        assert media_type == "video"
        return 4

    def GetEndFrame(self):
        return 100


def test_rendered_box_gate_measures_actual_frame_contribution(tmp_path, monkeypatch):
    item = _Item()
    # Resolve compacted an empty planned lane, so the actual caption is
    # on V2 even though the build record named V3.
    timeline = _Timeline(item, item_track=2)
    asset = tmp_path / "asset.png"
    alpha = np.zeros((100, 100, 4), dtype=np.uint8)
    alpha[10:30, 12:42] = 255
    Image.fromarray(alpha).save(asset)

    def render(resolve, project, timeline, frame, output_dir,
               resolution="full"):
        assert frame == 29
        pixels = np.zeros((100, 100, 3), dtype=np.uint8)
        # A separate animated picture element can change between Resolve
        # renders. It must not become part of this item's measured box.
        pixels[:5, :5] = 90
        if item.enabled:
            pixels[54:62, 55:67] = 255
        path = tmp_path / f"{int(item.enabled)}.png"
        Image.fromarray(pixels).save(path)
        return str(path)

    def render_batch(resolve, project, timeline, ranges, output_dir,
                     resolution, name_prefix):
        assert ranges == [(23, 34)]
        assert resolution == (100, 100)
        return SimpleNamespace(segments=[SimpleNamespace(
            success=True, path=f"{name_prefix}.mov", error="")])

    def extract_frames(segment, frames, output_dir):
        path = render(None, None, timeline, frames[0], output_dir)
        return {frames[0]: path}

    monkeypatch.setattr(
        "library.tools.segment_renderer.render_batch", render_batch)
    monkeypatch.setattr(
        "library.tools.segment_renderer.extract_frames", extract_frames)
    monkeypatch.setattr(
        "library.tools.segment_renderer.cleanup_segment", lambda path: None)
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), timeline,
        [{"label": "test", "track_index": 3,
          "timeline_start_frame": 20, "duration_frames": 20,
          "absolute_box": (50, 50, 90, 90),
          "geometry": GeometryDescriptor(
              frame=(100, 100), box=(50, 50, 90, 90), canvas=(100, 100),
              native_projection={"Pan": 40.0, "Tilt": -30.0}).to_dict(),
          "canvas_wh": (100, 100), "overlay_path": str(asset)}],
        str(tmp_path / "evidence"))

    assert result["passed"] is True
    assert result["checked"] == 1
    assert item.enabled is True


def test_rendered_box_gate_catches_wrong_pixels_without_property_readback(
        tmp_path, monkeypatch):
    item = _Item()
    asset = tmp_path / "asset.png"
    alpha = np.zeros((100, 100, 4), dtype=np.uint8)
    alpha[10:30, 12:42] = 255
    Image.fromarray(alpha).save(asset)

    def render(resolve, project, timeline, ranges, output_dir,
               resolution, name_prefix):
        return SimpleNamespace(segments=[SimpleNamespace(
            success=True, path="segment.mov", error="")])

    def extract(segment, frames, output_dir):
        pixels = np.zeros((100, 100, 3), dtype=np.uint8)
        if item.enabled:
            pixels[54:62, 75:87] = 255  # 20 px right of the declared box
        path = tmp_path / f"{int(item.enabled)}.png"
        Image.fromarray(pixels).save(path)
        return {frames[0]: str(path)}

    monkeypatch.setattr("library.tools.segment_renderer.render_batch", render)
    monkeypatch.setattr("library.tools.segment_renderer.extract_frames", extract)
    monkeypatch.setattr(
        "library.tools.segment_renderer.cleanup_segment", lambda path: None)
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(item),
        [{"label": "wrong-but-stored", "track_index": 3,
          "timeline_start_frame": 20, "duration_frames": 20,
          "absolute_box": (50, 50, 90, 90),
          "geometry": GeometryDescriptor(
              frame=(100, 100), box=(50, 50, 90, 90),
              canvas=(100, 100),
              native_projection={"Pan": 100.0, "Tilt": 0.0}).to_dict(),
          "canvas_wh": (100, 100), "overlay_path": str(asset)}],
        str(tmp_path / "evidence"))

    assert result["passed"] is False
    assert result["checked"] == 1
    assert result["findings"][0]["measured"] == (75, 54, 87, 62)
    assert result["findings"][0]["edge_gaps"] == (20, 0, 20, 0)


def test_rendered_box_gate_reports_resolve_render_failure(tmp_path, monkeypatch):
    item = _Item()
    asset = tmp_path / "asset.png"
    alpha = np.zeros((100, 100, 4), dtype=np.uint8)
    alpha[10:30, 12:42] = 255
    Image.fromarray(alpha).save(asset)
    monkeypatch.setattr(
        "library.tools.segment_renderer.render_batch",
        lambda *args, **kwargs: SimpleNamespace(segments=[SimpleNamespace(
            success=False, path="", error="Resolve refused MarkIn 29")]))
    with pytest.raises(RuntimeError, match="Resolve refused MarkIn 29"):
        overlay_verify.verify_rendered_boxes(
            object(), object(), _Timeline(item),
            [{"label": "test", "track_index": 3,
              "timeline_start_frame": 20, "duration_frames": 20,
              "absolute_box": (0, 0, 100, 100),
              "geometry": GeometryDescriptor(
                  frame=(100, 100), box=(0, 0, 100, 100),
                  canvas=(100, 100)).to_dict(),
              "canvas_wh": (100, 100), "overlay_path": str(asset)}],
            str(tmp_path / "evidence"))

    assert item.enabled is True


def test_rendered_box_gate_refuses_descriptor_box_disagreement(tmp_path):
    record = {
        "label": "mismatched", "absolute_box": (50, 50, 90, 90),
        "geometry": GeometryDescriptor(
            frame=(100, 100), box=(49, 50, 90, 90), canvas=(100, 100)
        ).to_dict(),
    }
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(_Item()), [record], str(tmp_path))
    assert result["passed"] is False
    assert "geometry descriptor box differs" in result["findings"][0]["reason"]


def test_rendered_box_gate_retries_transient_frame_extraction(
        tmp_path, monkeypatch):
    item = _Item()
    asset = tmp_path / "asset.png"
    alpha = np.zeros((100, 100, 4), dtype=np.uint8)
    alpha[10:30, 12:42] = 255
    Image.fromarray(alpha).save(asset)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[54:62, 55:67] = 255
    rendered = tmp_path / "rendered.png"
    Image.fromarray(frame).save(rendered)
    blank = tmp_path / "blank.png"
    Image.fromarray(np.zeros_like(frame)).save(blank)
    attempts = []

    monkeypatch.setattr(
        "library.tools.segment_renderer.render_batch",
        lambda *args, **kwargs: SimpleNamespace(segments=[SimpleNamespace(
            success=True, path="segment.mp4", error="")]))

    def extract(segment, frames, output_dir):
        attempts.append(tuple(frames))
        if len(attempts) not in {2, 4}:
            return {}
        return {frames[0]: str(rendered if item.enabled else blank)}

    monkeypatch.setattr(
        "library.tools.segment_renderer.extract_frames", extract)
    monkeypatch.setattr(
        "library.tools.segment_renderer.cleanup_segment", lambda path: None)

    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(item),
        [{"label": "retry", "timeline_start_frame": 20,
          "duration_frames": 20, "absolute_box": (50, 50, 90, 90),
          "geometry": GeometryDescriptor(
              frame=(100, 100), box=(50, 50, 90, 90),
              canvas=(100, 100)).to_dict(),
          "canvas_wh": (100, 100), "overlay_path": str(asset)}],
        str(tmp_path / "evidence"))

    assert result["passed"] is True
    assert len(attempts) == 4


def test_rendered_box_gate_falls_back_to_requested_window_frame(
        tmp_path, monkeypatch):
    item = _Item()
    asset = tmp_path / "asset.png"
    alpha = np.zeros((100, 100, 4), dtype=np.uint8)
    alpha[10:30, 12:42] = 255
    Image.fromarray(alpha).save(asset)
    monkeypatch.setattr(
        "library.tools.segment_renderer.render_batch",
        lambda *args, **kwargs: SimpleNamespace(segments=[SimpleNamespace(
            success=True, path="segment.mp4", error="")]))
    monkeypatch.setattr(
        "library.tools.segment_renderer.extract_frames",
        lambda *args, **kwargs: {})
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    def extract_direct(command, **kwargs):
        pixels = np.zeros((100, 100, 3), dtype=np.uint8)
        if item.enabled:
            pixels[54:62, 55:67] = 255
        Image.fromarray(pixels).save(command[-1])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", extract_direct)
    monkeypatch.setattr(
        "library.tools.segment_renderer.cleanup_segment", lambda path: None)
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(item),
        [{"label": "fallback", "timeline_start_frame": 20,
          "duration_frames": 20, "absolute_box": (50, 50, 90, 90),
          "geometry": GeometryDescriptor(
              frame=(100, 100), box=(50, 50, 90, 90),
              canvas=(100, 100)).to_dict(),
          "canvas_wh": (100, 100), "overlay_path": str(asset)}],
        str(tmp_path / "evidence"))

    assert result["passed"] is True


def test_rendered_box_gate_refuses_missing_expected_box(tmp_path):
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(_Item()),
        [{"label": "unboxed", "track_index": 3,
          "timeline_start_frame": 20, "duration_frames": 20}], str(tmp_path))

    assert result["passed"] is False
    assert result["skipped"][0]["reason"] == "no declared absolute box"


def test_rendered_box_gate_refuses_missing_geometry_descriptor(tmp_path):
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(_Item()),
        [{"label": "legacy", "absolute_box": (0, 0, 100, 100),
          "canvas_wh": (100, 100)}], str(tmp_path))
    assert result["passed"] is False
    assert result["skipped"][0]["reason"] == "geometry descriptor is missing"


def test_rendered_box_gate_refuses_empty_scope(tmp_path):
    result = overlay_verify.verify_rendered_boxes(
        object(), object(), _Timeline(_Item()), [], str(tmp_path))

    assert result["passed"] is False
    assert result["checked"] == 0


def test_rendered_box_gate_accepts_explicitly_declared_empty_scope(tmp_path):
    result = overlay_verify.verify_rendered_boxes(
        None, None, None, [], str(tmp_path), allow_empty=True)

    assert result["passed"] is True
    assert result["detail"] == "build declared no overlay placements"
