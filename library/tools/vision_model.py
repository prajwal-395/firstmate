import sys
import time
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
            # stderr, not stdout: a step's stdout is its JSON result, and
            # this line landed in the middle of one. See
            # library/tools/step_stdout.py.
            print(f"Loading {MODEL_ID}...", file=sys.stderr)
            t0 = time.time()
            self._model, self._proc = load(MODEL_ID)
            self._load_time = time.time() - t0
            print(f"Model loaded in {self._load_time:.1f}s", file=sys.stderr)

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

    def analyze_video(self, video_path: str, prompt: str, max_tokens: int = 800) -> str:
        """Analyze a video file natively."""
        self._ensure_loaded()
        
        prompt_with_video = f"<|video|>{prompt}"
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt_with_video, num_images=0
        )
        
        r = generate(
            self._model, self._proc,
            prompt=formatted,
            video=video_path,
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        return r.text if hasattr(r, "text") else str(r)


def get_model() -> VisionModel:
    """Return the VisionModel singleton instance."""
    return VisionModel()
