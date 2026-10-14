"""Thin `ren project-model` entry point over the headless engine adapters.

Audit gap G8: ``project_model_effectcraft.py`` ships an argparse ``main``
but only ``ren qualification pm-e2e`` was reachable, so scouts drove
modules by path and the capability matrix had no user-facing route.
This module is that route: ``ren project-model project --engine
filmcraft|effectcraft --out <plan.json>`` plans the model onto the named
engine, writes the plan, and prints a summary - and it executes the
plan against the pinned headless CLI only with ``--execute``.

Read-only until explicit: the plan path runs no engine subprocess, takes
no resolve slot, and writes nothing but ``--out``. The project itself is
opened read-only (registry lookup with no format migration, like
``project_work_state``); ``--execute`` additionally writes the engine
project/receipt files it names in the plan and nothing else.

No second implementation of either adapter lives here. The FilmCraft
half plans through the real Rust adapter
(``native/ren-filmcraft/src/adapter.rs``) exposed on the ``ren_native``
extension: the projection, the per-edit translation, the receipt
reconciliation, the media verification and the operation-to-command
mapping all run there, so a plan this verb prints and the run the
driver executes cannot drift. A stale or missing extension is refused,
never worked around. The EffectCraft half delegates to the real
projector (``project_model_effectcraft.project_to_request``); execution
mirrors ``project_model_qualification._ec_execute`` (run, rgba render,
ingest). No axi-style reads: this verb plans and executes engine
work, it never serves live-project state.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

from library.tools import filmcraft_preconform as fcp
from library.tools.native_overlay_media import prepare_projection
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

FILMCRAFT_REVISION = "0800a8b0aa4a703ba42172f415b45a61f8d8a5e1"
FILMCRAFT_VERSION = "0.6.0"
EFFECTCRAFT_REVISION = "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
EFFECTCRAFT_VERSION = "0.7.0"

FILMCRAFT_FUNCTIONS = (
    "filmcraft_plan_projection",
    "filmcraft_plan_picture_effects",
    "filmcraft_add_tracks_operation",
    "filmcraft_plan_ren_edit",
    "filmcraft_describe_operation",
    "filmcraft_reconcile_clip_ids",
    "filmcraft_verify_media",
    "filmcraft_import_operation",
    "filmcraft_placement_operations",
    "filmcraft_plan_dual_mono_mapping",
)
"""The ``ren_native`` entry points the FilmCraft half plans through."""

EXPORT_FORMATS = {"h264": "h264", "prores": "pro_res", "wav": "wav"}
"""User-facing export names to ``ExportFormat`` JSON (serde snake_case)."""


class ProjectModelCliRefused(RenRefusal, RuntimeError):
    """This verb refused before running engine work."""

    CODE = "REN_PM_CLI_REFUSED"
    USER_MESSAGE = ("I couldn't safely project this model. Nothing ran "
                    "and nothing changed; fix what follows and retry.")
    RECOVERY_CLASS = "blocked"


def _refuse(what: str, why: str, fix: str, code: str = "") -> ProjectModelCliRefused:
    return ProjectModelCliRefused(what=what, why=why, fix=fix,
                                 code=code or ProjectModelCliRefused.CODE)


_ADAPTER_CODE = re.compile(r"FilmCraft projection refused \(([A-Za-z0-9_]+)\)")


def _adapter_code(message: str) -> str | None:
    """The adapter's own refusal code out of its Display text, if any."""
    match = _ADAPTER_CODE.search(message or "")
    return match.group(1) if match else None


def _refuse_from_adapter(action: str, exc: Exception) -> ProjectModelCliRefused:
    """Refuse with the adapter's own words and code; nothing ran."""
    message = str(exc)
    code = _adapter_code(message)
    return _refuse(f"{action}: {message[:500]}",
                   "the pinned FilmCraft adapter refused the model",
                   "fix the model per the named code in native/ren-filmcraft/src/adapter.rs"
                   + (f" (code {code})" if code else ""),
                   code=code or "REN_PM_CLI_ADAPTER_REFUSED")


# ── Project + model loading (read-only) ──────────────────────────


def resolve_project_root(ref: str) -> Path:
    """The project's root directory, read-only: no format migration.

    Mirrors ``project_work_state``: a filesystem path resolves in place,
    otherwise the registry slug index answers. Raises
    :class:`ProjectModelCliRefused` when nothing answers.
    """
    from library.tools.project_registry import resolve_project_path, scan_projects

    yaml_path = resolve_project_path(ref)
    if yaml_path is not None:
        return yaml_path.parent
    for config in scan_projects():
        if config.slug == ref:
            return Path(config.project_root)
    raise _refuse(f"no Ren project found for {ref!r}",
                  "neither a project path nor a registry slug answered",
                  "pass a project directory holding project.yaml, or a slug from `ren projects`",
                  code="REN_PM_CLI_NO_PROJECT")


def load_model(project_root: Path, model_arg: str | None) -> tuple[dict[str, Any], str]:
    """The canonical model plus where it came from.

    ``--model`` names an explicit model JSON (validated). Otherwise the
    project's own model store answers; a project with no store refuses
    with the init command rather than an empty model.
    """
    from library.tools.project_model import read_project_model

    if model_arg:
        path = Path(model_arg).expanduser()
        try:
            return read_project_model(path), str(path)
        except ValueError as exc:
            raise _refuse(f"cannot read project model {path}",
                          str(exc)[:400],
                          "pass a valid Project Model JSON, or omit --model to use the project's store",
                          code="REN_PM_CLI_BAD_MODEL") from exc
    from library.tools.project_model_store import load_project_model as load_store

    try:
        revision, model = load_store(project_root)
    except (FileNotFoundError, ValueError) as exc:
        raise _refuse(f"no canonical Project Model in {project_root}",
                      str(exc)[:400],
                      "commit one with `python3 -m library.tools.project_model_store init <project> <model.json>`, or pass --model <model.json>",
                      code="REN_PM_CLI_NO_MODEL") from exc
    return model, f"{project_root}/project_model (revision {revision})"


# ── FilmCraft through the real adapter ───────────────────────────
#
# Every function below calls the pinned Rust adapter on ren_native and
# translates only its answers into plan/receipt JSON. No placement
# math, no edit translation, no refusal codes live here.


def filmcraft_native():
    """The ren_native extension with the FilmCraft adapter, or refuse.

    Selected the way production selects it (this tree's per-tree cache
    entry); a missing or stale build is refused, never worked around
    with a Python copy of the adapter.
    """
    from ren.engine_root import find_engine_root

    from library.tools import ren_native_cache

    module = ren_native_cache.select(find_engine_root())
    if module is not None and all(hasattr(module, name) for name in FILMCRAFT_FUNCTIONS):
        return module
    raise _refuse("the ren_native extension for this tree is not built",
                  "the FilmCraft plan runs in the pinned Rust adapter, and no matching build is cached",
                  "rebuild with `ren setup` (scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>), then retry",
                  code="REN_PM_CLI_NO_NATIVE")


def plan_filmcraft_projection(model: dict, native) -> dict:
    """The adapter's own projection for this model, as a dict."""
    try:
        return native.filmcraft_plan_projection(model)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("FilmCraft projection refused", exc) from exc


def classify_filmcraft_edit(model: dict, projection: dict, edit: dict, native) -> tuple[bool, str | None]:
    """Whether --execute can translate this edit, and the adapter's code.

    Planning runs the real translation with placeholder clip ids (every
    placed use maps to id 0): parameter validation is id-agnostic, so
    the verdict is exactly what execution would get.
    """
    dummy_ids = {placement["clip_use_id"]: [0] for placement in projection["placements"]}
    try:
        native.filmcraft_plan_ren_edit(model, projection, edit, dummy_ids, "")
    except Exception as exc:  # noqa: BLE001 - the code is the verdict
        return False, _adapter_code(str(exc)) or "adapter_refused"
    return True, None


def plan_filmcraft(model: dict, native, project_root: Path | None = None, *, sequence_size: str | None = None) -> dict:
    """The plan: the adapter's projection plus per-edit executability."""
    model = _model_with_native_moves(model, sequence_size)
    projection = plan_filmcraft_projection(model, native)
    dummy_ids = {placement["clip_use_id"]: [0]
                 for placement in projection["placements"]}
    picture_effects = translate_filmcraft_picture_effects(
        model, projection, dummy_ids, "<project>", native)
    mask_commands = translate_filmcraft_masks(
        model, projection, dummy_ids, "<project>", native,
        project_root=project_root)
    edit_rows = []
    for edit in model.get("edits", []):
        executable, code = classify_filmcraft_edit(model, projection, edit, native)
        row: dict[str, Any] = {"id": edit["id"], "operation": edit.get("operation"),
                               "executable": executable}
        if code:
            row["code"] = code
        edit_rows.append(row)
    return {"engine": "filmcraft",
            "adapter_version": projection["adapter_version"],
            "engine_revision": projection["engine_revision"],
            "timeline_clock": projection["timeline_clock"],
            "imports": projection["imports"],
            "placements": projection["placements"],
            "losses": projection["losses"],
            "picture_effects": [{"command": command["command"],
                                 "params": command["params"]}
                                for command in picture_effects],
            "mask_commands": [{"command": command["command"],
                                "params": command["params"]}
                               for command in mask_commands],
            "edits": edit_rows,
            "executed": False}


def translate_filmcraft_edits(model: dict, projection: dict, clip_ids: dict[str, list[int]],
                              project: str, native) -> list[dict]:
    """Engine commands for every model edit, in model order.

    Raises with the adapter's named code before returning anything
    partial: callers translate the whole model before executing any of
    it, so an untranslatable model refuses with nothing mutated.
    """
    commands: list[dict] = []
    for edit in model.get("edits", []):
        try:
            operations = native.filmcraft_plan_ren_edit(
                model, projection, edit, clip_ids, project)
        except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
            raise _refuse_from_adapter(f"edit `{edit.get('id')}` refused", exc) from exc
        for operation in operations:
            try:
                described = native.filmcraft_describe_operation(operation)
            except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
                raise _refuse_from_adapter(
                    f"edit `{edit.get('id')}` describes no engine command", exc) from exc
            commands.append({"command": described["command"],
                             "params": described["params"],
                             "save": described["save"]})
    return commands


