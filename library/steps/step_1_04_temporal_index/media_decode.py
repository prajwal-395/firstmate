"""Decode one clip into the frame representations used by temporal indexing.

The temporal index keeps its existing sampling rates, sizes, pixel formats,
and filters. A single ffmpeg filter graph splits the decoded source frames to
those consumers, avoiding a full source decode per measurement.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DecodedMedia:
    scene_stderr: str
    motion_gray: str
    flow_gray: str
    face_rgb: str
    vision_jpegs: tuple[str, ...]
    hue_rgb: str
    face_width: int
    face_height: int
    _scratch: tempfile.TemporaryDirectory

    def close(self) -> None:
        self._scratch.cleanup()


def decode_temporal_media(
    video_path: str,
    face_width: int,
    face_height: int,
    duration: float,
) -> DecodedMedia | None:
    """Fan one source decode out to all clip-wide video measurements.

    A failed shared decode yields ``None`` so the caller can use the
    established independent paths. The caller closes the returned result
    after all measurements have consumed its scratch representations.
    """
    scratch = tempfile.TemporaryDirectory(prefix="ren-temporal-media-")
    root = Path(scratch.name)
    vision_dir = root / "vision"
    vision_dir.mkdir()
    paths = {
        "motion": root / "motion.gray",
        "flow": root / "flow.gray",
        "face": root / "face.rgb",
        "hue": root / "hue.rgb",
        "vision": vision_dir / "f_%05d.jpg",
    }
    graph = ";".join((
        "[0:v:0]split=6[scene_in][motion_in][flow_in][face_in]"
        "[vision_in][hue_in]",
        "[scene_in]select='gt(scene,0.3)',metadata=print,nullsink",
        "[motion_in]fps=30,scale=160:90,format=gray[motion]",
        "[flow_in]fps=5,scale=160:90,format=gray[flow]",
        f"[face_in]fps=5,scale={face_width}:{face_height}[face]",
        f"[vision_in]fps=5,scale={face_width}:{face_height}[vision]",
        "[hue_in]fps=1,scale=80:45[hue]",
    ))
    command = [
        "ffmpeg", "-i", video_path,
        "-filter_complex", graph,
        "-map", "[motion]", "-f", "rawvideo", "-pix_fmt", "gray",
        str(paths["motion"]),
        "-map", "[flow]", "-f", "rawvideo", "-pix_fmt", "gray",
        str(paths["flow"]),
        "-map", "[face]", "-f", "rawvideo", "-pix_fmt", "rgb24",
        str(paths["face"]),
        "-map", "[vision]", "-f", "image2", "-q:v", "2",
        str(paths["vision"]),
        "-map", "[hue]", "-f", "rawvideo", "-pix_fmt", "rgb24",
        str(paths["hue"]),
    ]
    timeout = max(600.0, float(duration) * 1.5 + 120.0)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"  WARNING: shared video decode failed ({error}); "
              "using individual measurements", file=sys.stderr)
        scratch.cleanup()
        return None

    required = (paths["motion"], paths["flow"], paths["face"], paths["hue"])
    if result.returncode != 0 or any(not path.is_file() for path in required):
        detail = result.stderr.decode("utf-8", errors="replace")[-500:]
        print("  WARNING: shared video decode failed: "
              f"{detail}; using individual measurements",
              file=sys.stderr)
        scratch.cleanup()
        return None

    vision_paths = tuple(sorted(
        str(path) for path in vision_dir.iterdir()
        if path.suffix.lower() == ".jpg"
    ))
    if not vision_paths:
        print("  WARNING: shared video decode produced no Vision samples; "
              "using individual measurements", file=sys.stderr)
        scratch.cleanup()
        return None
    return DecodedMedia(
        scene_stderr=result.stderr.decode("utf-8", errors="replace"),
        motion_gray=str(paths["motion"]),
        flow_gray=str(paths["flow"]),
        face_rgb=str(paths["face"]),
        vision_jpegs=vision_paths,
        hue_rgb=str(paths["hue"]),
        face_width=face_width,
        face_height=face_height,
        _scratch=scratch,
    )
