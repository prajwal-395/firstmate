"""D-02: a seed grounds a track only when it names the frame it was
measured on.

The defect these tests name: the detector seed carried a box and
nothing else, and the seeder prompted the tracker on the clip's FIRST
frame whatever frame the box came from. A box measured at 5.5s was
placed on the picture at t=0, the track was born on whatever happened
to sit under it, and the tracker inherited a seed that was never
grounded. Measured on real footage, the seeded track covered 1-3
frames of the clip's start and never reached the labelled frame at
all (docs/benchmarks/2026-10-21-seed-grounding.md).

What each test names:

- a seed that does not say where it was measured is refused: the
  tracker would prompt a frame the box was never measured on;
- a seed whose frame is outside the clip is refused, not clamped;
- a seed measured on a ROTATED rendering of the clip is refused: a
  normalized box cannot be mapped across a rotation, and placing it
  anyway seeds the wrong content;
- a seed measured on a DOWNSCALED copy of the same frame still maps
  (a resized frame keeps its aspect), so the guard does not refuse
  every resized frame;
- the tracker is prompted on the seed's OWN frame, not on frame 0;
- the track that grows from that prompt starts on that frame.

Nothing here loads a model or reads a real project: the frames are
tiny generated JPEGs under `tmp_path` and the predictor is a fake
that records the prompt it was given.
"""

from pathlib import Path

import numpy as np
import pytest

from library.tools.analysis import object_segmentation as seg

SAMPLE_FPS = 2.0
FRAME_COUNT = 24
WIDTH = 64
HEIGHT = 48


def _seed(**overrides):
    """A detector seed that says where its box was measured."""
    seed = {
        "box": [0.25, 0.25, 0.5, 0.5],
        "label": "baseball cap",
        "category": "cap",
        "detector": "owlv2",
        "frame": {"time_seconds": 5.5, "width": WIDTH, "height": HEIGHT},
    }
    seed.update(overrides)
    return seed


# ── The contract: a seed names its frame ──


def test_seed_without_a_frame_is_refused():
    """The defect: the box said nothing about the frame it came from,
    so the tracker was free to prompt any frame - and prompted the
    first one."""
    seed = _seed()
    seed.pop("frame")
    with pytest.raises(TypeError, match="names no frame"):
        seg.check_seed_boxes([seed])


def test_seed_frame_without_a_time_is_refused():
    seed = _seed(frame={"width": WIDTH, "height": HEIGHT})
    with pytest.raises(ValueError, match="time_seconds"):
        seg.check_seed_boxes([seed])


def test_seed_frame_without_a_size_is_refused():
    seed = _seed(frame={"time_seconds": 11.0})
    with pytest.raises(ValueError, match="pixel size"):
        seg.check_seed_boxes([seed])


def test_a_seed_keeps_the_frame_it_was_measured_on():
    seeds = seg.check_seed_boxes([_seed()])
    assert seeds[0].frame_time_seconds == 5.5
    assert seeds[0].frame_width == WIDTH
    assert seeds[0].frame_height == HEIGHT


# ── The placement: the seed's frame maps onto the clip's samples ──


def test_seed_frame_outside_the_clip_is_refused():
    seed = seg.check_seed_boxes([_seed(
        frame={"time_seconds": 99.0, "width": WIDTH, "height": HEIGHT})])[0]
    with pytest.raises(ValueError, match="not in this clip"):
        seg.seed_sample_index(seed, SAMPLE_FPS, FRAME_COUNT)


def test_seed_frame_lands_on_the_nearest_sample():
    seed = seg.check_seed_boxes([_seed(
        frame={"time_seconds": 5.6, "width": WIDTH, "height": HEIGHT})])[0]
    # 5.6s at 2 fps is sample 11.2, and the nearest sample is 11.
    assert seg.seed_sample_index(seed, SAMPLE_FPS, FRAME_COUNT) == 11


def test_seed_measured_on_a_rotated_frame_is_refused():
    """A phone clip carries its rotation as side data, so the same
    clip decodes at two sizes. A box measured on the portrait
    rendering cannot be mapped onto the landscape frames."""
    portrait = seg.check_seed_boxes([_seed(
        frame={"time_seconds": 5.5, "width": 1080, "height": 1920})])[0]
    landscape = seg.check_seed_boxes([_seed(
        frame={"time_seconds": 5.5, "width": 1920, "height": 1080})])[0]
    assert not seg.seed_geometry_matches(portrait, 1920, 1080)
    assert not seg.seed_geometry_matches(landscape, 1080, 1920)


def test_seed_measured_on_a_downscaled_frame_still_maps():
    """A resized frame keeps its aspect, so a seed measured on a
    downscaled copy of the frame still maps onto it."""
    seed = seg.check_seed_boxes([_seed(
        frame={"time_seconds": 11.0, "width": 32, "height": 24})])[0]
    assert seg.seed_geometry_matches(seed, WIDTH, HEIGHT)


# ── The tracker: prompted on the seed's own frame ──


class _FakeLogits:
    """A mask logits array, with the tensor API the segmenter uses."""

    def __init__(self, array):
        self._array = array

    def cpu(self):
        return self

    def numpy(self):
        return self._array


