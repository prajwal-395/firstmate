import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
import numpy as np
import tempfile
import subprocess
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model_lifecycle import managed_model

try:
    import torch
    from sam2.build_sam import build_sam2_hf, build_sam2_video_predictor_hf
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
except ImportError:
    # Allow import when running tests without sam2 installed
    torch = None
    build_sam2_hf = None
    build_sam2_video_predictor_hf = None
    SAM2AutomaticMaskGenerator = None


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

@dataclass
class TrackedObject:
    object_id: str
    label: str
    category: str
    frames: List[int]
    masks_rle: Dict[int, str]
    bboxes: Dict[int, Tuple[int, int, int, int]]
    avg_area_ratio: float

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

    def save(self, output_dir: str):
        out_path = Path(output_dir) / f"{Path(self.video_path).stem}_segmentation.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "video_path": self.video_path,
            "frame_count": self.frame_count,
            "resolution": self.resolution,
            "sample_fps": self.sample_fps,
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
            objects=objects
        )


def _resolve_device(name: str) -> "torch.device":
    """The named device if this machine has it, else the CPU.

    Only MPS can be absent, and falling back is the safe direction: the CPU is
    where the generator is trusted anyway."""
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
    
    def segment_clip(self, video_path: str, sample_fps: float = 2.0) -> SegmentationResult:
        """Samples frames from the video, runs auto-mask generation, tracks objects across frames."""
        
        def _load_sam2():
            if build_sam2_video_predictor_hf is None:
                raise ImportError("sam2 is not installed.")
            print(f"Loading {SAM2_MODEL_ID}...", file=sys.stderr)
            predictor = build_sam2_video_predictor_hf(
                SAM2_MODEL_ID, device=_resolve_device(PROPAGATION_DEVICE))
            generator = SAM2AutomaticMaskGenerator(
                build_sam2_hf(SAM2_MODEL_ID,
                              device=_resolve_device(GENERATOR_DEVICE)))
            return predictor, generator

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
            
            with managed_model("sam2", _load_sam2) as (predictor, generator):
                # 1. Run automatic mask generation on the first frame to find objects
                masks = generator.generate(first_frame_img)
                
                masks = sorted(masks, key=lambda x: x["area"],
                               reverse=True)[:MAX_TRACKED_OBJECTS]
                
                # 2. Init video predictor state. Everything that touches the
                # predictor stays INSIDE this block: managed_model moves the
                # model to CPU on exit, and inference_state holds MPS tensors,
                # so propagating after the block is a device mismatch.
                inference_state = predictor.init_state(video_path=str(tmpdir_path))
            
                objects_dict = {}
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
                        avg_area_ratio=0.0
                    )
            
                # 3. Propagate masks across frames
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
            
                # 4. avg_area_ratio is the share of the frame the MASK covers,
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
                    objects=final_objects
                )

def get_segmenter() -> ObjectSegmenter:
    """Return the ObjectSegmenter singleton instance."""
    return ObjectSegmenter()
