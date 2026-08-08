import cv2
import easyocr
import numpy as np
from dataclasses import dataclass, asdict
from typing import Tuple, List, Dict, Optional
import json
import math
import os
from collections import defaultdict
from pathlib import Path

@dataclass
class TextDetection:
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    bbox_normalized: Tuple[float, float, float, float]

@dataclass
class TrackedText:
    text: str
    confidence: float
    appearances: List[Tuple[float, float]]
    bbox: Tuple[int, int, int, int]
    bbox_normalized: Tuple[float, float, float, float]
    is_static: bool

@dataclass
class OCRResult:
    video_path: str
    frame_count: int
    resolution: Tuple[int, int]
    sample_fps: float
    detections: List[TrackedText]
    raw_frame_detections: Dict[int, List[TextDetection]]

    def save(self, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "ocr_result.json")
        data = {
            "video_path": self.video_path,
            "frame_count": self.frame_count,
            "resolution": self.resolution,
            "sample_fps": self.sample_fps,
            "detections": [asdict(d) for d in self.detections],
            "raw_frame_detections": {k: [asdict(d) for d in v] for k, v in self.raw_frame_detections.items()}
        }
        with open(out_path, 'w') as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, output_dir: str) -> 'OCRResult':
        in_path = os.path.join(output_dir, "ocr_result.json")
        with open(in_path, 'r') as f:
            data = json.load(f)
        
        detections = [TrackedText(**d) for d in data["detections"]]
        raw_frame_detections = {
            int(k): [TextDetection(**d) for d in v] 
            for k, v in data["raw_frame_detections"].items()
        }
        return cls(
            video_path=data["video_path"],
            frame_count=data["frame_count"],
            resolution=tuple(data["resolution"]),
            sample_fps=data["sample_fps"],
            detections=detections,
            raw_frame_detections=raw_frame_detections
        )

def bbox_iou(bbox1: Tuple[float, float, float, float], bbox2: Tuple[float, float, float, float]) -> float:
    x1, y1, w1, h1 = bbox1
    x2, y2, w2, h2 = bbox2
    
    left = max(x1, x2)
    right = min(x1+w1, x2+w2)
    top = max(y1, y2)
    bottom = min(y1+h1, y2+h2)
    
    if right < left or bottom < top:
        return 0.0
        
    intersection = (right - left) * (bottom - top)
    area1 = w1 * h1
    area2 = w2 * h2
    union = area1 + area2 - intersection
    
    return intersection / union if union > 0 else 0.0

class Track:
    def __init__(self, detection: TextDetection, timestamp: float):
        self.text = detection.text
        self.confidences = [detection.confidence]
        self.bboxes_norm = [detection.bbox_normalized]
        self.bboxes_pixel = [detection.bbox]
        self.appearances = [[timestamp, timestamp]]
        self.last_timestamp = timestamp
        
    def add(self, detection: TextDetection, timestamp: float):
        self.confidences.append(detection.confidence)
        self.bboxes_norm.append(detection.bbox_normalized)
        self.bboxes_pixel.append(detection.bbox)
        
        # If gap <= 2.0 seconds, extend current appearance
        if timestamp - self.appearances[-1][1] <= 2.0:
            self.appearances[-1][1] = timestamp
        else:
            self.appearances.append([timestamp, timestamp])
            
        self.last_timestamp = timestamp
        
    def to_tracked_text(self) -> TrackedText:
        avg_conf = sum(self.confidences) / len(self.confidences)
        
        x = sum(b[0] for b in self.bboxes_pixel) / len(self.bboxes_pixel)
        y = sum(b[1] for b in self.bboxes_pixel) / len(self.bboxes_pixel)
        w = sum(b[2] for b in self.bboxes_pixel) / len(self.bboxes_pixel)
        h = sum(b[3] for b in self.bboxes_pixel) / len(self.bboxes_pixel)
        rep_bbox = (int(x), int(y), int(w), int(h))
        
        nx = sum(b[0] for b in self.bboxes_norm) / len(self.bboxes_norm)
        ny = sum(b[1] for b in self.bboxes_norm) / len(self.bboxes_norm)
        nw = sum(b[2] for b in self.bboxes_norm) / len(self.bboxes_norm)
        nh = sum(b[3] for b in self.bboxes_norm) / len(self.bboxes_norm)
        rep_bbox_norm = (float(nx), float(ny), float(nw), float(nh))
        
        is_static = True
        if len(self.bboxes_norm) > 1:
            var_x = sum((b[0] - nx)**2 for b in self.bboxes_norm) / len(self.bboxes_norm)
            var_y = sum((b[1] - ny)**2 for b in self.bboxes_norm) / len(self.bboxes_norm)
            if var_x > 0.01 or var_y > 0.01:
                is_static = False
                
        return TrackedText(
            text=self.text,
            confidence=float(avg_conf),
            appearances=[(float(a[0]), float(a[1])) for a in self.appearances],
            bbox=rep_bbox,
            bbox_normalized=rep_bbox_norm,
            is_static=is_static
        )

