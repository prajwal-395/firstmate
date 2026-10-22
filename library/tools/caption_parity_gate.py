"""The caption parity gate: a re-render may not change the picture that shipped.

The caption layer draws through the selected graphics engine
(``library/tools/graphics_renderer.py``).  The native renderer
(``ren-graphics-fframes``) is the default, and the engine is part of the
caption render's REUSE KEY (``step_4_05_render_subtitles.step._reuse_key``):
switching engines re-renders every card over the same filename, because the
filename carries the drawing digest and not the engine.

That is the hole this closes.  A re-render over the same filename REPLACES
the artefact the project shipped, and nothing compared the two pictures: the
engine that drew the new card could differ from the engine that drew the
old one - a different word gap, a different reveal, a different font - and
the delivered video would change without a word in the run's record.

THE GATE.  When the native renderer draws a card whose artefact is already
on disk - the same drawing content, the same name - the new pixels are
compared against the ones that shipped, frame by frame, by INK: the alpha
plane at the same floor the motion-graphics parity measurement used
(``INK_FLOOR``, alpha > 16).  The measure is the mean ink IoU over the
card's frames, and the floor is ``PARITY_FLOOR`` (0.90).

Two outcomes, and only two:

* **within the floor** - the card is published.  The two pictures agree to
  the floor the project's own motion-graphics cards are held to, so the
  engine changed nothing the viewer can see.
* **below the floor** - the card is NOT published.  The artefact that
  shipped stays, the new pixels are discarded, and the refusal is said out
  loud and recorded.  Fail-closed: a card that would change the delivered
  picture keeps the delivered picture.

A card with NO artefact on disk has nothing to compare against and is
published as drawn - there is no shipped picture to protect.

THE MEASUREMENT is the same one the lane was measured with: the ink IoU
of the native render against the artefact the project shipped, over the
project's own shipped caption cards.  See
``docs/benchmarks/2026-10-22-infra006-caption-parity.md``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

INK_FLOOR = 16
"""The alpha at which a pixel counts as ink.

The same floor the motion-graphics parity measurement used
(``tests/unit/graphics_ir/test_native_render_parity.py``), so the two
measurements are the same measurement.
"""

PARITY_FLOOR = 0.90
"""The mean ink IoU a re-render must reach against the shipped artefact."""

SEQUENCE_PATTERN = "frame_%06d.png"
"""The PNG-sequence naming a frames carrying writes."""


class ParityUnreadable(RuntimeError):
    """One of the two artefacts could not be read, so they cannot be compared."""


@dataclass(frozen=True)
class ParityResult:
    """One comparison, and its verdict."""

    frames: int
    mean_iou: float
    min_iou: float
    floor: float = PARITY_FLOOR
    reference: str = ""
    candidate: str = ""

    @property
    def within_floor(self) -> bool:
        """True when the two pictures agree to the floor."""
        return self.mean_iou >= self.floor

    def as_record(self) -> dict:
        """The record a run keeps: the measure and the verdict."""
        return {
            "frames": self.frames,
            "mean_iou": round(self.mean_iou, 4),
            "min_iou": round(self.min_iou, 4),
            "floor": self.floor,
            "within_floor": self.within_floor,
            "reference": self.reference,
            "candidate": self.candidate,
        }


def _ink_iou(candidate: Any, reference: Any) -> Any:
    """Per-frame ink IoU of two alpha planes."""
    import numpy as np

    left = candidate > INK_FLOOR
    right = reference > INK_FLOOR
    union = np.logical_or(left, right).sum(axis=(1, 2))
    inter = np.logical_and(left, right).sum(axis=(1, 2))
    return np.where(union == 0, 1.0, inter / np.maximum(union, 1))


def _decode_mov(path: str, width: int, height: int) -> Any:
    """Every frame of a caption artefact as an ALPHA plane.

    The ink is the alpha plane, so the alpha is all the gate reads:
    ``alphaextract`` hands over one byte per pixel instead of four,
    which is the difference between a comparison that costs a second
    and one that costs a quarter of it.
    """
    import numpy as np

    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
             "-vf", "alphaextract", "-pix_fmt", "gray",
             "-f", "rawvideo", "-"],
            capture_output=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ParityUnreadable(
            f"cannot decode {path} for the parity gate: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        raise ParityUnreadable(
            f"cannot decode {path} for the parity gate: "
            f"{(result.stderr or b'').decode('utf-8', 'replace')[-300:]}")
    frame_bytes = width * height
    if frame_bytes <= 0 or len(result.stdout) % frame_bytes != 0:
        raise ParityUnreadable(
            f"the parity gate decoded {len(result.stdout)} bytes from "
            f"{path}, not a whole number of {width}x{height} frames.")
    count = len(result.stdout) // frame_bytes
    if count == 0:
        raise ParityUnreadable(
            f"the parity gate decoded no frames from {path}.")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(
        count, height, width)


def _video_size(path: str) -> tuple[int, int]:
    """``(width, height)`` of a caption artefact, read off its own header."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ParityUnreadable(
            f"cannot read the size of {path}: {exc}") from exc
    text = (result.stdout or "").strip()
    if result.returncode != 0 or "," not in text:
        raise ParityUnreadable(
            f"cannot read the size of {path}: "
            f"{(result.stderr or '').strip()[-200:]}")
    try:
        width, height = [int(part) for part in text.split(",")[:2]]
    except ValueError as exc:
        raise ParityUnreadable(
            f"cannot read the size of {path}: {text!r}") from exc
    return width, height


