#!/usr/bin/env python3
"""Step 4.06 post-bridge: render the planned motion-graphics layer.

Takes the model's `motion_graphics_plan` (see `handoff.md` and
`library/tools/motion_graphics_plan.py`), resolves it against the brand
template's palette when there is one, cuts it into non-overlapping
overlay segments and renders each to a ProRes 4444 clip with alpha.

**The layer is planned, not derived.**  This file used to resolve the
whole thing from `effect.motion_accents` and `effect.motion_progress_bar`,
so a project naming no brand template rendered eight fully transparent
segments and reported motion graphics as delivered.  The captain's
ruling of 2026-09-02 is that the gate was the bug.

**Every element carries its own timing.**  A segment's span comes from
the entries clustered into it, never from a spine block; several
elements can be on screen at once, on different rows, inside one
segment.

This step is also where the OTHER two Remotion-rendered things a brand
template may declare become files, because they need the same prepped
Remotion project and both have to exist before compile_manifest can
reference them:

* composition-mode bookends (``content.bookends``, V1) - see
  ``library/tools/bookend_render.py``;
* timed text moments (``effect.timed_text_overlay``, V6) - see
  ``library/tools/timed_text_overlay.py``.  A template that declares no
  moments renders none, and says so in the output rather than emitting
  ``available: false``, which the runner reads as a failed step.

Classification: Hybrid / Content generation
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from generate_motion_props import PLAN_KEY, generate_motion_props

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402
from library.tools.overlay_mode import (  # noqa: E402
    GEOMETRIES,
    OVERLAY_CARRIAGE,
    resolve_motion_graphics_geometry,
)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.render_cache import (  # noqa: E402
    content_key as _content_key,
    drawing_digest as _drawing_digest_of,
    motion_segment_name,
)


def _timed_text_output(segments: list, fps: int) -> dict:
    """The step's timed_text_overlay output for the segments it rendered.

    An undeclared slot carries NO `available` key on purpose:
    `check_output_is_real` in run_pipeline treats `available: false`
    anywhere in a step's output as a failed run, and "this template
    declares no timed text" is the normal case, not a failure.
    """
    if not segments:
        return {
            "declared": False,
            "segments": [],
            "reason": "brand template declares no effect.timed_text_overlay",
        }
    return {
        "declared": True,
        "available": True,
        "segments": segments,
        "format": "ProRes 4444",
        "has_alpha": True,
        "fps": fps,
        "total_segments": len(segments),
    }


def _nothing_to_draw_output(reason: str, basis: dict = None) -> dict:
    """The motion_graphics_overlay output when no segment would draw.

    Shaped like `_timed_text_output`'s undeclared case and for the same
    reason: no `available` key at all. `check_output_is_real` in
    run_pipeline reads `available: false` anywhere in a step's output as
    a failed run, and an empty layer is a legitimate answer.

    **It carries the basis.** An empty layer that says WHICH absence it
    is cannot be misread as a clean one: `no_elements_planned` is a
    decision the model took and `every_entry_dropped` is the absence of
    one surviving, and the dropped entries are named with their reasons.
    Same line `vfx_plan_basis` draws.
    """
    out = {
        "declared": False,
        "segments": [],
        "total_segments": 0,
        "reason": reason,
    }
    if basis is not None:
        out["planning_basis"] = basis
    return out


class MotionGraphicsRenderRefused(Exception):
    """The step cannot deliver overlays, and says so carrying its payload.

    The three refusal paths used to emit and then `sys.exit(1)` where
    they stood.  Both halves still happen in `main()`; a function that
    kills its caller's process cannot be called by one.
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(
            payload.get("motion_graphics_overlay", {}).get("error", "refused"))


def _remotion_dir() -> str:
    """The one Remotion project, from this file's own location.

    The same four `dirname` calls `render_motion_graphics` already made
    inline, so a caller that has no `data` dict can find it too.
    """
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        "remotion-subtitles")