def translate_filmcraft_picture_effects(
        model: dict, projection: dict, clip_ids: dict[str, list[int]],
        project: str, native) -> list[dict]:
    """Translate model-owned picture transforms before the native edit writes."""
    try:
        operations = native.filmcraft_plan_picture_effects(
            model, projection, clip_ids, project)
        return [_describe(native, operation) for operation in operations]
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("model picture effect refused", exc) from exc


def translate_filmcraft_masks(
        model: dict, projection: dict, clip_ids: dict[str, list[int]],
        project: str, native, *, project_root: Path | None = None,
        memory_root: Path | None = None) -> list[dict]:
    """Seed and track source-segmented person masks on matching reel clips.

    Only SAM2/M3d raster masks bound to the exact source clock and a linked
    entity are eligible. Face boxes are observations, not person mattes, and
    are deliberately left as named projection losses. `memory_root` overrides
    the machine source-memory root for `region_rle` sidecars (tests); when
    None the shared per-machine root is used.
    """
    uses = {row["id"]: row for row in model["clip_uses"]}
    clocks = {row["id"]: row for row in model["clocks"]}
    placements = {row["clip_use_id"]: row for row in projection["placements"]}
    operations = []
    mask_indices: dict[int, int] = {}
    for mask in model["masks"]:
        representation = mask["representation"]
        kind = representation["kind"]
        if kind not in ("sam2_rle_track", "region_rle"):
            continue
        for use_id, use in uses.items():
            placement = placements.get(use_id)
            if placement is None or mask["entity_id"] not in use["entity_links"]:
                continue
            source = use["source_interval"]
            interval = mask["interval"]
            if interval["clock_id"] != source["clock_id"]:
                continue
            if not interval["start"] <= source["start"] < interval["end"]:
                continue
            clock = clocks[interval["clock_id"]]
            domain = clock["domain"]
            if (domain["kind"] != "source" or domain["asset_id"] != use["asset_id"]
                    or use["role"] not in ("a_roll", "b_roll")):
                continue
            ticks_per_second = int(clock["ticks_per_second"])
            seed_tick = round(source["start"] * 1000 / ticks_per_second)
            try:
                from library.tools import project_model_masks as masks_api
                if kind == masks_api.SAM2_RLE_TRACK_KIND:
                    width, height, samples = masks_api.sam2_track_frames(
                        representation, project_folder=project_root,
                        mask_id=mask["id"])
                    rle = masks_api.rle_frame_at(samples, seed_tick, mask["id"])
                    alpha = masks_api.decode_rle_to_alpha(rle, width, height, mask["id"])
                else:
                    width, height, samples = masks_api.region_track_frames(
                        representation, memory_root=memory_root,
                        mask_id=mask["id"])
                    runs = masks_api.region_frame_at(samples, seed_tick, mask["id"])
                    alpha = masks_api.decode_region_runs(runs, width, height, mask["id"])
                paths, agreement = _mask_contour_paths(alpha, width, height)
            except Exception as exc:  # noqa: BLE001 - refuse an unmeasured matte before execution
                raise _refuse(
                    f"mask `{mask['id']}` has no usable native seed",
                    str(exc)[:400],
                    "rebuild the named SAM2/M3d segmentation so a source-frame matte covers the clip start",
                    code="REN_PM_CLI_MASK_SEED") from exc
            if agreement < 0.92:
                raise _refuse(
                    f"mask `{mask['id']}` contour agreement is too low",
                    f"polygon seed IoU is {agreement:.4f}; FilmCraft mask path would lose too much matte area",
                    "use a denser supported mask path or improve the segmentation",
                    code="REN_PM_CLI_MASK_AGREEMENT")
            ids = clip_ids.get(use_id)
            if not ids:
                raise _refuse(f"mask `{mask['id']}` targets an unplaced use `{use_id}`",
                              "the clip use has no reconciled FilmCraft clip id",
                              "place the clip before projecting its mask",
                              code="REN_PM_CLI_MASK_TARGET")
            try:
                clock_rate = clock["frame_rate"]
                frames = round((source["end"] - source["start"])
                               * int(clock_rate["numerator"])
                               / (ticks_per_second * int(clock_rate["denominator"])))
            except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                raise _refuse(
                    f"mask `{mask['id']}` target has no usable source rate",
                    str(exc)[:300],
                    "use a video source clock with a declared frame rate",
                    code="REN_PM_CLI_MASK_RATE") from exc
            if frames <= 0:
                raise _refuse(
                    f"mask `{mask['id']}` target has no trackable source frames",
                    f"the placed source window converts to {frames} frames",
                    "use a positive, frame-aligned video source window",
                    code="REN_PM_CLI_MASK_RATE")
            mask_index = mask_indices.get(ids[0], 0)
            for path in paths:
                add = _describe(native, {"kind": "mask_add", "project": project,
                                         "clip": ids[0], "shape": "polygon",
                                         "path": path})
                track = _describe(native, {"kind": "mask_track", "project": project,
                                           "clip": ids[0], "mask": mask_index,
                                           "frames": frames,
                                           "method": "positionScaleRotation"})
                operations.extend((add, track))
                mask_index += 1
            mask_indices[ids[0]] = mask_index
    return operations


def _mask_contour_paths(alpha: bytes, width: int, height: int) -> tuple[list[list[list[float]]], float]:
    """Polygonize raster masks and return the measured union IoU."""
    import cv2
    import numpy as np

    source = np.frombuffer(alpha, dtype=np.uint8).reshape((height, width))
    contours, _ = cv2.findContours((source > 0).astype(np.uint8),
                                   cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError("segmentation seed is empty")
    paths, polygons = [], []
    for contour in contours:
        if cv2.contourArea(contour) < max(4.0, width * height * 0.00002):
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, max(1.0, perimeter * 0.002), True)
        if len(polygon) < 3:
            continue
        points = polygon.reshape((-1, 2))
        paths.append([[float(x) / width, float(y) / height]
                      for x, y in points.tolist()])
        polygons.append(points)
    if not polygons:
        raise ValueError("segmentation seed has no polygon with three vertices")
    rendered = np.zeros_like(source, dtype=np.uint8)
    cv2.fillPoly(rendered, polygons, 255)
    truth = source > 0
    estimate = rendered > 0
    union = int(np.logical_or(truth, estimate).sum())
    iou = float(np.logical_and(truth, estimate).sum()) / union if union else 0.0
    return paths, iou


def translate_filmcraft_add_tracks(projection: dict, project: str, native):
    """Describe the one track-provision operation, when overlays need it."""
    try:
        operation = native.filmcraft_add_tracks_operation(projection, project)
        return _describe(native, operation) if operation is not None else None
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("overlay track provisioning refused", exc) from exc


# ── Pre-conform (opt-in, default off) ────────────────────
#
# The native FilmCraft path ingests camera originals slowly (a fixed
# ~150-400 s open/scan per 4K MXF plus ~0.1-0.3 s per frame, ~43x below
# ffmpeg on the same bytes; measured 2026-10-11, PR 2302). Pre-conform
# transcodes each used a_roll/b_roll span (plus handles) once to
# edit-resolution ProRes under the project's scratch, cached by source
# content fingerprint, and the engine places the proxies instead.
#
# Lossy by construction (a ProRes generation under the engine's
# center-crop conform), so this stays OFF unless `--preconform` is
# passed: fidelity is the captain's priority and the default export
# must stay pixel-identical to today's. Anything inexact falls back to
# the camera original for that asset - never a guessed proxy - and the
# receipt records which assets proxied and which stayed direct.


#: Roles whose sources may pre-conform. Overlays and cards carry alpha
#: the ProRes 422 proxy cannot hold, and stems are sound the engine
#: ingests without the picture penalty - so only base picture proxies.
PRECONFORM_SPINE_ROLES = ("a_roll", "b_roll")

#: The engine tick grid placements address (ENGINE_TICKS_PER_SECOND in
#: native/ren-filmcraft/src/adapter.rs - duplicated, not derived, because
#: the adapter exposes no tick constant to Python).
ENGINE_TICKS_PER_SECOND = 254016000000

#: Cache identity lives here: under the project, never under the
#: captain's media folders (the module never picks its own directory).
PRECONFORM_CACHE_SUBDIR = Path("pipeline_output") / "scratch" / "preconform"


def _parse_preconform_size(text: str) -> tuple[int, int]:
    """The proxy frame as (width, height), which must equal the engine sequence frame."""
    parts = (text or "").strip().lower().split("x")
    if len(parts) != 2:
        raise _refuse(f"`--preconform-size` reads WIDTHxHEIGHT, got {text!r}",
                      "the proxy frame must equal the engine sequence frame, and the model carries no canvas",
                      "pass e.g. --preconform-size 1080x1920 for a vertical sequence "
                      "(a fresh FilmCraft sequence defaults to 1920x1080)",
                      code="REN_PM_CLI_PRECONFORM")
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        raise _refuse(f"`--preconform-size` reads WIDTHxHEIGHT, got {text!r}",
                      "neither dimension parsed as an integer",
                      "pass e.g. --preconform-size 1080x1920",
                      code="REN_PM_CLI_PRECONFORM") from None
    if width <= 0 or height <= 0:
        raise _refuse(f"`--preconform-size` must be positive, got {width}x{height}",
                      "a proxy frame with no pixels conforms nothing",
                      "pass e.g. --preconform-size 1080x1920",
                      code="REN_PM_CLI_PRECONFORM")
    return width, height


