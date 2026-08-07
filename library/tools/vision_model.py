import time
import subprocess
import tempfile
import os
from pathlib import Path
from typing import List, Optional, Tuple

try:
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
except ImportError:
    # Allow import when running tests without mlx_vlm installed
    load = None
    generate = None
    apply_chat_template = None


MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"


class VisionModel:
    """Lazy-loaded singleton wrapper for Gemma4 12B (MLX) vision model."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._model = None
            cls._instance._proc = None
            cls._instance._load_time = 0.0
        return cls._instance

    def _ensure_loaded(self):
        if self._model is None:
            if load is None:
                raise ImportError("mlx_vlm is not installed.")
            print(f"Loading {MODEL_ID}...")
            t0 = time.time()
            self._model, self._proc = load(MODEL_ID)
            self._load_time = time.time() - t0
            print(f"Model loaded in {self._load_time:.1f}s")

    def analyze_image(self, image_path: str, prompt: str, max_tokens: int = 600) -> str:
        """Analyze a single image."""
        self._ensure_loaded()
        
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt, num_images=1
        )
        
        r = generate(
            self._model, self._proc,
            prompt=formatted,
            image=[image_path],
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        return r.text if hasattr(r, "text") else str(r)

    def analyze_images(self, image_paths: List[str], prompt: str, max_tokens: int = 800) -> str:
        """Analyze multiple images."""
        self._ensure_loaded()
        
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt, num_images=len(image_paths)
        )
        
        r = generate(
            self._model, self._proc,
            prompt=formatted,
            image=image_paths,
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        return r.text if hasattr(r, "text") else str(r)

    def analyze_video_frames(self, video_path: str, prompt: str, sample_count: int = 5, max_tokens: int = 800) -> str:
        """Extract frames from a video and analyze them."""
        # Get duration
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', 
                   '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
            dur_res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            duration = float(dur_res.stdout.strip())
        except Exception as e:
            raise RuntimeError(f"Could not get duration for {video_path}: {e}")

        if duration <= 0:
            raise ValueError(f"Invalid video duration: {duration}")

        sample_points = [duration * (i + 1) / (sample_count + 1) for i in range(sample_count)]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            extracted_paths = []
            for i, t in enumerate(sample_points):
                img_path = os.path.join(tmpdir, f'frame_{i}.jpg')
                cmd = ['ffmpeg', '-y', '-ss', str(t), '-i', video_path, 
                       '-vframes', '1', '-q:v', '2', img_path]
                subprocess.run(cmd, capture_output=True, timeout=15)
                if os.path.exists(img_path):
                    extracted_paths.append(img_path)
            
            if not extracted_paths:
                raise RuntimeError(f"Failed to extract any frames from {video_path}")
            
            return self.analyze_images(extracted_paths, prompt, max_tokens)


def get_model() -> VisionModel:
    """Return the VisionModel singleton instance."""
    return VisionModel()
