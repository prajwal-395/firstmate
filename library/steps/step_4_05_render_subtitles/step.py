#!/usr/bin/env python3
"""
Step 4.05: Render Subtitles (Remotion)

Takes the subtitle plan from step 4.01 and renders it to per-spine-block
ProRes 4444 video overlays with alpha channel using Remotion.

Each spine block with subtitles gets its own rendered overlay clip. The
Resolve builder (step 6.01) places each segment at its timeline position
on V3.

Workflow:
  1. Generate per-block Remotion input props from the subtitle plan
     (via generate_remotion_props.py)
  2. Write props to per-block JSON files
  3. Run `npx remotion render` for each block
  4. Return the list of rendered overlay paths for the manifest

Classification: Deterministic / Direct Action
Idempotent: Yes (same subtitle plan -> same rendered overlays)

Input:  {
    "subtitle_plan": { subtitle_entries: [...] },
    "audio_spine": { structure: [...] }
}
Output: {
    "subtitle_overlay": {
        "available": bool,
        "segments": [
            {
                "overlay_path": str,
                "timeline_start": float,
                "timeline_end": float,
                "block_position": int,
                "total_frames": int
            }
        ],
        "format": "ProRes 4444",
        "has_alpha": true,
        "fps": 30
    }
}

The work is `render_subtitle_overlays`, which takes its inputs as
arguments and RETURNS the payload.  `main()` owns the process: claiming
stdout, reading stdin, emitting, and choosing the exit code.  The split
is what lets an operation name this step's work without going through
the DAG, and it is why the refusals below travel as an exception
carrying their payload rather than as `emit` plus `sys.exit` inline -
a function that kills its caller's process cannot be called by one.
See AGENTS.md 3.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from generate_remotion_props import generate_subtitle_props_per_block

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools.delivery_format import resolve_delivery_format
from library.tools.remotion_batch import (
    PersistentRenderer,
    RendererUnavailable,
)
from library.tools.overlay_mode import (
    CONTAINERS,
    GEOMETRIES,
    resolve_overlay_container,
    resolve_overlay_geometry,
)
from library.tools.project_layout import Area, ProjectLayout
from library.tools.qa.subtitle_qa import (
    ALPHA_INK_THRESHOLD,
    check_caption_geometry,
)
from library.tools.step_stdout import claim_stdout, emit
from library.tools.subtitle_segment_id import (
    assert_named_timeline,
    assert_unique_segment_names,
    segment_binding,
    segment_identifier,
    timeline_scope,
)

# Where the Remotion project lives, repo-relative.  A module constant so
# a caller can point the render somewhere else without reconstructing
# the path from `__file__` itself.
PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
REMOTION_DIR = os.path.join(PILOT_ROOT, "remotion-subtitles")

# Above this share of failed segment renders the step refuses rather
# than delivering a partial overlay track.
MAX_RENDER_FAILURE_RATE = 0.1

# The frame filename pattern for sequence renders, in Remotion's
# `[frame]` vocabulary (zero-padded, one file per frame). Resolve
# detects the pattern as one image-sequence pool item - measured with
# `frame-00.png` … `frame-46.png` naming, 2026-09-08.
FRAME_PATTERN = "frame-[frame].png"


class SubtitleRenderRefused(Exception):
    """The step cannot deliver overlays, and says so carrying its payload.

    The three refusal paths used to `emit(...)` and then `sys.exit(1)`
    where they stood.  Both halves are kept - `main()` still emits the
    same payload and still exits 1 - but they happen at the process
    boundary, so a caller that is not a process gets an exception it can
    catch instead of a dead interpreter.
    """

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(
            payload.get("subtitle_overlay", {}).get("error", "refused"))


RENDERED = "rendered"
REUSED = "reused"
FAILED = "failed"
PROVENANCES = (RENDERED, REUSED, FAILED)
"""What actually happened to one segment.  Complete, and required.

A two-valued return - an entry, or None for failure - has no room for a
third outcome, and skip-if-present is a third outcome.  While the return
was `dict | None` the caller inferred success from the ABSENCE of a
value, so a skipped segment could only be reported by pretending it had
been rendered.  Inference from absence is what made the collapse
possible; every segment now says which of these three it is."""


def _props_digest(props: dict) -> str:
    """A stable hash of everything about this segment that draws pixels.

    The whole props object, including its `_`-prefixed metadata.  Hashing
    more than strictly draws is the SAFE direction: it re-renders on a
    metadata-only change, where hashing less would skip on a real one.
    """
    canonical = json.dumps(props, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def renderer_fingerprint(remotion_dir: str) -> str:
    """The identity of the code and fonts that turn props into pixels.

    Props do NOT capture the Remotion composition or the bundled font, so
    a props hash alone would skip every segment forever after an edit to
    `remotion-subtitles/src/` - the pixels change and the key does not.
    That is exactly the defect `library/tools/code_identity.py` exists to
    remove one layer down, in its own words: "the system reports success
    while the work did not happen."

    Returns `""` when the tree cannot be read, and `""` NEVER matches -
    see `_reuse_key`.  Unavailable evidence must not read as matching
    evidence.
    """
    root = Path(remotion_dir)
    parts = []
    for pattern in ("src/**/*.tsx", "src/**/*.ts", "public/fonts/*"):
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return ""
            parts.append(f"{path.relative_to(root)}={digest}")
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def _reuse_key(props: dict, remotion_dir: str) -> str:
    """The pair that has to match for a skip to be safe, or `""`.

    Empty means "cannot be established", and every comparison against it
    fails, so an unreadable renderer tree renders rather than skips.
    """
    fingerprint = renderer_fingerprint(remotion_dir)
    if not fingerprint:
        return ""
    return f"{_props_digest(props)}+{fingerprint}"


def _tally(segments) -> dict:
    """How many of each provenance, DERIVED from the segments themselves.

    Never counted alongside them.  A separate counter is a second source
    of truth for one fact, and the two drift the first time an early exit
    is added - which is how `Rendered N/M` came to report work that had
    not happened.  A segment with no provenance raises rather than being
    counted as anything.
    """
    counts = {name: 0 for name in PROVENANCES}
    for segment in segments:
        provenance = segment.get("provenance")
        if provenance not in counts:
            raise ValueError(
                f"segment {segment.get('segment_id', '?')} reports "
                f"provenance {provenance!r}; every segment must say which "
                f"of {PROVENANCES} happened to it.")
        counts[provenance] += 1
    return counts


class _TightFailed(Exception):
    """The tight path cannot deliver this segment, with the reason.

    Raised inside the probe/measure gate and converted to a FAILED
    entry at the one place that owns entries - never warned past, so a
    box that clips ink refuses the step through the existing
    availability rule instead of landing cut-off text on a timeline.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _box_sidecar_path(out_dir: str, segment_name: str, suffix: str) -> str:
    return os.path.join(out_dir, f"{segment_name}{suffix}_box.json")