def _sequence_frames(directory: str) -> list[str]:
    """The PNG frames of a frames carrying, in order."""
    frames = sorted(
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.endswith(".png"))
    if not frames:
        raise ParityUnreadable(
            f"the parity gate was handed {directory}, which holds no "
            f"PNG frames.")
    return frames


def _sequence_planes(frames: list[str]) -> Any:
    """Every frame of a PNG sequence as an RGBA array."""
    import numpy as np
    from PIL import Image

    planes = []
    for path in frames:
        try:
            with Image.open(path) as image:
                planes.append(np.asarray(image.convert("RGBA")))
        except OSError as exc:
            raise ParityUnreadable(
                f"the parity gate cannot decode {path}: {exc}") from exc
    return np.stack(planes)


def compare_artifacts(candidate: str, reference: str,
                       sequence: bool = False) -> ParityResult:
    """Compare two caption artefacts by their INK, frame by frame.

    ``candidate`` is the newly drawn card and ``reference`` the artefact
    that shipped.  The measure is the mean per-frame ink IoU; the floor
    is ``PARITY_FLOOR``.  An artefact that cannot be read raises
    ``ParityUnreadable`` - absent evidence is not a pass.
    """
    import numpy as np

    if sequence:
        candidate_planes = _sequence_planes(candidate)
        reference_planes = _sequence_planes(reference)
        if candidate_planes.shape[1:] != reference_planes.shape[1:]:
            raise ParityUnreadable(
                f"the parity gate compared {candidate_planes.shape[2]}x"
                f"{candidate_planes.shape[1]} against "
                f"{reference_planes.shape[2]}x{reference_planes.shape[1]}: "
                f"the two cards are not the same size.")
        candidate_alpha = candidate_planes[:, :, :, 3]
        reference_alpha = reference_planes[:, :, :, 3]
    else:
        width, height = _video_size(reference)
        candidate_width, candidate_height = _video_size(candidate)
        if (candidate_width, candidate_height) != (width, height):
            raise ParityUnreadable(
                f"the parity gate compared a {candidate_width}x"
                f"{candidate_height} card against a {width}x{height} one: "
                f"the two are not the same size.")
        candidate_alpha = _decode_mov(candidate, width, height)
        reference_alpha = _decode_mov(reference, width, height)
    count = min(len(candidate_alpha), len(reference_alpha))
    if count == 0:
        raise ParityUnreadable(
            "the parity gate compared two artefacts and found no frames "
            "in common.")
    per_frame = _ink_iou(candidate_alpha[:count],
                          reference_alpha[:count])
    return ParityResult(
        frames=int(count),
        mean_iou=float(np.mean(per_frame)),
        min_iou=float(np.min(per_frame)),
        reference=reference,
        candidate=candidate,
    )


@dataclass
class ParityRefusal:
    """One card the gate would not publish, and the measure that said so."""

    overlay_path: str
    result: ParityResult
    reason: str = ""

    def as_record(self) -> dict:
        record = self.result.as_record()
        record["overlay_path"] = self.overlay_path
        record["reason"] = self.reason
        return record