def _parse_sequence_size(text: str) -> tuple[int, int]:
    parts = (text or "").strip().lower().split("x")
    try:
        width, height = (int(value) for value in parts)
    except (TypeError, ValueError):
        raise _refuse(f"`--sequence-size` reads WIDTHxHEIGHT, got {text!r}",
                      "the native sequence frame is not a valid pixel size",
                      "pass e.g. --sequence-size 1080x1920",
                      code="REN_PM_CLI_SEQUENCE_SIZE") from None
    if width <= 0 or height <= 0:
        raise _refuse(f"`--sequence-size` must be positive, got {width}x{height}",
                      "a sequence with no pixels cannot be rendered",
                      "pass a positive WIDTHxHEIGHT",
                      code="REN_PM_CLI_SEQUENCE_SIZE")
    return width, height


def _preconform_spec(model: dict, projection: dict, width: int, height: int) -> fcp.PreconformSpec:
    """The proxy frame: the stated size at the timeline rate.

    The size is an operator statement because the model carries no
    canvas and `create_project` sends no dimensions - there is nothing
    to derive it from. The rate comes from the timeline clock instead:
    the proxy must play 1:1 on the sequence, so any other rate would
    resample behind the placement math.
    """
    clock_id = projection.get("timeline_clock")
    clock = next((c for c in model.get("clocks", []) if c.get("id") == clock_id), None)
    if clock is None:
        raise _refuse(f"the model names no timeline clock `{clock_id}`",
                      "the proxy rate follows the timeline clock, which is absent",
                      "project a model whose timeline clock carries a frame rate",
                      code="REN_PM_CLI_PRECONFORM")
    rate = clock.get("frame_rate") or {}
    try:
        fps_num, fps_den = int(rate["numerator"]), int(rate["denominator"])
    except (KeyError, TypeError, ValueError):
        raise _refuse(f"timeline clock `{clock_id}` names no frame rate",
                      "the proxy rate follows the timeline clock, which carries none",
                      "project a model whose timeline clock carries a frame rate",
                      code="REN_PM_CLI_PRECONFORM") from None
    if fps_num <= 0 or fps_den <= 0:
        raise _refuse(f"timeline clock `{clock_id}` rate must be positive, got {fps_num}/{fps_den}",
                      "a proxy at a non-positive rate plays nothing",
                      "project a model whose timeline clock carries a frame rate",
                      code="REN_PM_CLI_PRECONFORM")
    spec = fcp.PreconformSpec(width=width, height=height,
                              fps_num=fps_num, fps_den=fps_den)
    try:
        spec.validate()
    except fcp.PreconformError as exc:
        raise _refuse(f"pre-conform spec refused: {exc}",
                      "the stated proxy frame cannot be built",
                      "pass a positive WIDTHxHEIGHT for --preconform-size",
                      code="REN_PM_CLI_PRECONFORM") from exc
    return spec


def _probe_source_total_frames(source: Path, fps_num: int, fps_den: int) -> int | None:
    """Source length in frames from ffprobe duration, or None when unprobed.

    Duration, not frame count: counting frames decodes the whole file,
    which is the cost pre-conform exists to avoid. A tail wobble of a
    frame is absorbed downstream - the transcode verifies its own frame
    count and the rebase refuses out-of-window placements.
    """
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=duration:format=duration",
         "-of", "json", str(source)],
        capture_output=True, text=True, encoding="utf-8",
        timeout=120, check=False)
    if done.returncode != 0:
        return None
    try:
        doc = json.loads(done.stdout)
        streams = doc.get("streams") or []
        duration_text = (streams[0].get("duration")
                         if streams else None) or (doc.get("format") or {}).get("duration")
        total = int(float(duration_text) * fps_num / fps_den)
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    return total if total > 0 else None


