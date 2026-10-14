import json
from dataclasses import dataclass, asdict
from typing import TYPE_CHECKING, List, Dict, Tuple, Optional, Any
from pathlib import Path
import numpy as np
import tempfile
import subprocess
import os
import contextlib
import sys

from library.tools.model_lifecycle import (
    load_model,
    managed_model,
    resident_model,
)

if TYPE_CHECKING:
    import torch

# torch and sam2 are imported where SAM 2 is LOADED, never at module import:
# `subject_grade` reads this module's RLE codec and labels, so a top-level
# import charged step 5.04 compile_manifest - a deterministic step that loads
# no model - 1.7s of torch and torchvision on every launch.


SAM2_MODEL_ID = "facebook/sam2.1-hiera-small"
"""The model, named the way `sam2` itself names it.

`sam2.build_sam.HF_MODEL_ID_TO_FILENAMES` is the package's own table mapping
this id to the config that ships inside the package
(`configs/sam2.1/sam2.1_hiera_s.yaml`) and to the checkpoint file. Pass the id
and let that table answer, rather than spelling either half here: the config is
named for the SIZE (`_s`) while the checkpoint is named for the WORD
(`_small`), and a hand-written `sam2.1_hiera_small.yaml` is neither - it is the
checkpoint stem, and hydra's search root is the package, so it also lacked the
`configs/sam2.1/` prefix. That string is what raised
`MissingConfigException: Cannot find primary config` and stopped this module
ever running. See docs/RULE_EVIDENCE.md#sam-2-1-was-asked-for-a-config-that-does-not-exist
"""

GENERATOR_DEVICE = "cpu"
"""The automatic mask generator runs on the CPU, and that is a measurement.

On MPS the generator returns a fraction of the regions it finds on the CPU -
on 001's clip_001 first frame, 1 mask against 5, and with the confidence
filters removed the MEDIAN predicted IoU over the same 256 point prompts was
0.092 on MPS against 0.333 on the CPU. The maxima agree (0.965 / 0.974), so a
prompt landing squarely on an object still scores; it is the broad sweep that
degrades, and the default `pred_iou_thresh=0.8` / `stability_score_thresh=0.95`
then discard nearly all of it. Nothing raises - the generator returns a short
list and says nothing about it.

It runs ONCE per clip on ONE frame, so the cost of being right is bounded:
15-35s per clip, against propagation which is per frame. See
docs/RULE_EVIDENCE.md#sam-2-1-was-asked-for-a-config-that-does-not-exist
"""

PROPAGATION_DEVICE = "mps"
"""Propagation runs on MPS, and that is the same measurement read the other way.

Handed IDENTICAL seed masks, MPS and CPU propagation agree on every frame where
the track holds - per-object IoU 0.88 to 0.98 over 001's clip_008 - and MPS is
3.8x faster (1.57s against 5.98s per frame). They diverge only on WHICH frame
each gives up on an object, which is past the point the track is usable. Resolved
against availability by `_resolve_device`."""

MAX_TRACKED_OBJECTS = 10
"""How many of the first frame's automatic masks are propagated.

The generator returns every region it can find - a few hundred on real footage,
most of them foliage and texture. Tracking all of them costs propagation time
per object with nothing to show for it, so the largest ones are kept."""

FACE_SEED_LABEL = "face_seeded_subject"
"""Label for the single target seeded from a face box.

Blob-seeded objects are `auto_object_N` / `unknown`; a face-seeded one says
what it is, so a reader of the masks knows the target was named, not found."""

FACE_SEED_CATEGORY = "person"


DETECTOR_SEED_PREFIX = "detector:"
"""Tracked-object `seed` values naming an open-vocabulary detector
(MU-15): `detector:owlv2` means the target was seeded from that
detector's box for a Gemma phrase, not found by the blob generator.
The evidence migration reads this prefix to mint an `ObjectClass`
with taxonomy `open-vocabulary`; anything else keeps the legacy
`sam2_seed_category`."""