def _probe_tight_box(props: dict, out_dir: str, engine,
                     remotion_dir: str, container: str,
                     probe_mov: str | None, progress: str):
    """Measure this segment's box off a decoded probe render.

    Returns `(box_or_None, probe_tmpdir, probe_frames, union_or_None)`:
    None where the probe draws nothing (the caller keeps the
    full-canvas path), else the measured `TightBox` with the frames it
    was measured from. The caller owns `probe_tmpdir` - render, verify,
    then delete it. Raises `_TightFailed` where no exact box exists.
    """
    import shutil
    import tempfile

    from library.tools.overlay_placement import (
        sequence_frame_paths as _sequence_paths,
    )
    from library.tools.tight_box import (
        TightBoxClipsInk,
        TightBoxMismatch,
        extract_frames,
        ink_union_of_frames,
        tighten_measured,
    )

    probe_tmpdir = tempfile.mkdtemp(prefix="probe_", dir=out_dir)
    try:
        if probe_mov:
            probe_frames = extract_frames(probe_mov, probe_tmpdir)
        elif container == "frames":
            probe_props_path = os.path.join(probe_tmpdir,
                                            "probe_props.json")
            with open(probe_props_path, "w") as handle:
                json.dump(props, handle, indent=2)
            ok, error = engine.render(probe_props_path, probe_tmpdir,
                                      sequence=True)
            if not ok:
                raise _TightFailed(
                    f"probe render failed: "
                    f"{(error or '').strip()[:300] or 'render failed'}")
            probe_frames = _sequence_paths(probe_tmpdir)
            if not probe_frames:
                raise _TightFailed(
                    "probe render reported success but holds no frames")
        else:
            probe_mov_path = os.path.join(probe_tmpdir, "probe.mov")
            probe_props_path = os.path.join(probe_tmpdir,
                                            "probe_props.json")
            with open(probe_props_path, "w") as handle:
                json.dump(props, handle, indent=2)
            ok, error = engine.render(probe_props_path, probe_mov_path,
                                      sequence=False)
            if not ok:
                raise _TightFailed(
                    f"probe render failed: "
                    f"{(error or '').strip()[:300] or 'render failed'}")
            probe_frames = extract_frames(probe_mov_path, probe_tmpdir)
        try:
            union = ink_union_of_frames(probe_frames)
        except TightBoxMismatch as exc:
            raise _TightFailed(str(exc)) from exc
        if union is None:
            print(f"  {progress} probe draws nothing - full canvas",
                  file=sys.stderr)
            return None, probe_tmpdir, probe_frames, None
        try:
            box = tighten_measured(props, union)
        except (TightBoxClipsInk, ValueError) as exc:
            raise _TightFailed(str(exc)) from exc
        print(f"  {progress} measured {box.width}x{box.height} "
              f"(full {box.full_width}x{box.full_height}, "
              f"{union.inked_frames} inked frames)", file=sys.stderr)
        return box, probe_tmpdir, probe_frames, union
    except Exception:
        shutil.rmtree(probe_tmpdir, ignore_errors=True)
        raise


