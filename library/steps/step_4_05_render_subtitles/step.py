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
import json
import os
import subprocess
import sys
import threading
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
    DEFAULT_GEOMETRY,
    GEOMETRIES,
    OVERLAY_CARRIAGE,
    resolve_overlay_container,
    resolve_overlay_geometry,
)
from library.tools.project_layout import Area, ProjectLayout
from library.tools.qa.subtitle_qa import (
    ALPHA_INK_THRESHOLD,
    check_caption_geometry,
)
from library.tools.render_cache import (
    content_key as _content_key,
    drawing_digest as _drawing_digest_of,
    renderer_fingerprint as _renderer_fingerprint,
)
from library.tools.step_stdout import claim_stdout, emit
from library.tools.subtitle_segment_id import (
    assert_no_content_collision,
    segment_binding,
    segment_identifier,
    timeline_scope,
)
from library.tools.caption_asset_gc import (
    LedgerError,
    card_key,
    record_rendered_segments,
)
from library.tools.reel_proposal import refuse_rejected_reel_timeline

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

# The full-canvas probe file inside the probe temp dir. Named once, so
# the measurer and the crop/fallback copier cannot disagree about which
# file the tight output is cut from.
PROBE_MOV_NAME = "probe.mov"


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


# Props keys that are PLACEMENT or provenance, never pixels. Everything
# else in the props - `subtitles` (text + frame timings + emphasis +
# words + fit), `style`, `fps`, `width`/`height`, `durationInFrames`,
# `_source_in_frame`/`_source_out_frame` - draws, and stays in the
# digest.
#
# What each excluded key is, so a future props addition lands on the
# right side: `timeline` never reaches the props (it travels as the
# caller's label); `_block_position` is the ordinal within one spine;
# `_timeline_start`/`_timeline_end` are absolute timeline bounds;
# `_speaker`/`_source_clip_id`/`_source_start`/`_source_end` are the
# provenance the filename stem already carries. Duration stays IN: it
# is the file's frame count, and two variants holding one caption for
# genuinely different lengths must still render twice, because the
# pixels really differ. The source in/out frames stay IN: they are
# derived from the same timing the subtitles carry, so they agree on
# every genuine hit, and hashing them is the SAFE direction against a
# derivation drift - it re-renders where hashing less would skip.
NON_DRAWING_PROPS_KEYS = frozenset((
    "_block_position",
    "_timeline_start",
    "_timeline_end",
    "_speaker",
    "_source_clip_id",
    "_source_start",
    "_source_end",
))
"""Props that must never decide reuse. Complete, and load-bearing.

A NEW metadata key defaults INTO the digest (safe: it re-renders),
and joins this set only by an edit that says why it draws nothing.
A new DRAWING input needs no edit at all - which is the direction a
default must fail in.
"""


def _drawing_digest(props: dict, geometry: str = "full",
                    container: str = "video") -> str:
    """A stable hash of everything about this segment that draws pixels.

    The drawing props (see `NON_DRAWING_PROPS_KEYS`) plus the
    carrying: geometry (the delivery frame, or the tight canvas
    floored at `tight_box.MIN_CANVAS_HEIGHT`) and container (stitched
    mov versus frames directory - different artefacts of the same
    pixels).

    For the tight carrying `style.safeArea` is positioning, not
    pixels, and stays out: the frame-relative insets move the probe's
    ink within the delivery frame, but the tight crop follows the ink,
    so two rows of the same card cut byte-identical canvases.
    Measured on geo-podcast (issue #915): 145 caption pairs sharing a
    provenance stem held identical bytes under two digests whose only
    drawing-side difference was `safeArea.bottom` 331 (the engine's
    old row) versus 540 (the project's declared caption row) - one
    content rendered under two names, and every row change re-rendered
    the whole directory beside itself. The full carrying keeps the
    insets in the digest: there the row really moves pixels.
    A composition that ever DRAWS from an inset (wraps from it, clips
    to it) must put it back - today the composition positions from
    `top`/`bottom` only (`SubtitleOverlay/index.tsx`) and never reads
    `left`/`right` at all.
    """
    drawing = {k: v for k, v in props.items()
               if k not in NON_DRAWING_PROPS_KEYS}
    style = drawing.get("style")
    if geometry == "tight" and isinstance(style, dict):
        style = {k: v for k, v in style.items() if k != "safeArea"}
        drawing["style"] = style
    drawing["_geometry"] = geometry
    drawing["_container"] = container
    return _drawing_digest_of(drawing)


def renderer_fingerprint(remotion_dir: str) -> str:
    """The identity of the code and fonts that turn props into pixels.

    One spelling, in `library/tools/render_cache.py`: step 4.05's key
    used to carry its own copy, and two fingerprints of one tree is
    how a renderer edit reuses on one path and re-renders on the
    other.  Kept here as the name the tests and the key builder read.
    """
    return _renderer_fingerprint(remotion_dir)


def _reuse_key(props: dict, remotion_dir: str,
               geometry: str = "full", container: str = "video") -> str:
    """The three things that have to match for a skip to be safe, or `""`.

    The drawing digest (never placement: no timeline, no block
    ordinal, no absolute timeline bounds), the renderer fingerprint,
    and the carriage.  Empty means "cannot be established", and every
    comparison against it fails, so an unreadable renderer tree
    renders rather than skips.

    The CARRIAGE is in the key (`overlay_mode.OVERLAY_CARRIAGE`)
    because an artefact from a previous carriage is not stale, it is
    UNUSABLE: the `frame-baked-1` era baked the position into
    delivery-frame pixels and placed with no transform, so reusing one
    under today's rule would place a full-frame clip AND transform it
    off the frame. A key that did not name the carriage would let
    exactly that through as a hit.
    """
    return _content_key(_drawing_digest(props, geometry, container),
                        remotion_dir, OVERLAY_CARRIAGE)


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