def _render_motion_graphics_file(props_path: str, dest_path: str,
                                 remotion: str, name: str) -> bool:
    """One `npx remotion render` of the MotionGraphics composition.

    Judged by what it RETURNS - True only when the process exited 0.
    One spelling, because a tight graphic may render twice: once to
    its own union canvas, and again full canvas where the pad onto the
    delivery frame could not be proved.
    """
    try:
        result = subprocess.run(
            ["npx", "remotion", "render",
             "MotionGraphics",
             dest_path,
             "--props", props_path,
             "--codec", "prores",
             "--prores-profile", "4444",
             "--image-format", "png",
             "--transparent",
             ],
            cwd=remotion,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print(f"    WARN: Render timed out for {name}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"    WARN: Render failed: {result.stderr[:200]}",
              file=sys.stderr)
        return False
    return True


def _mg_drawing_digest(render_props: dict, geometry: str,
                       tight_box: dict | None) -> str:
    """A stable hash of everything about this graphic that draws pixels.

    The props actually rendered (the tightened union canvas where a
    tight carrying verified, the full props otherwise) plus the
    carrying: the resolved geometry and the tight box's placement and
    size, or None for a full-canvas draw. Placement - the reel, the
    index, the absolute timeline span - is never part of it: three
    variants playing the same graphic compute the same digest and
    share the file. Duration stays IN through the props'
    `durationInFrames`: it is the file's frame count, and a graphic
    held for genuinely different lengths must still render twice.
    """
    return _drawing_digest_of({
        "props": render_props,
        "geometry": geometry,
        "tight_box": tight_box,
    })


def _mg_reuse_key(digest: str, remotion_dir: str) -> str:
    """The three things that have to match for a skip to be safe, or `""`.

    The drawing digest, the renderer fingerprint (the MotionGraphics
    composition lives in the same `remotion-subtitles/src/` tree the
    fingerprint covers), and the carriage. Empty never matches: an
    unreadable renderer tree renders rather than skips.
    """
    return _content_key(digest, remotion_dir, OVERLAY_CARRIAGE)


def render_one_segment(planned: dict, out_dir: str,
                       segment_name: str = "",
                       remotion_dir: str = "",
                       progress: str = "",
                       overlay_geometry: str = None,
                       project_folder: str = "",
                       reuse: bool = False) -> Optional[dict]:
    """Render ONE motion-graphics segment, and return what was placed.

    Lifted out of :func:`render_motion_graphics`'s loop unchanged - same
    composition, same codec, same profile, same `--transparent`, same
    timeout - so there is ONE `npx remotion render MotionGraphics` call
    in this repository rather than two.

    It exists because the REELS path needs the unit and not the pass:
    `reel_build` plans a graphic on one reel's own timebase and has no
    master spine, no bookends and no timed text to render beside it.
    That is the same reason `subtitles.render_segment` sits beside
    `subtitles.render`, and it is registered the same way
    (`library/tools/operations.py`).

    `segment_name` names the PLACING, not the file.  The master pass
    leaves it empty and gets `mg_<index>`, which is what every existing
    record is called; a reel passes its own prefixed name
    (`vox_<reel-slug>_<index>`), because AGENTS.md 5 requires an
    overlay record to carry its context.  The FILE is content-keyed -
    `mg_<project>_<digest>` (`library/tools/render_cache.py`) - so two
    variants rendering the same graphic share it: the name travels on
    the entry as `placement_label`, never on disk.  Two reels writing
    one graphic is then one file placed twice, not one reel's graphic
    on another reel's timeline.

    `overlay_geometry` chooses the carrying (`library/tools/overlay_mode.py`):
    a tight canvas instead of the delivery frame. Explicit values win;
    otherwise the project's declaration is read, and a project that
    declares nothing renders tight (the default since 2026-09-10 -
    `library/tools/overlay_mode.py`). The tight canvas is the
    drawn union (`library/tools/mg_tight_box.py`); compositions whose
    union is effectively the frame (corner accents, asset elements)
    render full-canvas even when tight is asked, and say so.

    Returns None where the render failed or timed out - REPORTED on
    stderr and skipped, never substituted.
    """
    import sys
    geometry = overlay_geometry or resolve_motion_graphics_geometry(
        project_folder or None)
    if geometry not in GEOMETRIES:
        raise ValueError(
            f"Unknown overlay_geometry {geometry!r}; "
            f"known: {list(GEOMETRIES)}.")
    props = planned["props"]
    # The tight canvas, where declared. Computed from the same props
    # the full render draws from, so the box fits the layout the frame
    # would have drawn - see library/tools/mg_tight_box.py.
    tight = None
    render_props = props
    tight_fallback = ""
    if geometry == "tight":
        from library.tools.mg_tight_box import tighten_motion_graphics_props
        from library.tools.tight_box import TightBoxMismatch
        # The timeline this graphic lands on: the delivery format, the
        # same size the props render at. Resolved here rather than
        # trusted from the props so a stale or foreign prop cannot
        # gate itself against the wrong frame.
        timeline_size = tuple(resolve_delivery_format(
            project_folder or None))
        try:
            tight = tighten_motion_graphics_props(
                props, project_folder or "",
                timeline_size=timeline_size)
        except TightBoxMismatch as exc:
            # The clamp gate: this graphic cannot ride a small box on
            # this timeline, so it renders full canvas. SAID, not
            # silent - the caption path records the same fallback as
            # `tight_fallback`.
            tight = None
            tight_fallback = str(exc)[:500]
            print(f"  {progress} union unplaceable as tight - full "
                  f"canvas: {tight_fallback[:300]}", file=sys.stderr)
        if tight is None and not tight_fallback:
            print(f"  {progress} union covers the frame - full canvas",
                   file=sys.stderr)
        elif tight is not None:
            render_props = tight.props
            print(f"  {progress} tight {tight.width}x{tight.height} "
                  f"(full {tight.full_width}x{tight.full_height})",
                  file=sys.stderr)
    # The placing this render serves. Recorded on the entry, never on
    # disk: the file is content-keyed below.
    placement_label = segment_name or f"mg_{planned['index']:03d}"
    remotion = remotion_dir or _remotion_dir()

    def _tight_record():
        return ({
            "width": tight.width,
            "height": tight.height,
            "placement": tight.placement,
        } if tight is not None else None)

    def _resolved_geometry():
        # What the file IS, for the record and the digest: a clamp
        # refusal resets to full - the file IS full canvas.
        if geometry == "tight" and tight is None and tight_fallback:
            return "full"
        return geometry

    def _name_and_key(drawn_props, box):
        digest = _mg_drawing_digest(
            drawn_props, _resolved_geometry(), box)
        content_name = motion_segment_name(project_folder, digest)
        return (content_name,
                os.path.join(out_dir, f"{content_name}.mov"),
                os.path.join(out_dir, f"{content_name}_props.json"),
                os.path.join(out_dir, f"{content_name}_reuse_key.txt"),
                _mg_reuse_key(digest, remotion))

    def _read_recorded_key(key_path):
        try:
            with open(key_path, encoding="utf-8") as handle:
                return handle.read().strip()
        except OSError:
            return ""

    def _entry(overlay_path, provenance, reuse_key_value):
        return {
            "segment_id": os.path.splitext(
                os.path.basename(overlay_path))[0],
            "placement_label": placement_label,
            "overlay_path": overlay_path,
            "timeline_start": planned["timeline_start"],
            "timeline_end": planned["timeline_end"],
            "total_frames": planned["total_frames"],
            # Which LANE this segment plays on. Segments on one lane
            # never overlap, so a lane is a Resolve row - the placers
            # read it rather than assuming one row per overlay kind
            # (`motion_graphics_plan.plan_segments`).
            "lane": int(planned.get("lane", 0)),
            "element_count": planned["element_count"],
            "elements": planned["elements"],
            "provenance": provenance,
            "reuse_key": reuse_key_value,
            # The carrying, so a reader knows what the file IS without
            # re-deriving it: full-canvas video is today's path, and the
            # tight canvas is the option. A clamp refusal resets the
            # record to full - the file IS full canvas - exactly as the
            # caption path does when its tight output does not verify.
            "geometry": _resolved_geometry(),
            # Why this graphic is full canvas although tight was asked,
            # or "" when that did not happen. The caption record carries
            # the same field under the same name.
            "tight_fallback": tight_fallback,
            # What was DRAWN and where it lands: a tight file rides
            # the placement in `tight_box` at build time; a full-canvas
            # file needs no transform.
            "tight_box": _tight_record(),
        }

    def _reuse_hit(content_name, overlay_path, key_path, key):
        """A recorded identical render, paired back - or None.

        The two-factor hit: the file AND its recorded key present and
        matching. Presence alone never hits.
        """
        if not reuse or not key:
            return None
        if _read_recorded_key(key_path) != key:
            return None
        if not os.path.isfile(overlay_path):
            return None
        print(f"  {progress} {placement_label} reused "
              f"({content_name}, tl:{planned['timeline_start']:.2f}-"
              f"{planned['timeline_end']:.2f}s)", file=sys.stderr)
        return _entry(overlay_path, "reused", key)

    content_name, overlay_path, props_path, key_path, key = \
        _name_and_key(render_props, _tight_record())
    # A tight graphic is drawn to its own small canvas and placed with
    # the Scaling/Pan/Tilt the box computed (`tight_box.placement`),
    # read back at placement time - see `library/tools/tight_box.py`.
    render_path = overlay_path

    hit = _reuse_hit(content_name, overlay_path, key_path, key)
    if hit is not None:
        return hit
    if reuse and not key:
        print(f"    note: renderer fingerprint unavailable, rendering "
              f"{placement_label} rather than reusing", file=sys.stderr)

    with open(props_path, "w", encoding="utf-8") as f:
        json.dump(render_props, f, indent=2)

    print(f"  {progress} {placement_label} "
          f"({', '.join(planned['elements'])}, "
          f"{planned['total_frames']}f, "
          f"tl:{planned['timeline_start']:.2f}-"
          f"{planned['timeline_end']:.2f}s)", file=sys.stderr)

    if not _render_motion_graphics_file(props_path, render_path,
                                        remotion, placement_label):
        return None

    if tight is not None:
        print(f"    tight {tight.width}x{tight.height} placed with "
              f"Pan {tight.placement['pan']:.1f} / "
              f"Tilt {tight.placement['tilt']:.1f}", file=sys.stderr)

    print(f"    OK: {overlay_path}", file=sys.stderr)

    # Recorded only after a render that SUCCEEDED, so a failed render
    # leaves no key claiming the file is current.
    if key:
        try:
            with open(key_path, "w", encoding="utf-8") as handle:
                handle.write(key)
        except OSError as exc:
            print(f"    note: could not record the reuse key for "
                  f"{placement_label} ({exc}); it will re-render next time",
                  file=sys.stderr)

    return _entry(overlay_path, "rendered", key)


def render_motion_graphics(data: dict, reuse: bool = False) -> dict:
    """Render the model's motion-graphics plan, its bookends and timed text.

    `data` is the merged dict the runner hands a post-bridge: the step's
    inputs, the pre-bridge's output, and the model's answer.  Raises
    `MotionGraphicsRenderRefused` where nothing can be delivered.

    `reuse` is OFF by default, so a plain run re-renders exactly as it
    always has - reuse is the optimisation and fresh is the contract,
    the same line step 4.05 draws.  A caller rebuilding identical
    graphics (the reels path) opts in.
    """
    import sys
    audio_spine = data.get("audio_spine", {})
    project_folder = data.get("project_folder", "")
    # The model's answer. `enhancement_spec` and `creative_direction`
    # are declared and routed for the PROMPT, which is where they are
    # read now that this step has one - the runner projects them into
    # the context and the plan comes back here already decided.
    motion_graphics_plan = data.get(PLAN_KEY)

    # Find Remotion project (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")

    if not os.path.isdir(REMOTION_DIR):
        print(f"ERROR: Remotion project not found at {REMOTION_DIR}",
              file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/"
            }
        })

    # Prep Remotion: link brand assets (logos, fonts) into Remotion's
    # public/brand/ directory so staticFile("brand/...") resolves at render
    # time.  There is no composition staging or Root.tsx generation -
    # compositions live in src/compositions/ and Root.tsx is committed.
    try:
        sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
        from tools.remotion_brand_linker import prep_remotion
        prep_result = prep_remotion(project_folder=project_folder)
        brand_info = prep_result.get("brand", {})
        if brand_info.get("linked"):
            print(f"  Linked {brand_info['count']} brand assets from {brand_info['source']}",
                  file=sys.stderr)
    except ImportError:
        pass

    # Output directory. No repo fallback: see step 4.05 and
    # library/tools/project_layout.py.
    layout = ProjectLayout(project_folder)
    mg_output_dir = str(layout.write_dir(
        Area.MOTION_GRAPHICS_SEGMENTS, step="render_motion_graphics"))

    # The timebase. Every planned span is expressed in timeline
    # seconds and converted here; nothing is derived from a block.
    fps = data.get("project_fps", 30)
    # The overlay is rendered AT THE DELIVERY FORMAT, so it composites
    # 1:1 onto the timeline. Reading a source-derived resolution here is
    # what put a vertical overlay on a landscape timeline as a lighter
    # band down the middle. See library/tools/delivery_format.py.
    width, height = resolve_delivery_format(project_folder)
    # Bookends first: an intro / outro / end card the brand template
    # declared reaches the spine in step 2.05, and has to be a file before
    # compile_manifest can put it on V1. A template that declares none -
    # which is every template unless someone opted in - renders none.
    # See library/tools/bookends.py.
    try:
        sys.path.insert(0, PILOT_ROOT)
        from library.tools.bookend_render import render_declared_bookends
        bookends_rendered = render_declared_bookends(
            audio_spine.get("structure", []), REMOTION_DIR,
            fps=fps, width=width, height=height)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"bookend render failed: {e}",
            }
        })
    if bookends_rendered:
        print(f"Bookends ready: {len(bookends_rendered)}", file=sys.stderr)

    # Timed text moments. Declare none and render none - the opt-in shape
    # of every effect slot. The spine is what a moment is timed FROM, and
    # what bounds a moment given in absolute frames; see
    # library/tools/timed_text_overlay.py.
    #
    # The PROJECT's own `effect.timed_text_overlay` wins over the brand
    # template's, because a card is series artwork and artwork is a
    # project asset (docs/ASSET_LIBRARY_PLAN.md section 3, ratified
    # 2026-08-20). resolve_declaration is where that precedence lives.
    structure = audio_spine.get("structure", [])
    try:
        from library.tools.timed_text_overlay import resolve_declaration
        from library.tools.timed_text_render import render_timed_text_segments
        timed_text_segments = render_timed_text_segments(
            resolve_declaration(data.get("brand_effect", {}), project_folder),
            REMOTION_DIR,
            str(layout.write_dir(
                Area.TIMED_TEXT_SEGMENTS, step="render_motion_graphics")),
            fps=fps, width=width, height=height,
            spine_structure=structure, project_folder=project_folder,
        )
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": f"timed text render failed: {e}",
            }
        })

    timed_text_overlay = _timed_text_output(timed_text_segments, fps)

    segments_plan, resolved = generate_motion_props(
        motion_graphics_plan,
        audio_spine,
        fps=fps, width=width, height=height,
        # The palette REFINES an entry's colour_role. A project that
        # names no template resolves nothing here and its plan states
        # its own colours; the layer is not reduced by the absence.
        brand_style=data.get("brand_style", {}),
        # Not a gate: the ONLY thing read out of it here is the caption
        # style's `position`, which says which band the captions own.
        # library/tools/caption_band.py.
        brand_effect=data.get("brand_effect", {}),
        project_folder=project_folder,
        asked=motion_graphics_plan is not None,
    )
    basis = resolved.basis_record()

    for dropped in resolved.dropped:
        print(f"  dropped {dropped.element}: {dropped.reason}"
              + (f" - {dropped.detail}" if dropped.detail else ""),
              file=sys.stderr)

    if not segments_plan:
        print(f"No motion graphics to draw ({basis['basis']}): "
              f"{basis['what_the_basis_means']}", file=sys.stderr)
        return {
            "motion_graphics_overlay": _nothing_to_draw_output(
                basis["what_the_basis_means"], basis),
            "timed_text_overlay": timed_text_overlay,
        }

    print(f"Rendering {len(segments_plan)} motion graphics segments "
          f"({basis['resolved']} elements)...", file=sys.stderr)

    # Explicit values win; otherwise the project's declaration, and a
    # project that declares nothing renders tight (`overlay_mode`).
    geometry = resolve_motion_graphics_geometry(project_folder or None)
    print(f"Motion-graphics carrying: {geometry} geometry",
          file=sys.stderr)

    segments = []
    for i, planned in enumerate(segments_plan):
        rendered = render_one_segment(
            planned, mg_output_dir, remotion_dir=REMOTION_DIR,
            progress=f"[{i+1}/{len(segments_plan)}]",
            overlay_geometry=geometry,
            project_folder=project_folder,
            reuse=reuse)
        if rendered is not None:
            segments.append(rendered)

    print(f"\nRendered {len(segments)}/{len(segments_plan)} motion graphics "
          f"segments", file=sys.stderr)

    if segments:
        try:
            sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            from tools.qa.asset_qa import verify_alpha_channel
            if not verify_alpha_channel(segments[0]["overlay_path"]):
                print("WARNING: QA Check 2.1 Failed: First motion graphics "
                      "segment missing alpha or purely black", file=sys.stderr)
            else:
                print("QA Check 2.1 Passed: Motion graphics alpha verified",
                      file=sys.stderr)
        except Exception as e:
            print(f"WARNING: QA Check 2.1 execution failed: {e}",
                  file=sys.stderr)

    return {
        "motion_graphics_overlay": {
            "available": len(segments) > 0,
            "segments": segments,
            "format": "ProRes 4444",
            "has_alpha": True,
            "fps": fps,
            "total_segments": len(segments),
            "planning_basis": basis,
            # What this pass carried, so a reader knows without
            # re-deriving it per segment.
            "geometry": geometry,
        },
        "timed_text_overlay": timed_text_overlay,
    }


def main():
    import sys
    try:
        result = render_motion_graphics(json.loads(sys.stdin.read()))
    except MotionGraphicsRenderRefused as refusal:
        json.dump(refusal.payload, sys.stdout, indent=2)
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
