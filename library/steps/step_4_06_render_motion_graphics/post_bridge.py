#!/usr/bin/env python3
"""Step 4.06 post-bridge: render the planned motion-graphics layer.

Takes the model's `motion_graphics_plan` (see `handoff.md` and
`library/tools/motion_graphics_plan.py`), resolves it against the brand
template's palette when there is one, cuts it into non-overlapping
overlay segments and renders each to one overlay artefact with
alpha (`library/tools/overlay_carriage.py`).

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

from library.tools.plan_keys import refuse_unknown_keys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.delivery_format import resolve_delivery_format  # noqa: E402
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN  # noqa: E402
from library.tools.overlay_carriage import (  # noqa: E402
    OVERLAY_FORMAT_NAME,
    OVERLAY_VIDEO_CODEC,
    transcode_in_place,
)
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
        "format": OVERLAY_FORMAT_NAME,
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
    """The one Remotion project, from the one module that locates it.

    Was four `dirname` calls from this file, which is a third derivation
    of a path `shared_environment` owns and the only one that would not
    have followed `PIPELINE_REMOTION_DIR` (docs/SHARED_ENVIRONMENT.md).
    """
    from library.tools import shared_environment
    return str(shared_environment.remotion_dir())


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


def _render_motion_graphics_hyperframes(render_props: dict, dest_path: str,
                                        remotion: str, name: str,
                                        project_folder: str) -> bool:
    """One HyperFrames render of the MotionGraphics composition.

    Judged the same way - False where nothing was drawn, with the
    reason on stderr. The composition HAS a HyperFrames form
    (`hyperframes/compositions/MotionGraphics.html`), so under that
    selection this is the render rather than a fallback. The carriage
    below treats its output like any overlay file: already `qtrle`
    passes through, anything else is transcoded bit-exact or refused.
    """
    from library.tools import hyperframes_render as _hf
    try:
        _hf.render_one_card(
            "MotionGraphics", render_props, dest_path,
            os.path.dirname(os.path.abspath(dest_path)),
            project_folder,
            os.path.dirname(os.path.abspath(remotion)))
    except (_hf.HyperFramesUnavailable,
            _hf.HyperFramesRenderError) as exc:
        print(f"    WARN: HyperFrames render failed for {name}: "
              f"{str(exc)[:200]}", file=sys.stderr)
        return False
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) == 0:
        print(f"    WARN: HyperFrames render reported success but "
              f"{dest_path} is missing or empty", file=sys.stderr)
        return False
    return True


# Element props keys that are PLACEMENT or provenance, never pixels.
# Everything else in an element - `element`, `anchor`, `row`, `runs`,
# `color`, `entrance`/`exit`, `startFrame`/`durationFrames`, `asset`,
# `footprint`, `emphasis`, `data` - draws, and stays in the digest.
#
# What each excluded key is, so a future plan addition lands on the
# right side: `timeline_start`/`timeline_end` are absolute timeline
# bounds, kept beside the frame counts so a reader never divides;
# `timelineProgressStart`/`timelineProgressEnd` are whole-piece
# fractions every element carries so a reader never recomputes them,
# drawn only by `progress_bar` (the one element that keeps them - see
# `_mg_drawing_element`); `timing_basis`/`subject`/`why`/`colorBasis`
# are the plan's provenance and the model's reasoning. Duration stays
# IN: it is the file's frame count, and two reels holding one card
# for genuinely different lengths must still render twice, because
# the pixels really differ.
#
# A NEW metadata key defaults INTO the digest (safe: it re-renders),
# and joins this set only by an edit that says why it draws nothing -
# the same fail direction step 4.05 draws with its own
# `NON_DRAWING_PROPS_KEYS`.
#
# Measured on geo-podcast: 26 of 29 Craig lower thirds and all 27
# Akshita ones differed only in these keys (the progress fractions
# move with each reel's length, the bounds with each placing) yet
# decoded framemd5-identical - 53 renders of 2 pixel-contents, 51 of
# them wasted. The 3 remaining Craig cards genuinely differed
# (different durations), and still digest differently.
MG_NON_DRAWING_ELEMENT_KEYS = frozenset((
    "timeline_start",
    "timeline_end",
    "timing_basis",
    "subject",
    "why",
    "colorBasis",
    "timelineProgressStart",
    "timelineProgressEnd",
    # Above the picture or behind the segmented subject: where the
    # build composites the file, not what pixels it carries. Two
    # placings of the same graphic share the file.
    "layer",
))
"""Element keys that must never decide motion-graphics reuse. Complete,
and load-bearing."""


def _mg_drawing_element(element: dict) -> dict:
    """One element as the renderer sees it: placement and provenance off.

    See `MG_NON_DRAWING_ELEMENT_KEYS`: the plan carries timeline bounds,
    whole-piece progress fractions, timing provenance and the model's
    reasoning on every element so a reader of the props file never has
    to recompute them (`library/tools/motion_graphics_plan.py`), but
    the composition (`remotion-subtitles/src/compositions/
    MotionGraphics/index.tsx`) never reads them - except `progress_bar`,
    the one element that draws its progress fractions, which keeps
    them.
    """
    drawing = {k: v for k, v in element.items()
               if k not in MG_NON_DRAWING_ELEMENT_KEYS}
    if element.get("element") == "progress_bar":
        for key in ("timelineProgressStart", "timelineProgressEnd"):
            if key in element:
                drawing[key] = element[key]
    return drawing


def _mg_drawing_digest(render_props: dict, geometry: str,
                       tight_box: dict | None) -> str:
    """A stable hash of everything about this graphic that draws pixels.

    The props actually rendered (the tightened union canvas where a
    tight carrying verified, the full props otherwise) MINUS the
    placement and provenance keys the plan carries for readers
    (`_mg_drawing_element`), plus the carrying: the resolved geometry
    and the tight box's placement and size, or None for a full-canvas
    draw. Placement - the reel, the index, the absolute timeline span,
    the whole-piece progress fractions - is never part of it: three
    variants playing the same graphic compute the same digest and
    share the file. Duration stays IN through the props'
    `durationInFrames`: it is the file's frame count, and a graphic
    held for genuinely different lengths must still render twice.
    """
    drawing_props = dict(render_props)
    elements = drawing_props.get("elements")
    if isinstance(elements, list):
        drawing_props["elements"] = [
            _mg_drawing_element(item) if isinstance(item, dict) else item
            for item in elements
        ]
    return _drawing_digest_of({
        "props": drawing_props,
        "geometry": geometry,
        "tight_box": tight_box,
    })


def _mg_reuse_key(digest: str, remotion_dir: str,
                  engine: str = "remotion") -> str:
    """The three things that have to match for a skip to be safe, or `""`.

    The drawing digest, the renderer fingerprint (the MotionGraphics
    composition lives in the same `remotion-subtitles/src/` tree the
    fingerprint covers - or the HyperFrames template tree under that
    selection), and the carriage. Empty never matches: an
    unreadable renderer tree renders rather than skips.
    """
    if engine == "hyperframes":
        from library.tools import hyperframes_render as _hf
        renderer_dir = str(_hf.hyperframes_dir(
            os.path.dirname(os.path.abspath(remotion_dir))))
    else:
        renderer_dir = remotion_dir
    return _content_key(digest, renderer_dir, OVERLAY_CARRIAGE,
                        engine=engine)


def _report_palette_state(template_name: str, palette: dict) -> None:
    """Say whose palette answered, before a render, on every run.

    The loud half of the wrong-palette defect: a palette that resolves
    `text`/`outline` but has no usable accent is the shape that once
    drew a whole layer in another series' colour with nothing saying
    so. REPORTED, never a gate - the template refines and does not
    gate, so the layer resolves exactly as it always has and this line
    is what makes the state enumerable rather than silent. The same
    fact travels machine-readably on the step output's planning_basis.
    """
    roles = palette.get("roles") or {}
    drawn = int(palette.get("moments_drawn_in_palette_colours") or 0)
    stated = int(palette.get("moments_drawn_in_plan_stated_colours") or 0)
    if not template_name and not roles:
        print("  palette: no brand palette supplied - every colour is "
              "the plan's own", file=sys.stderr)
        return
    accent = palette.get("has_usable_accent")
    print(f"  palette {template_name!r} resolves {roles} "
          f"(usable accent: {accent}); {drawn} element(s) draw in "
          f"palette colours, {stated} in plan-stated colours",
          file=sys.stderr)
    if accent is False:
        print(f"  NOTE: palette {template_name!r} has no usable accent - "
              f"any entry asking for colour_role 'accent' falls back to "
              f"its own stated colour or is dropped by name",
              file=sys.stderr)


def _sequence_behind_segment(rendered: dict) -> dict:
    """A behind title's .mov as a numbered PNG sequence, in place.

    Rewrites `overlay_path` to the sequence's first frame and records
    the sequence beside it. Refuses (rather than shipping the .mov a
    Loader cannot resolve) where ffmpeg fails or the frame count is
    not what the segment promised.
    """
    source = str(rendered.get("overlay_path", ""))
    want = int(rendered.get("total_frames", 0) or 0)
    if not source.endswith(".mov") or want <= 0:
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": (f"behind_subject segment {rendered.get('segment_id', '?')!r} "
                          f"has no rendered .mov to sequence "
                          f"({source!r})."),
            }
        })
    stem = os.path.splitext(os.path.basename(source))[0]
    pattern = os.path.join(os.path.dirname(source),
                           f"{stem}_behind_%05d.png")
    first = pattern % 0
    proc = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", source,
         "-frames:v", str(want), "-start_number", "0", pattern],
        capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": (f"behind_subject segment {rendered.get('segment_id', '?')!r} "
                          f"would not sequence: {proc.stderr.strip()[:300]}"),
            }
        })
    have = sum(1 for i in range(want)
               if os.path.exists(pattern % i))
    if not os.path.exists(first) or have != want:
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": (f"behind_subject segment {rendered.get('segment_id', '?')!r} "
                          f"sequenced {have}/{want} frames - the Loader "
                          f"would run dry mid-span."),
            }
        })
    rendered["overlay_path"] = first
    rendered["sequence"] = {
        "pattern": pattern,
        "first_frame": first,
        "frame_count": want,
    }
    rendered["format"] = "PNG image sequence (RGBA)"
    return rendered


def render_one_segment(planned: dict, out_dir: str,
                       segment_name: str = "",
                       remotion_dir: str = "",
                       progress: str = "",
                       overlay_geometry: str = None,
                       project_folder: str = "",
                       reuse: bool = False,
                       draw_gain: float = FALLBACK_DRAW_GAIN
                       ) -> Optional[dict]:
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
    tight_refusal = None
    if geometry == "tight":
        from library.tools.mg_tight_box import (
            TightRefusal,
            tighten_motion_graphics_props_with_reason,
        )
        from library.tools.tight_box import (
            TightBoxClipsInk,
            TightBoxMismatch,
        )
        # The timeline this graphic lands on: the delivery format, the
        # same size the props render at. Resolved here rather than
        # trusted from the props so a stale or foreign prop cannot
        # gate itself against the wrong frame.
        timeline_size = tuple(resolve_delivery_format(
            project_folder or None))
        try:
            tight, tight_refusal = \
                tighten_motion_graphics_props_with_reason(
                    props, project_folder or "",
                    timeline_size=timeline_size,
                    draw_gain=draw_gain)
        except (TightBoxClipsInk, TightBoxMismatch) as exc:
            # The clamp gate and the frame bound: this graphic cannot
            # ride a small box on this timeline (or its predicted box
            # is bigger than the frame), so it renders full canvas.
            # SAID, not silent - the caption path records the same
            # fallback as `tight_fallback`, and the artefact sidecar
            # records the machine-readable refusal beside it.
            tight = None
            tight_refusal = getattr(exc, "refusal", None)
            if tight_refusal is None:
                code = ("canvas_larger_than_frame"
                        if isinstance(exc, TightBoxClipsInk)
                        else "placement_unholdable")
                kinds = sorted({
                    str(e.get("element", ""))
                    for e in props.get("elements", [])
                    if e.get("element")})
                tight_refusal = TightRefusal(
                    reason=code, element=",".join(kinds) or None,
                    detail=str(exc)[:500])
            tight_fallback = str(exc)[:500]
            print(f"  {progress} union unplaceable as tight - full "
                  f"canvas: {tight_fallback[:300]}", file=sys.stderr)
        if tight is None and tight_refusal is not None:
            # A structural refusal (accents, assets, zones, coverage):
            # the record says why, exactly as the raised paths do.
            if not tight_fallback:
                tight_fallback = tight_refusal.message()
            print(f"  {progress} union covers the frame - full canvas "
                  f"({tight_refusal.reason})", file=sys.stderr)
        elif tight is not None:
            render_props = tight.props
            print(f"  {progress} tight {tight.width}x{tight.height} "
                  f"(full {tight.full_width}x{tight.full_height})",
                  file=sys.stderr)
    else:
        # Full carrying was DECLARED (explicit geometry or the
        # project's own `pipeline.motion_graphics_overlay_geometry`),
        # so the tighten path is never asked - and the artefact still
        # says why it is full canvas.
        from library.tools.mg_tight_box import TightRefusal
        source = ("explicit overlay_geometry='full'"
                  if overlay_geometry else
                  "project declaration "
                  "(pipeline.motion_graphics_overlay_geometry: full)")
        tight_refusal = TightRefusal(
            reason="geometry_full_declared", element=None,
            detail=f"full-canvas carrying declared via {source}")
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
        # What the file IS, for the record and the digest: a clamp or
        # frame-bound refusal resets to full - the file IS full canvas.
        if geometry == "tight" and tight is None and tight_fallback:
            return "full"
        return geometry

    def _name_and_key(drawn_props, box):
        from library.tools import graphics_renderer as _engines
        digest = _mg_drawing_digest(
            drawn_props, _resolved_geometry(), box)
        content_name = motion_segment_name(project_folder, digest)
        return (content_name,
                os.path.join(out_dir, f"{content_name}.mov"),
                os.path.join(out_dir, f"{content_name}_props.json"),
                os.path.join(out_dir, f"{content_name}_reuse_key.txt"),
                _mg_reuse_key(
                    digest, remotion,
                    _engines.resolve_engine(project_folder or None)))

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

    def _write_tightness_sidecar(props_path):
        """The artefact's own account of its carrying, beside its props.

        Written on a fresh render AND on a reuse hit, so every overlay
        file on disk carries one whatever route produced it. A
        full-canvas artefact without one is what the build-time guard
        refuses (`mg_tight_box.check_motion_graphics_files`).
        """
        from library.tools.mg_tight_box import (
            sidecar_path_for,
            tightness_record,
        )
        record = tightness_record(
            tight, tight_refusal,
            int(props.get("width", 0)), int(props.get("height", 0)))
        try:
            with open(sidecar_path_for(props_path), "w",
                      encoding="utf-8") as handle:
                json.dump(record, handle, indent=2)
        except OSError as exc:
            print(f"    note: could not record the tightness sidecar "
                  f"for {placement_label} ({exc}); the build-time "
                  f"guard will refuse the artefact without it",
                  file=sys.stderr)

    def _reuse_hit(content_name, overlay_path, key_path, key,
                   props_path):
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
        _write_tightness_sidecar(props_path)
        return _entry(overlay_path, "reused", key)

    def _maybe_bind_measured(rendered):
        """A predicted refusal, rebound from the render's own pixels.

        The predicted tighten path sizes the canvas from the
        composition's literals, and that prediction misses the drawn
        ink wherever the renderer shapes it differently - over-wide
        on a `title_lockup` it sized past the frame
        (`canvas_larger_than_frame`), while the drawn ink binds
        cleanly. So a segment the prediction refused is not left full
        canvas on the prediction's word: the full-canvas file is kept
        as the probe and `mg_tight_box.bind_probe_tight` measures the
        drawn union across every frame and crops the probe around it -
        the same measured route the speaker lower thirds bind through,
        never a re-render, so the copy cannot re-wrap. A segment that
        does not bind stays full canvas with its NAMED reason.

        Only GEOMETRY refusals retry (`MEASURED_RETRY_REASONS`): a
        refusal about what the element IS (corner accents spanning by
        design, an asset file nothing measures) is not a geometry the
        pixels can change, so it stands. A DECLARED full canvas
        (`geometry_full_declared`, or any explicit full geometry)
        never asks - the declaration wins over the pixels.
        """
        if rendered is None or geometry != "tight":
            return rendered
        if tight is not None or not tight_fallback:
            return rendered
        from library.tools.mg_tight_box import MEASURED_RETRY_REASONS
        from library.tools import mg_tight_box as mgt
        if (tight_refusal is not None
                and tight_refusal.reason not in MEASURED_RETRY_REASONS):
            return rendered
        mgt.bind_probe_tight(
            placement_label, {"props": props}, rendered,
            int(props.get("width", 0)), int(props.get("height", 0)),
            draw_gain=draw_gain)
        return rendered

    content_name, overlay_path, props_path, key_path, key = \
        _name_and_key(render_props, _tight_record())
    # A tight graphic is drawn to its own small canvas and placed with
    # the Scaling/Pan/Tilt the box computed (`tight_box.placement`),
    # read back at placement time - see `library/tools/tight_box.py`.
    render_path = overlay_path

    hit = _reuse_hit(content_name, overlay_path, key_path, key,
                     props_path)
    if hit is not None:
        return _maybe_bind_measured(hit)
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
    from library.tools import graphics_renderer as _engines
    use_hyperframes = _engines.is_hyperframes(project_folder or None)
    if use_hyperframes:
        print(f"    engine: HyperFrames for {placement_label} "
              f"(MotionGraphics has a HyperFrames form; selected by "
              f"{_engines.USER_SETTING_KEY} or the project's "
              f"pipeline.graphics_renderer)", file=sys.stderr)
        if not _render_motion_graphics_hyperframes(
                render_props, render_path, remotion, placement_label,
                project_folder or ""):
            return None
    elif not _render_motion_graphics_file(props_path, render_path,
                                          remotion, placement_label):
        return None

    # ── The carriage ──
    #
    # Remotion cannot write it. `renderMedia` offers no `qtrle` codec,
    # and the ffmpeg Remotion bundles is compiled `--disable-encoders`
    # with an explicit enable list that has no `qtrle` in it, so even
    # an `ffmpegOverride` rewriting the encoder arguments fails with
    # `Unknown encoder 'qtrle'` (measured 2026-09-12). Unlike the
    # caption path - which was already re-encoding, in the tight crop,
    # and gets the change for free - a motion graphic therefore pays
    # one transcode after its render. Measured on this project's own
    # graphics: 0.31 s per file, against a render of several seconds,
    # for a file 4.35x smaller. It is VERIFIED bit-exact before it
    # replaces the render, and a failure returns None like every other
    # failure here: reported and skipped, never substituted.
    carried = transcode_in_place(render_path)
    if carried.get("error"):
        print(f"    WARN: {placement_label} rendered but could not be "
              f"carried as {OVERLAY_VIDEO_CODEC}: "
              f"{carried['error'][:300]}", file=sys.stderr)
        return None
    if carried.get("changed"):
        print(f"    carried as {OVERLAY_VIDEO_CODEC}: "
              f"{carried['before']:,} -> {carried['after']:,} bytes",
              file=sys.stderr)

    if tight is not None:
        print(f"    tight {tight.width}x{tight.height} placed with "
              f"Pan {tight.placement['pan']:.1f} / "
              f"Tilt {tight.placement['tilt']:.1f}", file=sys.stderr)

    print(f"    OK: {overlay_path}", file=sys.stderr)

    _write_tightness_sidecar(props_path)

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

    return _maybe_bind_measured(_entry(overlay_path, "rendered", key))


# The model-plan entry keys `motion_graphics_plan` reads. Anything
# else on an entry is REFUSED, never dropped: an unread key is how a
# probe's SFX `at_word` landed 3.06 s early on the block start.
# `element_key`, `asset_file`, `colour` and `rationale` are the legacy
# spellings of `element`, `asset`, `color` and `why`; `anchor_phrase`
# and `hold_seconds`/`subject` are the anchored-timing form, read
# beside the timed `start_seconds`/`duration_seconds` pair. Timeline
# bounds and progress fractions (`timeline_start`, `timing_basis`,
# ...) are writer-side keys the planner module adds to RENDERED
# elements - a plan entry carrying them is refused like any other key
# nothing reads.
MG_PLAN_ENTRY_KEYS = frozenset({
    "element",
    "element_key",
    "anchor",
    "start_seconds",
    "duration_seconds",
    "anchor_phrase",
    "hold_seconds",
    "subject",
    "asset",
    "asset_file",
    "data",
    "entrance",
    "exit",
    "row",
    "footprint",
    "emphasis",
    "why",
    "rationale",
    "copy",
    "colour_role",
    "color",
    "colour",
    "layer",
})


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

    # A plan key nothing here reads is refused before anything renders
    # - the refusal travels the post-bridge retry path so the model
    # re-plans instead of an entry drawing without what the key asked
    # for. (The reels path builds its own entries through the shared
    # planner module, never through this function, so it is unaffected.)
    refuse_unknown_keys(motion_graphics_plan or [], MG_PLAN_ENTRY_KEYS,
                         step="render_motion_graphics",
                         plan="motion_graphics_plan")

    # The Remotion project comes from `_remotion_dir`, which is the one
    # locator, rather than a fourth derivation beside it.  PILOT_ROOT is
    # still needed below, to put `library/` on sys.path - that is the
    # REPO, which is a different question from where the renderer is.
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    REMOTION_DIR = _remotion_dir()

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
            fps=fps, width=width, height=height,
            project_folder=project_folder)
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

    # Whose palette answers colour roles, so a resolved colour names
    # its source on its colorBasis. The runner injects the resolved
    # template dict for any step declaring the `brand_template` input
    # (library/processes/edit_video/run_pipeline.py); "" where the
    # project named no template, which resolves nothing and changes
    # nothing downstream.
    brand_template_name = str(
        (data.get("brand_template") or {}).get("series_id") or "")
    segments_plan, resolved = generate_motion_props(
        motion_graphics_plan,
        audio_spine,
        fps=fps, width=width, height=height,
        # The palette REFINES an entry's colour_role. A project that
        # names no template resolves nothing here and its plan states
        # its own colours; the layer is not reduced by the absence.
        brand_style=data.get("brand_style", {}),
        brand_template_name=brand_template_name,
        # Not a gate: the ONLY thing read out of it here is the caption
        # style's `position`, which says which band the captions own.
        # library/tools/caption_band.py.
        brand_effect=data.get("brand_effect", {}),
        project_folder=project_folder,
        asked=motion_graphics_plan is not None,
    )
    basis = resolved.basis_record()
    _report_palette_state(brand_template_name, basis["palette"])

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
            "behind_subject_overlays": {
                "available": False,
                "segments": [],
                "total_segments": 0,
                "reason": "the plan names no behind_subject entries",
            },
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
        # A behind_subject segment is precomposited 1:1 under the
        # picture's subject matte at compile time, so it always
        # renders full canvas - a tight canvas would need a transform
        # nobody declared. The explicit 'full' writes the
        # geometry_full_declared sidecar, which is what the tightness
        # guard below asks for.
        planned_geometry = (
            "full" if planned.get("layer") == "behind_subject"
            else geometry)
        rendered = render_one_segment(
            planned, mg_output_dir, remotion_dir=REMOTION_DIR,
            progress=f"[{i+1}/{len(segments_plan)}]",
            overlay_geometry=planned_geometry,
            project_folder=project_folder,
            reuse=reuse)
        if rendered is not None:
            rendered["layer"] = planned.get("layer", "above")
            segments.append(rendered)

    print(f"\nRendered {len(segments)}/{len(segments_plan)} motion graphics "
          f"segments", file=sys.stderr)

    # The build-time guard: every full-canvas artefact this pass
    # produced must have DECLARED why it is full canvas (the sidecar
    # `render_one_segment` wrote beside each props file). A missing
    # or silent sidecar is the defect sixteen lower thirds shipped
    # as, so it refuses the step rather than warning past it. Cheap
    # and deterministic - JSON reads only, no renders.
    from library.tools.mg_tight_box import check_motion_graphics_files
    guard_errors, tightness_census = check_motion_graphics_files(
        [str(seg["overlay_path"])[:-len(".mov")] + "_props.json"
         for seg in segments
         if str(seg.get("overlay_path", "")).endswith(".mov")],
        width, height)
    print(f"Motion-graphics tightness: {tightness_census['tight']} "
          f"tight, {tightness_census['full_by_design']} full by design, "
          f"{tightness_census['full_with_reason']} full with reason, "
          f"{tightness_census['full_undeclared']} undeclared",
          file=sys.stderr)
    if guard_errors:
        raise MotionGraphicsRenderRefused({
            "motion_graphics_overlay": {
                "available": False,
                "segments": [],
                "error": ("undeclared full-canvas overlay(s): "
                          + "; ".join(guard_errors[:5])),
                "tightness": tightness_census,
            }
        })

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

    # Above-picture segments ride motion-graphics rows; behind_subject
    # ones are precomposed under the subject's matte at compile time
    # into overlays that join the same rows - so they travel on their
    # own output key, where compile grounds each against its matte
    # (library/tools/behind_subject.py) and appends the verified
    # precomp to the row placer list. A behind request with no usable
    # matte refuses there, never silently rejoins this list.
    #
    # A behind title reaches compile as a PNG image sequence, not as
    # the .mov it rendered as: compile precomposites frame by frame,
    # and the numbered sequence is the random-access frame source it
    # reads. The .mov stays on disk as provenance; the sequence is
    # what the manifest names.
    for seg in segments:
        if seg.get("layer", "above") == "behind_subject":
            _sequence_behind_segment(seg)
    above_segments = [s for s in segments
                      if s.get("layer", "above") != "behind_subject"]
    behind_segments = [s for s in segments
                       if s.get("layer", "above") == "behind_subject"]

    # An empty side states WHICH absence it is: the hollow check
    # (`check_output_is_real`) reads an `available: false` with no
    # reason as a failed run, and a behind-only plan - or an
    # above-only one - is a legitimate layer, not a failure.
    mg_overlay = {
        "available": len(above_segments) > 0,
        "segments": above_segments,
        "format": OVERLAY_FORMAT_NAME,
        "has_alpha": True,
        "fps": fps,
        "total_segments": len(above_segments),
        "planning_basis": basis,
        # What this pass carried, so a reader knows without
        # re-deriving it per segment.
        "geometry": geometry,
        # The tight/refused census the guard computed above - the
        # same counts the conformance sweep surfaces per project.
        "tightness": tightness_census,
    }
    if not above_segments:
        mg_overlay["reason"] = (
            f"no above-picture entries planned; "
            f"{len(behind_segments)} behind_subject segment(s) travel "
            f"on behind_subject_overlays" if behind_segments else
            "no above-picture entries planned and none rendered")
    behind_overlay = {
        "available": len(behind_segments) > 0,
        "segments": behind_segments,
        "format": "PNG image sequence (RGBA)",
        "has_alpha": True,
        "fps": fps,
        "total_segments": len(behind_segments),
    }
    if not behind_segments:
        behind_overlay["reason"] = (
            "the plan names no behind_subject entries")

    return {
        "motion_graphics_overlay": mg_overlay,
        "behind_subject_overlays": behind_overlay,
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