def normalize_seed_box(box) -> List[float] | None:
    """A normalized [x1, y1, x2, y2] detector box, or None when it
    names no area. Same clamping contract as `normalize_face_box`: a
    partially overlapping box is clamped (an object at the frame edge
    is still an object); a box covering nothing after clamping is not
    a seed, so SAM 2 is never prompted with a degenerate box."""
    try:
        x1, y1, x2, y2 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x1 = min(max(x1, 0.0), 1.0)
    y1 = min(max(y1, 0.0), 1.0)
    x2 = min(max(x2, 0.0), 1.0)
    y2 = min(max(y2, 0.0), 1.0)
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def normalize_face_box(fx, fy, fw, fh, frame_w, frame_h):
    """A pixel detection rect as a normalized box, clamped to the frame.

    `compute_face_presence` measures (x, y, w, h) in its own sample pixels;
    the segmenter Prompts SAM 2 in the extraction frame's pixels. The box
    travels between the two NORMALIZED - [x1, y1, x2, y2], each 0..1 -
    so neither side names the other's size.

    Returns None when there is no box to carry: a non-positive size, a
    non-positive frame, or a rect lying fully outside the frame. A partially
    overlapping rect is clamped, because a face at the frame edge is still a
    face.
    """
    try:
        fx, fy, fw, fh = float(fx), float(fy), float(fw), float(fh)
        frame_w, frame_h = float(frame_w), float(frame_h)
    except (TypeError, ValueError):
        return None
    if fw <= 0 or fh <= 0 or frame_w <= 0 or frame_h <= 0:
        return None
    x1 = min(max(fx / frame_w, 0.0), 1.0)
    y1 = min(max(fy / frame_h, 0.0), 1.0)
    x2 = min(max((fx + fw) / frame_w, 0.0), 1.0)
    y2 = min(max((fy + fh) / frame_h, 0.0), 1.0)
    if x2 <= x1 or y2 <= y1:
        return None
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def first_frame_face_box(face_presence, width, height, sample_index=0):
    """The face box to seed SAM 2 with, in extraction-frame pixels.

    Reads the `face_boxes` track of a `face_presence` block - the normalized
    [x1, y1, x2, y2] per 5 Hz sample that `compute_face_presence` records -
    and scales one sample onto a (width, height) frame, clamped to it.

    Returns None whenever there is no seed: no block, no track, an index past
    the track, None at that sample, or a box covering no pixels after
    clamping. A block that predates `face_boxes` (center and width only) also
    returns None: those two cannot place the box vertically, and inventing a y
    would fabricate the seed. The caller declines honestly instead.
    """
    if not face_presence or width <= 0 or height <= 0:
        return None
    boxes = face_presence.get("face_boxes")
    if not boxes or sample_index < 0 or sample_index >= len(boxes):
        return None
    box = boxes[sample_index]
    if box is None:
        return None
    try:
        x1n, y1n, x2n, y2n = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x1 = min(max(x1n * width, 0.0), float(width))
    y1 = min(max(y1n * height, 0.0), float(height))
    x2 = min(max(x2n * width, 0.0), float(width))
    y2 = min(max(y2n * height, 0.0), float(height))
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


@dataclass
class ObjectSeed:
    """One detector-seeded target (MU-15): the open-vocabulary box a
    SAM 2 track grows from.

    `box` is normalized [x1, y1, x2, y2]; `label` the Gemma phrase the
    detector answered ("black laptop"); `category` the class the
    `ObjectClass` is minted from ("laptop"); `detector` the slate name
    that produced the box ("owlv2"). `score` is the detector's own
    box score, or None where the seeding run recorded none (VI-05):
    a null score is unrecorded evidence, never zero confidence, and
    it rides the content key nowhere - a score-only change re-measures
    nothing because the track never read it. `check_seed_boxes`
    refuses a seed with no area, no label or no class: a track
    without a class mints no `ObjectClass`, which is the whole point
    of seeding it.
    """

    box: List[float]
    label: str
    category: str
    detector: str
    score: Optional[float] = None


