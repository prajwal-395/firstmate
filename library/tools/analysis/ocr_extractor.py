from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import TYPE_CHECKING, Tuple, List, Dict, Optional, Any
import json
import math
import os
import unicodedata
from collections import defaultdict
from pathlib import Path

if TYPE_CHECKING:  # pragma: no cover - import-time only, for the frame hint
    import numpy as np

OCR_SCHEMA_VERSION = 2

@dataclass
class TextDetection:
    text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    bbox_normalized: Tuple[float, float, float, float]
    polygon_normalized: Tuple[Tuple[float, float], ...] = ()
    language: Optional[str] = None
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    # False when the recognizer names text without locating it (the MLX
    # VLM text pass): the whole frame was examined, so the stored box is
    # the full frame as the region examined, never a located box.
    spatially_grounded: bool = True

@dataclass
class TrackedText:
    text: str
    confidence: float
    appearances: List[Tuple[float, float]]
    bbox: Tuple[int, int, int, int]
    bbox_normalized: Tuple[float, float, float, float]
    is_static: bool
    track_id: str = ""
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    language: Optional[str] = None
    samples: List[Dict[str, Any]] = field(default_factory=list)
    appearance_intervals: List[Dict[str, Any]] = field(default_factory=list)
    spatially_grounded: bool = True

