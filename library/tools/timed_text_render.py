"""Render the timed text segments a brand template declares.

``library/tools/timed_text_overlay.py`` says what a declaration means and
groups its moments into non-overlapping segments; this turns each segment
into an overlay artefact with an alpha channel
(``library/tools/overlay_carriage.py`` owns what one IS), which is what
``resolve_build_timeline`` can place on V6 over the finished picture.

Called by ``render_motion_graphics`` (4.06), beside the bookend render,
because both are "make the files the manifest is about to reference" and
both need the Remotion project already prepped.

A failed render RAISES. The two gates that would otherwise notice are the
coverage assertion in ``compile_manifest`` and the black-frame probe in
6.02 - and neither notices this one at all, because a missing overlay
leaves the picture underneath intact. So a template that declared an
episode number would simply ship without it, which is the exact defect
this module was written to end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from library.tools.timed_text_overlay import (
    TIMED_TEXT_COMPOSITION,
    plan_timed_text_segments,
)
from library.tools.overlay_carriage import (
    OVERLAY_VIDEO_CODEC,
    transcode_in_place,
)
from library.tools import perf_ledger
from library.tools.project_layout import AREAS, Area

# Where the rendered segments go.  The directory is the layout owner's to
# name (library/tools/project_layout.py); this is the same string, kept
# for callers that want the leaf name rather than a project path.
TIMED_TEXT_RENDER_DIRNAME = Path(
    AREAS[Area.TIMED_TEXT_SEGMENTS].relpath).name

# A timed text segment is a few seconds of type on transparency. If it has
# not rendered in this long, something is wrong that waiting will not fix.
RENDER_TIMEOUT_SECONDS = 300


class TimedTextRenderError(RuntimeError):
    """A declared timed text segment could not be turned into a file."""


def render_timed_text_segments(
    template_effect: dict,
    remotion_dir: str,
    output_dir: str,
    fps: int = 30,
    *,
    width: int,
    height: int,
    spine_structure: list | None = None,
    project_folder: str | None = None,
    stream=sys.stderr,
) -> list[dict]:
    """Render every segment the declaration implies; ``[]`` if undeclared.

    `width`/`height` are the DECLARED delivery frame and have no default:
    a default is what let these render vertical onto a landscape timeline
    (project 001's lighter central band). The caller states the frame -
    see library/tools/delivery_format.py.

    Each returned entry carries what the renderer needs to place the file:
    ``overlay_path``, ``timeline_start``, ``timeline_end`` and
    ``total_frames``, the same shape ``motion_graphics_overlay.segments``
    uses.
    """
    segments = plan_timed_text_segments(
        template_effect, fps=fps, width=width, height=height,
        spine_structure=spine_structure, project_folder=project_folder,
    )
    if not segments:
        return []

    os.makedirs(output_dir, exist_ok=True)
    print(f"  Timed text: {len(segments)} segment(s) declared",
          file=stream)

    rendered = []
    for segment in segments:
        name = f"timed_text_{segment['index']:03d}"
        out_path = os.path.join(output_dir, f"{name}.mov")
        props_path = os.path.join(output_dir, f"{name}_props.json")
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(segment["props"], f, indent=2, sort_keys=True)

        print(f"    [{segment['index']}] {name}: "
              f"{segment['moment_count']} moment(s), "
              f"{segment['total_frames']}f, "
              f"tl:{segment['timeline_start']:.2f}-"
              f"{segment['timeline_end']:.2f}s", file=stream)

        _render_one(name, out_path, props_path, remotion_dir,
                    project_folder=project_folder or "", stream=stream)

        rendered.append({
            "overlay_path": out_path,
            "props_path": props_path,
            "timeline_start": segment["timeline_start"],
            "timeline_end": segment["timeline_end"],
            "total_frames": segment["total_frames"],
            "moment_count": segment["moment_count"],
            "bytes": os.path.getsize(out_path),
        })
        print(f"      OK: {out_path} ({rendered[-1]['bytes']} bytes)",
              file=stream)

    return rendered


def _render_one(name: str, out_path: str, props_path: str,
                remotion_dir: str, *, project_folder: str = "",
                stream=sys.stderr) -> None:
    """One render of TimedTextOverlay, judged by its result.

    Through HyperFrames where that engine is selected (the project
    wins over the user - `library/tools/graphics_renderer.py`), which
    stages the comp beside vendored GSAP and the brand files, renders
    the PNG sequence, premultiplies into the overlay carriage and
    stitches `qtrle` itself; through `npx remotion render` otherwise,
    with the carriage transcode below.
    """
    from library.tools import graphics_renderer as _engines
    if _engines.is_hyperframes(project_folder or None):
        _render_one_hyperframes(
            name, out_path, props_path, remotion_dir,
            project_folder=project_folder or "", stream=stream)
        return
    command = [
        "npx", "remotion", "render",
        TIMED_TEXT_COMPOSITION, out_path,
        "--props", props_path,
        "--codec", "prores",
        "--prores-profile", "4444",
        "--image-format", "png",
        # Without this the composition renders on black and the segment
        # hides the picture it is supposed to sit over.
        "--transparent",
    ]
    try:
        result = perf_ledger.run(
            "remotion_render",
            command, cwd=remotion_dir, capture_output=True, check=False,
            # The pipeline writes UTF-8 status glyphs; the locale codec
            # would fail a render on a check mark in a child's stderr.
            text=True, encoding="utf-8", errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimedTextRenderError(
            f"timed text segment '{name}' render timed out after "
            f"{RENDER_TIMEOUT_SECONDS}s") from exc

    if result.returncode != 0:
        raise TimedTextRenderError(
            f"timed text segment '{name}' render failed: "
            f"{result.stderr[-500:]}")
    if not os.path.exists(out_path):
        raise TimedTextRenderError(
            f"timed text segment '{name}' reported success but wrote no "
            f"file at {out_path}")

    # ── The carriage ──
    #
    # What Remotion wrote is not the artefact: it renders ProRes 4444,
    # and it cannot write the overlay codec - `renderMedia` takes no
    # `qtrle`, and its BUNDLED ffmpeg is compiled without that encoder
    # entirely (`library/tools/overlay_carriage.py`). Step 4.06 already
    # reports these segments as `OVERLAY_FORMAT_NAME`, so a file left
    # as ProRes would be an artefact encoded the old way and stamped
    # the new way. The transcode is VERIFIED bit-exact before it
    # replaces the render, and a failure RAISES like every other
    # failure here: a missing overlay leaves the picture underneath
    # intact, so a warning would ship an episode silently without the
    # text the template declared.
    carried = transcode_in_place(out_path)
    if carried.get("error"):
        raise TimedTextRenderError(
            f"timed text segment '{name}' rendered but could not be "
            f"carried as {OVERLAY_VIDEO_CODEC}: "
            f"{carried['error'][:400]}")
    if carried.get("changed"):
        print(f"      carried as {OVERLAY_VIDEO_CODEC}: "
              f"{carried['before']:,} -> {carried['after']:,} bytes",
              file=stream)


def _render_one_hyperframes(name: str, out_path: str, props_path: str,
                            remotion_dir: str, *,
                            project_folder: str = "",
                            stream=sys.stderr) -> None:
    """One HyperFrames render of TimedTextOverlay, judged by its result.

    The composition HAS a HyperFrames form (unlike MotionGraphics and
    project-owned staged compositions, which stay on Remotion, stated).
    Reads the props file the caller already wrote, draws through
    `hyperframes_render.render_one_card` - which premultiplies and
    stitches the carriage codec itself, so there is no transcode below
    - and raises exactly like the Remotion path: a missing overlay
    leaves the picture underneath intact, so a warning would ship an
    episode silently without the text the template declared.
    """
    import json as _json
    import os as _os

    from library.tools import hyperframes_render as _hf
    try:
        with open(props_path, encoding="utf-8") as handle:
            props = _json.load(handle)
    except (OSError, ValueError) as exc:
        raise TimedTextRenderError(
            f"timed text segment '{name}' carries no readable props at "
            f"{props_path}: {exc}") from exc
    print(f"      engine: HyperFrames "
          f"(selected by graphics_renderer; {TIMED_TEXT_COMPOSITION} "
          f"has a HyperFrames form)", file=stream)
    try:
        _hf.render_one_card(
            TIMED_TEXT_COMPOSITION, props, out_path,
            _os.path.dirname(_os.path.abspath(out_path)),
            project_folder,
            _os.path.dirname(_os.path.abspath(remotion_dir)))
    except (_hf.HyperFramesUnavailable,
            _hf.HyperFramesRenderError) as exc:
        raise TimedTextRenderError(
            f"timed text segment '{name}' HyperFrames render failed: "
            f"{exc}") from exc
    if not _os.path.isfile(out_path):
        raise TimedTextRenderError(
            f"timed text segment '{name}' reported success but wrote no "
            f"file at {out_path}")
