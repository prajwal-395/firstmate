"""Gap 1: composite EffectCraft treatments into the FilmCraft render.

Trial gap 1 (``data/vep-native-real-reel-trial/`` in firstmate's home):
the native FilmCraft render matched the Resolve reel at only SSIM Y 0.40
because it carried no grade, no Fusion-equivalent treatments and no
tv_frame bezel. The two engine halves already project their share -
EffectCraft maps ``color.grade``/``blur.gaussian``/``color.saturation``,
``move.*`` and fades (``library/tools/project_model_effectcraft.py``),
FilmCraft executes LUTs, keyframed transforms and volume
(``native/ren-filmcraft/src/adapter.rs``) - but nothing ties them into
one render. This module is that tie: it resolves WHAT to composite from
the project's own declarations, mints the model effects the engines
already execute, and composites the renders with ffmpeg.

Fail-closed rules:

- Every value is read off a project declaration; nothing here carries a
  look strength, a bezel path, a punch-in factor or a grade number.
  A project that declares no ``style.series_look`` gets no grade, and a
  project that declares no tv_frame asset gets no bezel - there is no
  fallback bezel, because a fallback bezel is the house look the
  captain refused (``library/tools/tv_frame.py``).
- An element with no measured native route is REFUSED by name, never
  approximated: glow/grain/vignette/defocus cite the measured sweep in
  ``REFUSED_EFFECT_COVERAGE``; a non-identity CDL power term has no
  grade counterpart and refuses; a DRX power grade has no LUT file and
  refuses as unmapped rather than inventing one.
- The engine route is the shipped grade path (minted ``color.grade``
  model effects through the verified EffectCraft law, ``color.lut``
  through FilmCraft where the declaration names a LUT file). The ffmpeg
  ``eq``/``colorchannelmixer`` filter is the MEASUREMENT proxy only -
  a first-order picture check that the declared values move SSIM, not
  the grade itself. Both read the same resolved grade dict.
- Every ``subprocess.run`` capturing text passes ``encoding="utf-8"``.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

try:
    from library.tools import series_look, tv_frame
    from library.tools.project_model_effectcraft import (
        GRADE_NEUTRAL,
        REFUSED_EFFECT_COVERAGE,
    )
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools import series_look, tv_frame
    from tools.project_model_effectcraft import (
        GRADE_NEUTRAL,
        REFUSED_EFFECT_COVERAGE,
    )

#: Model effect classes the FilmCraft adapter executes natively. Mirrors
#: ``filmcraft_executed`` in ``native/ren-filmcraft/src/adapter.rs``;
#: ``tests/unit/context/test_native_effects_composite.py`` trips when the
#: two drift apart.
FILMCRAFT_EXECUTED_CLASSES = frozenset({
    "color.lut",
    "transform.position",
    "transform.scale",
    "transform.rotation",
    "transform.crop",
    "opacity.opacity",
    "audio.volume",
})

#: Model effect classes the EffectCraft adapter carries as transform
#: animation rather than engine effects. Mirrors
#: ``MOVE_CLASS_IDS`` in
#: ``library/tools/project_model_effectcraft_moves.py``.
MOVE_CLASS_PREFIX = "move."

#: Model effect classes the EffectCraft adapter carries as opacity keys.
#: Mirrors ``FADE_CLASS_IDS`` in
#: ``library/tools/project_model_effectcraft.py``.
FADE_CLASS_IDS = frozenset({"fade.opacity", "transition.fade_to_black"})


def _mean(values: Any, what: str) -> float:
    items = list(values)
    if len(items) != 3:
        raise ValueError(f"{what} must be a 3-channel triple, got {values!r}")
    numbers = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise TypeError(f"{what} must be numbers, got {values!r}")
        value = float(item)
        if not math.isfinite(value):
            raise ValueError(f"{what} must be finite, got {values!r}")
        numbers.append(value)
    return sum(numbers) / 3.0


def resolve_native_grade(
    project_folder: str | None,
    template_style: Any = None,
) -> dict[str, Any] | None:
    """The grade this run composites, read off project declarations.

    Precedence is project over template
    (``series_look.effective_series_look``): the whole slot is replaced,
    never merged. Returns None when neither declares a look. Otherwise
    returns ``{"name", "grade": {"gain", "contrast", "brightness",
    "saturation"}, "refused": [...]}`` where ``grade`` is the
    ``color.grade`` parameter dict the EffectCraft law reads
    (``_project_grade_params``: slope = gain * (1 + contrast),
    offset = brightness - 0.5 * contrast) and ``refused`` names every
    declared element with no native route and its measured reason.

    Absent axes read neutral (``GRADE_NEUTRAL``) - neutrality draws
    nothing, so there is nothing to substitute. A non-identity CDL
    power term refuses: the grade law has no power counterpart and
    folding it into gain would invent a grade.
    """
    declaration = series_look.effective_series_look(
        template_style or {}, project_folder)
    look = series_look.resolve_look(declaration)
    if look is None:
        return None
    refused: list[dict[str, str]] = []
    if look.power is not None and any(
            channel != 1.0 for channel in look.power):
        refused.append({
            "element": "cdl.power",
            "reason": (
                f"look {look.name!r} declares cdl.power "
                f"{list(look.power)!r}: the native color.grade law "
                "carries gain/contrast/brightness/saturation only, so "
                "a non-identity power term has no counterpart and "
                "refuses rather than folding into gain"
            ),
        })
    for element, class_id in (
            ("glow", "glow.soft"),
            ("grain", "grain.film"),
            ("vignette", "vignette.ellipse")):
        if element in (look.declared or ()):
            refused.append({
                "element": element,
                "reason": REFUSED_EFFECT_COVERAGE.get(
                    class_id,
                    f"Ren effect class {class_id!r} has no verified "
                    "EffectCraft mapping"),
            })
    grade = {
        "gain": _mean(look.slope, "cdl.slope") if look.slope else GRADE_NEUTRAL["gain"],
        "contrast": float(look.contrast) if look.contrast is not None else GRADE_NEUTRAL["contrast"],
        "brightness": _mean(look.offset, "cdl.offset") if look.offset else GRADE_NEUTRAL["brightness"],
        "saturation": float(look.saturation) if look.saturation is not None else GRADE_NEUTRAL["saturation"],
    }
    for key, value in grade.items():
        if not math.isfinite(value):
            raise ValueError(
                f"look {look.name!r} resolves {key} off the finite range: {value!r}")
    return {"name": look.name, "grade": grade, "refused": refused}


def mint_grade_effects(
    resolved_grade: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    effect_id_prefix: str = "native-grade",
) -> list[dict[str, Any]]:
    """One ``color.grade`` model effect per target clip use.

    ``targets`` are ``{"clip_use_id", "clock_id", "start", "end"}``
    mappings - the target's own timeline interval, so the effect spans
    exactly the clip it grades. Parameters are the resolved grade dict
    untouched: no per-clip adjustment, because per-clip correction is
    step 5.01's measurement to make, not this module's.
    """
    grade = resolved_grade["grade"]
    effects = []
    for index, target in enumerate(targets):
        for key in ("clip_use_id", "clock_id", "start", "end"):
            if key not in target:
                raise ValueError(
                    f"grade target {index} names no {key!r}: {target!r}")
        if target["end"] <= target["start"]:
            raise ValueError(
                f"grade target {target['clip_use_id']!r} spans no time")
        effects.append({
            "id": f"{effect_id_prefix}-{index:03d}",
            "class_id": "color.grade",
            "target_id": target["clip_use_id"],
            "interval": {
                "clock_id": target["clock_id"],
                "start": target["start"],
                "end": target["end"],
            },
            "parameters": dict(grade),
            "keyframes": [],
        })
    return effects


def resolve_native_bezel(
    project_folder: str | None,
    template: Any = None,
) -> dict[str, Any] | None:
    """The tv_frame bezel this run composites, or None for no look.

    Pass-through to ``tv_frame.resolve_tv_frame``: project over
    template, None when no asset is declared anywhere, raise on a
    malformed declaration. The asset path, punch-in, scale and offset
    are the declaration's own - this module stages them, never edits
    them.
    """
    return tv_frame.resolve_tv_frame(project_folder, template)


def mint_punchin_effects(
    bezel: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    effect_id_prefix: str = "native-punchin",
) -> list[dict[str, Any]]:
    """One ``transform.scale`` model effect per target clip use.

    The V1 zoom the footage plays under the frame:
    ``tv_frame.v1_zoom_for_look`` - the declared punch-in times the
    declared scale, ABSOLUTE rather than multiplied over the conform
    fill, because under the frame the bezel is the framing. The zoom
    rides model keyframes at the interval's start and end ticks (the
    FilmCraft adapter expresses keyframed picture values through
    keyframes only - static parameters refuse there), so a one-tick
    target mints a single key. Targets are the same
    ``{"clip_use_id", "clock_id", "start", "end"}`` mappings
    :func:`mint_grade_effects` takes.
    """
    zoom = tv_frame.v1_zoom_for_look(bezel["punch_in"], bezel.get("scale", 1.0))
    if not math.isfinite(zoom) or zoom <= 0:
        raise ValueError(f"declared punch-in resolves off the usable range: {zoom!r}")
    effects = []
    for index, target in enumerate(targets):
        for key in ("clip_use_id", "clock_id", "start", "end"):
            if key not in target:
                raise ValueError(
                    f"punch-in target {index} names no {key!r}: {target!r}")
        if target["end"] <= target["start"]:
            raise ValueError(
                f"punch-in target {target['clip_use_id']!r} spans no time")
        ticks = sorted({target["start"], target["end"] - 1})
        effects.append({
            "id": f"{effect_id_prefix}-{index:03d}",
            "class_id": "transform.scale",
            "target_id": target["clip_use_id"],
            "interval": {
                "clock_id": target["clock_id"],
                "start": target["start"],
                "end": target["end"],
            },
            "parameters": {},
            "keyframes": [{"tick": tick, "value": zoom} for tick in ticks],
        })
    return effects


def partition_effect_classes(class_ids: list[str]) -> dict[str, list[str]]:
    """Route model effect classes to the engine that executes them.

    - ``filmcraft``: classes ``filmcraft_executed`` owns (LUT,
      keyframed transforms, volume).
    - ``effectcraft``: classes the verified EffectCraft map, the
      ``move.*`` transforms and the fade opacity keys own.
    - ``unmapped``: everything else - refused by name downstream,
      never guessed.

    ``color.grade`` routes to EffectCraft (the verified OCIO CDL law),
    not to FilmCraft: the FilmCraft adapter reports it under
    ``effect_not_projected`` because its LUT path needs a LUT file,
    not grade numbers.
    """
    try:
        from library.tools.project_model_effectcraft import VERIFIED_EFFECT_MAP
    except ImportError:
        from tools.project_model_effectcraft import VERIFIED_EFFECT_MAP
    routed: dict[str, list[str]] = {
        "filmcraft": [], "effectcraft": [], "unmapped": []}
    for class_id in class_ids:
        if class_id in FILMCRAFT_EXECUTED_CLASSES:
            routed["filmcraft"].append(class_id)
        elif (class_id in VERIFIED_EFFECT_MAP
                or class_id.startswith(MOVE_CLASS_PREFIX)
                or class_id in FADE_CLASS_IDS):
            routed["effectcraft"].append(class_id)
        else:
            routed["unmapped"].append(class_id)
    return routed


def grade_ffmpeg_filter(grade: dict[str, float]) -> str:
    """The measurement-proxy ffmpeg filter for a resolved grade dict.

    Scalar gain rides ``colorchannelmixer`` (equal channels, so hue is
    untouched); brightness offset, contrast pivot and saturation ride
    ``eq``. First-order only: the shipped grade is the minted
    ``color.grade`` model effect through the verified EffectCraft law,
    and this filter exists to measure that the declared values move
    the picture, not to deliver the look.
    """
    gain = float(grade.get("gain", 1.0))
    contrast = float(grade.get("contrast", 0.0))
    brightness = float(grade.get("brightness", 0.0))
    saturation = float(grade.get("saturation", 1.0))
    for key, value in (
            ("gain", gain), ("contrast", contrast),
            ("brightness", brightness), ("saturation", saturation)):
        if not math.isfinite(value):
            raise ValueError(f"grade {key} is not finite: {value!r}")
    return (
        f"colorchannelmixer=rr={gain}:gg={gain}:bb={gain},"
        f"eq=brightness={brightness}:contrast={1.0 + contrast}"
        f":saturation={saturation}"
    )


def composite_native(
    base_mp4: str | Path,
    out_mp4: str | Path,
    *,
    grade: dict[str, float] | None = None,
    bezel_asset: str | Path | None = None,
    bezel_scale: float = 1.0,
    bezel_offset_y: int = 0,
    bezel_rotate: Any = None,
) -> dict[str, Any]:
    """Composite treatments over a FilmCraft base render with ffmpeg.

    - ``grade`` (a resolved grade dict) applies the measurement-proxy
      filter; None applies no grade.
    - ``bezel_asset`` (a declared tv_frame PNG) is turned, cover-drawn
      and shrunk exactly as ``reel_look.frame_overlay_segments`` draws
      it - rotation from ``bezel_rotate`` (the resolved look's
      ``rotate``, ``"auto"`` when None), drawn size from
      ``tv_frame.cover_size`` over the turned asset, the declared
      ``bezel_scale`` shrunk inside the same canvas over opaque black
      - then overlaid at the declared ``bezel_offset_y``; None
      overlays no bezel.
    - Both together apply the grade first, then the bezel over the
      graded picture - the Resolve order (grade on V1, frame on V2).

    Returns ``{"out", "filter", "grade_applied", "bezel_applied"}``.
    Refuses when the base or the bezel asset is absent, and when a
    bezel scale is outside (0, 1].
    """
    base = Path(base_mp4)
    if not base.is_file():
        raise FileNotFoundError(f"native base render is absent: {base}")
    if bezel_asset is not None:
        asset = Path(bezel_asset)
        if not asset.is_file():
            raise FileNotFoundError(
                f"declared tv_frame asset is absent: {asset}")
        if (isinstance(bezel_scale, bool)
                or not isinstance(bezel_scale, (int, float))
                or not 0.0 < float(bezel_scale) <= 1.0):
            raise ValueError(
                f"bezel scale must be above 0 and at most 1, got {bezel_scale!r}")
    out = Path(out_mp4)
    out.parent.mkdir(parents=True, exist_ok=True)

    label = "[0:v]"
    filters = []
    if grade is not None:
        filters.append(f"{label}{grade_ffmpeg_filter(grade)}[graded]")
        label = "[graded]"
    if bezel_asset is not None:
        width, height = _probe_dimensions(base)
        bezel_chain = _bezel_filter(
            Path(bezel_asset), bezel_scale, width, height,
            look_rotate=tv_frame.AUTO_ROTATE if bezel_rotate is None else bezel_rotate)
        filters.append(
            f"[1:v]{bezel_chain}[bezel];"
            f"{label}[bezel]overlay="
            f"x=(W-w)/2:y=(H-h)/2+{int(bezel_offset_y)}[out]")
        inputs = [str(base), str(bezel_asset)]
    else:
        filters.append(f"{label}null[out]")
        inputs = [str(base)]
    command = ["ffmpeg", "-v", "error", "-y"]
    for path in inputs:
        command += ["-i", path]
    command += [
        "-filter_complex", ";".join(filters),
        "-map", "[out]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-c:a", "aac", str(out),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode != 0 or not out.is_file():
        raise RuntimeError(
            f"native composite render failed: {completed.stderr.strip()[:500]}")
    return {
        "out": str(out),
        "filter": ";".join(filters),
        "grade_applied": grade is not None,
        "bezel_applied": bezel_asset is not None,
    }


def _probe_dimensions(path: Path) -> tuple[int, int]:
    """The base render's own (width, height): the bezel covers these."""
    command = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ffprobe could not read {path}: {completed.stderr.strip()[:300]}")
    streams = json.loads(completed.stdout or "{}").get("streams", [])
    if not streams:
        raise RuntimeError(f"ffprobe found no video stream in {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def _bezel_filter(
    asset: Path,
    scale: float,
    frame_width: int,
    frame_height: int,
    *,
    look_rotate: Any,
) -> str:
    """The ffmpeg filter drawing a declared tv_frame asset over delivery.

    Mirrors ``reel_look.frame_overlay_segments`` without the imaging
    stack: the landscape asset turns upright for a portrait delivery
    (``tv_frame.applied_rotation``), draws at the COVER size
    (``tv_frame.cover_size``), and a scaled look shrinks inside the
    same canvas over opaque black rather than zooming the overlay
    (which would uncover the frame's edges). Rotation mapping follows
    the PIL branch there: 90 draws as 90 CW (``transpose=1``),
    270 as 90 CCW (``transpose=2``), 180 as both.
    """
    asset_width, asset_height = _probe_dimensions(asset)
    rotation = tv_frame.applied_rotation(
        {"rotate": look_rotate}, (asset_width, asset_height),
        frame_width, frame_height)
    oriented = tv_frame.oriented_size((asset_width, asset_height), rotation)
    drawn_width, drawn_height = tv_frame.cover_size(
        oriented, frame_width, frame_height)
    stages = []
    if int(rotation) % 360 == 90:
        stages.append("transpose=1")
    elif int(rotation) % 360 == 270:
        stages.append("transpose=2")
    elif int(rotation) % 360 == 180:
        stages.append("transpose=2,transpose=2")
    elif int(rotation) % 360 != 0:
        raise ValueError(
            f"tv_frame rotation {rotation!r} is not a right angle; "
            "only right-angle rotations preserve the frame's pixels")
    stages.append(f"scale={drawn_width}:{drawn_height}:flags=lanczos")
    shrink = float(scale)
    if shrink != 1.0:
        stages.append(
            f"scale=trunc(iw*{shrink}/2)*2:trunc(ih*{shrink}/2)*2,"
            f"pad={drawn_width}:{drawn_height}:(ow-iw)/2:(oh-ih)/2:color=black")
    # The drawn canvas covers the delivery by construction, but even
    # rounding can leave a pixel either side; conform exactly as the
    # Resolve placer does (cover, never fit).
    stages.append(
        f"scale={frame_width}:{frame_height}:force_original_aspect_ratio=increase,"
        f"crop={frame_width}:{frame_height}")
    return ",".join(stages)