@dataclass
class OCRResult:
    video_path: str
    frame_count: int
    resolution: Tuple[int, int]
    sample_fps: float
    detections: List[TrackedText]
    raw_frame_detections: Dict[int, List[TextDetection]]
    graphics: List[Dict[str, Any]] = field(default_factory=list)
    raw_frame_graphics: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)
    # Which recognizer wrote this artifact (GEO-025 provenance). The
    # incumbent default is EasyOCR; the MLX VLM pass records its own
    # precision and revision so a reader knows what measured the text.
    method: str = "EasyOCR"
    precision: Optional[str] = None
    model_revision: Optional[str] = None

    def save(self, output_dir: str):
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, "ocr_result.json")
        data = {
            "schema_version": OCR_SCHEMA_VERSION,
            "video_path": self.video_path,
            "frame_count": self.frame_count,
            "resolution": self.resolution,
            "sample_fps": self.sample_fps,
            "method": self.method,
            "precision": self.precision,
            "model_revision": self.model_revision,
            "detections": [asdict(d) for d in self.detections],
            "raw_frame_detections": {k: [asdict(d) for d in v] for k, v in self.raw_frame_detections.items()},
            "graphics": self.graphics,
            "raw_frame_graphics": self.raw_frame_graphics,
        }
        with open(out_path, 'w', encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, output_dir: str) -> 'OCRResult':
        in_path = os.path.join(output_dir, "ocr_result.json")
        with open(in_path, 'r', encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema_version", 1) > OCR_SCHEMA_VERSION:
            raise ValueError(
                f"OCR artifact schema {data['schema_version']} is newer "
                f"than supported schema {OCR_SCHEMA_VERSION}")
        
        detections = [TrackedText(**{
            **d,
            "samples": d.get("samples", []),
            "alternatives": d.get("alternatives", []),
            "track_id": d.get("track_id", ""),
            "language": d.get("language"),
            "appearance_intervals": d.get("appearance_intervals", []),
            "spatially_grounded": d.get("spatially_grounded", True),
        }) for d in data["detections"]]
        raw_frame_detections = {
            int(k): [TextDetection(**{
                **d,
                "polygon_normalized": tuple(tuple(p) for p in d.get("polygon_normalized", ())),
                "language": d.get("language"),
                "alternatives": d.get("alternatives", []),
                "spatially_grounded": d.get("spatially_grounded", True),
            }) for d in v]
            for k, v in data["raw_frame_detections"].items()
        }
        return cls(
            video_path=data["video_path"],
            frame_count=data["frame_count"],
            resolution=tuple(data["resolution"]),
            sample_fps=data["sample_fps"],
            detections=detections,
            raw_frame_detections=raw_frame_detections,
            graphics=data.get("graphics", []),
            raw_frame_graphics={int(k): v for k, v in data.get("raw_frame_graphics", {}).items()},
            method=data.get("method", "EasyOCR"),
            precision=data.get("precision"),
            model_revision=data.get("model_revision"),
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
    def __init__(self, detection: TextDetection, timestamp: float,
                 frame_index: Optional[int] = None,
                 grounded: bool = True):
        self.text = detection.text
        self.grounded = grounded and detection.spatially_grounded
        self.confidences = []
        self.bboxes_norm = []
        self.bboxes_pixel = []
        self.appearances = []
        self.last_timestamp = timestamp
        self.samples = []
        self.add(detection, timestamp, frame_index)
        
    def add(self, detection: TextDetection, timestamp: float,
            frame_index: Optional[int] = None):
        self.confidences.append(detection.confidence)
        self.bboxes_norm.append(detection.bbox_normalized)
        self.bboxes_pixel.append(detection.bbox)
        
        # If gap <= 2.0 seconds, extend current appearance
        if not self.appearances:
            self.appearances.append([timestamp, timestamp])
        elif timestamp - self.appearances[-1][1] <= 2.0:
            self.appearances[-1][1] = timestamp
        else:
            self.appearances.append([timestamp, timestamp])
            
        self.last_timestamp = timestamp
        self.samples.append({
            "timestamp": float(timestamp),
            "frame_index": frame_index,
            "bbox": list(detection.bbox),
            "bbox_normalized": list(detection.bbox_normalized),
            "polygon_normalized": [list(p) for p in detection.polygon_normalized],
            "confidence": float(detection.confidence),
            "text": detection.text,
            "language": detection.language,
            "alternatives": detection.alternatives,
        })
        
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
            is_static=is_static,
            track_id="",  # Assigned deterministically after tracking.
            alternatives=list({
                json.dumps(candidate, sort_keys=True): candidate
                for sample in self.samples
                for candidate in sample.get("alternatives", [])
            }.values()),
            language=_language_hint(self.text),
            samples=self.samples,
            spatially_grounded=self.grounded,
        )


def _language_hint(text: str) -> Optional[str]:
    """Return a script-specific language hint only when the script supports it.

    Latin text is deliberately left unknown: OCR configuration is not proof
    that the visible language is English.
    """
    for char in text:
        name = unicodedata.name(char, "")
        if "CYRILLIC" in name:
            return "und-Cyrl"
        if "HIRAGANA" in name or "KATAKANA" in name:
            return "ja"
        if "HANGUL" in name:
            return "ko"
        if "CJK UNIFIED IDEOGRAPH" in name:
            return "und-Hani"
        if "ARABIC" in name:
            return "und-Arab"
        if "HEBREW" in name:
            return "he"
        if "DEVANAGARI" in name:
            return "hi"
    return None


def _polygon_iou(a: List[List[float]], b: List[List[float]]) -> float:
    """A conservative overlap proxy from polygon bounding boxes."""
    if not a or not b:
        return 0.0
    def bounds(points):
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        return min(xs), min(ys), max(xs), max(ys)
    ax1, ay1, ax2, ay2 = bounds(a)
    bx1, by1, bx2, by2 = bounds(b)
    intersection = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = max(0.0, (ax2 - ax1) * (ay2 - ay1)) + max(0.0, (bx2 - bx1) * (by2 - by1)) - intersection
    return intersection / union if union else 0.0


def _graphic_candidates(frame, text_boxes: List[TextDetection]) -> List[Dict[str, Any]]:
    """Find conservative UI/graphic candidates from contours without a model.

    These are measured shapes, not semantic button or cursor claims. A
    triangle can be a cursor candidate; a rectangle can be a control or
    graphic candidate. Labels preserve that uncertainty.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []
    h, w = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if len(frame.shape) == 3 else frame
    edges = cv2.Canny(gray, 60, 160)
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw < 5 or bh < 5 or area < 25 or area > w * h * 0.12:
            continue
        approx = cv2.approxPolyDP(contour, 0.035 * cv2.arcLength(contour, True), True)
        vertices = len(approx)
        if vertices not in (3, 4, 5, 6) or not cv2.isContourConvex(approx):
            continue
        norm = [
            [float(x / w), float(y / h)],
            [float((x + bw) / w), float(y / h)],
            [float((x + bw) / w), float((y + bh) / h)],
            [float(x / w), float((y + bh) / h)],
        ]
        box = (x / w, y / h, bw / w, bh / h)
        if any(bbox_iou(box, d.bbox_normalized) > 0.6 for d in text_boxes):
            continue
        ratio = bw / bh
        kind = ("cursor_candidate" if vertices <= 6 and area <= 1000
                and bw <= 28 and bh <= 32 and 0.45 <= ratio <= 2.2
                else None)
        if kind is None and vertices == 4 and bw >= 30 and bh >= 16:
            kind = "ui_control_candidate" if ratio <= 12 else "graphic_region_candidate"
        if kind is None:
            continue
        roi = gray[y:y + bh, x:x + bw]
        color = (np.median(frame[y:y + bh, x:x + bw], axis=(0, 1)).round().astype(int).tolist()
                 if len(frame.shape) == 3 else [int(round(float(np.median(roi))))])
        out.append({"kind": kind, "bbox_normalized": list(box),
                    "polygon_normalized": norm, "median_color": color,
                    "visual_state": ",".join(map(str, color)),
                    "contour_area": area, "vertices": vertices})
    # Canny contours commonly return both sides of one outlined control.
    # Keep the largest contour for highly overlapping same-kind regions.
    selected = []
    for candidate in sorted(out, key=lambda row: row["contour_area"], reverse=True):
        if any(old["kind"] == candidate["kind"]
               and bbox_iou(tuple(old["bbox_normalized"]),
                            tuple(candidate["bbox_normalized"])) > 0.65
               for old in selected):
            continue
        selected.append(candidate)
    return selected

# Backend ids for the 1.07 text pass. The incumbent default never moves;
# a caller opts into the MLX VLM explicitly and the artifact says which
# ran (GEO-015 vs GEO-016/017, GEO-025 provenance).
OCR_BACKEND_EASYOCR = "easyocr"
OCR_BACKEND_PADDLE_MLX = "paddleocr_vlm_mlx"
OCR_BACKENDS = (OCR_BACKEND_EASYOCR, OCR_BACKEND_PADDLE_MLX)

# The measured MLX build (GEO-017): same weights as the publisher
# checkpoint, converted locally. Overridable per run; the artifact
# records the effective precision and revision, never this default.
MLX_MODEL_PATH_ENV = "OCR_MLX_MODEL_PATH"
MLX_MODEL_PATH_DEFAULT = "/tmp/ocr-mlx-paddleocr-vl-1.6-bf16"
MLX_PRECISION = "bfloat16 (MLX, unquantized)"
MLX_MODEL_REVISION = (
    "PaddlePaddle/PaddleOCR-VL-1.6@676e2aad67375fec9c7dcc9740babec7a0d0d110"
    " + mlx-vlm 0.7.2 convert --dtype bfloat16")


class OCRExtractor:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(OCRExtractor, cls).__new__(cls)
            cls._instance.reader = None
            cls._instance.mlx_model = None
        return cls._instance
        
    def _get_reader(self):
        if self.reader is None:
            try:
                import easyocr
            except ImportError as exc:
                raise ImportError(
                    "easyocr is not installed, so on-screen text cannot be "
                    "measured. Install the ML environment "
                    "(docs/ML_ENVIRONMENT.md) or "
                    "`pip install -r requirements.txt`."
                ) from exc
            # Model acquisition is an explicit environment setup step. A
            # pipeline run must never download weights implicitly.
            self.reader = easyocr.Reader(['en'], download_enabled=False)
        return self.reader

    def _get_mlx(self):
        if self.mlx_model is None:
            try:
                from mlx_vlm import load
            except ImportError as exc:
                raise ImportError(
                    "mlx_vlm is not installed, so the MLX OCR backend "
                    "cannot run. Install the ML environment "
                    "(docs/ML_ENVIRONMENT.md)."
                ) from exc
            model_path = os.environ.get(MLX_MODEL_PATH_ENV,
                                        MLX_MODEL_PATH_DEFAULT)
            self.mlx_model, self.mlx_processor = load(
                model_path, trust_remote_code=True)
            self.mlx_model_path = model_path
        return self.mlx_model, self.mlx_processor

    def extract_text_from_frame_mlx(self, frame: np.ndarray) -> List[TextDetection]:
        """One frame through the MLX VLM text pass (GEO-016/017).

        The VLM names text without locating it: each returned string
        becomes an ungrounded detection over the examined frame, never
        a located box. Empty/blank output is no detection, not an
        empty string detection.
        """
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        model, processor = self._get_mlx()
        h, w = frame.shape[:2]
        template = apply_chat_template(
            processor, model.config, prompt="OCR:", num_images=1)
        import tempfile
        try:
            from PIL import Image as _Image
        except ImportError as exc:
            raise ImportError(
                "PIL is not installed, so frames cannot reach the MLX "
                "OCR backend. pip install pillow."
            ) from exc
        pil_frame = _Image.fromarray(frame[:, :, ::-1])
        with tempfile.NamedTemporaryFile(suffix=".png",
                                         delete=True) as tmp:
            pil_frame.save(tmp.name)
            result = generate(model, processor, template, image=tmp.name,
                              max_tokens=1024)
        detections = []
        for line in (result.text or "").splitlines():
            text = line.strip()
            if not text:
                continue
            detections.append(TextDetection(
                text=text,
                confidence=1.0,
                bbox=(0, 0, int(w), int(h)),
                bbox_normalized=(0.0, 0.0, 1.0, 1.0),
                polygon_normalized=(),
                language=_language_hint(text),
                alternatives=[],
                spatially_grounded=False,
            ))
        return detections

    def extract_text_from_frame(self, frame: np.ndarray,
                                backend: str = OCR_BACKEND_EASYOCR,
                                ) -> List[TextDetection]:
        if backend == OCR_BACKEND_PADDLE_MLX:
            return self.extract_text_from_frame_mlx(frame)
        reader = self._get_reader()
        results = reader.readtext(frame)
        
        h, w = frame.shape[:2]
        detections = []
        
        for results_item in results:
            if not isinstance(results_item, (list, tuple)) or len(results_item) < 3:
                continue
            bbox, text, conf = results_item[:3]
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
            
            polygon = tuple((float(pt[0] / w), float(pt[1] / h)) for pt in bbox)
            detections.append(TextDetection(
                text=text,
                confidence=float(conf),
                bbox=pixel_bbox,
                bbox_normalized=norm_bbox,
                polygon_normalized=polygon,
                language=_language_hint(text),
                alternatives=(list(results_item[3]) if len(results_item) > 3
                              and isinstance(results_item[3], (list, tuple))
                              else []),
            ))
            
        return detections

    def extract_text_from_clip(self, video_path: str, sample_fps: float = 1.0, scene_boundaries: Optional[List[float]] = None, backend: str = OCR_BACKEND_EASYOCR) -> OCRResult:
        if backend not in OCR_BACKENDS:
            raise ValueError(
                f"unknown OCR backend {backend!r}; expected one of "
                f"{OCR_BACKENDS}")
        grounded = backend == OCR_BACKEND_EASYOCR
        try:
            import cv2
        except ImportError as exc:
            raise ImportError(
                "opencv (cv2) is not installed, so video frames cannot be "
                "read for OCR. Install the ML environment "
                "(docs/ML_ENVIRONMENT.md) or "
                "`pip install -r requirements.txt`."
            ) from exc
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
        raw_graphics = {}
        tracks: List[Track] = []
        graphic_tracks: List[Dict[str, Any]] = []
        
        for frame_idx in target_frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue
                
            timestamp = frame_idx / fps if fps > 0 else 0
            
            frame_dets = self.extract_text_from_frame(
                frame, backend=backend)
            raw_detections[frame_idx] = frame_dets
            # Ungrounded text has no location, so it must not suppress
            # graphic candidates the way located boxes do.
            graphic_dets = _graphic_candidates(
                frame, frame_dets if grounded else [])
            raw_graphics[frame_idx] = graphic_dets

            for det in frame_dets:
                matched = False
                for track in tracks:
                    if track.text.lower() == det.text.lower():
                        iou = bbox_iou(track.bboxes_norm[-1], det.bbox_normalized)
                        if iou > 0.3:
                            track.add(det, timestamp, frame_idx)
                            matched = True
                            break

                if not matched:
                    tracks.append(Track(det, timestamp, frame_idx,
                                        grounded=grounded))

            for graphic in graphic_dets:
                candidates = [track for track in graphic_tracks
                              if track["kind"] == graphic["kind"]
                              and timestamp - track["last_timestamp"] <= 2.0]
                best = max(candidates,
                           key=lambda track: _polygon_iou(
                               track["samples"][-1]["polygon_normalized"],
                               graphic["polygon_normalized"]),
                           default=None)
                overlap = (_polygon_iou(best["samples"][-1]["polygon_normalized"],
                                        graphic["polygon_normalized"])
                           if best else 0.0)
                if graphic["kind"] == "cursor_candidate" and best and overlap < 0.2:
                    old_box = best["samples"][-1]["bbox_normalized"]
                    new_box = graphic["bbox_normalized"]
                    old_center = (old_box[0] + old_box[2] / 2,
                                  old_box[1] + old_box[3] / 2)
                    new_center = (new_box[0] + new_box[2] / 2,
                                  new_box[1] + new_box[3] / 2)
                    distance = math.dist(old_center, new_center)
                    if distance > 0.15:
                        best = None
                elif best and overlap < 0.1:
                    best = None
                if best is None:
                    best = {"kind": graphic["kind"], "samples": [],
                            "appearances": [[timestamp, timestamp]],
                            "last_timestamp": timestamp}
                    graphic_tracks.append(best)
                elif timestamp - best["last_timestamp"] <= 2.0:
                    best["appearances"][-1][1] = timestamp
                else:
                    best["appearances"].append([timestamp, timestamp])
                best["samples"].append({"timestamp": timestamp,
                                         "frame_index": frame_idx, **graphic})
                best["last_timestamp"] = timestamp
                    
        cap.release()
        
        final_detections = []
        for index, track in enumerate(tracks, 1):
            item = track.to_tracked_text()
            item.track_id = f"text-{index:04d}"
            period = 1.0 / sample_fps if sample_fps > 0 else 0.001
            item.appearance_intervals = [{
                "start_seconds": float(start),
                "end_seconds": min(duration, float(end) + period),
                "boundary_basis": "sampled_presence_lower_bound",
                "end_exclusive": True,
            } for start, end in track.appearances
                if min(duration, float(end) + period) > float(start)]
            final_detections.append(item)
        final_graphics = []
        for index, track in enumerate(graphic_tracks, 1):
            final_graphics.append({
                "track_id": f"graphic-{index:04d}",
                "kind": track["kind"],
                "appearance_intervals": track["appearances"],
                "appearance_interval_basis": "sampled_presence_lower_bound",
                "samples": track["samples"],
            })
        
        if backend == OCR_BACKEND_PADDLE_MLX:
            method = "PaddleOCR-VL-1.6-MLX"
            precision: Optional[str] = MLX_PRECISION
            model_revision: Optional[str] = MLX_MODEL_REVISION
        else:
            method = "EasyOCR"
            precision = None
            model_revision = None
        return OCRResult(
            video_path=video_path,
            frame_count=total_frames,
            resolution=(width, height),
            sample_fps=sample_fps,
            detections=final_detections,
            raw_frame_detections=raw_detections,
            graphics=final_graphics,
            raw_frame_graphics=raw_graphics,
            method=method,
            precision=precision,
            model_revision=model_revision,
        )