class SubprocessRenderer:
    """Today's mechanism: one `npx remotion render` process per card.

    THE RENDERER SEAM.  `render_one_segment` decides WHICH cards render
    and reports WHAT CAME BACK; a renderer decides only HOW one card
    becomes pixels.  Keeping them apart means the expensive question -
    763 process launches for a 19-reel pass, which is what makes the
    caption path unusable at scale - can be answered by replacing this
    class with a bundle-once/render-many one, without touching selection,
    provenance or reporting.

    The contract is two methods and no more:

        render(props_path, overlay_path, sequence=False) -> (ok, error)
        close()                                          -> None, optional

    A replacement holding expensive state - a Remotion bundle, a browser -
    should build it LAZILY, on the first card that actually renders.
    `render_one_segment` can return without rendering at all: with reuse
    on, a region-scoped pass skips most cards, so an eagerly-built bundle
    would pay its whole cost to render one card and make the region path
    slower than the thing it replaced.
    """

    TIMEOUT_SECONDS = 180

    def __init__(self, remotion_dir: str):
        self.remotion_dir = remotion_dir

    def render(self, props_path: str, overlay_path: str, sequence: bool = False):
        """Draw one card, as video or as a PNG sequence.

        `sequence` renders straight to frames (`--sequence`) into
        `overlay_path` as a directory - the frames-direct container, so
        no intermediate video is rendered first. Video (the default) is
        today's stitched ProRes path, unchanged.
        """
        args = ["npx", "remotion", "render",
                "SubtitleOverlay",
                overlay_path,
                "--props", props_path,
                "--image-format", "png",
                "--transparent",
                ]
        if sequence:
            args += ["--sequence",
                     "--image-sequence-pattern", FRAME_PATTERN]
        else:
            args += ["--codec", "prores",
                     "--prores-profile", "4444"]
        try:
            result = subprocess.run(
                args,
                cwd=self.remotion_dir,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=self.TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return False, f"render timed out after {self.TIMEOUT_SECONDS}s"
        if result.returncode != 0:
            return False, (result.stderr or "").strip() or \
                f"exit {result.returncode}"
        return True, ""

    def close(self):
        """Nothing to release - a subprocess owns nothing between cards."""


CAPTION_RENDERERS = ("subprocess", "persistent")
"""The renderers the caption step can use. Complete, and required."""

DEFAULT_CAPTION_RENDERER = "subprocess"
"""Today's mechanism stays the default until the persistent path is
proven. The captain's ruling, 2026-09-09: retain the version within the
pipeline as is until the other version is known to work as wanted. A
change that silently switches the renderer under approved reels is the
one outcome to avoid, so this default moves only on their word."""


class PersistentCaptionRenderer:
    """The bundle-once renderer behind the same two-method seam.

    `SubprocessRenderer` above is unchanged: one `npx remotion render`
    per card. This class holds ONE `PersistentRenderer`
    (`library/tools/remotion_batch.py`) - one node process, one bundle,
    many cards - and answers the same contract:

        render(props_path, overlay_path, sequence=False) -> (ok, error)
        close()                                          -> None

    so `render_one_segment` needs no touch: selection, provenance and
    reporting cannot tell which renderer drew the card.

    LAZY, as the seam requires. Construction starts nothing - the
    bundle is paid on the first card that actually renders, and a pass
    that draws nothing pays nothing. `close()` on a renderer that never
    started is a no-op.

    The two failure kinds stay distinct. A card that fails to draw
    returns `(False, error)` and the renderer stays up; a dead renderer
    RAISES `RendererUnavailable`, and the orchestrator stops the pass
    rather than marching every remaining card into a closed pipe.

    The codec path carries: the serve script this holds renders
    `codec: "prores"` with `proResProfile: "4444"` - the same options
    the subprocess call passes as `--codec prores --prores-profile
    4444` - because both go through the same `renderOne` in
    `render-batch.mjs`.
    """

    def __init__(self, remotion_dir: str,
                 composition: str = "SubtitleOverlay"):
        # The persistent renderer takes a repo root, not a Remotion
        # dir; the parent of `remotion-subtitles/` IS the root. Derived
        # rather than imported, so an overridden remotion_dir (tests,
        # alternate checkouts) still resolves against itself.
        repo_root = os.path.dirname(os.path.abspath(remotion_dir))
        self._inner = PersistentRenderer(composition=composition,
                                         repo_root=repo_root)

    def render(self, props_path: str, overlay_path: str,
               sequence: bool = False):
        """Draw one card. `(ok, error)` - the step's seam, unchanged.

        `sequence=True` refuses loudly: this renderer stitches video
        and cannot draw a frame sequence, and a sequence reported as
        drawn would be success reported while the work did not happen.
        """
        return self._inner.render(props_path, overlay_path,
                                  sequence=sequence)

    def close(self):
        """Idempotent, like the renderer it holds."""
        self._inner.close()

    def __enter__(self):
        # Deliberately does NOT start, like the renderer it holds: the
        # bundle is paid on the first card that actually renders, so a
        # `with` block that draws nothing pays nothing.
        return self

    def __exit__(self, *exc):
        self.close()
        return False        # never swallow the exception that got us here


def render_one_segment(props: dict, out_dir: str, timeline_label: str,
                       remotion_dir: str = None,
                       progress: str = "",
                       reuse: bool = False,
                       renderer=None,
                       overlay_geometry: str = None,
                       overlay_container: str = None,
                       project_folder: str = "",
                       probe_mov: str | None = None) -> dict:
    """Render ONE subtitle segment.  ALWAYS returns an entry.

    The per-segment unit, split out from the orchestrator's loop because
    that is where a re-render decides things: whether this segment is
    already on disk for these exact props, and whether it falls inside a
    region being redone.  Both are questions about ONE segment, and
    neither has anywhere to live while the loop body is inline.

    The entry carries `provenance`, one of `PROVENANCES`, and there is no
    default: a failed render is REPORTED rather than dropped, because a
    dropped segment is a segment the manifest never learns about, and
    `compile_manifest._assert_subtitle_overlay_matches_plan` then refuses
    the compile for a reason unrelated to the real fault.

    `reuse` is OFF by default, so a plain run re-renders exactly as it
    always has.  Reuse is the optimisation and fresh is the contract:
    it trades ~7.7s a segment for the possibility of serving a stale
    overlay, and stale-but-reported-fresh is the defect class this
    refactor exists to remove.  A caller asking for a region, or asking
    for reuse outright, opts in.

    A skip requires BOTH the overlay and its recorded reuse key to be on
    disk and to match - never mere presence.  The filename is built from
    `SEGMENT_BINDING_KEYS`, which carries no caption content, so a
    text-only correction produces the identical name: measured on project
    001, changing every caption in a block changed 0 of 8 filenames.
    Skipping on presence would skip exactly the work an operator asked
    for.

    `overlay_geometry` / `overlay_container` choose the alternative
    carrying (`library/tools/overlay_mode.py`): a tight canvas instead
    of the delivery frame, a PNG sequence instead of a stitched mov.
    Explicit values win; otherwise the project's declaration is read,
    and a project that declares nothing renders exactly as before.

    Where the geometry is tight the box is MEASURED off a decoded
    probe render, never predicted: a full-canvas probe is rendered to
    a temp dir (or `probe_mov` is measured directly where the caller
    hands one over - a migration whose full renders already exist),
    the ink union across its frames sizes the canvas, and the tight
    output is verified frame-by-frame against the probe before it is
    kept. A probe that draws nothing keeps the full-canvas path;
    anything else that cannot deliver exactly FAILS the segment.
    """
    remotion_dir = remotion_dir or REMOTION_DIR

    geometry = overlay_geometry or resolve_overlay_geometry(
        project_folder or None)
    container = overlay_container or resolve_overlay_container(
        project_folder or None)
    if geometry not in GEOMETRIES:
        raise ValueError(
            f"Unknown overlay_geometry {geometry!r}; "
            f"known: {list(GEOMETRIES)}.")
    if container not in CONTAINERS:
        raise ValueError(
            f"Unknown overlay_container {container!r}; "
            f"known: {list(CONTAINERS)}.")
    is_frames = container == "frames"

    block_pos = props.get("_block_position")
    tl_start = props.get("_timeline_start")
    tl_end = props.get("_timeline_end")
    total_frames = props["durationInFrames"]
    num_subs = len(props.get("subtitles", []))

    # The tight canvas, where declared, is MEASURED off a decoded
    # probe further down - never predicted (the PIL predictor
    # under-measures against Chromium and clips ink). The suffix is
    # known up front; a probe that draws nothing resets it to the
    # full-canvas name when it falls back.
    tight = None
    suffix = "_tight" if geometry == "tight" else ""
    render_props = props

    # Generate output path.
    #
    # The name BINDS the segment to its speaker, its timeline and the
    # source audio span it was transcribed from. It used to be
    # `sub_block_<block_position>`, an ordinal within one spine, and
    # this directory is per PROJECT rather than per timeline - so a
    # reel's `body_1` silently overwrote the master's, and no name
    # said whose speech it captioned. See
    # library/tools/subtitle_segment_id.py.
    #
    # The geometry/container ride in the FILENAME (`_tight`, `.frames`)
    # so one directory can hold today's render beside the alternative
    # without either overwriting the other: the reuse key already
    # separates them, but a listing should say so too.
    binding = segment_binding(
        timeline=timeline_label,
        speaker=props.get("_speaker"),
        block_position=block_pos,
        source_clip_id=props.get("_source_clip_id"),
        source_start=props.get("_source_start"),
        source_end=props.get("_source_end"),
    )
    segment_name = segment_identifier(binding)
    # Frame directories carry NO extension: the CLI's `--sequence`
    # mode treats its output as a directory and refuses one that has
    # an extension (`..._tight.frames` fails with "cannot have an
    # extension. Got: frames").
    if is_frames:
        overlay_path = os.path.join(out_dir, f"{segment_name}{suffix}_frames")
    else:
        overlay_path = os.path.join(out_dir, f"{segment_name}{suffix}.mov")
    props_path = os.path.join(out_dir, f"{segment_name}{suffix}_props.json")
    key_path = os.path.join(out_dir, f"{segment_name}{suffix}_reuse_key.txt")

    def _frames_on_disk() -> bool:
        """A sequence skip needs every frame, not just the directory."""
        try:
            names = os.listdir(overlay_path)
        except OSError:
            return False
        return (sum(1 for name in names if name.endswith(".png"))
                == total_frames)

    def _overlay_on_disk() -> bool:
        if is_frames:
            return os.path.isdir(overlay_path) and _frames_on_disk()
        return os.path.isfile(overlay_path)

    def _placement_record() -> dict:
        if tight is None:
            return None
        return {
            "width": tight.width,
            "height": tight.height,
            "placement": tight.placement,
        }

    def _frames_record() -> dict:
        if not is_frames:
            return None
        return {
            "dir": overlay_path,
            "pattern": FRAME_PATTERN,
            "count": total_frames,
        }

    def entry(provenance: str, **extra) -> dict:
        """One segment's manifest entry.  `provenance` is never defaulted."""
        if provenance not in PROVENANCES:
            raise ValueError(
                f"provenance must be one of {PROVENANCES}, got "
                f"{provenance!r}. A segment that does not say what "
                f"happened to it is the thing this field exists to stop.")
        record = {
            # Empty for a sequence: what reaches Resolve is the frame
            # directory in `frames`, and a path here would send readers
            # to a file that was never rendered.
            "overlay_path": overlay_path if not is_frames else "",
            "segment_id": segment_name,
            # The unabridged binding. The filename slugs and truncates;
            # this is what a reader checks a segment against its audio
            # with, without parsing a name.
            "binding": binding,
            "timeline_start": tl_start,
            "timeline_end": tl_end,
            "block_position": block_pos,
            "provenance": provenance,
            # The carrying, so a reader knows what the file (or the
            # directory) IS without re-deriving it: full-canvas video
            # is today's path, and everything else is the option.
            "geometry": geometry,
            "container": container,
            # Where a tight clip lands. None for full-canvas, which
            # needs no transform.
            "tight_box": _placement_record(),
            # Where a sequence lives. None for stitched video.
            "frames": _frames_record(),
        }
        record.update(extra)
        return record

    def measured(provenance: str) -> dict:
        return entry(
            provenance,
            reuse_key=key,
            # The rendered clip carries animation handles either side of
            # the content; these trim them off at placement time so blocks
            # sit on their true bounds and never overlap.
            source_in_frame=props["_source_in_frame"],
            source_out_frame=props["_source_out_frame"],
            total_frames=props["_source_out_frame"] - props["_source_in_frame"],
            rendered_frames=total_frames,
        )

    # The reuse key digests what DRAWS - the full props, plus the
    # renderer fingerprint. The tight output is a deterministic
    # function of those two (same props through the same renderer
    # measure the same box), so one key covers both geometries and a
    # reuse hit needs no probe render. Placement itself is restored
    # from the box sidecar below, never re-derived.
    key = _reuse_key(props, remotion_dir)

    # ── Skip only on proven-identical CONTENT ──
    if reuse:
        recorded = ""
        try:
            recorded = Path(key_path).read_text(encoding="utf-8").strip()
        except OSError:
            recorded = ""
        if key and recorded == key and _overlay_on_disk():
            if geometry == "tight":
                # Placement lives in the box sidecar, not in the
                # filename: restore it without a probe render. A
                # missing or unreadable sidecar (e.g. a predictor-era
                # file) falls through to a fresh measured render -
                # never an assumed placement.
                try:
                    with open(_box_sidecar_path(out_dir, segment_name,
                                                suffix)) as handle:
                        sidecar = json.load(handle)
                    from library.tools.tight_box import TightBox
                    union = sidecar["union"]
                    tight = TightBox(
                        width=int(sidecar["width"]),
                        height=int(sidecar["height"]),
                        props={},
                        placement=sidecar["placement"],
                        union_w=float(union["x1"] - union["x0"]),
                        union_h=float(union["y1"] - union["y0"]),
                        full_width=int(props.get("width", 0)),
                        full_height=int(props.get("height", 0)),
                    )
                    render_props = props
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    print(f"    note: {segment_name} reuses its key but "
                          f"its box sidecar is unreadable ({exc}); "
                          f"re-rendering measured rather than assuming",
                          file=sys.stderr)
                else:
                    print(f"  {progress} {segment_name}{suffix} reused "
                          f"(tl:{tl_start:.1f}-{tl_end:.1f}s)",
                          file=sys.stderr)
                    return measured(REUSED)
            else:
                print(f"  {progress} {segment_name}{suffix} reused "
                      f"(tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)
                return measured(REUSED)
        if not key:
            print(f"    note: renderer fingerprint unavailable, rendering "
                  f"{segment_name} rather than reusing", file=sys.stderr)
        elif recorded and recorded != key:
            print(f"    note: {segment_name} changed since it was rendered "
                  f"- re-rendering", file=sys.stderr)

    # ── Measure the tight box off a decoded probe ──
    #
    # The predictor is retired from this path: it under-measures
    # against Chromium. The probe is a full-canvas render to a temp
    # dir (or `probe_mov` measured directly), the union across its
    # frames sizes the canvas, and the tight output is verified
    # against the probe after rendering. Temp dir is owned here and
    # deleted once the segment is kept or refused.
    probe_tmpdir = None
    probe_frames = None
    probe_union = None
    if geometry == "tight":
        probe_engine = renderer or SubprocessRenderer(remotion_dir)
        try:
            tight, probe_tmpdir, probe_frames, probe_union = \
                _probe_tight_box(
                    props, out_dir, probe_engine, remotion_dir,
                    container, probe_mov, progress)
        except _TightFailed as exc:
            return entry(FAILED, failure=str(exc.reason)[:500].strip()
                         or "tight probe failed")
        if tight is None:
            suffix = ""
            overlay_path = os.path.join(out_dir, f"{segment_name}.mov") \
                if not is_frames else os.path.join(
                    out_dir, f"{segment_name}_frames")
            props_path = os.path.join(out_dir, f"{segment_name}_props.json")
            key_path = os.path.join(out_dir, f"{segment_name}_reuse_key.txt")
            print(f"  {progress} no subtitles to bound - full canvas",
                  file=sys.stderr)
        else:
            render_props = tight.props

    # Write props file
    with open(props_path, "w") as f:
        json.dump(render_props, f, indent=2)

    print(f"  {progress} {segment_name}{suffix} "
          f"({num_subs} subs, {total_frames}f, "
          f"tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)

    # Render.  WHICH cards render and what comes back is this function's
    # business; HOW one card is turned into pixels is the renderer's, and
    # the two are deliberately separable - see `SubprocessRenderer`.
    import shutil as _shutil_probe

    def _drop_probe():
        if probe_tmpdir:
            _shutil_probe.rmtree(probe_tmpdir, ignore_errors=True)

    engine = renderer or SubprocessRenderer(remotion_dir)
    ok, error = engine.render(props_path, overlay_path,
                              sequence=is_frames)
    if not ok:
        _drop_probe()
        print(f"    WARN: Render failed: {error[:200]}", file=sys.stderr)
        return entry(FAILED, failure=error[:500].strip() or "render failed")
    if is_frames and not _frames_on_disk():
        _drop_probe()
        print(f"    WARN: Render reported success but {overlay_path} "
              f"holds no complete sequence", file=sys.stderr)
        return entry(FAILED, failure="sequence incomplete on disk")
    if tight is not None:
        # The gate: the tight output IS the probe crop, or the
        # segment is refused. Placement is read off the two renders
        # (the composition re-centers in the narrower canvas), then
        # proven frame by frame. Cut-off text never reaches a
        # timeline on a warning.
        from library.tools.overlay_placement import (
            sequence_frame_paths as _sequence_paths,
        )
        from library.tools.tight_box import (
            TightBoxMismatch,
            extract_frames,
            finalize_box_placement,
            ink_union_of_frames,
            verify_frames,
        )
        try:
            if is_frames:
                tight_frames = _sequence_paths(overlay_path)
            else:
                tight_frames = extract_frames(
                    overlay_path,
                    os.path.join(probe_tmpdir, "verify_tight"))
            tight_union = ink_union_of_frames(tight_frames)
            if tight_union is None:
                raise TightBoxMismatch(
                    "tight render draws nothing the probe drew: "
                    "no correspondence exists.")
            from library.tools.delivery_format import (
                resolve_delivery_format,
            )
            timeline_size = tuple(resolve_delivery_format(
                project_folder or None))
            tight = finalize_box_placement(tight, probe_union,
                                           tight_union, timeline_size)
            report = verify_frames(probe_frames, tight_frames, tight)
        except TightBoxMismatch as exc:
            _drop_probe()
            print(f"    WARN: Tight output mismatch: {str(exc)[:200]}",
                  file=sys.stderr)
            return entry(FAILED, failure=str(exc)[:500].strip()
                         or "tight output mismatch")
        print(f"    OK: {overlay_path} "
              f"(verified {report['frames']} frames, "
              f"maxdiff {report['max_diff']}, "
              f"IoU {report['min_iou']:.4f})", file=sys.stderr)
        union_path = _box_sidecar_path(out_dir, segment_name, suffix)
        try:
            with open(union_path, "w") as handle:
                json.dump({
                    "width": tight.width,
                    "height": tight.height,
                    "placement": tight.placement,
                    "union": {
                        "x0": probe_union.x0,
                        "y0": probe_union.y0,
                        "x1": probe_union.x1,
                        "y1": probe_union.y1,
                    },
                    "verify": report,
                }, handle, indent=2)
        except OSError as exc:
            _drop_probe()
            print(f"    WARN: could not record the box sidecar for "
                  f"{segment_name} ({exc})", file=sys.stderr)
            return entry(FAILED, failure="box sidecar unwritable")
    else:
        print(f"    OK: {overlay_path}", file=sys.stderr)
    _drop_probe()

    # Recorded only after a render that SUCCEEDED, so a failed or
    # interrupted render leaves no key claiming the file is current.
    if key:
        try:
            Path(key_path).write_text(key, encoding="utf-8")
        except OSError as exc:
            print(f"    note: could not record the reuse key for "
                  f"{segment_name} ({exc}); it will re-render next time",
                  file=sys.stderr)

    return measured(RENDERED)


def render_subtitle_overlays(subtitle_plan: dict, audio_spine: dict,
                             project_folder: str = "", fps: int = 30,
                             remotion_dir: str = None,
                             reuse: bool = False,
                             scope=None,
                             renderer=None,
                             renderer_kind: str = DEFAULT_CAPTION_RENDERER,
                             require_named_timeline: bool = False,
                             overlay_geometry: str = None,
                             overlay_container: str = None) -> dict:
    """Render one overlay per captioned spine block: stitched ProRes 4444
    video by default, a tight canvas and/or a PNG sequence where the
    project declares it (`library/tools/overlay_mode.py`).

    Returns the `subtitle_overlay` payload.  Raises
    `SubtitleRenderRefused` where the step cannot deliver: no Remotion
    project, subtitle QA failed, more than
    `MAX_RENDER_FAILURE_RATE` of the segments failed to render - or the
    renderer itself died mid-pass, which is ONE fault, not one failure
    per remaining card.

    `renderer_kind` chooses HOW one card becomes pixels - `"subprocess"`
    (today's one-`npx`-per-card mechanism, the default) or `"persistent"`
    (one bundle for the whole pass). An explicit `renderer` still wins
    over either. An unknown kind raises rather than falling back,
    because a fallback would run a renderer the caller did not ask for
    while reporting success.

    An empty plan is NOT a refusal - it returns `available: False` with
    the reason, which is what the step has always done.
    """
    remotion_dir = remotion_dir or REMOTION_DIR

    if renderer_kind not in CAPTION_RENDERERS:
        raise ValueError(
            f"Unknown renderer_kind {renderer_kind!r}; "
            f"known: {list(CAPTION_RENDERERS)}. A renderer the caller "
            f"did not ask for is never substituted silently.")

    # Explicit values win; otherwise the project's declaration, and a
    # project that declares nothing renders exactly as before.
    geometry = overlay_geometry or resolve_overlay_geometry(
        project_folder or None)
    container = overlay_container or resolve_overlay_container(
        project_folder or None)
    if geometry not in GEOMETRIES:
        raise ValueError(
            f"Unknown overlay_geometry {geometry!r}; "
            f"known: {list(GEOMETRIES)}.")
    if container not in CONTAINERS:
        raise ValueError(
            f"Unknown overlay_container {container!r}; "
            f"known: {list(CONTAINERS)}.")
    print(f"Overlay carrying: {geometry} geometry, {container} container",
          file=sys.stderr)

    # The persistent renderer stitches video through one bundle; a PNG
    # sequence is a carrying it cannot draw. Refuse BEFORE anything
    # renders rather than failing card by card, or - worse - reporting
    # a sequence as drawn that was never drawn.
    if renderer is None and renderer_kind == "persistent" \
            and container == "frames":
        raise ValueError(
            "renderer_kind='persistent' cannot render a 'frames' "
            "container: the persistent renderer stitches video and "
            "refuses sequences. Use the default subprocess renderer "
            "for frame sequences.")

    if not os.path.isdir(remotion_dir):
        print(f"ERROR: Remotion project not found at {remotion_dir}",
              file=sys.stderr)
        raise SubtitleRenderRefused({
            "subtitle_overlay": {
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

    # Output directory.
    #
    # There is no repo fallback any more. Without a project_folder this
    # step wrote its rendered overlays into <repo>/pipeline_output/ -
    # inside the checkout, and inside a disposable worktree whenever the
    # run happened in one. The layout owner raises instead, so a run with
    # no project says so rather than banking work somewhere nothing will
    # look for it. See library/tools/project_layout.py.
    layout = ProjectLayout(project_folder)
    sub_output_dir = str(
        layout.write_dir(Area.SUBTITLE_SEGMENTS, step="render_subtitles"))

    # The overlay is rendered AT THE DELIVERY FORMAT, so it composites
    # 1:1 onto the timeline. Reading a source-derived resolution here is
    # what put a vertical overlay on a landscape timeline as a lighter
    # band down the middle. See library/tools/delivery_format.py.
    width, height = resolve_delivery_format(project_folder)
    props_list = generate_subtitle_props_per_block(subtitle_plan, fps=fps, width=width, height=height, audio_spine=audio_spine)

    if not props_list:
        print("WARNING: No subtitle blocks to render", file=sys.stderr)
        return {
            "subtitle_overlay": {
                "available": False,
                "segments": [],
                "reason": "No subtitle entries found in subtitle plan"
            }
        }

    # ── Region scope narrows WHICH segments render, and nothing else ──
    #
    # Filtered on the spine block, not on the segment's own time span: a
    # segment IS a block, so a region that clips a block still re-renders
    # the whole of it.  Rendering half a block's captions would leave a
    # card split across two vintages of the plan.
    planned_total = len(props_list)
    if scope is not None and scope.is_region:
        from library.tools.spine_contract import blocks_overlapping
        span = scope.region_span
        wanted = {b["position"]
                  for b in blocks_overlapping(
                      audio_spine.get("structure", []), span.start, span.end)}
        props_list = [p for p in props_list
                      if p.get("_block_position") in wanted]
        if not props_list:
            return {
                "subtitle_overlay": {
                    "available": False,
                    "segments": [],
                    "reason": (f"region {span} covers no captioned block "
                               f"(of {planned_total} planned)"),
                }
            }
        print(f"Region {span}: {len(props_list)} of {planned_total} "
              f"segments", file=sys.stderr)

    print(f"Rendering {len(props_list)} subtitle segments...",
          file=sys.stderr)

    # Which timeline these overlays belong to. Measured off the spine
    # when the spine came from a real timeline; otherwise the project's
    # declaration. Never invented - an unnamed timeline that collides is
    # a visible bug, a made-up name that does not is a silent one.
    timeline_label = timeline_scope(audio_spine, project_config=None)
    print(f"Naming segments under timeline "
          f"{timeline_label or '<unnamed>'}", file=sys.stderr)

    # ── No two segments may share a filename, checked BEFORE anything
    #    is written ──
    #
    # `SEGMENT_BINDING_KEYS` carries no caption content, so uniqueness
    # rests entirely on the binding - and across reels the only thing
    # separating two reels' closers is the TIMELINE component. The
    # captain's format closes every reel on a call to action taken from
    # anywhere in the episode, so a shared closer is normal rather than
    # exceptional: measured on the field test, seven of nineteen reels
    # close on one identical sentence and five more on another, so a
    # 19-reel pass collides on twelve of them.
    #
    # Over the whole set and before the loop, because a collision found
    # after rendering is a file already overwritten and nothing
    # downstream reads content to notice:
    # `_assert_subtitle_overlay_matches_plan` compares block position and
    # time span only, so it passes while the wrong words are on screen.
    bindings = [
        segment_binding(
            timeline=timeline_label,
            speaker=props.get("_speaker"),
            block_position=props.get("_block_position"),
            source_clip_id=props.get("_source_clip_id"),
            source_start=props.get("_source_start"),
            source_end=props.get("_source_end"),
        )
        for props in props_list
    ]
    if require_named_timeline:
        for binding in bindings:
            assert_named_timeline(binding, where="render_subtitle_overlays")
    assert_unique_segment_names(bindings)

    # ONE renderer for the whole pass, so a replacement holding a bundle
    # or a browser builds it once rather than per card.  See
    # `SubprocessRenderer` for the contract and for why it should be lazy.
    #
    # `renderer_kind="persistent"` builds the bundle-once renderer
    # beside the default instead of in place of it - the default stays
    # today's subprocess mechanism until the new path is proven. An
    # explicit `renderer` still wins over either kind.
    #
    # Constructing the persistent renderer starts NOTHING: the bundle is
    # paid on the first card that actually renders, so a pass that draws
    # nothing (a region covering no captioned block returns above; reuse
    # skipping every card) pays nothing.
    #
    # CLOSED IN A `finally`, and only if WE built it.  A renderer holding
    # a Remotion bundle or a browser owns an OS resource, and a pass that
    # raises - a QA refusal, a failed segment - would otherwise leak it;
    # over nineteen reels that is nineteen leaks.  Ownership decides who
    # closes: a caller who passed one in may be reusing it across several
    # passes, and closing someone else's renderer is how the second pass
    # fails for a reason the first pass caused.
    if renderer is not None:
        engine = renderer
        owns_engine = False
    elif renderer_kind == "persistent":
        engine = PersistentCaptionRenderer(remotion_dir)
        owns_engine = True
    else:
        engine = SubprocessRenderer(remotion_dir)
        owns_engine = True

    segments = []
    try:
        for i, props in enumerate(props_list):
            segment = render_one_segment(
                props, sub_output_dir, timeline_label,
                remotion_dir=remotion_dir,
                progress=f"[{i+1}/{len(props_list)}]",
                reuse=reuse, renderer=engine,
                overlay_geometry=geometry,
                overlay_container=container,
                project_folder=project_folder)
            # Appended unconditionally, failures included.  A dropped
            # segment is one the manifest never learns about, and 5.04
            # then refuses the compile citing a missing block rather than
            # the render that actually failed.
            segments.append(segment)
    except RendererUnavailable as exc:
        # The RENDERER died - not a card. Every remaining card would fail
        # the same way, so the pass stops here and reports ONE fault
        # carrying the cards rendered so far, rather than one FAILED
        # segment per card that never had a chance.
        raise SubtitleRenderRefused({
            "subtitle_overlay": {
                "available": False,
                "error": (f"the caption renderer died after "
                          f"{len(segments)} of {len(props_list)} segments: "
                          f"{exc}"),
                "segments": segments,
            }
        }) from exc
    finally:
        if owns_engine:
            closer = getattr(engine, "close", None)
            if callable(closer):
                closer()

    # DERIVED from the segments, never tallied alongside them.  A second
    # counter is a second source of truth for one fact and the two drift
    # the first time an early exit is added.  `Rendered N/M` was the old
    # line and it is false about every reused file - it reports work that
    # did not happen.
    tally = _tally(segments)
    print(f"\nrendered {tally[RENDERED]}, reused {tally[REUSED]}, "
          f"failed {tally[FAILED]}  ({len(props_list)} planned)",
          file=sys.stderr)

    usable = [s for s in segments if s["provenance"] != FAILED]
    if usable:
        try:
            sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            if usable[0].get("container") == "frames":
                _qa_frame_sequence(usable[0])
            else:
                from tools.qa.subtitle_qa import run_subtitle_qa
                run_subtitle_qa(usable[0]["overlay_path"], project_folder)
        except Exception as e:
            error_msg = f"Subtitle QA Validation Failed: {e!s}"
            print(f"ERROR: {error_msg}", file=sys.stderr)
            # Don't fail the step if it's just QA that failed, unless it's a critical error
            raise SubtitleRenderRefused({
                "subtitle_overlay": {
                    "available": False,
                    "error": error_msg
                }
            })

    # ── Availability is not a proportion ──
    #
    # This was `failure_rate > MAX_RENDER_FAILURE_RATE`, and a proportion
    # means how many segments may vanish silently GROWS with the
    # timeline: one failure of eight trips a 10% bound, one failure of
    # eleven does not.  What may be missing is not a quantity this step
    # gets to have an opinion about.
    failed = [s for s in segments if s["provenance"] == FAILED]
    if failed:
        detail = "; ".join(
            f"{s['segment_id']}: {s.get('failure', 'no reason recorded')}"
            for s in failed[:5])
        error_msg = (f"{len(failed)} of {len(props_list)} subtitle segments "
                     f"failed to render - {detail}")
        print(f"ERROR: {error_msg}", file=sys.stderr)
        raise SubtitleRenderRefused({
            "subtitle_overlay": {
                "available": False,
                "error": error_msg,
                "segments": segments,
            }
        })

    return {
        "subtitle_overlay": {
            # True only when every planned segment is present and none
            # failed.  `len(segments) > 0` was true on a partial render.
            "available": len(segments) == len(props_list) and not failed,
            "segments": segments,
            "format": ("PNG sequence" if container == "frames"
                       else "ProRes 4444"),
            "has_alpha": True,
            "fps": fps,
            # What this pass carried, so a reader knows without
            # re-deriving it per segment.
            "geometry": geometry,
            "container": container,
            "total_segments": len(segments),
            "rendered": tally[RENDERED],
            "reused": tally[REUSED],
            "failed": tally[FAILED],
            "planned": len(props_list),
        }
    }


def _qa_frame_sequence(segment: dict) -> None:
    """The mechanical half of subtitle QA, for a PNG sequence.

    `run_subtitle_qa` reads a mov (ffprobe duration, ffmpeg extracts);
    frames are already discrete files, so the sampling is local: score
    every frame's alpha channel with PIL, check the geometry of the two
    most-inked, and raise on the same terms - a sequence that draws
    nothing, or draws past its own edges, refuses the step exactly as
    a bad mov does.
    """
    from PIL import Image

    frames_info = segment.get("frames") or {}
    frame_dir = frames_info.get("dir", "")
    try:
        names = sorted(f for f in os.listdir(frame_dir)
                       if f.endswith(".png"))
    except OSError as exc:
        raise RuntimeError(
            f"Subtitle QA Failed:\nFAIL\n{frame_dir} cannot be read: "
            f"{exc}") from exc
    if not names:
        raise RuntimeError(
            f"Subtitle QA Failed:\nFAIL\n{frame_dir} holds no frames: "
            f"the overlay is blank, not merely paused.")

    def ink(path: str) -> int:
        try:
            with Image.open(path) as im:
                alpha = im.convert("RGBA").getchannel("A")
                return sum(1 for value in alpha.getdata()
                           if value >= ALPHA_INK_THRESHOLD)
        except OSError:
            return 0

    scored = sorted(((ink(os.path.join(frame_dir, name)), name)
                     for name in names), reverse=True)
    if scored[0][0] == 0:
        raise RuntimeError(
            f"Subtitle QA Failed:\nFAIL\n{os.path.basename(frame_dir)} "
            f"draws nothing anywhere in its {len(names)} frames: every "
            f"frame measured an empty alpha channel. The overlay is "
            f"blank, not merely paused.")
    problems = []
    for _, name in scored[:2]:
        problems.extend(check_caption_geometry(
            os.path.join(frame_dir, name)))
    if problems:
        raise RuntimeError(
            "Subtitle QA Failed:\nFAIL\n"
            + "\n".join(f"  - {p}" for p in problems))
    print("  Caption geometry OK: inside the frame, bottom-positioned.",
          file=sys.stderr)


def main():
    # First, before any dependency can grab it: vision_model printed
    # its model-loading line into the middle of this step's result.
    # See library/tools/step_stdout.py.
    claim_stdout()
    data = json.loads(sys.stdin.read())

    try:
        result = render_subtitle_overlays(
            subtitle_plan=data.get("subtitle_plan", {}),
            audio_spine=data.get("audio_spine", {}),
            project_folder=data.get("project_folder", ""),
            fps=data.get("project_fps", 30),
            # Optional, and absent means today's mechanism: the default
            # stays the subprocess renderer until the persistent path is
            # proven (see DEFAULT_CAPTION_RENDERER).
            renderer_kind=data.get("caption_renderer",
                                   DEFAULT_CAPTION_RENDERER),
        )
    except SubtitleRenderRefused as refusal:
        emit(refusal.payload)
        sys.exit(1)

    emit(result)


if __name__ == "__main__":
    main()