def measure_ssim(
    first_mp4: str | Path,
    second_mp4: str | Path,
    *,
    frames: int | None = None,
) -> dict[str, float]:
    """Whole-file (or first-``frames``) SSIM between two renders.

    Trims both inputs to the same head range so a short proof never
    renders the whole reel. Returns the Y/U/V/All averages ffmpeg
    prints. Both files must exist; ``frames`` must be positive.
    """
    for path in (Path(first_mp4), Path(second_mp4)):
        if not path.is_file():
            raise FileNotFoundError(f"SSIM input is absent: {path}")
    if frames is not None and (not isinstance(frames, int) or frames <= 0):
        raise ValueError(f"SSIM frame count must be positive, got {frames!r}")
    head = f"trim=end_frame={frames}," if frames else ""
    command = [
        "ffmpeg", "-v", "info",
        "-i", str(first_mp4), "-i", str(second_mp4),
        "-filter_complex",
        (f"[0:v]{head}setpts=PTS-STARTPTS[b];"
         f"[1:v]{head}setpts=PTS-STARTPTS[c];[b][c]ssim"),
        "-f", "null", "-",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    combined = (completed.stderr or "") + "\n" + (completed.stdout or "")
    scores: dict[str, float] = {}
    for line in combined.splitlines():
        if "SSIM" not in line:
            continue
        for key in ("Y", "U", "V", "All"):
            marker = f"{key}:"
            if marker in line:
                tail = line.split(marker, 1)[1].strip().split()[0]
                try:
                    scores[key] = float(tail)
                except ValueError:
                    continue
    if "Y" not in scores:
        raise RuntimeError(
            f"ffmpeg printed no SSIM Y score: {combined.strip()[-500:]}")
    return scores


def composite_receipt(
    *,
    project_folder: str | None,
    grade_name: str | None,
    grade_applied: bool,
    bezel_applied: bool,
    refused: list[dict[str, str]],
    before: dict[str, float],
    after: dict[str, float],
) -> dict[str, Any]:
    """The run's own account of what composited and what it measured."""
    return {
        "project_folder": project_folder,
        "grade_name": grade_name,
        "grade_applied": grade_applied,
        "bezel_applied": bezel_applied,
        "refused": list(refused),
        "ssim_before_y": before.get("Y"),
        "ssim_after_y": after.get("Y"),
        "ssim_before": dict(before),
        "ssim_after": dict(after),
        "ssim_delta_y": after.get("Y", 0.0) - before.get("Y", 0.0),
    }


def receipt_json(receipt: dict[str, Any]) -> str:
    """Canonical JSON encoding of a composite receipt."""
    return json.dumps(receipt, indent=2, sort_keys=True) + "\n"
