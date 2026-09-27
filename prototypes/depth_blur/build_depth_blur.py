"""Build a source-resolution, offline broad depth-blur prototype video."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

MODEL_REPOSITORY = "depth-anything/Video-Depth-Anything-Small"
MODEL_REVISION = "256875362cff76724b920335dfb4b29dd611f66e"
CHECKPOINT_SHA256 = "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609"
MODEL_CONFIG = {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Source video")
    parser.add_argument(
        "--output", type=Path, required=True, help="Output blurred video"
    )
    parser.add_argument(
        "--model-repo",
        type=Path,
        required=True,
        help="Pinned Video-Depth-Anything source checkout",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        required=True,
        help="Video-Depth-Anything Small checkpoint",
    )
    parser.add_argument(
        "--python-deps",
        type=Path,
        help="Optional isolated directory for model-only Python dependencies",
    )
    parser.add_argument(
        "--start-seconds", type=float, required=True, help="Source in point"
    )
    parser.add_argument(
        "--duration-seconds", type=float, required=True, help="Duration to process"
    )
    parser.add_argument(
        "--input-size", type=int, default=518, help="VDA network input size"
    )
    parser.add_argument(
        "--max-inference-dimension",
        type=int,
        default=960,
        help="Maximum inference frame dimension",
    )
    parser.add_argument(
        "--blur-sigma",
        type=float,
        default=22.0,
        help="Background Gaussian blur radius in source pixels",
    )
    parser.add_argument(
        "--foreground-center",
        type=float,
        default=0.40,
        help="Normalized relative-depth center of the foreground transition",
    )
    parser.add_argument(
        "--foreground-feather",
        type=float,
        default=0.18,
        help="Normalized relative-depth width of the soft transition",
    )
    parser.add_argument(
        "--mask-softness-px",
        type=float,
        default=2.0,
        help="Additional mask softening in source pixels",
    )
    parser.add_argument(
        "--device", choices=("auto", "mps", "cuda", "cpu"), default="auto"
    )
    parser.add_argument(
        "--metrics",
        type=Path,
        help="Optional JSON metrics destination; defaults beside the output",
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def load_model(args: argparse.Namespace):
    repository = args.model_repo.resolve()
    if not (repository / "video_depth_anything" / "video_depth.py").is_file():
        raise FileNotFoundError(f"not a Video-Depth-Anything checkout: {repository}")
    dependency_root = repository.parent / "python-deps"
    if dependency_root.is_dir():
        sys.path.insert(0, str(dependency_root))
    if args.python_deps:
        dependency_root = args.python_deps.resolve()
        if not dependency_root.is_dir():
            raise FileNotFoundError(
                f"model dependency directory does not exist: {dependency_root}"
            )
        sys.path.insert(0, str(dependency_root))
    sys.path.insert(0, str(repository))
    checkpoint = args.checkpoint.resolve()
    actual_hash = sha256(checkpoint)
    if actual_hash != CHECKPOINT_SHA256:
        raise ValueError(f"checkpoint SHA-256 mismatch: {actual_hash}")

    import torch
    from video_depth_anything.video_depth import VideoDepthAnything

    if args.device == "auto":
        device = (
            "mps"
            if torch.backends.mps.is_available()
            else "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )
    else:
        device = args.device
    if device == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS was requested but is unavailable")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable")

    model = VideoDepthAnything(**MODEL_CONFIG)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    return model.to(device).eval(), torch, device, actual_hash


def open_clip(path: Path):
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise OSError(f"could not open source video: {path}")
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if width <= 0 or height <= 0 or fps <= 0 or count <= 0:
        capture.release()
        raise ValueError(
            "source video has invalid dimensions, frame rate or frame count"
        )
    return capture, width, height, fps, count


def read_inference_frames(
    path: Path, start_frame: int, frame_count: int, max_dimension: int
) -> tuple[np.ndarray, tuple[int, int], float]:
    capture, width, height, fps, source_count = open_clip(path)
    if start_frame < 0 or start_frame + frame_count > source_count:
        capture.release()
        raise ValueError("requested source frame range falls outside the clip")
    scale = min(1.0, max_dimension / max(width, height))
    inference_width = even(round(width * scale))
    inference_height = even(round(height * scale))
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    frames = []
    try:
        for index in range(frame_count):
            ok, frame = capture.read()
            if not ok:
                raise EOFError(f"source ended at requested frame {start_frame + index}")
            if (inference_width, inference_height) != (width, height):
                frame = cv2.resize(
                    frame,
                    (inference_width, inference_height),
                    interpolation=cv2.INTER_AREA,
                )
            frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    finally:
        capture.release()
    return np.stack(frames), (width, height), fps


def normalize_depth(depths: np.ndarray) -> tuple[np.ndarray, float, float]:
    low, high = np.percentile(depths, [2, 98])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        raise ValueError("depth model produced a constant or non-finite map")
    return np.clip((depths - low) / (high - low), 0.0, 1.0), float(low), float(high)


def foreground_alpha(
    depth: np.ndarray, args: argparse.Namespace, size: tuple[int, int]
) -> np.ndarray:
    low = args.foreground_center - args.foreground_feather / 2
    high = args.foreground_center + args.foreground_feather / 2
    alpha = np.clip((depth - low) / (high - low), 0.0, 1.0)
    alpha = alpha * alpha * (3.0 - 2.0 * alpha)
    width, height = size
    alpha = cv2.resize(alpha, (width, height), interpolation=cv2.INTER_LINEAR)
    if args.mask_softness_px > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=args.mask_softness_px)
    return np.clip(alpha, 0.0, 1.0)


def encode_blurred_video(
    args: argparse.Namespace,
    normalized_depths: np.ndarray,
    source_size: tuple[int, int],
    fps: float,
    start_frame: int,
    frame_count: int,
) -> None:
    width, height = source_size
    temporary = args.output.with_name(f".{args.output.stem}.tmp{args.output.suffix}")
    if temporary.exists():
        temporary.unlink()
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        f"{fps:.8f}",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "16",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(temporary),
    ]
    capture, source_width, source_height, source_fps, source_count = open_clip(
        args.input
    )
    if (
        (source_width, source_height) != source_size
        or source_fps <= 0
        or start_frame + frame_count > source_count
    ):
        capture.release()
        raise RuntimeError(
            "source properties changed between depth inference and compositing"
        )
    capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    assert process.stderr is not None
    try:
        for index, depth in enumerate(normalized_depths):
            ok, original_bgr = capture.read()
            if not ok:
                raise EOFError(
                    f"source ended during compositing at frame {start_frame + index}"
                )
            alpha = foreground_alpha(depth, args, source_size)[:, :, None]
            background = cv2.GaussianBlur(original_bgr, (0, 0), sigmaX=args.blur_sigma)
            processed_bgr = np.clip(
                original_bgr.astype(np.float32) * alpha
                + background.astype(np.float32) * (1.0 - alpha),
                0,
                255,
            ).astype(np.uint8)
            process.stdin.write(
                cv2.cvtColor(processed_bgr, cv2.COLOR_BGR2RGB).tobytes()
            )
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait()
        if return_code:
            raise subprocess.CalledProcessError(return_code, command, stderr=stderr)
        os.replace(temporary, args.output)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            process.wait()
        if temporary.exists():
            temporary.unlink()
        raise
    finally:
        capture.release()
        process.stderr.close()


def main() -> int:
    args = parse_args()
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    args.model_repo = args.model_repo.resolve()
    args.checkpoint = args.checkpoint.resolve()
    args.metrics = (
        args.metrics.resolve()
        if args.metrics
        else args.output.with_suffix(".metrics.json")
    )
    if not args.input.is_file() or not args.checkpoint.is_file():
        raise FileNotFoundError("input video and checkpoint must exist")
    if args.output.exists() or args.metrics.exists():
        raise FileExistsError(
            "output or metrics file already exists; refusing to overwrite"
        )
    if args.duration_seconds <= 0 or args.start_seconds < 0 or args.input_size <= 0:
        raise ValueError(
            "start time and model size must be valid, with positive duration"
        )
    if not 0 < args.foreground_feather <= 1 or not 0 <= args.foreground_center <= 1:
        raise ValueError("foreground center and feather must be normalized values")
    if (
        args.blur_sigma <= 0
        or args.mask_softness_px < 0
        or args.max_inference_dimension <= 0
    ):
        raise ValueError("blur, mask and inference dimensions must be positive")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.metrics.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model_started = time.perf_counter()
    model, torch, device, checkpoint_hash = load_model(args)
    model_load_seconds = time.perf_counter() - model_started

    initial_capture, _, _, source_fps, _ = open_clip(args.input)
    initial_capture.release()
    fps = source_fps
    start_frame = round(args.start_seconds * fps)
    frame_count = round(args.duration_seconds * fps)
    source_frames = read_inference_frames(
        args.input, start_frame, frame_count, args.max_inference_dimension
    )
    frames, source_size, decoded_fps = source_frames
    if not math.isclose(decoded_fps, fps, rel_tol=0.0, abs_tol=0.01):
        raise RuntimeError("source frame rate changed while opening the clip")

    inference_started = time.perf_counter()
    with torch.no_grad():
        depths, model_fps = model.infer_video_depth(
            frames,
            target_fps=fps,
            input_size=args.input_size,
            device=device,
            fp32=True,
        )
    inference_seconds = time.perf_counter() - inference_started
    if len(depths) != frame_count or not math.isclose(
        float(model_fps), fps, abs_tol=0.01
    ):
        raise RuntimeError(
            f"model returned {len(depths)} frames at {model_fps} fps; expected {frame_count} at {fps}"
        )

    normalized, depth_low, depth_high = normalize_depth(depths)
    encode_blurred_video(args, normalized, source_size, fps, start_frame, frame_count)
    total_seconds = time.perf_counter() - started
    record = {
        "model": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "checkpoint_sha256": checkpoint_hash,
        "device": device,
        "input": str(args.input),
        "output": str(args.output),
        "source_start_seconds": args.start_seconds,
        "source_duration_seconds": args.duration_seconds,
        "source_start_frame": start_frame,
        "frame_count": frame_count,
        "source_width": source_size[0],
        "source_height": source_size[1],
        "source_fps": fps,
        "inference_width": int(frames.shape[2]),
        "inference_height": int(frames.shape[1]),
        "model_input_size": args.input_size,
        "depth_percentiles": {"p02": depth_low, "p98": depth_high},
        "blur_sigma_source_pixels": args.blur_sigma,
        "foreground_center": args.foreground_center,
        "foreground_feather": args.foreground_feather,
        "mask_softness_source_pixels": args.mask_softness_px,
        "model_load_seconds": model_load_seconds,
        "model_inference_seconds": inference_seconds,
        "model_inference_seconds_per_frame": inference_seconds / frame_count,
        "processing_wall_seconds": total_seconds,
    }
    args.metrics.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