def _check_seed_score(value: Any, label: str) -> Optional[float]:
    """A detector score as a 0..1 float, or None where none arrived.

    Refused when present but outside 0..1: a score outside the range
    is not a score, and carrying it would fail evidence validation
    far from where it was coined."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"object seed {label!r} score {value!r} is not a number")
    if not 0.0 <= number <= 1.0:
        raise ValueError(
            f"object seed {label!r} score {number} is outside 0..1: "
            f"not a detector score")
    return number


def check_seed_boxes(seed_boxes) -> List[ObjectSeed]:
    """Detector seeds as clean `ObjectSeed`s. None (or an empty list)
    means no detector seeding; a malformed entry is refused, never
    skipped - a silently dropped seed would read as a detector miss."""
    if not seed_boxes:
        return []
    seeds = []
    for entry in seed_boxes:
        if not isinstance(entry, dict):
            raise ValueError(
                f"an object seed is a dict with box/label/category/"
                f"detector, got {entry!r}")
        box = normalize_seed_box(entry.get("box"))
        if box is None:
            raise ValueError(
                f"object seed {entry.get('label')!r} has no usable box: "
                f"{entry.get('box')!r}")
        label = str(entry.get("label") or "").strip()
        category = str(entry.get("category") or "").strip().lower()
        detector = str(entry.get("detector") or "").strip()
        if not label or not category or not detector:
            raise ValueError(
                f"object seed {entry!r} needs a label, a category and a "
                f"detector; a track without a class mints no ObjectClass")
        seeds.append(ObjectSeed(box=box, label=label, category=category,
                                detector=detector,
                                score=_check_seed_score(
                                    entry.get("score"), label)))
    return seeds


@dataclass
class TrackedObject:
    object_id: str
    label: str
    category: str
    frames: List[int]
    masks_rle: Dict[int, str]
    bboxes: Dict[int, Tuple[int, int, int, int]]
    avg_area_ratio: float
    seed: Optional[str] = None
    """How the target was seeded: "face" for the face-seeded subject,
    "detector:<name>" for an MU-15 open-vocabulary seed, "blob" for the
    legacy automatic regions. Files written before seeds existed carry
    no key and read as None - "unseeded history", not a kind."""
    score: Optional[float] = None
    """The detector's own box score where a detector seed recorded one
    (VI-05); None everywhere else, including every file written before
    scores travelled. Metadata, never track identity: the content key
    does not cover it."""

@dataclass
class MatchCutCandidate:
    frame_a: int
    frame_b: int
    object_id_a: str
    object_id_b: str
    score: float

@dataclass
class SegmentationResult:
    video_path: str
    frame_count: int
    resolution: Tuple[int, int]
    sample_fps: float
    objects: List[TrackedObject]
    seed_note: Optional[str] = None
    """How the tracked targets were chosen, or why there are none.

    A result with no objects after blob seeding is an accident worth
    investigating; a result with no objects because no face was present is
    the honest decline issue #268 asks for. The note tells them apart.
    """

    def save(self, output_dir: str):
        out_path = Path(output_dir) / f"{Path(self.video_path).stem}_segmentation.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "video_path": self.video_path,
            "frame_count": self.frame_count,
            "resolution": self.resolution,
            "sample_fps": self.sample_fps,
            "seed_note": self.seed_note,
            "objects": []
        }
        
        for obj in self.objects:
            obj_dict = asdict(obj)
            data["objects"].append(obj_dict)
            
        with open(out_path, 'w') as f:
            json.dump(data, f, indent=2)
            
    @classmethod
    def load(cls, output_dir: str, video_stem: str) -> "SegmentationResult":
        out_path = Path(output_dir) / f"{video_stem}_segmentation.json"
        with open(out_path, 'r') as f:
            data = json.load(f)
            
        objects = []
        for obj_data in data["objects"]:
            # Convert string keys in dicts back to ints (JSON limitation)
            obj_data["masks_rle"] = {int(k): v for k, v in obj_data["masks_rle"].items()}
            obj_data["bboxes"] = {int(k): tuple(v) for k, v in obj_data["bboxes"].items()}
            objects.append(TrackedObject(**obj_data))
            
        return cls(
            video_path=data["video_path"],
            frame_count=data["frame_count"],
            resolution=tuple(data["resolution"]),
            sample_fps=data["sample_fps"],
            objects=objects,
            # Files written before seed_note existed carry no key; that is
            # "unseeded history", not a decline, so it reads as None.
            seed_note=data.get("seed_note"),
        )


def _resolve_device(name: str) -> "torch.device":
    """The named device if this machine has it, else the CPU.

    Only MPS can be absent, and falling back is the safe direction: the CPU is
    where the generator is trusted anyway."""
    import torch
    if name == "mps" and not torch.backends.mps.is_available():
        return torch.device("cpu")
    return torch.device(name)


def encode_rle(mask: np.ndarray) -> str:
    """Encode binary mask to simple RLE string."""
    flat = mask.flatten()
    flat = np.where(flat > 0, 1, 0)
    padded = np.pad(flat, (1, 1), mode='constant', constant_values=0)
    diffs = np.diff(padded)
    starts = np.where(diffs == 1)[0]
    ends = np.where(diffs == -1)[0]
    lengths = ends - starts
    
    res = [f"{s} {l}" for s, l in zip(starts, lengths)]
    return ",".join(res)

def decode_rle(rle_str: str, shape: Tuple[int, int]) -> np.ndarray:
    """Decode simple RLE string to binary mask."""
    flat = np.zeros(shape[0] * shape[1], dtype=np.uint8)
    if rle_str:
        parts = rle_str.split(",")
        for part in parts:
            s, l = map(int, part.split(" "))
            flat[s:s+l] = 1
    return flat.reshape(shape)

def find_match_cut_candidates(masks_a: SegmentationResult, masks_b: SegmentationResult, method: str = "iou") -> List[MatchCutCandidate]:
    """Compare object silhouettes between two clips to find frames with highest mask overlap."""
    candidates = []
    
    if masks_a.resolution != masks_b.resolution:
        # Require same resolution for direct mask overlap
        return candidates
        
    shape = masks_a.resolution
    
    for obj_a in masks_a.objects:
        for obj_b in masks_b.objects:
            # Only compare if they have substantial frames
            if not obj_a.frames or not obj_b.frames:
                continue
                
            for frame_a in obj_a.frames:
                if frame_a not in obj_a.masks_rle:
                    continue
                mask_a = decode_rle(obj_a.masks_rle[frame_a], shape)
                area_a = mask_a.sum()
                if area_a == 0:
                    continue
                    
                for frame_b in obj_b.frames:
                    if frame_b not in obj_b.masks_rle:
                        continue
                    mask_b = decode_rle(obj_b.masks_rle[frame_b], shape)
                    area_b = mask_b.sum()
                    if area_b == 0:
                        continue
                        
                    intersection = (mask_a & mask_b).sum()
                    union = area_a + area_b - intersection
                    
                    if method == "iou":
                        score = intersection / union if union > 0 else 0
                    else:
                        score = intersection / min(area_a, area_b)
                        
                    if score > 0.5: # Threshold for considering it a match cut
                        candidates.append(MatchCutCandidate(
                            frame_a=frame_a,
                            frame_b=frame_b,
                            object_id_a=obj_a.object_id,
                            object_id_b=obj_b.object_id,
                            score=float(score)
                        ))
                        
    # Sort candidates by score descending
    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates


class ObjectSegmenter:
    """Wrapper for SAM 2 video segmentation model using managed_model lifecycle."""

    def begin_run(self):
        """Start a SAM 2 model session inside its scheduler reservation."""
        if getattr(self, "_run_model_scope", None) is None:
            scope = resident_model("sam2", self._load_sam2)
            scope.__enter__()
            self._run_model_scope = scope

    @property
    def run_active(self):
        return getattr(self, "_run_model_scope", None) is not None

    def end_run(self):
        """Release the run's SAM weights, including after a failed clip."""
        scope = getattr(self, "_run_model_scope", None)
        if scope is not None:
            self._run_model_scope = None
            scope.__exit__(None, None, None)

    @staticmethod
    def _load_sam2():
        try:
            from sam2.build_sam import (build_sam2_hf,
                                        build_sam2_video_predictor_hf)
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
        except ImportError as exc:
            raise ImportError("sam2 is not installed.") from exc
        print(f"Loading {SAM2_MODEL_ID}...", file=sys.stderr)
        predictor = build_sam2_video_predictor_hf(
            SAM2_MODEL_ID, device=_resolve_device(PROPAGATION_DEVICE))
        generator = SAM2AutomaticMaskGenerator(
            build_sam2_hf(SAM2_MODEL_ID,
                          device=_resolve_device(GENERATOR_DEVICE)))
        return predictor, generator
    
    def segment_clip(self, video_path: str, sample_fps: float = 2.0,
                       face_box=None, require_face: bool = False,
                       seed_boxes=None,
                       ) -> SegmentationResult:
        """Samples frames from the video, seeds targets, tracks them across frames.

        `face_box` is the normalized [x1, y1, x2, y2] (each 0..1) of the
        largest face on the FIRST sampled frame - the `face_boxes` track
        `compute_face_presence` records at 5 Hz. When it is given, the clip
        is seeded from that one deliberate target through SAM 2's
        `add_new_points_or_box` and the automatic blob seeding is skipped
        entirely: one tracked object instead of ten, and the seed is the
        subject rather than whichever region happened to be largest (issue
        #268 - on a wide shot that was a car window or a patch of road).

        `seed_boxes` (MU-15) is detector seeding: a list of
        `{box, label, category, detector}` dicts (see `check_seed_boxes`)
        from an open-vocabulary detector answering Gemma's object
        phrases. Each box prompts one more SAM 2 target beside the face
        seed, so a clip carries its subject AND its named objects ("mask
        out the logo" finally has a track). A clip with detector seeds
        but no face box is still segmented - named objects need no face -
        while a clip with neither is declined exactly as before.

        When face seeding is asked for and no box arrives (`require_face`
        with `face_box=None`, or a box covering no pixels after clamping),
        the clip is HONESTLY DECLINED - an empty result naming the absence -
        rather than tracking a blob. The decline returns before ffmpeg or
        the model is touched. Without either flag the legacy blob path runs
        unchanged, so nothing that does not opt in behaves differently.
        """

        def _validate_face_box(box) -> List[float]:
            try:
                vals = [float(v) for v in box]
            except (TypeError, ValueError):
                raise ValueError(
                    f"face_box must be four numbers [x1, y1, x2, y2], "
                    f"got {box!r}")
            if len(vals) != 4:
                raise ValueError(
                    f"face_box must be four numbers [x1, y1, x2, y2], "
                    f"got {box!r}")
            return vals

        def _decline(reason: str) -> SegmentationResult:
            return SegmentationResult(
                video_path=video_path,
                frame_count=0,
                resolution=(0, 0),
                sample_fps=sample_fps,
                objects=[],
                seed_note=f"declined: {reason}",
            )

        if face_box is not None:
            normalized_box = _validate_face_box(face_box)
        elif require_face and not seed_boxes:
            return _decline(
                "face-seeded masking was requested but no face box arrived "
                "for the first frame - no target worth tracking, so no "
                "masks rather than a blob.")
        else:
            normalized_box = None
        detector_seeds = check_seed_boxes(seed_boxes)
        
        # We need to extract frames to a temporary directory for SAM 2 predictor
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            
            # Extract frames at sample_fps
            subprocess.run([
                "ffmpeg", "-y", "-i", video_path, 
                "-vf", f"fps={sample_fps}", 
                "-q:v", "2", 
                str(tmpdir_path / "%05d.jpg")
            ], capture_output=True, check=True)
            
            frame_files = sorted(list(tmpdir_path.glob("*.jpg")))
            if not frame_files:
                raise RuntimeError(f"Failed to extract frames from {video_path}")
                
            import cv2
            first_frame_img = cv2.imread(str(frame_files[0]))
            if first_frame_img is None:
                raise RuntimeError("Failed to read first frame")
                
            first_frame_img = cv2.cvtColor(first_frame_img, cv2.COLOR_BGR2RGB)
            height, width = first_frame_img.shape[:2]
            resolution = (height, width)
            
            if self.run_active:
                models = load_model("sam2", self._load_sam2)
                model_scope = contextlib.nullcontext(models)
            else:
                model_scope = managed_model("sam2", self._load_sam2)
            with model_scope as (predictor, generator):
                # The frame the seeds are read from. Both paths prompt frame
                # 0; only what the prompt IS differs.
                inference_state = predictor.init_state(video_path=str(tmpdir_path))

                seed_note: Optional[str]
                objects_dict = {}
                next_obj_id = 1
                seed_notes = []
                if normalized_box is not None:
                    # The face-seeded path (issue #268). One deliberate
                    # target through add_new_points_or_box; the generator is
                    # never run, so no blob is ever tracked. The box arrives
                    # normalized and SAM 2 takes frame pixels, so it is
                    # scaled onto the extraction frame and clamped to it.
                    x1 = min(max(normalized_box[0] * width, 0.0), float(width))
                    y1 = min(max(normalized_box[1] * height, 0.0), float(height))
                    x2 = min(max(normalized_box[2] * width, 0.0), float(width))
                    y2 = min(max(normalized_box[3] * height, 0.0), float(height))
                    if x2 <= x1 or y2 <= y1:
                        return _decline(
                            f"face box {normalized_box} covers no pixels "
                            f"of a {width}x{height} frame after clamping.")
                    pixel_box = np.array([x1, y1, x2, y2], dtype=np.float32)

                    _, out_obj_ids, out_mask_logits = predictor.add_new_points_or_box(
                        inference_state=inference_state,
                        frame_idx=0,
                        obj_id=1,
                        box=pixel_box,
                    )

                    objects_dict[1] = TrackedObject(
                        object_id="obj_1",
                        label=FACE_SEED_LABEL,
                        category=FACE_SEED_CATEGORY,
                        frames=[],
                        masks_rle={},
                        bboxes={},
                        avg_area_ratio=0.0,
                        seed="face",
                    )
                    next_obj_id = 2
                    seed_notes.append(
                        "face_seeded: one subject target from the "
                        f"first-frame face box {normalized_box}; automatic "
                        "blob seeding skipped (1 tracked object, not 10).")
                elif not detector_seeds:
                    # The legacy path: seed on the largest automatic regions.
                    # 1. Run automatic mask generation on the first frame to find objects
                    masks = generator.generate(first_frame_img)

                    masks = sorted(masks, key=lambda x: x["area"],
                                   reverse=True)[:MAX_TRACKED_OBJECTS]

                    # 2. Everything that touches the predictor stays INSIDE
                    # this block: managed_model moves the model to CPU on
                    # exit, and inference_state holds MPS tensors, so
                    # propagating after the block is a device mismatch.
                    for i, mask_data in enumerate(masks):
                        obj_id = f"obj_{i+1}"
                        seg_mask = mask_data["segmentation"] # boolean numpy array

                        # Add mask to predictor
                        _, out_obj_ids, out_mask_logits = predictor.add_new_mask(
                            inference_state=inference_state,
                            frame_idx=0,
                            obj_id=i+1,
                            mask=seg_mask
                        )

                        objects_dict[i+1] = TrackedObject(
                            object_id=obj_id,
                            label=f"auto_object_{i+1}",
                            category="unknown",
                            frames=[],
                            masks_rle={},
                            bboxes={},
                            avg_area_ratio=0.0,
                            seed="blob",
                        )
                    seed_notes.append(
                        f"blob_seeded: {len(objects_dict)} largest "
                        "first-frame regions; no face box was provided.")

                if detector_seeds:
                    # MU-15: one SAM 2 target per detector box, beside
                    # whatever the face/blob path seeded. The box arrives
                    # normalized and SAM 2 takes frame pixels, so it is
                    # scaled onto the extraction frame and clamped to it;
                    # a seed that covers no pixels after clamping is
                    # refused loudly (a degenerate prompt tracks nothing
                    # and says nothing about it).
                    prompted = []
                    for seed in detector_seeds:
                        x1 = min(max(seed.box[0] * width, 0.0), float(width))
                        y1 = min(max(seed.box[1] * height, 0.0), float(height))
                        x2 = min(max(seed.box[2] * width, 0.0), float(width))
                        y2 = min(max(seed.box[3] * height, 0.0), float(height))
                        if x2 <= x1 or y2 <= y1:
                            raise ValueError(
                                f"object seed {seed.label!r} box {seed.box} "
                                f"covers no pixels of a {width}x{height} "
                                f"frame after clamping.")
                        pixel_box = np.array([x1, y1, x2, y2],
                                             dtype=np.float32)
                        _, out_obj_ids, out_mask_logits = (
                            predictor.add_new_points_or_box(
                                inference_state=inference_state,
                                frame_idx=0,
                                obj_id=next_obj_id,
                                box=pixel_box,
                            )
                        )
                        objects_dict[next_obj_id] = TrackedObject(
                            object_id=f"obj_{next_obj_id}",
                            label=seed.label,
                            category=seed.category,
                            frames=[],
                            masks_rle={},
                            bboxes={},
                            avg_area_ratio=0.0,
                            seed=f"{DETECTOR_SEED_PREFIX}{seed.detector}",
                            score=seed.score,
                        )
                        prompted.append(
                            f"{seed.label!r} ({seed.detector})")
                        next_obj_id += 1
                    seed_notes.append(
                        "detector_seeded: "
                        f"{len(prompted)} object target(s) "
                        f"{', '.join(prompted)}; each mints an ObjectClass "
                        "from its category on migration.")
                seed_note = "; ".join(seed_notes) if seed_notes else None
            
                # Propagate the seeds across frames. Seeding above is the only
                # thing that differs between the paths; everything from here
                # stays INSIDE this block: managed_model moves the model to
                # CPU on exit, and inference_state holds MPS tensors, so
                # propagating after the block is a device mismatch.
                mask_areas: Dict[int, List[int]] = {i: [] for i in objects_dict}
                for out_frame_idx, out_obj_ids, out_mask_logits in predictor.propagate_in_video(inference_state):
                    for i, obj_id in enumerate(out_obj_ids):
                        logits = out_mask_logits[i].cpu().numpy().squeeze()
                        # Apply threshold (typically > 0 for logits)
                        binary_mask = (logits > 0.0).astype(np.uint8)
                    
                        mask_area = int(binary_mask.sum())
                        if mask_area == 0:
                            continue
                        
                        # Calculate bbox (x, y, w, h). Width and height count
                        # PIXELS, so a one-pixel-wide mask is 1 and not 0.
                        y_indices, x_indices = np.where(binary_mask > 0)
                        x_min, x_max = x_indices.min(), x_indices.max()
                        y_min, y_max = y_indices.min(), y_indices.max()
                        bbox = (int(x_min), int(y_min),
                                int(x_max - x_min) + 1, int(y_max - y_min) + 1)
                    
                        rle = encode_rle(binary_mask)
                    
                        tr_obj = objects_dict[obj_id]
                        tr_obj.frames.append(out_frame_idx)
                        tr_obj.masks_rle[out_frame_idx] = rle
                        tr_obj.bboxes[out_frame_idx] = bbox
                        mask_areas[obj_id].append(mask_area)
            
                # avg_area_ratio is the share of the frame the MASK covers,
                # not the share its bounding box covers. A person with an arm
                # out has a box several times the area of the silhouette, and
                # this number is read as "how much of the picture is this
                # object".
                final_objects = []
                frame_area = width * height
                for obj_id, tr_obj in objects_dict.items():
                    if not tr_obj.frames:
                        continue
                    areas = mask_areas[obj_id]
                    tr_obj.avg_area_ratio = sum(areas) / (len(areas) * frame_area)
                    final_objects.append(tr_obj)

                return SegmentationResult(
                    video_path=video_path,
                    frame_count=len(frame_files),
                    resolution=resolution,
                    sample_fps=sample_fps,
                    objects=final_objects,
                    seed_note=seed_note,
                )

def get_segmenter() -> ObjectSegmenter:
    """Return the ObjectSegmenter singleton instance."""
    return ObjectSegmenter()