def _box_sidecar_path(out_dir: str, segment_name: str) -> str:
    return os.path.join(out_dir, f"{segment_name}_box.json")


def _probe_tight_box(props: dict, out_dir: str, engine,
                     remotion_dir: str, container: str, progress: str):
    """Measure this segment's box off a decoded probe render.

    Returns `(box_or_None, probe_tmpdir, probe_frames, union_or_None,
    probe_mov_or_None)`: None where the probe draws nothing (the caller
    keeps the full-canvas path), else the measured `TightBox` with the
    frames it was measured from. `probe_mov_or_None` is the full-canvas
    probe file itself where the container is video - the crop source,
    and the full-canvas fallback without a re-render. The caller owns
    `probe_tmpdir` - crop or copy, verify, then delete it. Raises
    `_TightFailed` where no exact box exists.
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
        probe_mov_path = None
        if container == "frames":
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
            probe_mov_path = os.path.join(probe_tmpdir, PROBE_MOV_NAME)
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
            # A probe that cannot be DECODED fails the segment, never
            # escapes as TightBoxMismatch: the one caller owns entries,
            # not exceptions (measured 2026-09-10 - a stub renderer
            # writing bytes let it escape past the _TightFailed catch).
            try:
                probe_frames = extract_frames(
                    probe_mov_path, probe_tmpdir)
            except TightBoxMismatch as exc:
                raise _TightFailed(str(exc)) from exc
        try:
            union = ink_union_of_frames(probe_frames)
        except TightBoxMismatch as exc:
            raise _TightFailed(str(exc)) from exc
        if union is None:
            print(f"  {progress} probe draws nothing - full canvas",
                  file=sys.stderr)
            return None, probe_tmpdir, probe_frames, None, probe_mov_path
        try:
            box = tighten_measured(props, union)
        except (TightBoxClipsInk, ValueError) as exc:
            raise _TightFailed(str(exc)) from exc
        print(f"  {progress} measured {box.width}x{box.height} "
              f"(full {box.full_width}x{box.full_height}, "
              f"{union.inked_frames} inked frames)", file=sys.stderr)
        return box, probe_tmpdir, probe_frames, union, probe_mov_path
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

DEFAULT_CAPTION_RENDERER = "persistent"
"""The bundle-once renderer is the default; the per-card path is the fallback.