class _FakePredictor:
    """Records the prompt it was given, and tracks a fixed rectangle."""

    device = "cpu"

    def __init__(self):
        self.prompts = []

    def init_state(self, video_path=None):
        return {"video_path": video_path}

    def add_new_points_or_box(self, inference_state=None, frame_idx=0,
                               obj_id=1, box=None):
        self.prompts.append({"frame_idx": frame_idx, "obj_id": obj_id,
                             "box": [float(v) for v in box]})
        return inference_state, [obj_id], _FakeLogits(
            np.zeros((1, HEIGHT, WIDTH), dtype=np.float32))

    def propagate_in_video(self, inference_state):
        # SAM 2 propagates from the frame a target was prompted on: the
        # frames before it carry no mask for that target, and the
        # segmenter skips an empty one.
        first = min((p["frame_idx"] for p in self.prompts), default=0)
        for frame_idx in range(FRAME_COUNT):
            mask = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
            if frame_idx >= first:
                mask[8:16, 16:32] = 1.0
            yield frame_idx, [1], [_FakeLogits(mask[None, ...])]


def _fake_frames(monkeypatch, tmp_path):
    """Teach the segmenter's ffmpeg call to write tiny frames."""
    from PIL import Image

    def _run(argv, *args, **kwargs):
        pattern = Path(argv[-1])
        pattern.parent.mkdir(parents=True, exist_ok=True)
        for index in range(1, FRAME_COUNT + 1):
            Image.new("RGB", (WIDTH, HEIGHT), "white").save(
                pattern.parent / f"{index:05d}.jpg")

    monkeypatch.setattr(seg.subprocess, "run", _run)
    return tmp_path


def _segment_with(monkeypatch, tmp_path, seeds):
    """Run the segmenter over generated frames with a fake predictor."""
    _fake_frames(monkeypatch, tmp_path)
    predictor = _FakePredictor()
    monkeypatch.setattr(seg, "load_model",
                        lambda *_args, **_kwargs: (predictor, None))
    segmenter = seg.ObjectSegmenter()
    segmenter._run_model_scope = object()
    result = segmenter.segment_clip(
        str(tmp_path / "clip.mov"), sample_fps=SAMPLE_FPS, face_box=None,
        require_face=False, seed_boxes=seeds)
    return result, predictor


def test_the_tracker_is_prompted_on_the_seeds_own_frame(
        monkeypatch, tmp_path):
    """The defect-naming test: before the fix the prompt was always
    frame 0, whatever frame the box was measured on."""
    _result, predictor = _segment_with(
        monkeypatch, tmp_path, [_seed()])
    assert len(predictor.prompts) == 1
    assert predictor.prompts[0]["frame_idx"] == 11


def test_a_seed_on_the_first_frame_still_prompts_the_first_frame(
        monkeypatch, tmp_path):
    _result, predictor = _segment_with(
        monkeypatch, tmp_path,
        [_seed(frame={"time_seconds": 0.0, "width": WIDTH,
                      "height": HEIGHT})])
    assert predictor.prompts[0]["frame_idx"] == 0


def test_the_seeded_track_starts_on_its_seed_frame(
        monkeypatch, tmp_path):
    result, _predictor = _segment_with(monkeypatch, tmp_path, [_seed()])
    tracked = [obj for obj in result.objects
               if obj.label == "baseball cap"]
    assert len(tracked) == 1
    assert tracked[0].seed_frame == 11
    assert tracked[0].frames[0] == 11


def test_a_seed_whose_frame_cannot_be_mapped_is_refused(
        monkeypatch, tmp_path):
    """A rotated rendering of the clip is refused with that reason,
    rather than placed on the wrong content."""
    _fake_frames(monkeypatch, tmp_path)
    predictor = _FakePredictor()
    monkeypatch.setattr(seg, "load_model",
                        lambda *_args, **_kwargs: (predictor, None))
    segmenter = seg.ObjectSegmenter()
    segmenter._run_model_scope = object()
    with pytest.raises(ValueError, match="rotated or cropped"):
        segmenter.segment_clip(
            str(tmp_path / "clip.mov"), sample_fps=SAMPLE_FPS,
            face_box=None, require_face=False,
            seed_boxes=[_seed(frame={"time_seconds": 5.5,
                                      "width": 1080, "height": 1920})])
    assert predictor.prompts == []


# ── The step: the content key covers the seed's frame ──


def test_the_content_key_covers_the_seeds_frame():
    from library.steps.step_1_06_object_segmentation import step as s1

    base = _seed()
    assert s1._seed_key([base]) != s1._seed_key(
        [dict(base, frame={"time_seconds": 6.0, "width": WIDTH,
                           "height": HEIGHT})])
    assert s1._seed_key([base]) != s1._seed_key(
        [dict(base, frame={"time_seconds": 5.5, "width": 1080,
                           "height": 1920})])


def test_the_content_key_ignores_a_score_only_change():
    from library.steps.step_1_06_object_segmentation import step as s1

    base = _seed()
    assert s1._seed_key([base]) == s1._seed_key([dict(base, score=0.87)])
