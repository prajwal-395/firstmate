import json
import os
import sys
import time
from contextlib import nullcontext
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from library.tools import perf_ledger

try:
    from mlx_vlm import load, generate
    from mlx_vlm.prompt_utils import apply_chat_template
except ImportError:
    # Allow import when running tests without mlx_vlm installed
    load = None
    generate = None
    apply_chat_template = None


MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"

# Resident mlx_vlm OpenAI-compatible server (see docs/GEMMA_SERVER.md).
# When it is up, calls go over HTTP and skip the ~6s / ~7GB in-process
# model load; when it is not reachable, every method falls back to the
# in-process path below with a LOUD stderr line. Empty string disables
# the server path entirely (in-process always).
SERVER_URL = os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:8080").rstrip("/")
SERVER_MODEL = os.environ.get("GEMMA_SERVER_MODEL", MODEL_ID)
try:
    SERVER_TIMEOUT_SECONDS = float(os.environ.get("GEMMA_SERVER_TIMEOUT", "300"))
except ValueError:
    SERVER_TIMEOUT_SECONDS = 300.0


class _ServerUnusable(RuntimeError):
    """The resident server cannot answer; caller must fall back loudly."""


def _server_chat(content_parts: list, max_tokens: int) -> str:
    """POST one chat/completions request; raise _ServerUnusable to fall back.

    HTTP 4xx (our payload is malformed) propagates - falling back would
    mask a client bug. Connection errors, timeouts and 5xx fall back.
    """
    if not SERVER_URL:
        raise _ServerUnusable("GEMMA_SERVER_URL is empty (server path disabled)")
    url = f"{SERVER_URL}/v1/chat/completions"
    body = json.dumps(
        {
            "model": SERVER_MODEL,
            "messages": [{"role": "user", "content": content_parts}],
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
    ).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with perf_ledger.span("gemma_inference", backend="gemma_server",
                              model=SERVER_MODEL, calls=1) as cost, \
                urllib.request.urlopen(req, timeout=SERVER_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            usage = payload.get("usage") if isinstance(payload, dict) else None
            if isinstance(usage, dict):
                cost["input_tokens"] = usage.get("prompt_tokens")
                cost["output_tokens"] = usage.get("completion_tokens")
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code < 600:
            raise _ServerUnusable(f"server HTTP {exc.code}") from exc
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _ServerUnusable(str(exc)) from exc
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise _ServerUnusable(f"unexpected response shape: {payload!r:.200}") from exc


def _via_server_or_fallback(content_parts: list, max_tokens: int, what: str,
                            route_metadata: dict | None = None,
                            inference_admission=None):
    """Return (text, True) from the server, or (None, False) to run in-process.

    Both directions print to stderr, never stdout - a step's stdout is its
    JSON result (see library/tools/step_stdout.py).
    """
    def _set_route(backend, model, cause=None):
        if route_metadata is None:
            return
        route_metadata.update({
            "backend": backend,
            "model": model,
            "model_version": model,
        })
        route_metadata.setdefault("fallback_causes", [])
        if cause:
            route_metadata["fallback_causes"].append(
                {"stage": "gemma_server", "cause": cause})

    if not SERVER_URL:
        _set_route("mlx_vlm", MODEL_ID, "server_disabled")
        print(
            f"GEMMA SERVER path disabled (GEMMA_SERVER_URL is empty); "
            f"using in-process {MODEL_ID}.",
            file=sys.stderr,
        )
        return None, False
    try:
        admission = (inference_admission()
                     if inference_admission is not None else nullcontext())
        with admission:
            text = _server_chat(content_parts, max_tokens)
    except _ServerUnusable as exc:
        _set_route("mlx_vlm", MODEL_ID, str(exc))
        print(
            f"GEMMA SERVER unreachable at {SERVER_URL} ({exc}); "
            f"falling back to in-process {MODEL_ID} "
            f"(full model load, ~6s, ~7GB resident).",
            file=sys.stderr,
        )
        return None, False
    _set_route("gemma_server", SERVER_MODEL)
    print(
        f"GEMMA SERVER answered {what} at {SERVER_URL} (resident, no model load).",
        file=sys.stderr,
    )
    return text, True


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

    def _generate(self, *, inference_admission=None, **kwargs):
        admission = (inference_admission()
                     if inference_admission is not None else nullcontext())
        with admission:
            with perf_ledger.span("gemma_inference", backend="mlx_vlm",
                                  model=MODEL_ID, calls=1) as cost:
                r = generate(self._model, self._proc, **kwargs)
                cost["input_tokens"] = getattr(r, "prompt_tokens", None)
                cost["output_tokens"] = getattr(r, "generation_tokens", None)
        return r

    def analyze_image(self, image_path: str, prompt: str, max_tokens: int = 600,
                      _route_metadata: dict | None = None,
                      inference_admission=None) -> str:
        """Analyze a single image."""
        parts = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_path}},
        ]
        text, via_server = _via_server_or_fallback(
            parts, max_tokens, "analyze_image", _route_metadata,
            inference_admission)
        if via_server:
            return text
        admission = (inference_admission()
                     if inference_admission is not None else nullcontext())
        with admission:
            self._ensure_loaded()
        
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt, num_images=1
        )
        
        r = self._generate(
            inference_admission=inference_admission,
            prompt=formatted,
            image=[image_path],
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        return r.text if hasattr(r, "text") else str(r)

    def analyze_images(self, image_paths: List[str], prompt: str,
                       max_tokens: int = 800,
                       _route_metadata: dict | None = None,
                       inference_admission=None) -> str:
        """Analyze multiple images."""
        parts = [{"type": "text", "text": prompt}]
        parts += [
            {"type": "image_url", "image_url": {"url": p}} for p in image_paths
        ]
        text, via_server = _via_server_or_fallback(
            parts, max_tokens, "analyze_images", _route_metadata,
            inference_admission)
        if via_server:
            return text
        admission = (inference_admission()
                     if inference_admission is not None else nullcontext())
        with admission:
            self._ensure_loaded()
        
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt, num_images=len(image_paths)
        )
        
        r = self._generate(
            inference_admission=inference_admission,
            prompt=formatted,
            image=image_paths,
            max_tokens=max_tokens,
            temperature=0.1,
            verbose=False,
        )
        return r.text if hasattr(r, "text") else str(r)

    def analyze_video(self, video_path: str, prompt: str, max_tokens: int = 800,
                      inference_admission=None) -> str:
        """Analyze a video file natively."""
        parts = [
            {"type": "text", "text": prompt},
            {"type": "video_url", "video_url": {"url": video_path}},
        ]
        text, via_server = _via_server_or_fallback(
            parts, max_tokens, "analyze_video",
            inference_admission=inference_admission)
        if via_server:
            return text
        admission = (inference_admission()
                     if inference_admission is not None else nullcontext())
        with admission:
            self._ensure_loaded()
        
        prompt_with_video = f"<|video|>{prompt}"
        formatted = apply_chat_template(
            self._proc, self._model.config, prompt_with_video, num_images=0
        )
        
        r = self._generate(
            inference_admission=inference_admission,
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