def _plan_preconform_asset(clocks: dict, uses: list[dict], placements: list[dict],
                           source_path: Path | None, *, spec: fcp.PreconformSpec) -> dict:
    """One asset's proxy span, or raise PreconformError for direct import.

    Raising here never refuses the run: the caller records the reason
    and imports the camera original, which is today's behavior.
    """
    if source_path is None:
        raise fcp.PreconformError("no resolved media path for this asset")
    if not uses:
        raise fcp.PreconformError("no clip use names this asset")
    exotic = sorted({use.get("role") for use in uses} - set(PRECONFORM_SPINE_ROLES))
    if exotic:
        raise fcp.PreconformError(
            f"role(s) {exotic} are not base picture; overlays, cards and stems keep their originals")
    clock_ids = {use["source_interval"]["clock_id"] for use in uses}
    if len(clock_ids) != 1:
        raise fcp.PreconformError(
            f"uses span {len(clock_ids)} source clocks; one proxy covers one clock")
    clock = clocks.get(next(iter(clock_ids)))
    if clock is None:
        raise fcp.PreconformError("the source clock is not in the model")
    try:
        tps = int(clock["ticks_per_second"])
        fps_num = int(clock["frame_rate"]["numerator"])
        fps_den = int(clock["frame_rate"]["denominator"])
    except (KeyError, TypeError, ValueError):
        raise fcp.PreconformError("the source clock names no frame rate") from None
    if tps <= 0 or fps_num <= 0 or fps_den <= 0:
        raise fcp.PreconformError(
            f"the source clock rate must be positive, got tps={tps} fps={fps_num}/{fps_den}")
    grid = tps * fps_den
    starts = [(use["source_interval"]["start"] * fps_num) // grid for use in uses]
    ends = [-((-use["source_interval"]["end"] * fps_num) // grid) for use in uses]
    span_min, span_max = min(starts), max(ends)
    if span_max <= span_min:
        raise fcp.PreconformError(f"used span {span_min}..{span_max} is empty")
    total = _probe_source_total_frames(source_path, fps_num, fps_den)
    if total is None:
        raise fcp.PreconformError(f"ffprobe gave no duration for {source_path}")
    if span_max > total:
        raise fcp.PreconformError(
            f"used span {span_min}..{span_max} lies outside a {total}-frame source")
    proxy_start = max(0, span_min - spec.handle_frames)
    proxy_end = min(total, span_max + spec.handle_frames)
    start_ticks = proxy_start * ENGINE_TICKS_PER_SECOND * fps_den
    end_ticks = proxy_end * ENGINE_TICKS_PER_SECOND * fps_den
    if start_ticks % fps_num or end_ticks % fps_num:
        raise fcp.PreconformError(
            f"proxy span {proxy_start}..{proxy_end} lands between engine ticks; refusing a rounded rebase")
    start_ticks //= fps_num
    end_ticks //= fps_num
    for placement in placements:
        begin = placement["source_in_ticks"]
        if begin < start_ticks or begin + placement["duration_ticks"] > end_ticks:
            raise fcp.PreconformError(
                f"placement `{placement.get('clip_use_id')}` reaches outside "
                f"the proxy span {proxy_start}..{proxy_end}")
    return {"status": "ready",
            "source_path": str(source_path),
            "source_fps_num": fps_num, "source_fps_den": fps_den,
            "use_start_frame": span_min, "use_end_frame": span_max,
            "total_frames": total,
            "proxy_start_frame": proxy_start, "proxy_end_frame": proxy_end,
            "proxy_start_ticks": start_ticks}


def plan_preconform(model: dict, projection: dict, resolved: dict[str, Path],
                    *, spec: fcp.PreconformSpec) -> dict:
    """Which assets proxy and which stay direct, with validated ranges.

    Read-only apart from ffprobe duration reads: no transcode runs here,
    so a downstream refusal still leaves no proxy files behind. Every
    projection import lands in exactly one status: `ready` (span plus
    engine-tick offsets validated) or `direct` (the camera original
    imports, with the reason named).
    """
    clocks = {clock.get("id"): clock for clock in model.get("clocks", [])}
    uses_by_asset: dict[str, list[dict]] = {}
    for use in model.get("clip_uses", []):
        uses_by_asset.setdefault(use.get("asset_id"), []).append(use)
    placements_by_asset: dict[str, list[dict]] = {}
    for placement in projection.get("placements", []):
        placements_by_asset.setdefault(placement.get("asset_id"), []).append(placement)
    assets: dict[str, dict] = {}
    for entry in projection.get("imports", []):
        asset_id = entry.get("asset_id")
        try:
            assets[asset_id] = _plan_preconform_asset(
                clocks, uses_by_asset.get(asset_id, []),
                placements_by_asset.get(asset_id, []),
                resolved.get(asset_id), spec=spec)
        except (fcp.PreconformError, OSError, ValueError,
                subprocess.TimeoutExpired) as exc:
            assets[asset_id] = {"status": "direct",
                                "reason": f"pre-conform planning refused ({exc}); "
                                          "the camera original imports"}
    return {"assets": assets}


def execute_preconform(planned: dict, resolved: dict[str, Path],
                       placements: list[dict],
                       *, spec: fcp.PreconformSpec, cache_dir: Path
                       ) -> tuple[dict[str, Path], list[dict], dict]:
    """Transcode every ready asset (cached by identity) and rebase placements.

    Returns the import media map (proxies swapped in), the placements to
    execute (source ticks rebased onto proxy-relative origins), and the
    receipt report. A transcode that refuses falls back to the camera
    original for that asset - today's behavior - with the reason
    recorded beside it.
    """
    import time

    media_map = dict(resolved)
    offsets: dict[str, int] = {}
    records: list[fcp.PreconformRecord] = []
    report_assets: dict[str, dict] = {}
    for asset_id, entry in planned["assets"].items():
        if entry["status"] != "ready":
            report_assets[asset_id] = {"status": "direct", "reason": entry["reason"],
                                       "path": str(resolved[asset_id])}
            continue
        started = time.monotonic()
        try:
            record = fcp.preconform_source(
                Path(entry["source_path"]),
                asset_id=asset_id,
                source_fps_num=entry["source_fps_num"],
                source_fps_den=entry["source_fps_den"],
                use_start_frame=entry["use_start_frame"],
                use_end_frame=entry["use_end_frame"],
                total_frames=entry["total_frames"],
                spec=spec, cache_dir=cache_dir)
        except (fcp.PreconformError, OSError, ValueError,
                subprocess.TimeoutExpired) as exc:
            report_assets[asset_id] = {
                "status": "direct",
                "reason": f"pre-conform transcode refused ({exc}); the camera original imports",
                "path": str(resolved[asset_id])}
            continue
        records.append(record)
        offsets[asset_id] = entry["proxy_start_ticks"]
        report_assets[asset_id] = {
            "status": "preconformed", "path": record.path,
            "cache_hit": record.cache_hit,
            "source_range_frames": [record.source_start_frame, record.source_end_frame],
            "transcode_seconds": round(time.monotonic() - started, 2)}
    swapped, swapback = fcp.apply_to_media_map(media_map, records)
    rebased = []
    for placement in placements:
        offset = offsets.get(placement.get("asset_id"))
        if offset is None:
            rebased.append(dict(placement))
            continue
        source_in = placement["source_in_ticks"] - offset
        if source_in < 0:  # plan_preconform validated the window; this is a bug alarm
            raise _refuse(f"placement `{placement.get('clip_use_id')}` rebases before its proxy origin",
                          "the planned proxy window passed but the built one moved",
                          "re-run without --preconform and report the receipt's preconform section",
                          code="REN_PM_CLI_PRECONFORM")
        rebased.append(dict(placement, source_in_ticks=source_in))
    report = {"enabled": True,
              "cache_dir": str(cache_dir),
              "spec": {"width": spec.width, "height": spec.height,
                       "fps": [spec.fps_num, spec.fps_den],
                       "prores_profile": spec.prores_profile},
              "assets": report_assets,
              "swapback": swapback}
    return swapped, rebased, report


# ── Engine CLIs ──────────────────────────────────────────────


def _filmcraft_cli() -> Path:
    import os

    from library.tools import filmcraft_patches

    override = os.environ.get("REN_FILMCRAFT_CLI") or os.environ.get("FILMCRAFT_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise _refuse(f"REN_FILMCRAFT_CLI does not name a file: {candidate}",
                      "the override must point at the pinned CLI",
                      "point REN_FILMCRAFT_CLI at the pinned filmcraft-cli, or unset it for the cache lookup",
                      code="REN_PM_CLI_ENGINE_ABSENT")
    # The digest-keyed cache the runtime install and the intake share:
    # revision plus the fork-patch-set key, so an unpatched binary is
    # never reused once the patch set changes.
    cached = filmcraft_patches.filmcraft_cli_path(Path.home(), FILMCRAFT_REVISION)
    if cached.is_file():
        return cached
    raise _refuse("pinned FilmCraft CLI is absent",
                  f"no binary at {cached}",
                  "run the upstream intake gate to build the pinned revision, then retry with --execute",
                  code="REN_PM_CLI_ENGINE_ABSENT")


def _effectcraft_cli() -> Path:
    import os

    override = os.environ.get("REN_EFFECTCRAFT_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise _refuse(f"REN_EFFECTCRAFT_CLI does not name a file: {candidate}",
                      "the override must point at the pinned CLI",
                      "point REN_EFFECTCRAFT_CLI at the pinned effectcraft-cli, or unset it for the cache lookup",
                      code="REN_PM_CLI_ENGINE_ABSENT")
    cached = (Path.home() / ".cache/ren/effectcraft" / EFFECTCRAFT_REVISION
              / "source/target/release/effectcraft-cli")
    if cached.is_file():
        return cached
    raise _refuse("pinned EffectCraft CLI is absent",
                  f"no binary at {cached}",
                  "run the upstream intake gate to build the pinned revision, then retry with --execute",
                  code="REN_PM_CLI_ENGINE_ABSENT")


def _check_cli_version(cli: Path, expect: str, engine: str) -> None:
    done = subprocess.run([str(cli), "--version"], capture_output=True,
                          text=True, encoding="utf-8", timeout=60, check=False)
    if expect not in (done.stdout + done.stderr):
        raise _refuse(f"{engine} CLI version drifted",
                      (done.stdout + done.stderr).strip()[-200:] or f"exit {done.returncode}",
                      f"re-qualify against pinned {expect}",
                      code="REN_PM_CLI_ENGINE_VERSION")


def _fc_exec(cli: Path, project: Path | None, command: str,
             params: dict, *, save: bool, save_as: Path | None = None) -> Any:
    argv = [str(cli)]
    if save_as is not None:
        argv += ["exec", command, json.dumps(params), "--compact", "--save-as", str(save_as)]
    else:
        if project is not None:
            argv += ["--project", str(project)]
        argv += ["exec", command, json.dumps(params), "--compact"]
        if save:
            argv.append("--save")
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise _refuse(f"FilmCraft command {command!r} failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise _refuse(f"FilmCraft command {command!r} answered non-JSON",
                      str(exc),
                      "re-run against the pinned CLI",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND") from exc


def _batch_enabled() -> bool:
    """One batched `run` session per phase, or the legacy per-`exec` loop.

    `REN_FILMCRAFT_BATCH=0` restores the legacy loop: the same commands,
    one process per command. The equivalence proof runs both and diffs the
    inspected timeline, so the hatch is the before/after switch as well as
    the emergency fallback.
    """
    import os

    return os.environ.get("REN_FILMCRAFT_BATCH", "1") != "0"


def _fc_run_batch(cli: Path, project: Path, commands: list[dict],
                  *, save: bool = True) -> list[Any]:
    """Run engine commands through one open backend (`filmcraft-cli run`).

    `commands` carries {"command", "params"} dicts in execution order - the
    same dicts the legacy loop feeds `_fc_exec` one at a time, so the batch
    sends the same bytes the per-`exec` path sent. Every entry becomes one
    `{"id", "params"}` line of a batch script; the CLI answers one
    `{"id", "line", "ok", "result"|"error"}` line per command and stops at
    the first failure (no `--keep-going`: the legacy loop aborts there
    too). A failing command is named by its engine id plus its batch line,
    so per-command error attribution survives the batching.

    Requires a non-empty batch; callers skip empty phases (a model with no
    edits runs no edit session).
    """
    import tempfile

    if not commands:
        raise _refuse("FilmCraft batch names no commands",
                      "the caller skipped no empty batch",
                      "run the batch only for a non-empty phase",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    script_lines = [json.dumps({"id": entry["command"], "params": entry["params"]},
                               ensure_ascii=False)
                    for entry in commands]
    with tempfile.TemporaryDirectory(prefix="ren-filmcraft-batch-") as tmp:
        script = Path(tmp) / "batch.jsonl"
        script.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        argv = [str(cli), "--project", str(project), "run", str(script), "--compact"]
        if save:
            argv.append("--save")
        done = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8",
                              timeout=300 + 5 * len(commands), check=False)
    if done.returncode != 0:
        raise _refuse("FilmCraft batch run failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    answers: list[Any] = []
    for raw in done.stdout.splitlines():
        if not raw.strip():
            continue
        try:
            answers.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise _refuse("FilmCraft batch answered non-JSON",
                          str(exc),
                          "re-run against the pinned CLI",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND") from exc
    # Fail closed: every answered line must pair with its command in order.
    results: list[Any] = []
    for index, entry in enumerate(answers):
        line = index + 1
        command = commands[index]["command"] if index < len(commands) else None
        if not isinstance(entry, dict) or entry.get("id") != command \
                or entry.get("line") != line:
            raise _refuse(f"FilmCraft batch line {line} answered for "
                          f"{entry.get('id') if isinstance(entry, dict) else entry!r}, "
                          f"expected {command!r}",
                          json.dumps(entry, default=str)[:400],
                          "re-run against the pinned CLI",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND")
        if not entry.get("ok"):
            raise _refuse(f"FilmCraft command {command!r} failed "
                          f"(batch line {line} of {len(commands)})",
                          str(entry.get("error") or "")[:800]
                          or json.dumps(entry, default=str)[:800],
                          "inspect the engine output; the model is unchanged",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND")
        results.append(entry.get("result"))
    if len(results) != len(commands):
        missing = commands[len(results)]["command"]
        raise _refuse(f"FilmCraft batch stopped after {len(results)} of "
                      f"{len(commands)} commands; {missing!r} never answered",
                      done.stderr.strip()[-400:] or "the CLI ended mid-batch",
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    return results


def _describe(native, operation: dict) -> dict:
    """The engine invocation for one adapter operation."""
    try:
        described = native.filmcraft_describe_operation(operation)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("operation describes no engine command", exc) from exc
    return {"command": described["command"], "params": described["params"],
            "save": described["save"]}


def _placement_clips(use_id: str, placed: Any) -> list[int]:
    """The minted clip ids from one place receipt, or refuse like before."""
    clips = placed.get("clips") or []
    if not clips:
        raise _refuse(f"FilmCraft placed no clips for `{use_id}`",
                      json.dumps(placed)[:400],
                      "check the engine timeline state",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    return list(clips)


def _with_measured_aroll_audio_streams(model: dict, media_map: dict) -> dict:
    """Fill missing A-roll audio metadata for the native channel planner.

    Reel-built models can name the video stream of an MXF without recording
    its embedded audio streams. The dual-mono planner intentionally stays
    pure and only acts on declared one-channel audio streams, so measure
    those declarations from the actual imported source before calling it.
    """
    audio_assets = {stream["asset_id"] for stream in model["streams"]
                    if stream["metadata"].get("codec_type") == "audio"}
    aroll_assets = {use["asset_id"] for use in model["clip_uses"]
                    if use["role"] == "a_roll"}
    augmented = dict(model)
    streams = list(model["streams"])
    for asset_id in sorted(aroll_assets.intersection(media_map) - audio_assets):
        media_path = Path(media_map[asset_id])
        # Test adapters and dry fixtures can intentionally name virtual
        # paths. Production media has already passed verify_media.
        if not media_path.is_file():
            continue
        measured = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index,channels", "-of", "json",
             str(media_path)], capture_output=True, encoding="utf-8",
            errors="replace", check=False)
        if measured.returncode != 0:
            raise _refuse(
                f"could not measure embedded A-roll audio streams in {media_path}",
                (measured.stderr or f"ffprobe exit {measured.returncode}").strip()[-400:],
                "check that ffprobe can read the imported source",
                code="REN_PM_CLI_AUDIO_STREAM_PROBE")
        try:
            audio_rows = json.loads(measured.stdout or "{}").get("streams", [])
        except (TypeError, ValueError) as exc:
            raise _refuse(
                f"ffprobe returned invalid audio stream data for {media_path}",
                str(exc), "check ffprobe output for the imported source",
                code="REN_PM_CLI_AUDIO_STREAM_PROBE") from exc
        video = next(stream for stream in streams
                     if stream["asset_id"] == asset_id)
        for audio in audio_rows:
            stream_index = audio.get("index")
            channels = audio.get("channels")
            if (not isinstance(stream_index, int) or isinstance(stream_index, bool)
                    or not isinstance(channels, int) or isinstance(channels, bool)):
                continue
            streams.append({
                "id": f"{video['id']}:audio:{stream_index}",
                "asset_id": asset_id,
                "clock_id": video["clock_id"],
                "stream_index": stream_index,
                "metadata": {"codec_type": "audio", "channels": channels},
            })
    augmented["streams"] = streams
    return augmented


def execute_filmcraft(model: dict, plan: dict, *, project_root: Path,
                      out: Path, media_root: Path | None,
                      filmcraft_project: Path | None,
                      export_path: Path | None,
                      export_format: str, native,
                      preconform: bool = False,
                      preconform_size: str | None = None,
                      sequence_size: str | None = None) -> dict:
    """Build the adapter-planned timeline on the pinned FilmCraft CLI.

    `preconform` stays off unless the operator passes `--preconform`:
    the proxy path is lossy and the default export must match today's
    pixels. When on, `--preconform-size` names the proxy frame and each
    used a_roll/b_roll span transcodes once to the project scratch
    cache; anything inexact imports the camera original instead.
    `sequence_size` configures and reads back the frame before media is
    imported, so sequence-pixel transform effects have an explicit canvas.
    """
    if export_path is not None and export_format != "wav":
        raw_path = export_path.with_name(
            f"{export_path.stem}.pre-master{export_path.suffix}")
        mastered_path = export_path.with_name(
            f".{export_path.stem}.mastering{export_path.suffix}")
        if raw_path.exists() or mastered_path.exists():
            raise _refuse(
                "native mastering output already exists",
                f"refusing to replace {raw_path} or {mastered_path}",
                "choose a fresh export path or move the previous evidence",
                code="REN_PM_CLI_MASTERING_OUTPUT_EXISTS")
    projection = {key: plan[key] for key in
                  ("adapter_version", "engine_revision", "timeline_clock",
                   "imports", "placements", "losses")}
    project = filmcraft_project or (out.parent / f"{out.stem}.fcproj")
    project_str = str(project)
    if project.suffix != ".fcproj":
        raise _refuse(f"FilmCraft project path must use the `.fcproj` extension: {project}",
                      "Save As assigns the project name from the file stem",
                      "rename the project path to end in .fcproj",
                      code="REN_PM_CLI_BAD_PROJECT_PATH")
    if project.exists():
        raise _refuse(f"refusing to replace existing project {project}",
                      "an execute run never overwrites an engine project",
                      "remove the file or pass --filmcraft-project at a fresh path",
                      code="REN_PM_CLI_PROJECT_EXISTS")
    sequence_dimensions = (_parse_sequence_size(sequence_size)
                          if sequence_size is not None else None)
    if preconform_size is not None and not preconform:
        raise _refuse("`--preconform-size` without `--preconform`",
                      "a proxy frame with no pre-conform run sizes nothing",
                      "drop --preconform-size, or pass --preconform to use it",
                      code="REN_PM_CLI_PRECONFORM")

    # Fail closed before any mutation: the create description, the media
    # check and every edit translate now, with placeholder ids; the run
    # below only substitutes reconciled clip ids for the same commands.
    # The native treatments resolve here too, for the same reason: a
    # malformed series_look/tv_frame declaration refuses before an
    # engine project exists, and the post-export composite reuses this
    # plan so one declaration cannot read two ways.
    create = _describe(native, {"kind": "create_project",
                                "path": project_str, "name": project.stem})
    media_dir = str(media_root or project_root)
    projection, overlay_preparation = prepare_projection(
        projection, media_dir,
        out.parent / f".{out.stem}.native_overlay_cache")
    if overlay_preparation:
        print("native overlay preparation: " + json.dumps(
            overlay_preparation, sort_keys=True), file=sys.stderr)
    try:
        resolved = native.filmcraft_verify_media(projection, media_dir)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("media verification refused", exc) from exc
    media_map = {key: Path(value) for key, value in resolved.items()}
    # The prebuilt plan can only name streams declared by the input model.
    # A-roll camera files sometimes omit their embedded audio stream rows;
    # measure them before finalizing placements so the adapter assigns A1.
    planner_model = _with_measured_aroll_audio_streams(model, media_map)
    if planner_model is not model:
        planner_model = _model_with_native_moves(planner_model, sequence_size)
        projection = plan_filmcraft_projection(planner_model, native)
    preconform_report: dict[str, Any] = {"enabled": False}
    if preconform:
        if preconform_size is None:
            raise _refuse("`--preconform` needs `--preconform-size WIDTHxHEIGHT`",
                          "the proxy frame must equal the engine sequence frame, "
                          "and the model carries no canvas",
                          "pass e.g. --preconform-size 1080x1920 for a vertical sequence "
                          "(a fresh FilmCraft sequence defaults to 1920x1080)",
                          code="REN_PM_CLI_PRECONFORM")
        width, height = _parse_preconform_size(preconform_size)
        spec = _preconform_spec(model, projection, width, height)
        planned = plan_preconform(model, projection, media_map, spec=spec)
        media_map, rebased, preconform_report = execute_preconform(
            planned, media_map, projection["placements"],
            spec=spec, cache_dir=project_root / PRECONFORM_CACHE_SUBDIR)
        projection = dict(projection, placements=rebased)
        proxied = sum(1 for asset in preconform_report["assets"].values()
                      if asset["status"] == "preconformed")
        print(f"pre-conform: {proxied}/{len(preconform_report['assets'])} asset(s) proxied "
              f"(cache {preconform_report['cache_dir']})", file=sys.stderr)
    dummy_ids = {placement["clip_use_id"]: [0] for placement in projection["placements"]}
    translate_filmcraft_picture_effects(
        model, projection, dummy_ids, project_str, native)
    translate_filmcraft_masks(
        model, projection, dummy_ids, project_str, native,
        project_root=project_root)
    translate_filmcraft_edits(model, projection, dummy_ids, project_str, native)
    add_tracks_command = translate_filmcraft_add_tracks(
        projection, project_str, native)
    treatments_plan = _plan_native_treatments(
        project_root, model=model, sequence_size=sequence_size)
    model = _model_with_native_moves(model, sequence_size)

    cli = _filmcraft_cli()
    _check_cli_version(cli, FILMCRAFT_VERSION, "FilmCraft")

    _fc_exec(cli, None, create["command"], create["params"],
             save=False, save_as=project)
    if not project.is_file():
        raise _refuse("FilmCraft could not create the project",
                      f"no file at {project} after file.newSequence",
                      "check the pinned CLI opens headless",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    if sequence_size is not None:
        width, height = sequence_dimensions
        _fc_exec(cli, project, "sequence.settings",
                 {"width": width, "height": height, "scaleMotion": False},
                 save=True)
        configured = _fc_exec(cli, project, "sequence.inspect", {}, save=False)
        settings = configured.get("settings") or {}
        if (settings.get("width"), settings.get("height")) != (width, height):
            raise _refuse(
                "FilmCraft did not retain the requested sequence size",
                f"requested {width}x{height}, read back "
                f"{settings.get('width')}x{settings.get('height')}",
                "check the pinned FilmCraft sequence.settings command",
                code="REN_PM_CLI_SEQUENCE_SIZE")
    try:
        import_operation = native.filmcraft_import_operation(
            projection, {key: str(value) for key, value in media_map.items()},
            project_str)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("import operation refused", exc) from exc
    described_import = _describe(native, import_operation)

    if add_tracks_command is not None:
        _fc_exec(cli, project, add_tracks_command["command"],
                 add_tracks_command["params"], save=add_tracks_command["save"])

    imported = _fc_exec(cli, project, described_import["command"],
                        described_import["params"],
                        save=described_import["save"])
    items = imported.get("items") or []
    if len(items) != len(projection["imports"]):
        raise _refuse("FilmCraft import receipt does not pair with the plan",
                      f"{len(items)} items for {len(projection['imports'])} imports: {json.dumps(imported)[:400]}",
                      "reconcile the import receipt before placing",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    item_by_asset = {imp["asset_id"]: item
                     for imp, item in zip(projection["imports"], items)}
    # FilmCraft imports discrete mono MXF streams as stereo pairs. Query the
    # actual engine mapping, then let the native adapter decide whether a
    # mono source needs Resolve-style dual-mono before any timeline placement.
    if item_by_asset:
        mapping_operation = {"kind": "audio_channels", "project": project_str,
                             "items": list(item_by_asset.values()),
                             "format": None, "clips": None}
        mapping_command = _describe(native, mapping_operation)
        channel_mapping = _fc_exec(cli, project, mapping_command["command"],
                                   mapping_command["params"], save=False)
        for asset_id, item in item_by_asset.items():
            try:
                dual_mono = native.filmcraft_plan_dual_mono_mapping(
                    planner_model, asset_id, item, project_str, channel_mapping)
            except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
                raise _refuse_from_adapter("dual-mono mapping refused", exc) from exc
            if dual_mono is not None:
                described = _describe(native, dual_mono)
                _fc_exec(cli, project, described["command"], described["params"],
                         save=described["save"])

    try:
        pairs = native.filmcraft_placement_operations(
            projection, item_by_asset, project_str)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("placement operations refused", exc) from exc
    receipts: list[list[int]] = []
    described_pairs = [(use_id, _describe(native, operation))
                       for use_id, operation in pairs]
    if _batch_enabled():
        # One batched session for every placement: the same commands the
        # legacy loop sent one process at a time, through one open backend.
        if described_pairs:
            placed_answers = _fc_run_batch(
                cli, project,
                [{"command": described["command"], "params": described["params"]}
                 for _, described in described_pairs],
                save=any(described["save"] for _, described in described_pairs))
            for (use_id, _), placed in zip(described_pairs, placed_answers):
                receipts.append(_placement_clips(use_id, placed))
    else:
        for use_id, described in described_pairs:
            placed = _fc_exec(cli, project, described["command"],
                              described["params"], save=described["save"])
            receipts.append(_placement_clips(use_id, placed))
    try:
        clip_ids = native.filmcraft_reconcile_clip_ids(projection, receipts)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("clip id reconciliation refused", exc) from exc

    mask_commands = translate_filmcraft_masks(
        model, projection,
        {key: list(value) for key, value in clip_ids.items()},
        project_str, native, project_root=project_root)
    picture_commands = translate_filmcraft_picture_effects(
        model, projection,
        {key: list(value) for key, value in clip_ids.items()},
        project_str, native)
    real_commands = mask_commands + picture_commands + translate_filmcraft_edits(
        model, projection,
        {key: list(value) for key, value in clip_ids.items()},
        project_str, native)
    engine_losses = list(imported.get("errors") or [])
    if _batch_enabled():
        # One batched session for every translated edit, as for placements.
        edit_answers = _fc_run_batch(cli, project, real_commands,
                                     save=any(command["save"]
                                              for command in real_commands)) \
            if real_commands else []
    else:
        edit_answers = [_fc_exec(cli, project, command["command"],
                                 command["params"], save=command["save"])
                        for command in real_commands]
    for answer in edit_answers:
        if isinstance(answer, dict) and answer.get("errors"):
            engine_losses.extend(answer["errors"])

    inspected = _fc_exec(cli, project, "sequence.inspect", {}, save=False)
    receipt: dict[str, Any] = {
        "engine": "filmcraft",
        "engine_revision": FILMCRAFT_REVISION,
        "engine_version": FILMCRAFT_VERSION,
        "project": project_str,
        "plan": {"imports": projection["imports"],
                 "placements": projection["placements"]},
        "clip_ids": clip_ids,
        "edit_commands": [{"command": c["command"], "params": c["params"]}
                          for c in real_commands],
        "inspected": inspected,
        "losses": projection["losses"] + [{"code": "engine_note", "detail": str(note)[:300]}
                                           for note in engine_losses],
        "preconform": preconform_report,
        "overlay_preparation": overlay_preparation,
        "executed": True,
    }
    if export_path is not None:
        needs_treatments = (treatments_plan["grade"] is not None
                            or treatments_plan["bezel"] is not None)
        if needs_treatments and export_format == "wav":
            raise _refuse(
                "video treatments cannot ride an audio-only export",
                "the run declares a grade or bezel but --export-format wav "
                "carries no picture",
                "export with --export-format h264 (or prores) so the "
                "treatments have a picture to land on, or drop the "
                "series_look/tv_frame declaration",
                code="REN_PM_CLI_NATIVE_TREATMENTS")
        if needs_treatments:
            # Single-encode delivery: the engine renders a ProRes
            # mezzanine intermediate and the treatments pass encodes
            # the delivery exactly once. The old path exported H264
            # from the engine and re-encoded the whole video with
            # libx264 in the composite - two full lossy encodes for
            # one delivery. The mezzanine is the simpler correct fix:
            # folding the grade/bezel into the engine would need new
            # engine work (the adapter reports color.grade as
            # effect_not_projected - its LUT path needs a LUT file,
            # not grade numbers - and the bezel is a declared PNG the
            # composite draws), while a ProRes export already exists
            # in the adapter and the engine.
            intermediate = export_path.with_name(
                export_path.stem + ".engine-base.mov")
            try:
                export_op = _describe(native, {"kind": "export", "project": project_str,
                                               "path": str(intermediate),
                                               "format": EXPORT_FORMATS["prores"],
                                               "hardware_encoder": "auto"})
            except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
                raise _refuse_from_adapter("export operation refused", exc) from exc
            exported = _fc_exec(cli, project, export_op["command"],
                                export_op["params"], save=export_op["save"])
            receipt["export"] = {"path": str(export_path),
                                 "intermediate": str(intermediate),
                                 "engine_format": "prores",
                                 "answer": exported}
            receipt["native_treatments"] = _composite_intermediate_to_final(
                intermediate, export_path, planned=treatments_plan,
                video_codec=("prores" if export_format == "prores"
                             else "h264"))
        else:
            try:
                export_op = _describe(native, {"kind": "export", "project": project_str,
                                               "path": str(export_path),
                                               "format": EXPORT_FORMATS[export_format],
                                               "hardware_encoder": "auto"})
            except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
                raise _refuse_from_adapter("export operation refused", exc) from exc
            exported = _fc_exec(cli, project, export_op["command"],
                                export_op["params"], save=export_op["save"])
            receipt["export"] = {"path": str(export_path),
                                 "intermediate": None,
                                 "engine_format": export_format,
                                 "answer": exported}
            # No declarations means no composite: the engine bytes stay
            # exactly as exported.
            receipt["native_treatments"] = {
                "out": str(export_path),
                "grade_name": None,
                "grade_applied": False,
                "bezel_applied": False,
                "refused": [],
                "filter": "",
                "intermediate": None,
                "engine_format": export_format,
                "lossy_encodes": (1 if export_format in ("h264", "prores")
                                  else 0),
            }
    else:
        receipt["native_treatments"] = {
            "out": None,
            "grade_name": None,
            "grade_applied": False,
            "bezel_applied": False,
            "refused": [],
            "filter": "",
            "intermediate": None,
            "engine_format": None,
            "lossy_encodes": 0,
            "skipped": "no export was requested, so there is no render to treat",
        }
    if export_path is not None and export_format != "wav":
        receipt["mastering"] = _master_filmcraft_export(
            project_root, export_path)
    elif export_path is not None:
        receipt["mastering"] = {
            "skipped": "audio-only WAV export has no video delivery master",
        }
    return receipt


def _master_filmcraft_export(project_root: Path, export_path: Path) -> dict:
    """Apply the project's delivery mastering to an audio-bearing export."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=index", "-of", "csv=p=0",
         str(export_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False)
    if probe.returncode != 0:
        raise _refuse(
            "could not inspect native export audio",
            probe.stderr[-1000:],
            "check that ffprobe can read the exported media",
            code="REN_PM_CLI_MASTERING_PROBE_FAILED")
    if not probe.stdout.strip():
        return {"skipped": "native video export contains no audio stream"}

    # Native FilmCraft exports are renders, not deliveries. Apply the same
    # measured mastering stage as `ren deliver`, using the project's spec.
    from library.tools.delivery_spec import resolve_delivery_spec_for_project
    from library.tools.master_loudness import (
        _loudnorm_measure, normalize_to_delivery, result_as_dict,
    )

    spec = resolve_delivery_spec_for_project(str(project_root))
    raw_path = export_path.with_name(
        f"{export_path.stem}.pre-master{export_path.suffix}")
    mastered_path = export_path.with_name(
        f".{export_path.stem}.mastering{export_path.suffix}")
    try:
        before_loudness = _loudnorm_measure(str(export_path))
        mastered = normalize_to_delivery(
            str(export_path), str(mastered_path),
            target_lufs=spec.loudness_target_lufs,
            tolerance=spec.loudness_tolerance_lufs,
            true_peak_ceiling=spec.true_peak_ceiling_dbtp)
        after_loudness = _loudnorm_measure(str(mastered_path))
        os.replace(export_path, raw_path)
        try:
            os.replace(mastered_path, export_path)
        except OSError:
            os.replace(raw_path, export_path)
            raise
    except Exception as exc:
        mastered_path.unlink(missing_ok=True)
        if not export_path.exists() and raw_path.exists():
            os.replace(raw_path, export_path)
        raise _refuse(
            f"native export mastering failed: {exc}",
            "the encoded native export did not complete the declared delivery mastering pass",
            "inspect the named measurement or encoder failure and retry to a fresh export path",
            code="REN_PM_CLI_MASTERING_FAILED") from exc
    mastering = result_as_dict(mastered)
    mastering["raw_output_path"] = str(raw_path)
    mastering["output_path"] = str(export_path)
    mastering["loudness_range_lu"] = {
        "input": before_loudness["input_lra"],
        "output": after_loudness["input_lra"],
    }
    return mastering


def _plan_native_treatments(project_root: Path, *, model: dict,
                             sequence_size: str | None) -> dict:
    """The declared grade/bezel plan, or refuse before any mutation."""
    from library.tools import native_effects_composite as nec

    try:
        planned = nec.plan_declared_treatments(str(project_root))
        compositions = _project_model_compositions(model, sequence_size)
        return {**planned, **compositions}
    except Exception as exc:  # noqa: BLE001 - declaration errors become refusals
        raise _refuse(f"native treatments refused: {exc}",
                      "the project's series_look/tv_frame declaration cannot be delivered as written",
                      "fix the declaration in project.yaml (or the named brand template) and retry",
                      code="REN_PM_CLI_NATIVE_TREATMENTS") from exc


def _project_model_compositions(model: dict, sequence_size: str | None) -> dict:
    """Resolve verified moves for the native render pass.

    Move curves are sampled through the same measured Fusion projection
    used by EffectCraft; only scale-only curves lower to FilmCraft's
    native picture keys. Model `color.grade` effects are NOT lowered:
    the per-clip BrightnessContrast proxy, stacked on the declared
    project look in the composite, moves the picture away from the
    deliverable by measured 0.07 SSIM-Y (see benchmark note), so grades
    stay refused as `effect_not_projected` until the proxy is calibrated.
    """
    from library.tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
        move_comp_frames,
        project_move,
    )

    sizes = _parse_sequence_size(sequence_size) if sequence_size else None
    clocks = {clock["id"]: clock for clock in model["clocks"]}
    uses = {use["id"]: use for use in model["clip_uses"]}
    max_frames = 0
    frame_rates: set[Fraction] = set()
    for use in model["clip_uses"]:
        interval = use["timeline_interval"]
        clock = clocks[interval["clock_id"]]
        rate = Fraction(clock["frame_rate"]["numerator"],
                        clock["frame_rate"]["denominator"])
        frame_rates.add(rate)
        end = Fraction(interval["end"] * rate.numerator,
                       clock["ticks_per_second"] * rate.denominator)
        max_frames = max(max_frames, math.ceil(end))
    if len(frame_rates) > 1:
        raise ValueError(f"native model timeline has inconsistent frame rates: {frame_rates}")
    frame_rate = float(next(iter(frame_rates))) if frame_rates else 0.0

    grade_spans: list[dict] = []
    native_moves = []
    lowered_move_ids = []
    for effect in model["effects"]:
        class_id = effect["class_id"]
        interval = effect["interval"]
        clock = clocks[interval["clock_id"]]
        if clock["domain"]["kind"] not in ("timeline", "project"):
            continue
        if class_id not in MOVE_CLASS_IDS:
            continue
        if not sizes:
            raise ValueError(
                f"move effect {effect['id']!r} needs --sequence-size WIDTHxHEIGHT to compose its curve")
        if effect["target_id"] not in uses:
            raise ValueError(
                f"move effect {effect['id']!r} targets absent use {effect['target_id']!r}")
        first_frame, last_frame = move_comp_frames(
            interval, clock["ticks_per_second"], frame_rate, effect["id"])
        ops = project_move(
            class_id=class_id, effect_id=effect["id"],
            parameters=effect["parameters"], layer=effect["target_id"],
            first_frame=first_frame, last_frame=last_frame,
            comp_frames=max_frames, comp_width=sizes[0], comp_height=sizes[1],
            frame_rate=frame_rate)
        scale_ops = [op for op in ops if op["path"] == "transform/scale"]
        other_ops = [op for op in ops if op["path"] != "transform/scale"]
        if other_ops or not scale_ops:
            continue
        target_use = uses[effect["target_id"]]
        timeline = target_use["timeline_interval"]
        timeline_clock = clocks[timeline["clock_id"]]
        timeline_rate = Fraction(timeline_clock["frame_rate"]["numerator"],
                                 timeline_clock["frame_rate"]["denominator"])
        ticks_per_frame = Fraction(timeline_clock["ticks_per_second"] * timeline_rate.denominator,
                                   timeline_rate.numerator)
        clip_start_frame = math.floor(
            Fraction(timeline["start"] * timeline_rate.numerator,
                     timeline_clock["ticks_per_second"] * timeline_rate.denominator))
        clip_end_frame = math.ceil(
            Fraction(timeline["end"] * timeline_rate.numerator,
                     timeline_clock["ticks_per_second"] * timeline_rate.denominator))
        keyframes = []
        for op in scale_ops:
            frame = round(float(op["time"]) * frame_rate)
            if not clip_start_frame <= frame < clip_end_frame:
                continue
            tick = frame * ticks_per_frame
            if tick.denominator != 1:
                raise ValueError(f"move effect {effect['id']!r} keyframe falls between timeline ticks")
            keyframes.append({"tick": int(tick), "value": float(op["value"][0]) / 100.0})
        if not keyframes:
            raise ValueError(f"move effect {effect['id']!r} has no scale keys inside its target clip")
        native_moves.append({
            "id": effect["id"], "class_id": "transform.scale",
            "target_id": effect["target_id"],
            "interval": dict(timeline), "parameters": {}, "keyframes": keyframes,
        })
        lowered_move_ids.append(effect["id"])
    return {"grade_spans": grade_spans, "native_moves": native_moves,
            "lowered_move_ids": lowered_move_ids}


def _model_with_native_moves(model: dict, sequence_size: str | None) -> dict:
    """Lower measured scale-only moves to FilmCraft's native picture keys."""
    planned = _project_model_compositions(model, sequence_size)
    lowered_ids = set(planned["lowered_move_ids"])
    if not lowered_ids:
        return model
    lowered = dict(model)
    lowered["effects"] = [effect for effect in model["effects"]
                          if effect["id"] not in lowered_ids]
    lowered["effects"].extend(planned["native_moves"])
    return lowered


def _composite_intermediate_to_final(
    intermediate: Path, final: Path, *, planned: dict, video_codec: str,
) -> dict:
    """Encode the delivery once over the mezzanine intermediate.

    The treated picture lands AT the export path: the treatments
    pass reads the ProRes ``intermediate`` the engine rendered and
    writes ``final`` in one encode, then removes the intermediate so
    the worktree holds the delivery and not the mezzanine. A plan
    with no grade and no bezel never reaches here (the caller
    exports straight to the delivery path); this helper always
    encodes exactly one delivery.
    """
    from library.tools import native_effects_composite as nec

    try:
        composited = nec.composite_declared_treatments(
            intermediate, final, planned=planned, video_codec=video_codec)
    except ProjectModelCliRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - composite errors become refusals
        raise _refuse(f"native treatments failed: {exc}",
                      "the ProRes intermediate is on disk; only the "
                      "grade/bezel delivery encode failed",
                      "check the declared tv_frame asset reads and ffmpeg "
                      "runs, then re-export (the intermediate at "
                      f"{intermediate.name} can be removed)",
                      code="REN_PM_CLI_NATIVE_TREATMENTS") from exc
    try:
        intermediate.unlink(missing_ok=True)
    except OSError:
        pass
    composited["intermediate"] = str(intermediate)
    composited["intermediate_removed"] = not intermediate.exists()
    composited["engine_format"] = "prores"
    composited["lossy_encodes"] = 1
    composited["native_move_effect_ids"] = [row["id"]
                                             for row in planned["native_moves"]]
    return composited


# ── EffectCraft plan + execute ───────────────────────────────


def _ec_op_to_command(op: dict, identity_map: dict) -> list[tuple[str, dict]]:
    kind = op.get("op")
    if kind == "new_comp":
        return [("comp.new", {"name": op["name"], "width": op["width"],
                              "height": op["height"],
                              "frameRate": op["frame_rate"],
                              "duration": op["duration"]})]
    if kind == "import_footage":
        return [("file.import", {"paths": [op["path"]]})]
    if kind == "add_footage_layer":
        params: dict[str, Any] = {"item": op["footage"]}
        if "time" in op:
            params["time"] = op["time"]
        return [("layer.addItem", params)]
    if kind == "add_mask":
        return [("layer.addMask", {"layer": op["layer"], "shape": op["shape"],
                                   "rect": op["rect"]})]
    if kind == "interpret_footage":
        return [("file.interpretFootage", {"frameRate": op["frame_rate"]})]
    if kind == "set_track_matte":
        return [("layer.setTrackMatte", {"layer": op["layer"], "matte": op["matte"],
                                         "kind": op["kind"]})]
    if kind == "apply_effect":
        index = next(
            (entry["effect_index"]
             for entry in (identity_map.get("effects") or {}).values()
             if entry["layer"] == op["layer"]
             and entry["effectcraft_id"] == op["effect_id"]),
            None)
        if index is None:
            raise _refuse(f"effect {op['effect_id']!r} on {op['layer']!r} has no identity-map slot",
                          "the projection's identity map must name every applied effect",
                          "re-project the model before executing",
                          code="REN_PM_CLI_BAD_PROJECTION")
        commands = [("effect.apply", {"layer": op["layer"], "effect": op["effect_id"]})]
        for param, value in (op.get("params") or {}).items():
            commands.append(("prop.set", {"layer": op["layer"],
                                          "path": f"effects/#{index}/{param}",
                                          "value": value}))
        return commands
    if kind == "keyframe":
        return [("prop.addKey", {"layer": op["layer"], "path": op["path"],
                                 "time": op["time"], "value": op["value"]})]
    if kind == "add_text":
        params = {"layer": op.get("layer"), "text": op.get("text")}
        params.update({k: v for k, v in op.items()
                       if k not in ("op", "layer", "text")})
        return [("layer.addText", params)]
    if kind == "add_shape":
        params = {"layer": op.get("layer")}
        params.update({k: v for k, v in op.items()
                       if k not in ("op", "layer")})
        return [("layer.addShape", params)]
    raise _refuse(f"projection op {kind!r} has no CLI mapping",
                  "the projection emitted an op the runner cannot execute",
                  "extend the op mapping with the measured CLI command",
                  code="REN_PM_CLI_BAD_PROJECTION")


def execute_effectcraft(model: dict, *, project_root: Path, out: Path,
                        media_root: Path | None, comp_name: str,
                        width: int, height: int, fps: float,
                        duration: float, render_output: str,
                        render_format: str, project_id: str | None) -> dict:
    """Run the EffectCraft projection on the pinned CLI. Mutating."""
    from library.tools.project_model_effectcraft import (
        CompSpec, RenderSpec, ingest_receipt, project_to_request,
    )

    comp = CompSpec(name=comp_name, width=width, height=height,
                    frame_rate=fps, duration_seconds=duration)
    expected_frames = comp.expected_frames()
    renders = [RenderSpec(format=render_format, output=render_output)]
    try:
        projection = project_to_request(
            model, comp, renders,
            media_root=str(media_root or project_root),
            project_id=project_id)
    except Exception as exc:
        raise _refuse(f"EffectCraft projection refused: {exc}",
                      "the model carries something the pinned registry cannot represent",
                      "re-cut the model onto verified effects and projectable masks",
                      code="REN_PM_CLI_UNPROJECTABLE") from exc

    cli = _effectcraft_cli()
    _check_cli_version(cli, EFFECTCRAFT_VERSION, "EffectCraft")

    commands: list[tuple[str, dict]] = []
    for op in projection.request["operations"]:
        commands.extend(_ec_op_to_command(op, projection.identity_map))

    ecproj = out.parent / f"{out.stem}.ecproj"
    if ecproj.exists():
        raise _refuse(f"refusing to replace existing project {ecproj}",
                      "an execute run never overwrites an engine project",
                      "remove the file or plan to a fresh --out path",
                      code="REN_PM_CLI_PROJECT_EXISTS")
    argv = [str(cli), "run"]
    for command, params in commands:
        argv += [command, json.dumps(params)]
    argv += ["--empty", "--save-as", str(ecproj), "--json"]
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise _refuse("EffectCraft run failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")
    try:
        saved = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise _refuse("EffectCraft run answered non-JSON",
                      str(exc),
                      "re-run against the pinned CLI",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND") from exc
    if str(saved.get("saved")) != str(ecproj):
        raise _refuse("EffectCraft did not save its project",
                      json.dumps(saved, default=str)[:400],
                      "re-run the projection on the pinned CLI",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")

    render_out = out.parent / render_output
    rendered = subprocess.run(
        [str(cli), "render", "--project", str(ecproj), "--comp", comp.name,
         "--out", str(render_out), "--format", "png",
         "--start", "0", "--end", repr(duration), "--channels", "rgba",
         "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        check=False)
    if rendered.returncode != 0:
        raise _refuse("EffectCraft render failed",
                      rendered.stderr.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")
    frames = sorted(render_out.parent.glob(render_out.name.replace("#####", "*")))
    frames = [p for p in frames if p.is_file()]
    if len(frames) != expected_frames:
        raise _refuse(f"EffectCraft rendered {len(frames)} frames, expected {expected_frames}",
                      rendered.stdout.strip()[-400:],
                      "re-render the comp on the pinned CLI",
                      code="REN_PM_CLI_RENDER_UNREADABLE")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt",
         "-of", "json", str(frames[0])],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    try:
        pix_fmt = json.loads(probe.stdout)["streams"][0]["pix_fmt"]
    except (KeyError, TypeError, ValueError, IndexError):
        pix_fmt = ""
    if "a" not in pix_fmt:
        raise _refuse("EffectCraft render has no alpha",
                      f"first frame pix_fmt reads {pix_fmt!r}",
                      "render with --channels rgba",
                      code="REN_PM_CLI_RENDER_UNREADABLE")

    import hashlib

    receipt = {"version": 1, "project_id": projection.project_id,
               "upstream_revision": EFFECTCRAFT_REVISION,
               "engine_version": EFFECTCRAFT_VERSION,
               "project_file": str(ecproj),
               "renders": [{"path": str(frames[0]), "width": comp.width,
                            "height": comp.height, "frames": len(frames),
                            "has_alpha": True}]}
    try:
        updated = ingest_receipt(model, projection, receipt,
                                 input_revision=f"cli:{out.stem}",
                                 render_id=None,
                                 output_digest="sha256:" + hashlib.sha256(
                                     frames[0].read_bytes()).hexdigest())
    except Exception as exc:
        raise _refuse(f"EffectCraft receipt ingest refused: {exc}",
                      "the render did not match the projection it was rendered from",
                      "re-render from the same projection",
                      code="REN_PM_CLI_RECEIPT_MISMATCH") from exc
    ingested_path = out.parent / f"{out.stem}.ingested.json"
    ingested_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return {"engine": "effectcraft",
            "engine_revision": EFFECTCRAFT_REVISION,
            "engine_version": EFFECTCRAFT_VERSION,
            "project": str(ecproj),
            "operations": len(projection.request["operations"]),
            "losses": projection.losses,
            "frames": len(frames),
            "pix_fmt": pix_fmt,
            "ingested_model": str(ingested_path),
            "executed": True}


# ── CLI ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ren project-model",
        description="Plan a project's model onto a headless engine (print); --execute runs it.")
    sub = parser.add_subparsers(dest="command", required=True)
    project = sub.add_parser("project", help="project a model onto an engine plan")
    project.add_argument("project", help="project slug or path")
    project.add_argument("--engine", required=True, choices=("filmcraft", "effectcraft"),
                         help="which headless engine to project onto")
    project.add_argument("--out", required=True, type=Path,
                         help="where to write the plan JSON")
    project.add_argument("--model", default=None,
                         help="explicit model JSON path (default: the project's model store)")
    project.add_argument("--execute", action="store_true",
                         help="run the plan on the pinned engine CLI (default: plan and print only)")
    project.add_argument("--media-root", default=None, type=Path,
                         help="footage directory for relative asset URIs (default: the project root)")
    project.add_argument("--comp", default="Main", help="EffectCraft comp name")
    project.add_argument("--width", type=int, default=96)
    project.add_argument("--height", type=int, default=64)
    project.add_argument("--fps", type=float, default=10.0)
    project.add_argument("--duration", type=float, default=0.5)
    project.add_argument("--render-output", default="comp_#####.png")
    project.add_argument("--render-format", default="rgba_png_sequence")
    project.add_argument("--project-id", default=None)
    project.add_argument("--filmcraft-project", default=None, type=Path,
                         help="engine project path for --execute (default: <out>.fcproj)")
    project.add_argument("--export", default=None, type=Path,
                         help="with --execute on filmcraft, also export the built timeline here")
    project.add_argument("--export-format", default="h264",
                         choices=tuple(sorted(EXPORT_FORMATS)))
    project.add_argument("--preconform", action="store_true",
                         help="with --execute on filmcraft, transcode used a_roll/b_roll "
                              "spans once to edit ProRes and place those (lossy; default off)")
    project.add_argument("--preconform-size", default=None,
                         help="proxy frame as WIDTHxHEIGHT (e.g. 1080x1920); required with "
                              "--preconform and must equal the engine sequence frame")
    project.add_argument("--sequence-size", default=None,
                         help="set the new FilmCraft sequence frame as WIDTHxHEIGHT "
                              "before placing clips (e.g. 1080x1920)")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "project":
            return cmd_project(parser, args)
        parser.error(f"unknown command {args.command}")
    except ProjectModelCliRefused as refused:
        from library.tools.ren_refusal import emit_refusal_result
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    return 0


def cmd_project(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        root = resolve_project_root(args.project)
    except ProjectModelCliRefused as exc:
        parser.error(str(exc.what))
    model, source = load_model(root, args.model)
    media_root = args.media_root

    if args.engine == "effectcraft":
        if args.execute:
            report = execute_effectcraft(
                model, project_root=root, out=args.out, media_root=media_root,
                comp_name=args.comp, width=args.width, height=args.height,
                fps=args.fps, duration=args.duration,
                render_output=args.render_output,
                render_format=args.render_format, project_id=args.project_id)
            args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        from library.tools.project_model_effectcraft import (
            CompSpec, RenderSpec, project_to_request,
        )
        comp = CompSpec(name=args.comp, width=args.width, height=args.height,
                        frame_rate=args.fps, duration_seconds=args.duration)
        renders = [RenderSpec(format=args.render_format, output=args.render_output)]
        try:
            projection = project_to_request(
                model, comp, renders,
                media_root=str(media_root or root),
                project_id=args.project_id)
        except Exception as exc:
            raise _refuse(f"EffectCraft projection refused: {exc}",
                          "the model carries something the pinned registry cannot represent",
                          "re-cut the model onto verified effects and projectable masks",
                          code="REN_PM_CLI_UNPROJECTABLE") from exc
        plan = {"engine": "effectcraft",
                "engine_revision": EFFECTCRAFT_REVISION,
                "engine_version": EFFECTCRAFT_VERSION,
                "model_source": source,
                "project_id": projection.project_id,
                "comp": {"name": comp.name, "width": comp.width,
                         "height": comp.height, "frame_rate": comp.frame_rate,
                         "duration_seconds": comp.duration_seconds},
                "renders": [{"format": r.format, "output": r.output,
                             "start_seconds": r.start_seconds,
                             "end_seconds": r.end_seconds}
                            for r in projection.renders],
                "request": projection.request,
                "identity_map": projection.identity_map,
                "losses": projection.losses,
                "executed": False}
        args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(json.dumps({"engine": "effectcraft", "project_id": projection.project_id,
                          "operations": len(projection.request["operations"]),
                          "losses": projection.losses, "out": str(args.out),
                          "executed": False}))
        return 0

    native = filmcraft_native()
    plan = plan_filmcraft(model, native, project_root=root, sequence_size=args.sequence_size)
    plan["model_source"] = source
    if args.execute:
        receipt = execute_filmcraft(
            model, plan, project_root=root, out=args.out,
            media_root=media_root, filmcraft_project=args.filmcraft_project,
            export_path=args.export, export_format=args.export_format,
            native=native, preconform=args.preconform,
            preconform_size=args.preconform_size,
            sequence_size=args.sequence_size)
        args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(json.dumps({k: v for k, v in receipt.items() if k != "inspected"},
                         indent=2, ensure_ascii=False, default=str))
        return 0
    args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"engine": "filmcraft",
                      "placements": len(plan["placements"]),
                      "edits": plan["edits"],
                      "losses": plan["losses"], "out": str(args.out),
                      "executed": False}))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