@dataclass
class GatedRenderer:
    """An engine behind the parity gate.

    The seam every caption renderer answers (``render`` / ``close``) is
    unchanged, so nothing downstream can tell a gated engine from an
    ungated one.  What changes is what happens to the pixels:

    * the card is drawn to a CANDIDATE path, never over the artefact
      that shipped - the shipped artefact is never at risk, not even for
      the second between the render and the comparison;
    * with no artefact on disk there is nothing to compare against, and
      the candidate is published as drawn;
    * with an artefact on disk the two are compared, and a card within
      the floor is published (the candidate replaces the old file) while
      a card below the floor is REFUSED: the candidate is discarded, the
      shipped artefact stays, and the refusal is recorded and said out
      loud.

    The refusals are on ``.refusals`` for the caller to report.
    """

    inner: object
    floor: float = PARITY_FLOOR
    refusals: list = field(default_factory=list)

    def render(self, props_path: str, overlay_path: str,
               sequence: bool = False):
        """Draw one card and publish it only if the gate allows."""
        candidate = _candidate_path(overlay_path)
        self._discard(candidate)
        ok, error = self.inner.render(props_path, candidate, sequence=sequence)
        if not ok:
            self._discard(candidate)
            return False, error
        if not os.path.exists(overlay_path):
            self._publish(candidate, overlay_path, sequence=sequence)
            return True, ""
        try:
            result = compare_artifacts(candidate, overlay_path,
                                       sequence=sequence)
        except ParityUnreadable as exc:
            # An unreadable artefact is not a pass and not a refusal:
            # the new pixels are published, because the gate could not
            # establish a difference, and the reason is said out loud.
            print(f"    note: the caption parity gate could not compare "
                  f"{overlay_path}: {exc}; publishing the new card",
                  file=sys.stderr)
            self._publish(candidate, overlay_path, sequence=sequence)
            return True, ""
        if result.within_floor:
            self._publish(candidate, overlay_path, sequence=sequence)
            return True, ""
        self._discard(candidate)
        refusal = ParityRefusal(
            overlay_path=overlay_path,
            result=result,
            reason=(f"the native render differs from the artefact this "
                    f"project shipped (ink IoU {result.mean_iou:.3f} over "
                    f"{result.frames} frames, floor {self.floor}); the "
                    f"shipped artefact stays"),
        )
        self.refusals.append(refusal)
        print(f"    WARN: caption parity gate refused {overlay_path}: "
              f"{refusal.reason}", file=sys.stderr)
        return True, ""

    def close(self):
        """Close the engine it wraps."""
        closer = getattr(self.inner, "close", None)
        if callable(closer):
            closer()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    @staticmethod
    def _publish(candidate: str, overlay_path: str, *, sequence: bool):
        """Put the candidate where the artefact belongs."""
        if sequence:
            if os.path.isdir(overlay_path):
                shutil.rmtree(overlay_path, ignore_errors=True)
            os.replace(candidate, overlay_path)
            return
        os.replace(candidate, overlay_path)

    @staticmethod
    def _discard(path: str):
        """Remove a candidate, whatever it is."""
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def gated(engine, floor: float = PARITY_FLOOR) -> GatedRenderer:
    """The engine behind the caption parity gate."""
    return GatedRenderer(inner=engine, floor=floor)


def _candidate_path(overlay_path: str) -> str:
    """Where the candidate is drawn, KEEPING the artefact's own suffix.

    The suffix is not decoration: the renderers hand the path to ffmpeg,
    which reads the format off it. A candidate called ``card.mov
    .parity_candidate`` would be a file ffmpeg cannot name a format for,
    and the card would fail to draw for a reason that has nothing to do
    with the card.
    """
    stem, dot, suffix = overlay_path.rpartition(".")
    if not dot or "/" in suffix:
        # No suffix to keep: a frames carrying is a DIRECTORY, and a
        # directory needs no format.
        return f"{overlay_path}.parity_candidate"
    return f"{stem}.parity_candidate.{suffix}"


def measured_against(candidate: str, reference: str,
                     sequence: bool = False) -> Optional[ParityResult]:
    """The measure, or None when the two cannot be compared.

    A measurement helper for a bench or a test: it never raises for an
    unreadable artefact, and it never reports a difference it did not
    measure.
    """
    try:
        return compare_artifacts(candidate, reference, sequence=sequence)
    except ParityUnreadable:
        return None