class OCRExtractor:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OCRExtractor, cls).__new__(cls)
            cls._instance.reader = None
        return cls._instance
        
    def _get_reader(self):
        if self.reader is None:
            self.reader = easyocr.Reader(['en'])
        return self.reader
        
    def extract_text_from_frame(self, frame: np.ndarray) -> List[TextDetection]:
        reader = self._get_reader()
        results = reader.readtext(frame)
        
        h, w = frame.shape[:2]
        detections = []
        
        for bbox, text, conf in results:
            x_coords = [pt[0] for pt in bbox]
            y_coords = [pt[1] for pt in bbox]
            
            x_min = min(x_coords)
            y_min = min(y_coords)
            x_max = max(x_coords)
            y_max = max(y_coords)
            
            box_w = x_max - x_min
            box_h = y_max - y_min
            
            pixel_bbox = (int(x_min), int(y_min), int(box_w), int(box_h))
            norm_bbox = (
                float(x_min / w),
                float(y_min / h),
                float(box_w / w),
                float(box_h / h)
            )
            
            detections.append(TextDetection(
                text=text,
                confidence=float(conf),
                bbox=pixel_bbox,
                bbox_normalized=norm_bbox
            ))
            
        return detections

    def extract_text_from_clip(self, video_path: str, sample_fps: float = 1.0, scene_boundaries: Optional[List[float]] = None) -> OCRResult:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"Could not open video {video_path}")
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        duration = total_frames / fps if fps > 0 else 0
        
        frame_interval = max(1, int(fps / sample_fps)) if sample_fps > 0 else int(fps)
        
        target_frames = list(range(0, total_frames, frame_interval))
        
        if scene_boundaries:
            for b in scene_boundaries:
                frame_idx = int(b * fps)
                if frame_idx < total_frames and frame_idx not in target_frames:
                    target_frames.append(frame_idx)
                    
        target_frames.sort()
        
        raw_detections = {}
        tracks: List[Track] = []
        
        for frame_idx in target_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue
                
            timestamp = frame_idx / fps if fps > 0 else 0
            
            frame_dets = self.extract_text_from_frame(frame)
            raw_detections[frame_idx] = frame_dets
            
            for det in frame_dets:
                matched = False
                for track in tracks:
                    if track.text.lower() == det.text.lower():
                        iou = bbox_iou(track.bboxes_norm[-1], det.bbox_normalized)
                        if iou > 0.3:
                            track.add(det, timestamp)
                            matched = True
                            break
                
                if not matched:
                    tracks.append(Track(det, timestamp))
                    
        cap.release()
        
        final_detections = [t.to_tracked_text() for t in tracks]
        
        return OCRResult(
            video_path=video_path,
            frame_count=total_frames,
            resolution=(width, height),
            sample_fps=sample_fps,
            detections=final_detections,
            raw_frame_detections=raw_detections
        )
