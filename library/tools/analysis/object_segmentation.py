import json
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional, Any
from pathlib import Path
import numpy as np
import tempfile
import subprocess
import os

try:
    import torch
    from sam2.build_sam import build_sam2_video_predictor, build_sam2
    from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
except ImportError:
    # Allow import when running tests without sam2 installed
    torch = None
    build_sam2_video_predictor = None
    build_sam2 = None
    SAM2AutomaticMaskGenerator = None

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
    """Lazy-loaded singleton wrapper for SAM 2 video segmentation model."""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._predictor = None
            cls._instance._generator = None
        return cls._instance
        
    def _ensure_loaded(self):
        if self._predictor is None:
            if build_sam2_video_predictor is None:
                raise ImportError("sam2 is not installed.")
                
            print("Loading sam2.1-hiera-small...")
            self.device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
            
            # Use sam2.1-hiera-small configuration
            model_cfg = "sam2.1_hiera_small.yaml"
            ckpt = "sam2.1_hiera_small.pt"
            
            self._predictor = build_sam2_video_predictor(model_cfg, ckpt, device=self.device)
            self._sam2 = build_sam2(model_cfg, ckpt, device=self.device)
            self._generator = SAM2AutomaticMaskGenerator(self._sam2)
            print("SAM 2 loaded.")
            
    def segment_clip(self, video_path: str, sample_fps: float = 2.0) -> SegmentationResult:
        """Samples frames from the video, runs auto-mask generation, tracks objects across frames."""
        self._ensure_loaded()
        
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
            
            # 1. Run automatic mask generation on the first frame to find objects
            masks = self._generator.generate(first_frame_img)
            
            # Filter masks based on area or just take top N (to avoid tracking hundreds of tiny grains)
            # Sort by area descending, take top 10 for performance
            masks = sorted(masks, key=lambda x: x["area"], reverse=True)[:10]
            
            # 2. Init video predictor state
            # Convert tmpdir path to str, sam2 expects string path to dir with JPEGs
            inference_state = self._predictor.init_state(video_path=str(tmpdir_path))
            
            objects_dict = {}
            for i, mask_data in enumerate(masks):
                obj_id = f"obj_{i+1}"
                seg_mask = mask_data["segmentation"] # boolean numpy array
                
                # Add mask to predictor
                _, out_obj_ids, out_mask_logits = self._predictor.add_new_mask(
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
            for out_frame_idx, out_obj_ids, out_mask_logits in self._predictor.propagate_in_video(inference_state):
                for i, obj_id in enumerate(out_obj_ids):
                    logits = out_mask_logits[i].cpu().numpy().squeeze()
                    # Apply threshold (typically > 0 for logits)
                    binary_mask = (logits > 0.0).astype(np.uint8)
                    
                    if binary_mask.sum() == 0:
                        continue
                        
                    # Calculate bbox (x, y, w, h)
                    y_indices, x_indices = np.where(binary_mask > 0)
                    if len(x_indices) > 0 and len(y_indices) > 0:
                        x_min, x_max = x_indices.min(), x_indices.max()
                        y_min, y_max = y_indices.min(), y_indices.max()
                        bbox = (int(x_min), int(y_min), int(x_max - x_min), int(y_max - y_min))
                        
                        rle = encode_rle(binary_mask)
                        
                        tr_obj = objects_dict[obj_id]
                        tr_obj.frames.append(out_frame_idx)
                        tr_obj.masks_rle[out_frame_idx] = rle
                        tr_obj.bboxes[out_frame_idx] = bbox
            
            # Calculate avg_area_ratio for each object
            final_objects = []
            frame_area = width * height
            for tr_obj in objects_dict.values():
                if not tr_obj.frames:
                    continue
                total_area = sum(tr_obj.bboxes[f][2] * tr_obj.bboxes[f][3] for f in tr_obj.frames)
                tr_obj.avg_area_ratio = total_area / (len(tr_obj.frames) * frame_area)
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