BOTH paths render through headless Chrome - the difference is not Node
versus browser, it is ONE browser and ONE bundle reused across every
card versus a FRESH browser and a FRESH bundle for every card,
~0.70s of bundling per card over 763 cards in a full pass. PR 780
proved three cards drawn both ways come out byte-identical, which is
why the switch is safe. The captain's ruling, 2026-09-09: the
persistent path is the default, the subprocess path stays selectable
and becomes the fallback - at STARTUP only, never per card, and never
silent (see `render_subtitle_overlays`)."""


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


# ── The unit-level default: one shared bundle-once renderer ──
#
# `render_subtitle_overlays` builds ONE engine for the whole pass, but
# most real caption work never enters that pass: region redos, reel
# fixes and agent-driven renders reach `render_one_segment` directly
# (the `subtitles.render_segment` operation), and that unit defaulted
# to a fresh `SubprocessRenderer` PER CALL - a fresh node, bundle and
# browser for the probe AND for the main render of every card, with no
# banner and no record anywhere. Measured 2026-09-11 on the session's
# own cards: that is the shape that rendered 136 cards at 18.2s each.
# The persistent default has to live HERE, not only on the pass.
_SHARED_CAPTION_ENGINES: dict = {}
"""One `_SharedPersistentEngine` per process per remotion dir."""

_SHARED_CAPTION_ENGINES_LOCK = threading.Lock()

_SHARED_CAPTION_NOTES: set = set()
"""One-time notes this process already printed. Loud once, never per card."""


def _note_once(key: str, message: str) -> None:
    """Print a renderer note on stderr the first time per process."""
    if key not in _SHARED_CAPTION_NOTES:
        _SHARED_CAPTION_NOTES.add(key)
        print(message, file=sys.stderr)


def _reset_shared_caption_renderers() -> None:
    """Close and forget every shared unit renderer. Tests only.

    Production never calls this: a process shares its bundle until it
    exits, and a started inner renderer is closed by its own atexit
    backstop (`library/tools/remotion_batch.py`).
    """
    for holder in list(_SHARED_CAPTION_ENGINES.values()):
        closer = getattr(holder, "close", None)
        if callable(closer):
            closer()
    _SHARED_CAPTION_ENGINES.clear()
    _SHARED_CAPTION_NOTES.clear()


class _SharedPersistentEngine:
    """One bundle-once renderer shared by every unit render in this process.

    The same two-method seam (`render` / `close`), so `render_one_segment`
    cannot tell it apart from a renderer built for one pass. The adapter
    inside builds lazily - constructing this starts nothing, and a process
    whose cards all reuse pays no bundle - and a started inner renderer
    rides its own atexit backstop, so nothing here needs closing for the
    browser to die with the process.

    The two failure kinds stay distinct, with the pass-level rule applied
    per process instead of per pass: a startup failure (the shared
    renderer never drew - `RendererUnavailable` before `_served`) falls
    back to per-call subprocess LOUDLY and once, because in a process
    without node that is today's behavior exactly. Anything later is a
    mid-run death and RAISES, for the same reason the orchestrator stops
    the pass rather than marching every remaining card into a closed pipe.
    """

    def __init__(self, remotion_dir: str):
        self._remotion_dir = remotion_dir
        self._engine = None
        self._served = 0
        self._dead = False

    def _ensure(self):
        if self._engine is None:
            self._engine = PersistentCaptionRenderer(self._remotion_dir)
        return self._engine

    def render(self, props_path: str, overlay_path: str,
               sequence: bool = False):
        if self._dead:
            return SubprocessRenderer(self._remotion_dir).render(
                props_path, overlay_path, sequence=sequence)
        try:
            ok, error = self._ensure().render(
                props_path, overlay_path, sequence=sequence)
        except RendererUnavailable as exc:
            if self._served:
                raise
            self._dead = True
            closer = getattr(self._engine, "close", None)
            if callable(closer):
                closer()
            self._engine = None
            reason = str(exc).strip() \
                or "the persistent renderer could not start"
            _note_once(
                "shared-persistent-fallback",
                "FALLBACK: the shared bundle-once caption renderer could "
                f"not START ({reason}); this process renders per-card - "
                "a fresh browser and a fresh bundle for every card. "
                "This run is SLOWER but the pixels are identical.")
            return SubprocessRenderer(self._remotion_dir).render(
                props_path, overlay_path, sequence=sequence)
        self._served += 1
        return ok, error

    def close(self) -> None:
        """Idempotent, like the renderer it holds."""
        if self._engine is not None:
            closer = getattr(self._engine, "close", None)
            if callable(closer):
                closer()
            self._engine = None


def _default_unit_engine(remotion_dir: str, container: str):
    """The renderer a unit-level render uses when the caller passed none.

    Video shares ONE bundle-once renderer per process per remotion dir,
    announced once on stderr so a later log can say which path a session
    took - the record the 2026-09-11 session never left. An explicit
    `renderer` still wins over this, everywhere.
    """
    if container == "frames":
        # The bundle-once renderer stitches video and cannot draw a frame
        # sequence; a sequence reported as drawn would be success reported
        # while the work did not happen. Per-call subprocess is today's
        # behavior here, kept, and said once per process.
        _note_once(
            "shared-persistent-frames",
            "note: frame-sequence carrying cannot use the shared "
            "bundle-once renderer; this process renders sequences "
            "per-card.")
        return SubprocessRenderer(remotion_dir)
    key = os.path.abspath(remotion_dir)
    with _SHARED_CAPTION_ENGINES_LOCK:
        holder = _SHARED_CAPTION_ENGINES.get(key)
        if holder is None:
            holder = _SharedPersistentEngine(remotion_dir)
            _SHARED_CAPTION_ENGINES[key] = holder
    _note_once(
        "shared-persistent",
        "caption renderer: one shared bundle-once process serves every "
        f"card rendered in this process ({remotion_dir}); pass an "
        "explicit renderer to use your own.")
    return holder


def _superseded_generations(out_dir: str, overlay_path: str) -> list:
    """Older generations of the card just rendered, still on disk.

    Same card (stem minus digest, same tightness), different digest -
    e.g. a source span that shifted within the millisecond the
    filename carries, so the new render wrote a new file instead of
    overwriting. Listed, never touched: the mark decides what they are
    by reachability, and the sweep moves them only on instruction.
    """
    base = os.path.basename(overlay_path)
    if not base.endswith(".mov"):
        return []
    mine, my_tight = card_key(base[:-4])
    older = []
    try:
        names = os.listdir(out_dir)
    except OSError:
        return []
    for name in names:
        if not name.endswith(".mov") or name == base:
            continue
        card, tight = card_key(name[:-4])
        if card == mine and tight == my_tight:
            older.append(os.path.join(out_dir, name))
    return sorted(older)


def render_one_segment(props: dict, out_dir: str, timeline_label: str,
                       remotion_dir: str = None,
                       progress: str = "",
                       reuse: bool = False,
                       renderer=None,
                       overlay_geometry: str = None,
                       overlay_container: str = None,
                       project_folder: str = "") -> dict:
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

    Without an explicit `renderer` the unit shares the process-wide
    bundle-once engine: one node process, one bundle, every card this
    process renders - announced once on stderr, so a later log says
    which path a session took. A frame-sequence carrying (`frames`)
    cannot use it and renders per-card, said once; a shared engine
    that never started falls back per-card loudly and once, while one
    that dies mid-run raises rather than degrading silently. An
    explicit renderer still wins over all of this.

    A skip requires BOTH the overlay and its recorded reuse key to be on
    disk and to match - never mere presence.  The filename carries the
    content digest but no timeline, so two variants captioning the same
    words compute the same name: that is the sharing, and the recorded
    key is still what says the file on disk is current - a renderer or
    carriage change mismatches the key and re-renders over the same
    filename rather than serving stale pixels.

    `overlay_geometry` / `overlay_container` choose the alternative
    carrying (`library/tools/overlay_mode.py`): a tight canvas instead
    of the delivery frame, a PNG sequence instead of a stitched mov.
    Explicit values win; otherwise the project's declaration is read,
    and a project that declares nothing renders tight (the default
    since 2026-09-10 - `library/tools/overlay_mode.py`).

    Where the geometry is tight the box is MEASURED off a decoded
    probe render, never predicted: a full-canvas probe is rendered to
    a temp dir (or `probe_mov` is measured directly where the caller
    hands one over - a migration whose full renders already exist),
    the ink union across its frames sizes the canvas, and the tight
    output is verified frame-by-frame against the probe before it is
    kept. A probe that draws nothing keeps the full-canvas path;
    anything else that cannot deliver exactly FAILS the segment.

    A produced segment (RENDERED or REUSED) is merged into the step's
    render ledger before it is returned, so the pipeline's own
    `pipeline:render_subtitles` root resolves it even when the render
    happened outside any pipeline run. Raises `LedgerError` where the
    ledger cannot be written; the pass converts that to a refusal, and
    a direct caller sees the exception itself. See
    `library/tools/caption_asset_gc.py`.

    A timeline label naming a REJECTED reel is refused FIRST - before
    reuse, before any render - by reading that
    reel's LIVE verdict off `reel_proposals_v2.json`
    (`library/tools/reel_proposal.py`). The master timeline names no
    reel and passes through untouched.
    """
    refuse_rejected_reel_timeline(timeline_label, project_folder)
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
    # under-measures against Chromium and clips ink). The name is
    # content-keyed (`segment_identifier` over the drawing digest,
    # which carries the geometry), so tight and full renderings of
    # one segment never share a filename: a canvas change cannot
    # serve the other carrying stale.
    # Set when a tight box was measured and its output did not verify, so
    # the card is carried full canvas instead. Initialised HERE, above
    # every `entry()` return including the reuse path, because `entry`
    # closes over it - a later assignment left the reuse path raising
    # NameError on a free variable.
    tight = None
    suffix = ""
    tight_fallback = ""
    render_props = props

    # Generate output path.
    #
    # The name is rooted in PROVENANCE and keyed by CONTENT. The stem
    # says which source footage the speech came from (speaker + clip +
    # source span, `subtitle_segment_id.provenance_stem`) - the
    # captain's ruling of 2026-09-09, so a listing says where a file
    # came from. The digest beside it is the drawing-inputs digest:
    # two variants captioning the same words compute the same name and
    # share the file, while anything that draws differently digests
    # differently and can never overwrite it. No timeline names a file
    # any more: the timeline survives in the recorded binding (which
    # placing this file serves), never in the identity. See
    # library/tools/subtitle_segment_id.py and
    # library/tools/render_cache.py.
    #
    # The container rides in the FILENAME (`.frames`) so one directory
    # can hold today's render beside the alternative without either
    # overwriting the other - and in the DIGEST, so the two carryings
    # of the same pixels never share a stem. The geometry rides in the
    # DIGEST too (`_drawing_digest` carries `_geometry`), never as a
    # suffix: tight and full renderings of one segment compute
    # different names, so a canvas change re-renders rather than
    # serving the other carrying stale (see `_reuse_key`).
    binding = segment_binding(
        timeline=timeline_label,
        speaker=props.get("_speaker"),
        block_position=block_pos,
        source_clip_id=props.get("_source_clip_id"),
        source_start=props.get("_source_start"),
        source_end=props.get("_source_end"),
    )
    segment_name = segment_identifier(
        binding, _drawing_digest(props, geometry, container))
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
            # The unabridged placement record. The filename stems the
            # provenance and digests the pixels; this is what a reader
            # checks a segment's placing against - speaker, timeline,
            # block ordinal and source span - without parsing a name.
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
            # Why this card is full canvas although the project
            # declared tight, or "" when it declared nothing.
            "tight_fallback": tight_fallback,
            # Where a sequence lives. None for stitched video.
            "frames": _frames_record(),
        }
        record.update(extra)
        return record

    def measured(provenance: str, superseded=None) -> dict:
        # Recorded before returning, so a killed pass still vouches for
        # every card it drew and a caller rendering one card outside any
        # run leaves the same record a full pass would. Raises
        # LedgerError (the pass converts it to a refusal): producing
        # pixels no root can resolve is the hole this closes, so it is
        # loud rather than warned past. FAILED entries never reach here.
        built = entry(
            provenance,
            reuse_key=key,
            # The generations this render replaced, named at once so
            # they become orphan candidates without waiting for a
            # sweep. The old files stay on disk - orphaned, not
            # deleted - and reachability still rules the mark: a
            # superseded file a timeline still places stays LIVE. See
            # library/tools/caption_asset_gc.py.
            superseded=sorted(superseded or []),
            # The rendered clip carries animation handles either side of
            # the content; these trim them off at placement time so blocks
            # sit on their true bounds and never overlap.
            source_in_frame=props["_source_in_frame"],
            source_out_frame=props["_source_out_frame"],
            total_frames=props["_source_out_frame"] - props["_source_in_frame"],
            rendered_frames=total_frames,
        )
        record_rendered_segments(out_dir, [built])
        return built

    # The reuse key digests what DRAWS - the drawing props (never
    # placement: no timeline, no block ordinal, no absolute timeline
    # bounds) plus the carrying, and the renderer fingerprint - and
    # WHAT THE ARTEFACT IS, the carriage.  See `_reuse_key`: an
    # artefact from a previous carriage re-renders rather than
    # serving stale pixels under a new one.
    key = _reuse_key(props, remotion_dir, geometry, container)

    def _content_paths(geometry_name: str) -> tuple:
        """The content-keyed stem and paths for one carrying.

        The digest carries the geometry, so tight and full renderings
        of one segment never share a file: a canvas change re-renders
        rather than serving the other carrying stale.
        """
        name = segment_identifier(
            binding, _drawing_digest(props, geometry_name, container))
        if is_frames:
            overlay = os.path.join(out_dir, f"{name}{suffix}_frames")
        else:
            overlay = os.path.join(out_dir, f"{name}{suffix}.mov")
        props_p = os.path.join(out_dir, f"{name}{suffix}_props.json")
        key_p = os.path.join(out_dir, f"{name}{suffix}_reuse_key.txt")
        return name, overlay, props_p, key_p

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
                # missing or unreadable sidecar falls through to a
                # fresh measured render - never an assumed placement.
                # And a READABLE one is re-gated against today's
                # timeline before it ships: the sidecar may predate
                # the clamp gate, or the delivery format may have
                # changed since. A refused restore re-renders measured
                # below, which carries the card full canvas instead.
                #
                # The import sits ABOVE the try, never inside it: the
                # except below names TightBoxMismatch, and an import
                # that only runs after a successful open leaves the
                # name unbound on exactly the path this comment
                # describes - a missing sidecar - turning the intended
                # fall-through into UnboundLocalError (measured
                # 2026-09-10, Reel 26 reusing a Reel 09 render whose
                # sidecar predates the box-sidecar mechanism).
                from library.tools.tight_box import (
                    TightBoxMismatch,
                    restore_reused_placement,
                )
                try:
                    with open(_box_sidecar_path(
                            out_dir, segment_name)) as handle:
                        sidecar = json.load(handle)
                    tight = restore_reused_placement(
                        sidecar, props,
                        tuple(resolve_delivery_format(
                            project_folder or None)))
                except (OSError, ValueError, KeyError, TypeError,
                        TightBoxMismatch) as exc:
                    print(f"    note: {segment_name} reuses its key but "
                          f"{exc}; "
                          f"re-rendering measured rather than assuming",
                          file=sys.stderr)
                else:
                    print(f"  {progress} {segment_name}{suffix} reused "
                          f"(tl:{tl_start:.1f}-{tl_end:.1f}s)",
                          file=sys.stderr)
                    return measured(REUSED)
            else:
                print(f"  {progress} {segment_name}{suffix} reused "
                      f"(tl:{tl_start:.1f}-{tl_end:.1f}s)",
                      file=sys.stderr)
                return measured(REUSED)
        if not key:
            print(f"    note: renderer fingerprint unavailable, rendering "
                  f"{segment_name} rather than reusing", file=sys.stderr)
        elif recorded and recorded != key:
            print(f"    note: {segment_name} changed since it was rendered "
                  f"- re-rendering", file=sys.stderr)

    # ── Measure the tight box off a decoded probe ──
    #
    # The box is MEASURED off a decoded probe render, never predicted.
    # The probe is a full-canvas render to a temp dir, the union across
    # its frames sizes the canvas, and the tight output is a CROP of
    # that probe - verified against it after cutting, never re-rendered
    # (the re-render's rasterization difference failed verify as `max
    # channel diff 5` on 90 historical cards; the crop's one ProRes
    # generation measures 3, inside the gate). Temp dir is owned here
    # and deleted once the segment is kept or refused.
    probe_tmpdir = None
    probe_frames = None
    probe_union = None
    probe_mov_path = None
    if geometry == "tight":
        probe_engine = renderer or _default_unit_engine(
            remotion_dir, container)
        try:
            tight, probe_tmpdir, probe_frames, probe_union, \
                probe_mov_path = _probe_tight_box(
                    props, out_dir, probe_engine, remotion_dir,
                    container, progress)
        except _TightFailed as exc:
            return entry(FAILED, failure=str(exc.reason)[:500].strip()
                         or "tight probe failed")
        if tight is None:
            # The probe draws nothing: the file IS full canvas, so the
            # record says so under the full-geometry content name -
            # leaving `geometry` at "tight" would pin a full-canvas
            # file as a tight one in the only record a staging render
            # leaves.
            geometry = "full"
            tight_fallback = "probe draws nothing - full canvas"
            (segment_name, overlay_path, props_path,
             key_path) = _content_paths(geometry)
            key = _reuse_key(props, remotion_dir, geometry, container)
            print(f"  {progress} no subtitles to bound - full canvas",
                  file=sys.stderr)
        else:
            render_props = tight.props

    # Write props file - except on the tight VIDEO path, which writes
    # its own (tight, then full on fallback) inside the crop block
    # below rather than printing one progress line twice.
    if tight is None or is_frames:
        with open(props_path, "w") as f:
            json.dump(render_props, f, indent=2)

        print(f"  {progress} {segment_name}{suffix} "
              f"({num_subs} subs, {total_frames}f, "
              f"tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)

    import shutil as _shutil_probe

    def _drop_probe():
        if probe_tmpdir:
            _shutil_probe.rmtree(probe_tmpdir, ignore_errors=True)

    # True once the output file is final without any engine render -
    # the probe copy a refused tight crop falls back to. The render
    # below is skipped then: re-rendering over the copy would restore
    # the exact third render this path exists to delete.
    output_ready = False
    if tight is not None and not is_frames:
        # ── Crop instead of re-render ──
        #
        # The tight output IS the probe crop, cut with ffmpeg and
        # verified against the probe with the same gate - no second
        # Remotion render. The re-render it replaces failed verify as
        # `max channel diff 5` on 90 historical cards over a systematic
        # one-step rasterization difference; the crop's one ProRes
        # generation measures 3, inside the gate. The frame-sequence
        # container keeps the re-render path below: a sequence has no
        # single file to crop.
        #
        # What changes on a refusal is what happens NEXT: the segment
        # falls back to full canvas by COPYING the probe - which already
        # is a full-canvas render of these exact props - under the
        # full-geometry content name. No render runs anywhere on this
        # path: one fresh tight card costs one probe render, and a
        # refused one costs the same one. The run still SAYS which card
        # lost its tight carriage and why. Nothing here widens
        # `verify_frames` - its tolerances are untouched and its verdict
        # is still final for the tight output.
        from library.tools.tight_box import (
            TightBoxMismatch,
            crop_probe_to_tight,
            extract_frames,
            finalize_box_placement,
            ink_union_of_frames,
            verify_frames,
        )
        # The props file describes the tight canvas - the geometry the
        # output file carries - exactly as the re-render path wrote it.
        with open(props_path, "w") as handle:
            json.dump(render_props, handle, indent=2)
        print(f"  {progress} {segment_name}{suffix} "
              f"({num_subs} subs, {total_frames}f, "
              f"tl:{tl_start:.1f}-{tl_end:.1f}s)", file=sys.stderr)
        try:
            crop_probe_to_tight(probe_mov_path, overlay_path, tight)
            tight_frames = extract_frames(
                overlay_path,
                os.path.join(probe_tmpdir, "verify_tight"))
            tight_union = ink_union_of_frames(tight_frames)
            if tight_union is None:
                raise TightBoxMismatch(
                    "cropped file draws nothing the probe drew: "
                    "no correspondence exists.")
            timeline_size = tuple(resolve_delivery_format(
                project_folder or None))
            tight = finalize_box_placement(tight, probe_union,
                                           tight_union, timeline_size)
            report = verify_frames(probe_frames, tight_frames, tight)
        except TightBoxMismatch as exc:
            print(f"    WARN: Tight crop mismatch, carrying this card "
                  f"FULL CANVAS instead (no re-render - the probe is "
                  f"already this card full canvas): {str(exc)[:300]}",
                  file=sys.stderr)
            try:
                if os.path.isfile(overlay_path):
                    os.remove(overlay_path)
                for stale in (props_path, key_path):
                    if os.path.isfile(stale):
                        os.remove(stale)
            except OSError:
                pass
            tight = None
            tight_fallback = str(exc)[:500].strip() or "tight crop mismatch"
            geometry = "full"
            render_props = props
            (segment_name, overlay_path, props_path,
             key_path) = _content_paths(geometry)
            key = _reuse_key(props, remotion_dir, geometry, container)
            with open(props_path, "w") as handle:
                json.dump(render_props, handle, indent=2)
            _shutil_probe.copy2(probe_mov_path, overlay_path)
            if not os.path.isfile(overlay_path):
                _drop_probe()
                return entry(FAILED, failure=(
                    f"tight refused ({tight_fallback}) and the probe copy "
                    f"for the full-canvas fallback is not on disk"))
            output_ready = True

    # Render.  WHICH cards render and what comes back is this function's
    # business; HOW one card is turned into pixels is the renderer's, and
    # the two are deliberately separable - see `SubprocessRenderer`.
    # Without an explicit renderer the unit shares the process-wide
    # bundle-once engine (`_default_unit_engine`), so a caller rendering
    # card by card still pays one bundle, not one per card.
    #
    # The tight VIDEO path cropped above and skips the render call
    # below: its output file is final (or the probe copy is, on
    # fallback) and its verified-tight tail joins the shared tail
    # further below. What renders here is full geometry, the
    # draws-nothing fallback, and the frame-sequence container (which
    # keeps its re-render: a sequence has no single file to crop).
    engine = renderer or _default_unit_engine(remotion_dir, container)
    if not output_ready and (tight is None or is_frames):
        ok, error = engine.render(props_path, overlay_path,
                                  sequence=is_frames)
        if not ok:
            _drop_probe()
            print(f"    WARN: Render failed: {error[:200]}",
                  file=sys.stderr)
            return entry(FAILED,
                         failure=error[:500].strip() or "render failed")
    if is_frames and not _frames_on_disk():
        _drop_probe()
        print(f"    WARN: Render reported success but {overlay_path} "
              f"holds no complete sequence", file=sys.stderr)
        return entry(FAILED, failure="sequence incomplete on disk")
    if tight is not None and is_frames:
        # The gate, frames-carrying half: the re-rendered tight output
        # IS the probe crop, or the segment is refused the tight
        # carrying. Placement is read off the two renders, then proven
        # frame by frame. What changes on a refusal is what happens
        # NEXT: the segment falls back to full canvas under the
        # full-geometry content name, the tight file is discarded, and
        # the run SAYS which card lost its tight carriage and why.
        # Nothing here widens `verify_frames` - its tolerances are
        # untouched and its verdict is still final for the tight
        # output.
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
            tight_frames = _sequence_paths(overlay_path)
            tight_union = ink_union_of_frames(tight_frames)
            if tight_union is None:
                raise TightBoxMismatch(
                    "tight render draws nothing the probe drew: "
                    "no correspondence exists.")
            timeline_size = tuple(resolve_delivery_format(
                project_folder or None))
            tight = finalize_box_placement(tight, probe_union,
                                           tight_union, timeline_size)
            report = verify_frames(probe_frames, tight_frames, tight)
        except TightBoxMismatch as exc:
            print(f"    WARN: Tight output mismatch, carrying this card "
                  f"FULL CANVAS instead: {str(exc)[:300]}", file=sys.stderr)
            try:
                if os.path.isfile(overlay_path):
                    os.remove(overlay_path)
                elif os.path.isdir(overlay_path):
                    _shutil_probe.rmtree(overlay_path, ignore_errors=True)
                for stale in (props_path, key_path):
                    if os.path.isfile(stale):
                        os.remove(stale)
            except OSError:
                pass
            tight = None
            tight_fallback = str(exc)[:500].strip() or "tight output mismatch"
            geometry = "full"
            render_props = props
            (segment_name, overlay_path, props_path,
             key_path) = _content_paths(geometry)
            key = _reuse_key(props, remotion_dir, geometry, container)
            with open(props_path, "w") as handle:
                json.dump(render_props, handle, indent=2)
            ok, error = engine.render(props_path, overlay_path,
                                      sequence=is_frames)
            if not ok:
                _drop_probe()
                return entry(FAILED, failure=(
                    f"tight refused ({tight_fallback}) and the full-canvas "
                    f"fallback also failed: "
                    f"{(error or '').strip()[:200] or 'render failed'}"))
            if is_frames and not _frames_on_disk():
                _drop_probe()
                return entry(FAILED, failure=(
                    f"tight refused ({tight_fallback}) and the full-canvas "
                    f"fallback holds no complete sequence"))
    # The verified-tight tail. `tight` is None where the box could not
    # be measured OR where its output did not verify and the card fell
    # back to full canvas above; neither has a `report` and neither
    # writes a box sidecar, because there is no box to record.
    if tight is not None:
        print(f"    OK: {overlay_path} "
              f"(verified {report['frames']} frames, "
              f"maxdiff {report['max_diff']}, "
              f"IoU {report['min_iou']:.4f})", file=sys.stderr)
        union_path = _box_sidecar_path(out_dir, segment_name)
        try:
            with open(union_path, "w") as handle:
                json.dump({
                    "width": tight.width,
                    "height": tight.height,
                    "placement": tight.placement,
                    # WHAT CARRIAGE this placement was computed under
                    # (`overlay_mode.OVERLAY_CARRIAGE`). The restore
                    # path refuses a sidecar from a superseded
                    # carriage rather than serving a placement that
                    # draws where it was not rendered for - every
                    # `tight-480-2` sidecar carries half the Tilt its
                    # artefact needs, because that carriage computed
                    # placements under a draw gain that does not
                    # exist.
                    "carriage": OVERLAY_CARRIAGE,
                    # WHERE the row sat when this placement was
                    # measured: the frame-relative insets the probe
                    # laid out from. The tight filename no longer
                    # carries them (one file per pixels - see
                    # `_drawing_digest`), so without this stamp a row
                    # change would reuse a placement measured for the
                    # old row: it reads back clean inside every rail
                    # and draws the caption on the wrong row. The
                    # restore path refuses a stamp that is missing or
                    # moved, and the caller re-renders measured over
                    # the same file.
                    "safe_area": ((props.get("style") or {}).get(
                        "safeArea") if isinstance(props, dict) else None),
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

    return measured(RENDERED,
                    superseded=_superseded_generations(
                        out_dir, overlay_path))


def render_subtitle_overlays(subtitle_plan: dict, audio_spine: dict,
                             project_folder: str = "", fps: int = 30,
                             remotion_dir: str = None,
                             reuse: bool = False,
                             scope=None,
                             renderer=None,
                             renderer_kind: str = DEFAULT_CAPTION_RENDERER,
                             overlay_geometry: str = None,
                             overlay_container: str = None) -> dict:
    """Render one overlay per captioned spine block: stitched ProRes
    4444 video by default, a PNG sequence where the project declares
    one (`library/tools/overlay_mode.py`).

    A tight segment renders only its drawn bounds (floored at
    `tight_box.MIN_CANVAS_HEIGHT`) and the placer carries it on
    Scaling/Pan/Tilt read back against the measured 3840 rail -
    see `library/tools/tight_box.py`.

    Returns the `subtitle_overlay` payload.  Raises
    `SubtitleRenderRefused` where the step cannot deliver: no Remotion
    project, subtitle QA failed, more than
    `MAX_RENDER_FAILURE_RATE` of the segments failed to render - or the
    renderer itself died mid-pass, which is ONE fault, not one failure
    per remaining card.

    `renderer_kind` chooses HOW one card becomes pixels -
    `"persistent"` (one browser, one bundle, reused across the pass -
    the default) or `"subprocess"` (today's one-`npx`-per-card
    mechanism, selectable as the explicit fallback). An explicit
    `renderer` still wins over either. An unknown kind raises rather
    than falling back, because a fallback would run a renderer the
    caller did not ask for while reporting success.

    The fallback is at STARTUP, never per card. Where the persistent
    renderer cannot START - `RendererUnavailable` before a single card
    is drawn, with a renderer this function built under the persistent
    kind - the pass continues on the subprocess renderer and SAYS SO
    LOUDLY: a FALLBACK banner on stderr and `renderer_fallback` in the
    payload, carrying what was requested, what drew instead, and why.
    A pass that silently ran the slow way while reporting success is
    exactly what the design refuses. Anything later - a renderer that
    dies MID-RUN, a card that fails to draw - keeps today's meaning:
    the pass stops with one fault, and the card is a card failure.

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
    # project that declares nothing renders tight (`overlay_mode`).
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
            "refuses sequences. Pass renderer_kind='subprocess' "
            "explicitly for frame sequences.")

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
        # A run that produced no assets records an empty set - a valid
        # ledger with no segments, which the pipeline root reads as an
        # `ok` root with no paths rather than as unreadable. Warn-only:
        # nothing was produced, so there is nothing to protect and no
        # reason to refuse the run over its record.
        try:
            record_rendered_segments(sub_output_dir, [])
        except LedgerError as exc:
            print(f"WARNING: the caption render ledger cannot be "
                  f"written ({exc})", file=sys.stderr)
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

    # Which timeline these overlays are PLACED on. Measured off the
    # spine when the spine came from a real timeline; otherwise the
    # project's declaration. Never invented - but it no longer names
    # any file: it is recorded on each entry's binding (which placing
    # the shared file serves) and it drives the rejected-reel refusal
    # in `render_one_segment`. An unnamed timeline shares correctly
    # and collides with nothing.
    timeline_label = timeline_scope(audio_spine, project_config=None)
    print(f"Placing segments for timeline "
          f"{timeline_label or '<unnamed>'}", file=sys.stderr)

    # ── No two segments may draw different pixels to one filename,
    #    checked BEFORE anything is written ──
    #
    # The filename is provenance plus content digest, so two entries
    # sharing a name share identical pixels - which is the
    # cross-variant sharing, not a collision. The captain's format
    # closes every reel on a call to action taken from anywhere in
    # the episode (seven of nineteen reels close on one identical
    # sentence): those closers compute ONE name and render ONCE.
    #
    # What is refused is one name behind two content keys - a drawing
    # input that escaped the digest. Nothing downstream reads content
    # (`_assert_subtitle_overlay_matches_plan` compares block position
    # and time span only), so a wrong-pixels overwrite would reach the
    # timeline with every check green.
    #
    # Over the whole set and before the loop, because a collision
    # found after rendering is a file already overwritten.
    named = []
    for props in props_list:
        binding = segment_binding(
            timeline=timeline_label,
            speaker=props.get("_speaker"),
            block_position=props.get("_block_position"),
            source_clip_id=props.get("_source_clip_id"),
            source_start=props.get("_source_start"),
            source_end=props.get("_source_end"),
        )
        digest = _drawing_digest(props, geometry, container)
        named.append((
            segment_identifier(binding, digest),
            _reuse_key(props, remotion_dir, geometry, container),
        ))
    assert_no_content_collision(named)

    # ONE renderer for the whole pass, so a replacement holding a bundle
    # or a browser builds it once rather than per card.  See
    # `SubprocessRenderer` for the contract and for why it should be lazy.
    #
    # The default BUILDS the bundle-once renderer: one browser, one
    # bundle, reused across every card (the captain's ruling,
    # 2026-09-09).  `"subprocess"` stays selectable by name - a fresh
    # browser and a fresh bundle per card - and is the STARTUP fallback
    # below.  An explicit `renderer` still wins over either kind.
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

    # The fallback is at STARTUP, never per card.  `PersistentRenderer`
    # refuses a per-card fallback BY DESIGN - it would quietly restore
    # the exact cost that module exists to remove while still reporting
    # success - so this fires only when the persistent renderer cannot
    # START: `RendererUnavailable` before a single card is drawn, on a
    # renderer WE built under the persistent kind.  Anything later is a
    # mid-run death and stops the pass in the `except` below; a card
    # that fails to draw stays a card failure.
    renderer_fallback = None

    def _render_all(active_engine):
        """Draw every planned card through one renderer, in order.

        Appends into `segments` rather than returning a list, so the
        `except` below can tell a startup failure (nothing recorded)
        from a mid-run death (cards already on the record). An
        assignment (`segments = _render_all(...)`) would only land on
        success and read empty exactly when the distinction matters.
        """
        for i, props in enumerate(props_list):
            segment = render_one_segment(
                props, sub_output_dir, timeline_label,
                remotion_dir=remotion_dir,
                progress=f"[{i+1}/{len(props_list)}]",
                reuse=reuse, renderer=active_engine,
                overlay_geometry=geometry,
                overlay_container=container,
                project_folder=project_folder)
            # Appended unconditionally, failures included.  A dropped
            # segment is one the manifest never learns about, and 5.04
            # then refuses the compile citing a missing block rather than
            # the render that actually failed.
            segments.append(segment)

    segments = []
    try:
        try:
            _render_all(engine)
        except RendererUnavailable as exc:
            if segments or renderer is not None \
                    or renderer_kind != "persistent":
                # Mid-run death, a caller-supplied renderer, or a kind
                # that was never the persistent one: not a startup, so
                # not a fallback.  Re-raised to the refusal below.
                #
                # Note the conservative edge: a REUSED segment also
                # counts as recorded, so a pass whose first cards reuse
                # and whose renderer then fails to start refuses rather
                # than falling back. Loud, never silently slow.
                raise
            reason = str(exc).strip() \
                or "the persistent renderer could not start"
            announcement = (
                "FALLBACK: the persistent (bundle-once) caption "
                "renderer could not START "
                f"({reason}); falling back to the per-card subprocess "
                "renderer - a fresh browser and a fresh bundle for "
                "every card, ~0.70s of bundling per card over 763 "
                "cards in a full pass. This run is SLOWER but the "
                "pixels are identical. To choose this path "
                "deliberately, pass caption_renderer=\"subprocess\".")
            bar = "=" * 70
            print(f"\n{bar}\n{announcement}\n{bar}", file=sys.stderr)
            closer = getattr(engine, "close", None)
            if callable(closer):
                closer()
            engine = SubprocessRenderer(remotion_dir)
            # Still ours: we built the replacement too, so the `finally`
            # below closes it.  `owns_engine` stays True.
            renderer_fallback = {
                "requested": "persistent",
                "effective": "subprocess",
                "reason": reason,
                "announcement": announcement,
            }
            # The startup drew nothing, so the re-run covers every card
            # from scratch - `segments` is still empty here.
            _render_all(engine)
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
    except LedgerError as exc:
        # The RECORD died - not a card. Cards already returned are in
        # the ledger (each records itself before returning); the pass
        # stops rather than producing further pixels no root can
        # resolve. Refusing is the safe direction: the alternative is
        # exactly the unprotected-asset hole this ledger closes.
        raise SubtitleRenderRefused({
            "subtitle_overlay": {
                "available": False,
                "error": (f"the caption render ledger cannot be written "
                          f"({exc}): refusing after {len(segments)} of "
                          f"{len(props_list)} segments rather than "
                          f"leaving new assets no root can resolve"),
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
    # Every older generation a render in this pass replaced, named at
    # once. The files stay on disk; the mark reads this list to say
    # WHAT replaced them, and reachability still decides their fate.
    superseded_generations = sorted({
        older
        for segment in segments
        for older in segment.get("superseded", [])
    })
    if superseded_generations:
        print(f"superseded {len(superseded_generations)} older "
              f"generation(s) - orphan candidates, still on disk",
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

    # Which renderer drew the cards, so the run's own record says HOW
    # as well as what.  `explicit:<Class>` is a caller-supplied
    # renderer we did not choose; the fallback entry below fires only
    # at startup and carries its own reason.
    if renderer_fallback is not None:
        effective_renderer = renderer_fallback["effective"]
    elif renderer is not None:
        effective_renderer = f"explicit:{type(renderer).__name__}"
    else:
        effective_renderer = renderer_kind
    overlay_payload = {
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
            # HOW the cards were drawn: the persistent default, the
            # explicit subprocess fallback, or a caller-supplied
            # renderer.  The fallback that fired is recorded beside it
            # with its reason - a pass that silently ran the slow way
            # while reporting success is what that entry exists to stop.
            "renderer": effective_renderer,
            "total_segments": len(segments),
            "rendered": tally[RENDERED],
            "reused": tally[REUSED],
            "failed": tally[FAILED],
            "planned": len(props_list),
            "superseded_generations": superseded_generations,
        }
    }
    if renderer_fallback is not None:
        overlay_payload["subtitle_overlay"]["renderer_fallback"] = \
            renderer_fallback
    return overlay_payload


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
            # Optional, and absent means the bundle-once default with a
            # LOUD startup fallback to the per-card path (see
            # DEFAULT_CAPTION_RENDERER). Pass "subprocess" explicitly to
            # choose the slow path deliberately - e.g. a `frames`
            # container, which the persistent renderer cannot draw and
            # refuses rather than falling back.
            renderer_kind=data.get("caption_renderer",
                                   DEFAULT_CAPTION_RENDERER),
        )
    except SubtitleRenderRefused as refusal:
        emit(refusal.payload)
        sys.exit(1)

    emit(result)


if __name__ == "__main__":
    main()
