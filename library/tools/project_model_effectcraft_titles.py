"""Lane B (audit G3): titles and motion graphics onto EffectCraft text layers.

Projects ``timed_text_overlay`` moments and the text-bearing
motion-graphics roster entries into the pinned EffectCraft driver's
``AddText``/``Keyframe`` ops. The driver already speaks those ops and
the headless qual proves they render (``native/ren-effectcraft/tests/
headless.rs``: ``animated_title_exports_rgba_png_sequence_and_prores_4444_alpha``);
what was missing is the model side: the pipeline drew every title,
lower third, timed-text card and staged explainer through Fusion comps,
V6 and Remotion burn-in, so a headless render silently lacked all of
them.

Inputs are the pipeline's own resolved shapes, not new ones: timed-text
props as :func:`timed_text_overlay.plan_timed_text_segments` returns
them (``moments`` with ``startFrame``/``durationFrames`` plus
``fontFamily``/``fontFile``), and motion moments as
:func:`motion_graphics_plan.resolve_plan` returns them (``element``,
``anchor``, ``row``, ``runs``, ``color``, ``startFrame``/
``durationFrames``, ``entrance``/``exit``, ``data.stage_offsets``).
Nothing is re-validated twice and nothing is re-resolved: the
declaration stays the single place each value is stated.

The first subset, and what it refuses
-------------------------------------
Only text-bearing roster entries project: ``copy`` of ``required`` or
``optional``. An entry with ``copy == "none"`` (``beat_accent``,
``progress_bar``, ``frame_accents``, ``channel_bug``) is refused with
:class:`NonTextElement` naming it, never drawn as an empty layer. An
element outside the roster is refused with :class:`UnknownTitleElement`
carrying :func:`motion_graphics_vocabulary.refusal_reason` - which is
also what routes a near miss to the enumeration that owns it.

Captions and subtitles are explicitly NOT projected here, two ways. A
``captions``/``subtitles`` key on the titles document, or an element
naming one, is refused with :class:`CaptionNotProjected`: burned-in
EffectCraft text layers are this lane's answer for titles, and the
captions route (the engine's 25 ``captions.*`` commands vs these layers)
is audit gap G4's decision, owned by another lane. Funnelling
``plan_subtitles`` cards through this projector would pick that fight
by implication.

Geometry: the anchor point is quoted from the composition
----------------------------------------------------------
``remotion-subtitles/src/compositions/MotionGraphics/index.tsx``
``anchorStyle`` pins a stack to the safe-area insets: side cells sit
``left``/``right`` inside the insets, centre cells span them, top stacks
grow down from ``safeArea.top`` and bottom stacks grow up from
``safeArea.bottom``. This projector quotes that geometry per run.

Two measurements ground it, both taken off the pinned engine, not
assumed. First, the engine centres point text horizontally on the
position but hangs it BELOW it: a 28px run centred at ``[270, 152]``
inks rows 132..152 and columns 201..337 - the point is the baseline
origin, 20px (0.71em) of caps above it, nothing below. So ``y`` is a
baseline, and a baseline needs the face's own ascent/descent to stay
inside a box - half the size is 6px short, which is exactly what the
qual pixel check caught. Second, the ratios: ascent 0.97, descent 0.26,
measured off the bundled Montserrat Variable default instance
(``remotion-subtitles/public/fonts/Montserrat-Variable.ttf``,
``getmetrics`` at 100px: 97/26; the engine's own render of caps at
0.71em agrees with the file's 0.70em cap ink). They are quoted as
``MONT_ASCENT_RATIO``/``MONT_DESCENT_RATIO`` and pinned by a test that
re-measures the file, so a new bundled cut updates them deliberately.

With those, per run of pixel size ``s`` (ascent ``a``, descent ``d``):

- ``x``: the middle of the usable span
  (``safe.left + usable_width / 2``) - what a centre cell centres
  against, not the frame middle, because the insets are asymmetric.
- ``top``: baselines grow down from the safe top -
  ``safe.top + a`` for the first line, each next line its
  predecessor plus ``descent + gap + ascent`` (``STACK_GAP_PX`` quoted
  from the same composition).
- ``bottom``: mirrored up from ``height - safe.bottom``.
- ``middle``: the composition centres middle stacks on the frame, so
  the whole block (summed line heights plus gaps) centres on
  ``height / 2``. The row is not separately offset there: the block
  already accounts for every run.

The ascent/descent envelope is conservative - caps-only copy inks well
inside it - and it is verified per render rather than trusted: the
qual proof (``tests/qualification/test_effectcraft_titles_proof.py``)
measures the rendered ink rows/columns against the safe rect.

Two deliberate narrowings, both recorded rather than silent:

- Only centre-horizontal anchors project (``top_centre``,
  ``centre``, ``bottom_centre``, ...). The driver's ``AddText`` is
  centre-justified, so a side-anchored run centred on the safe edge
  would bleed past it. Side anchors refuse with
  :class:`SideAnchorNotProjected` naming the anchor. ``tracked``
  refuses the same way ``resolve_plan`` drops it: no per-frame track
  reaches this layer.
- Only Montserrat projects (see Fonts below).

Fonts: a family that will really draw the glyphs, or a refusal
---------------------------------------------------------------
Resolution starts in :mod:`library.tools.render_fonts` and ends in
measured metrics. Only Montserrat projects: it is the bundled face,
the composition draws every motion run in it, and its ascent/descent
are measured above. A declaration carrying ``font_file`` refuses with
:class:`ProjectCarriedFont` (the engine resolves fonts by installed
family name and cannot load a project file; naming the family would
substitute silently). A deliverable-but-unmeasured family (today:
Helvetica, accepted as a system font precisely because its shape
depends on the render machine) refuses with
:class:`FontMetricsUnmeasured`: without measured metrics there is no
baseline inset that keeps the ink inside the safe area, and guessing
one is how captions ended up under the platform's UI. Anything else
refuses with :class:`UndeliverableFont`.

Sizes quote the composition, not the engine
--------------------------------------------
Motion entries state no pixel size - ``type_role`` is a weight, never
a size - so the run size quotes the composition's own ``TYPE_SIZE``
(``display`` 56, ``supporting`` 36, ``micro`` 24, comp pixels) times
the entry's ``footprint`` (the plan's share, default 1: "as drawn").
Timed-text moments state their own ``font_size`` and it is read
directly. Either way the magnitude is somebody's stated value, never
an engine constant.

Timing: opacity keys, and what they approximate
-----------------------------------------------
EffectCraft text layers span the comp; visibility is keyframed on
``transform/opacity`` (0/100, the headless qual's own idiom).
Timed-text fades become exact ramps over
``fade_in_frames``/``fade_out_frames``. Motion ``entrance``/``exit``
become opacity ramps over the composition's ``RAMP_FRAMES`` at the
plan's fps - with one recorded loss: only the timing is carried, the
spatial character (slide, scale, ...) is not, so a non-fade character
lands a ``entrance_motion_not_carried`` loss entry naming it rather
than rendering a different motion quietly. ``cut`` (0 frames) is a
hard key. Runs of a staged entry (``data.stage_offsets`` from
:mod:`library.tools.explainer_plan`, themselves searched from the
reel's own word timings) appear by opacity cut at their stage second:
exact timing, no invented ramp.

A cut is bracketed, never single: the driver's ``addKey`` interpolates
linearly (measured: keys 100 at 0.0s and 0 at 0.5s read back 50.0 at
0.25s) and snaps keys to the comp frame grid, with no hold
interpolation on the op. So a cut envelope over ``[a, b)`` is keyed
``(a - period, hidden), (a, shown)`` ... ``(b - period, shown),
(b, hidden)`` at the comp frame period: at frame samples the picture
is exact, and the ramp between the brackets is never sampled. A span
shorter than one frame refuses - a sub-frame title draws nothing
reliably - as does an entrance plus exit longer than the span.

AddShape: deliberately deferred, and why
-----------------------------------------
This subset mints ``AddText`` and ``Keyframe`` only. Every shape the
roster could want here is sized by text it sits behind (``subject_emblem``'s
backplate, ``lower_third``'s staged rule) and unmeasured plate
dimensions would be invented geometry. ``mg_tight_box`` measures those
boxes on the Remotion side; carrying its measurement into an
``AddShape`` rect is the follow-up, not a guess made here.

Read-only on live project data: the only inputs are the titles
document, the comp spec and the safe area. The only outputs are the
returned ops and records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from library.tools import motion_graphics_vocabulary as _vocabulary
    from library.tools.project_model_effectcraft import CompSpec
    from library.tools.render_fonts import font_is_deliverable, primary_family
    from library.tools.safe_area import SafeAreaInsets
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools import motion_graphics_vocabulary as _vocabulary
    from tools.project_model_effectcraft import CompSpec
    from tools.render_fonts import font_is_deliverable, primary_family
    from tools.safe_area import SafeAreaInsets


# ── Quoted from the composition ──────────────────────────────────────
#
# Both tables are the Remotion MotionGraphics composition's own drawing,
# quoted so this projector draws the same magnitudes there. If the
# composition changes either table, ``test_type_size_quote_matches_the_
# composition`` fails and this module is updated deliberately, not left
# quoting stale numbers.

TYPE_SIZE_BY_ROLE: dict[str, float] = {
    "display": 56.0,
    "supporting": 36.0,
    "micro": 24.0,
}
"""Comp pixels per ``type_role`` at footprint 1.

Quoted from ``TYPE_SIZE`` in
``remotion-subtitles/src/compositions/MotionGraphics/index.tsx``.
"""

STACK_GAP_PX = 24.0
"""Vertical gap between stacked rows, in comp pixels.

Quoted from ``STACK_GAP_PX`` in the same composition.
"""

RAMP_FRAMES: dict[str, int] = {
    "cut": 0,
    "fade": 8,
    "slide": 8,
    "scale": 8,
    "mask": 8,
    "draw": 12,
    "blur": 10,
    "typewriter": 20,
    "glitch": 10,
    "flip": 12,
}
"""Entrance/exit ramp lengths in composition frames.

Quoted from ``RAMP_FRAMES`` in the same composition. Divided by the
plan's fps, never the comp's: the numbers were drawn at project fps.
"""

#: Opacity keyframe values: the headless qual's own 0/100 idiom.
OPACITY_HIDDEN = 0.0
OPACITY_SHOWN = 100.0

#: The face the composition draws every motion run in.
#:
#: Quoted from ``remotion-subtitles/src/compositions/MotionGraphics/
#: index.tsx`` (``fontFamily: "Montserrat, sans-serif"``, loaded
#: blocking from the bundled file by ``src/fonts.ts``). A motion entry
#: declares no typeface of its own - the roster states none and the
#: engine ships none (AGENTS.md 14, 16) - so the composition's face is
#: what the run is drawn in, and quoting it here draws the same face
#: rather than the engine default behind the plan's back. The host
#: still needs the face installed where the headless engine resolves
#: fonts: the qual proof reads the engine's own ``text.fonts`` and
#: skips naming the missing piece when it is absent.
MOTION_FONT_FAMILY = "Montserrat"

#: Baseline-to-ink extents of the bundled Montserrat Variable default
#: instance, as shares of the pixel size. Measured off
#: ``remotion-subtitles/public/fonts/Montserrat-Variable.ttf``
#: (``getmetrics`` at 100px: ascent 97, descent 26; the pinned
#: engine's own render of caps inks 0.71em above the baseline against
#: the file's 0.70em cap ink). The envelope is conservative: caps-only
#: copy inks well inside it. Pinned by
#: ``test_montserrat_metrics_match_the_bundled_file`` - a new bundled
#: cut re-measures deliberately, and any other family is refused until
#: it is measured the same way (see :class:`FontMetricsUnmeasured`).
MONT_ASCENT_RATIO = 0.97
MONT_DESCENT_RATIO = 0.26

#: Horizontal anchor cells this subset can place. The driver's text is
#: centre-justified, so only centre cells project; see the docstring.
CENTRE_HORIZONTALS = ("centre",)


class TitleProjectionError(ValueError):
    """Base refusal for the titles-to-EffectCraft boundary."""


class NonTextElement(TitleProjectionError):
    """A roster entry with ``copy == "none"``: nothing to draw as text."""


class UnknownTitleElement(TitleProjectionError):
    """An element key the roster does not know."""


class CaptionNotProjected(TitleProjectionError):
    """Captions/subtitles addressed to this projector; G4 owns that route."""


class UndeliverableFont(TitleProjectionError):
    """A family ``render_fonts`` says will not draw the glyphs."""


class FontMetricsUnmeasured(TitleProjectionError):
    """A deliverable family with no measured baseline metrics."""


class ProjectCarriedFont(TitleProjectionError):
    """A project TTF the headless engine cannot load by family name."""


class TitleOutsideSafeArea(TitleProjectionError):
    """A timed-text moment centred in the platform's own interface band."""


class TitleOutsideComp(TitleProjectionError):
    """A title whose comp point or span leaves the composition."""


class SideAnchorNotProjected(TitleProjectionError):
    """A side- or tracked-anchored run the centred text op cannot place."""


def _anchor_cells(anchor: str) -> tuple[str, str]:
    """An anchor as (vertical band, horizontal cell).

    Mirrors ``anchorStyle``'s own decomposition in the composition:
    ``centre`` is middle/centre, anything else splits on the first ``_``.
    """
    name = (anchor or "").strip().lower()
    if name == "centre":
        return ("middle", "centre")
    vertical, _, horizontal = name.partition("_")
    return (vertical, horizontal or "centre")


def _require_known_element(key: str, label: str) -> Any:
    """The roster entry for ``key``, or a refusal naming what owns it."""
    element = _vocabulary.ELEMENTS_BY_KEY.get(key)
    if element is None:
        raise UnknownTitleElement(
            f"{label} names {key!r}: {_vocabulary.refusal_reason(key)}"
        )
    if element.copy == "none":
        raise NonTextElement(
            f"{label} names {key!r}, which carries no copy "
            f"(function {element.function!r}); a text layer for it would "
            f"draw nothing, and an overlay that draws nothing is not "
            f"rendered (AGENTS.md 10.2)"
        )
    return element


def _resolve_font(font_family: str, font_file: str | None, label: str) -> str:
    """The ``AddText`` font for a declaration, or a refusal.

    Only Montserrat projects: bundled, measured, and the face the
    composition draws. A project-carried file refuses (the engine
    resolves fonts by installed family name and cannot load the file).
    A deliverable-but-unmeasured family refuses with
    :class:`FontMetricsUnmeasured` rather than guessing a baseline
    inset for a shape that depends on the render machine.
    """
    if font_file:
        raise ProjectCarriedFont(
            f"{label} carries font_file={font_file!r}: the headless engine "
            f"resolves its font attribute by installed family name and "
            f"cannot load a project file, so projecting family "
            f"{font_family!r} would render in a substitute on any host "
            f"without it. Staging project fonts onto the render host is "
            f"a later lane."
        )
    if not font_is_deliverable(font_family, None):
        raise UndeliverableFont(
            f"{label} names font_family={font_family!r}, which is neither "
            f"bundled nor accepted as a system font (see "
            f"library/tools/render_fonts.py); refusing rather than "
            f"rendering in Chromium's fallback sans"
        )
    family = primary_family(font_family)
    if family != MOTION_FONT_FAMILY:
        raise FontMetricsUnmeasured(
            f"{label} names font_family={font_family!r}: deliverable, but "
            f"only {MOTION_FONT_FAMILY} has measured baseline metrics "
            f"(MONT_ASCENT_RATIO/MONT_DESCENT_RATIO). A baseline inset "
            f"for a machine-dependent system face would be a guess, and "
            f"a guessed inset is how copy ends up under the platform's "
            f"UI. Measure the face the way Montserrat was measured, then "
            f"project it."
        )
    return family


def _run_size(type_role: str, footprint: float | None, label: str) -> float:
    """Comp pixels for one copy run: the composition's size, scaled."""
    size = TYPE_SIZE_BY_ROLE.get(type_role)
    if size is None:
        raise TitleProjectionError(
            f"{label} run has type_role={type_role!r}; the vocabulary's "
            f"roles are {sorted(TYPE_SIZE_BY_ROLE)}"
        )
    scale = footprint if footprint and footprint > 0 else 1.0
    return size * scale


def _anchor_x(anchor: str, safe: SafeAreaInsets, width: int,
                label: str) -> float:
    """The comp x a run is centred on, from the safe-area geometry."""
    _, horizontal = _anchor_cells(anchor)
    if horizontal not in CENTRE_HORIZONTALS:
        raise SideAnchorNotProjected(
            f"{label} anchors {anchor!r}: the driver's text layers are "
            f"centre-justified, so a side-anchored run centred on the "
            f"safe edge would bleed past it. Only centre-horizontal "
            f"anchors project in this subset."
        )
    if horizontal == "tracked":
        raise SideAnchorNotProjected(
            f"{label} anchors {anchor!r}: a tracked position needs a "
            f"per-frame measurement nothing carries to this layer"
        )
    return safe.left + max(0, width - safe.left - safe.right) / 2.0


def _stacked_baseline(anchor: str, sizes: list[float], index: int,
                      row: int, safe: SafeAreaInsets, height: int,
                      label: str) -> float:
    """The comp baseline run ``index`` is hung from.

    The engine hangs point text below the position, so ``y`` is a
    baseline and each line is placed by the face's own ascent/descent:
    top-anchored stacks grow down from the safe top, bottom-anchored
    stacks grow up from ``height - safe.bottom``, and middle-anchored
    stacks centre the whole block on the frame (the composition centres
    the stack, so the row is not separately offset there).
    """
    vertical, _ = _anchor_cells(anchor)
    if vertical == "tracked":
        raise SideAnchorNotProjected(
            f"{label} anchors {anchor!r}: a tracked position needs a "
            f"per-frame measurement nothing carries to this layer"
        )
    ascents = [s * MONT_ASCENT_RATIO for s in sizes]
    descents = [s * MONT_DESCENT_RATIO for s in sizes]
    line = ascents[0] + descents[0] + STACK_GAP_PX
    if vertical == "top":
        prior = sum(a + d + STACK_GAP_PX
                    for a, d in zip(ascents[:index], descents[:index]))
        return safe.top + row * line + prior + ascents[index]
    if vertical == "bottom":
        prior = sum(a + d + STACK_GAP_PX
                    for a, d in zip(ascents[:index], descents[:index]))
        return (height - safe.bottom - row * line - prior
                - descents[index])
    total = (sum(ascents) + sum(descents)
             + STACK_GAP_PX * max(0, len(sizes) - 1))
    top = height / 2.0 - total / 2.0
    prior = sum(a + d + STACK_GAP_PX
                for a, d in zip(ascents[:index], descents[:index]))
    return top + prior + ascents[index]


def _anchor_point(anchor: str, size_px: float, row: int,
                  safe: SafeAreaInsets, width: int, height: int,
                  label: str) -> list[float]:
    """The comp point a single run is hung from (no stacking)."""
    x = _anchor_x(anchor, safe, width, label)
    y = _stacked_baseline(anchor, [size_px], 0, row, safe, height, label)
    if not (0.0 <= x <= width and 0.0 <= y <= height):
        raise TitleOutsideComp(
            f"{label} anchor {anchor!r} resolves to comp point "
            f"({x:.1f}, {y:.1f}) outside a {width}x{height} comp"
        )
    return [x, y]


def _require_fill(color: str, label: str) -> str:
    """A ``#rrggbb[aa]`` fill the engine's text op parses, or a refusal.

    The engine reads a hex string and fails anything else at execution
    time, after mutating the project - so the format is checked here,
    before any mutation, the same plan-time validation audit gap G10
    asks of transition names.
    """
    import re
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", color):
        return color
    raise TitleProjectionError(
        f"{label} has fill={color!r}; the headless text op parses "
        f"#rrggbb[aa] hex, and anything else fails at execution"
    )


def _key(layer: str, time: float, value: float) -> dict[str, Any]:
    return {"op": "keyframe", "layer": layer, "path": "transform/opacity",
            "time": time, "value": value}


def _cut_keys(layer: str, appear: float, disappear: float,
              frame_period: float) -> list[dict[str, Any]]:
    """Bracketed hard visibility for ``[appear, disappear)``.

    ``addKey`` interpolates linearly, so a lone pair ramps across the
    hold. Bracketing each edge one frame period out makes the picture
    exact at frame samples: the ramp between a bracket is never
    sampled. ``appear == 0`` needs no pre-bracket (nothing plays
    before the first frame).
    """
    ops = []
    if appear > 0:
        ops.append(_key(layer, max(0.0, appear - frame_period),
                        OPACITY_HIDDEN))
    ops.append(_key(layer, appear, OPACITY_SHOWN))
    ops.append(_key(layer, disappear - frame_period, OPACITY_SHOWN))
    ops.append(_key(layer, disappear, OPACITY_HIDDEN))
    return ops


def _require_span(label: str, appear: float, disappear: float,
                  frame_period: float) -> None:
    span = disappear - appear
    if span < frame_period - 1e-9:
        raise TitleOutsideComp(
            f"{label} spans {span:.4f}s, less than one comp frame; a "
            f"sub-frame title draws nothing reliably"
        )


def _opacity_keys(layer: str, appear: float, disappear: float,
                  ramp_in: float, ramp_out: float,
                  frame_period: float) -> list[dict[str, Any]]:
    """Visibility keys for one layer over ``[appear, disappear)`` seconds.

    Exact ramps where the plan ramps, bracketed cuts where it cuts.
    Times are comp seconds and must already be comp-relative. A span
    shorter than one frame, or ramps longer than the span, is refused:
    either draws nothing reliably, and clamping would invent timing.
    """
    _require_span(f"layer {layer!r}", appear, disappear, frame_period)
    span = disappear - appear
    if ramp_in + ramp_out > span + 1e-9:
        raise TitleProjectionError(
            f"layer {layer!r} entrance ({ramp_in:.3f}s) plus exit "
            f"({ramp_out:.3f}s) outlasts its {span:.3f}s span; refusing "
            f"rather than clamping the plan's timing"
        )
    ops: list[dict[str, Any]] = []
    if ramp_in > 0:
        ops.append(_key(layer, appear, OPACITY_HIDDEN))
        ops.append(_key(layer, appear + ramp_in, OPACITY_SHOWN))
    else:
        if appear > 0:
            ops.append(_key(layer, max(0.0, appear - frame_period),
                            OPACITY_HIDDEN))
        ops.append(_key(layer, appear, OPACITY_SHOWN))
    if ramp_out > 0:
        ops.append(_key(layer, disappear - ramp_out, OPACITY_SHOWN))
        ops.append(_key(layer, disappear, OPACITY_HIDDEN))
    else:
        ops.append(_key(layer, disappear - frame_period, OPACITY_SHOWN))
        ops.append(_key(layer, disappear, OPACITY_HIDDEN))
    return ops


@dataclass
class TitleRecord:
    """What one projected title is, for the identity map and the reader."""

    layer: str
    kind: str
    text: str
    position: list[float]
    appear_seconds: float
    disappear_seconds: float

    def as_json(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "kind": self.kind,
            "text": self.text,
            "position": list(self.position),
            "appear_seconds": self.appear_seconds,
            "disappear_seconds": self.disappear_seconds,
        }


@dataclass
class TitleProjection:
    """Ops plus identity records plus the losses the run must report."""

    operations: list[dict[str, Any]] = field(default_factory=list)
    titles: list[TitleRecord] = field(default_factory=list)
    losses: list[dict[str, str]] = field(default_factory=list)

    def extend(self, other: "TitleProjection") -> None:
        self.operations.extend(other.operations)
        self.titles.extend(other.titles)
        self.losses.extend(other.losses)


def _check_comp_span(appear: float, disappear: float, comp: CompSpec,
                     label: str) -> None:
    duration = comp.duration_seconds
    if not (0.0 <= appear < disappear <= duration + 1e-9):
        raise TitleOutsideComp(
            f"{label} spans comp seconds {appear:.3f}..{disappear:.3f} "
            f"outside a {duration:.3f}s comp; rebase timeline frames "
            f"against the comp start before projecting"
        )


def project_timed_text_moment(moment: dict[str, Any], index: int,
                              font_family: str, font_file: str | None,
                              comp: CompSpec, fps: float,
                              safe: SafeAreaInsets,
                              comp_start_seconds: float = 0.0,
                              ) -> TitleProjection:
    """One ``timed_text_overlay`` moment into ``AddText`` + opacity keys.

    ``moment`` is a resolved props moment (``text``, ``color``,
    ``fontSize``, ``startFrame``, ``durationFrames``, ``x``, ``y``,
    ``fadeInFrames``, ``fadeOutFrames``): the shape
    :func:`timed_text_overlay.plan_timed_text_segments` returns, which
    already refused malformed, unstyled and unsafe declarations. The
    containment is re-checked here so a caller hand-building props gets
    the same refusal rather than a quieter one.
    """
    label = f"timed_text_overlay moment {index}"
    out = TitleProjection()
    text = moment["text"]
    if not str(text).strip():
        raise TitleProjectionError(f"{label} has empty text")
    font = _resolve_font(font_family, font_file, label)
    x, y = float(moment["x"]), float(moment["y"])
    if not safe.contains_normalised(x, y):
        raise TitleOutsideSafeArea(
            f"{label} is centred at x={x}, y={y}, inside the platform's "
            f"own interface band (profile {safe.profile!r}); see "
            f"library/tools/timed_text_overlay.py"
        )
    size = moment["fontSize"]
    if not isinstance(size, (int, float)) or isinstance(size, bool) \
            or size <= 0:
        raise TitleProjectionError(
            f"{label} has font_size={size!r}; a moment's size is a "
            f"positive number of pixels the declaration states")
    position = [x * comp.width, y * comp.height]
    appear = moment["startFrame"] / fps - comp_start_seconds
    disappear = (moment["startFrame"] + moment["durationFrames"]) / fps \
        - comp_start_seconds
    _check_comp_span(appear, disappear, comp, label)
    for key in ("fontWeight", "textShadow", "textAlign"):
        if moment.get(key) not in (None, "", "center", "none"):
            out.losses.append({
                "reason": "timed_text_style_not_carried",
                "detail": f"{label} states {key}={moment[key]!r}; the "
                          f"headless text op carries size/fill/font only",
            })
    layer = f"timed-text-{index}"
    out.operations.append({
        "op": "add_text",
        "name": layer,
        "text": str(text),
        "size": float(size),
        "fill": _require_fill(moment["color"], label),
        "position": position,
        "font": font,
    })
    out.operations.extend(_opacity_keys(
        layer, appear, disappear,
        moment["fadeInFrames"] / fps, moment["fadeOutFrames"] / fps,
        1.0 / comp.frame_rate))
    out.titles.append(TitleRecord(
        layer=layer, kind="timed_text", text=str(text),
        position=position, appear_seconds=round(appear, 3),
        disappear_seconds=round(disappear, 3)))
    return out


def project_motion_moment(moment: dict[str, Any], comp: CompSpec,
                          fps: float, safe: SafeAreaInsets,
                          comp_start_seconds: float = 0.0,
                          ) -> TitleProjection:
    """One resolved motion-graphics moment into per-run text layers.

    One ``AddText`` per copy run so each run keeps its own declared
    ``type_role`` size; a single joined layer would flatten the
    display/supporting distinction the plan stated. Staged entries
    (``data.stage_offsets``) reveal run ``i`` by opacity cut at its
    stage second - exact timing over the reel's own word timings, no
    invented ramp.
    """
    out = TitleProjection()
    key = str(moment["element"])
    label = f"motion-graphics moment {key!r}"
    element = _require_known_element(key, label)
    _ = element
    anchor = str(moment["anchor"])
    row = max(0, int(moment.get("row", 0) or 0))
    runs = moment["runs"]
    if not runs:
        raise TitleProjectionError(
            f"{label} carries no copy runs; the resolver drops run-less "
            f"entries before this projector ever sees them")
    color = str(moment.get("color") or "")
    if not color and "colour_role" in _vocabulary.ELEMENTS_BY_KEY[key].axes:
        raise TitleProjectionError(
            f"{label} states no colour; the resolver drops colour-less "
            f"entries before this projector ever sees them")
    appear = moment["startFrame"] / fps - comp_start_seconds
    disappear = (moment["startFrame"] + moment["durationFrames"]) / fps \
        - comp_start_seconds
    _check_comp_span(appear, disappear, comp, label)
    entrance = str(moment.get("entrance") or "cut").lower()
    exit_ = str(moment.get("exit") or "cut").lower()
    ramp_in = RAMP_FRAMES.get(entrance, 0) / fps
    ramp_out = RAMP_FRAMES.get(exit_, 0) / fps
    for character, where in ((entrance, "entrance"), (exit_, "exit")):
        if character not in ("cut", "fade"):
            out.losses.append({
                "reason": "entrance_motion_not_carried",
                "detail": f"{label} {where}={character!r}: only its "
                          f"timing is keyframed (opacity over "
                          f"{RAMP_FRAMES.get(character, 0)} composition "
                          f"frames); the spatial character is not carried",
            })
    footprint = moment.get("footprint")
    footprint_value = (float(footprint) if isinstance(footprint, (int, float))
                       and not isinstance(footprint, bool) else None)
    offsets = (moment.get("data") or {}).get("stage_offsets")
    staged = isinstance(offsets, list) and len(offsets) == len(runs)
    if moment.get("data") and "stage_offsets" in (moment.get("data") or {}) \
            and not staged:
        raise TitleProjectionError(
            f"{label} stage_offsets has {len(offsets) if isinstance(offsets, list) else '?'} "
            f"entries for {len(runs)} runs; a stage per run or none")
    sizes: list[float] = []
    for position_index, run in enumerate(runs):
        role = run["type_role"]
        size = _run_size(role, footprint_value,
                         f"{label} run {position_index}")
        sizes.append(size)
    x = _anchor_x(anchor, safe, comp.width, label)
    for position_index, run in enumerate(runs):
        size = sizes[position_index]
        y = _stacked_baseline(anchor, sizes, position_index, row, safe,
                              comp.height,
                              f"{label} run {position_index}")
        if not (0.0 <= x <= comp.width and 0.0 <= y <= comp.height):
            raise TitleOutsideComp(
                f"{label} run {position_index} anchor {anchor!r} resolves "
                f"to comp point ({x:.1f}, {y:.1f}) outside a "
                f"{comp.width}x{comp.height} comp"
            )
        point = [x, y]
        layer = f"{key}-{moment['startFrame']}-{position_index}"
        out.operations.append({
            "op": "add_text",
            "name": layer,
            "text": run["text"],
            "size": size,
            "fill": _require_fill(color, f"{label} run {position_index}"),
            "position": point,
            "font": MOTION_FONT_FAMILY,
        })
        if staged:
            period = 1.0 / comp.frame_rate
            _require_span(layer, appear, disappear, period)
            at = appear + float(offsets[position_index])
            if not (appear - 1e-9 <= at <= disappear + 1e-9):
                raise TitleOutsideComp(
                    f"{label} run {position_index} stages at {at:.3f}s, "
                    f"outside its {appear:.3f}..{disappear:.3f}s span")
            if at <= appear:
                out.operations.extend(
                    _cut_keys(layer, appear, disappear, period))
            else:
                out.operations.append(_key(layer, appear, OPACITY_HIDDEN))
                out.operations.append(_key(layer, max(appear, at - period),
                                           OPACITY_HIDDEN))
                out.operations.append(_key(layer, at, OPACITY_SHOWN))
                out.operations.append(_key(layer, max(at, disappear - period),
                                           OPACITY_SHOWN))
                out.operations.append(_key(layer, disappear, OPACITY_HIDDEN))
        else:
            out.operations.extend(_opacity_keys(
                layer, appear, disappear, ramp_in, ramp_out,
                1.0 / comp.frame_rate))
        out.titles.append(TitleRecord(
            layer=layer, kind=key, text=run["text"],
            position=point, appear_seconds=round(appear, 3),
            disappear_seconds=round(disappear, 3)))
    return out


def _reject_caption_keys(document: dict[str, Any]) -> None:
    """Refuse anything caption-shaped before it becomes a text layer."""
    for key in ("captions", "subtitles", "subtitle_segments",
                "caption_cards"):
        if document.get(key):
            raise CaptionNotProjected(
                f"titles document carries {key!r}: captions/subtitles "
                f"are not projected as burned-in text layers here; the "
                f"caption route (engine captions.* vs these layers) is "
                f"audit gap G4's decision, owned by another lane"
            )
    for entry in document.get("motion_moments") or []:
        name = str((entry or {}).get("element") or "")
        if "caption" in name.lower() or "subtitle" in name.lower():
            raise CaptionNotProjected(
                f"motion entry names {name!r}: caption treatments belong "
                f"to the caption system (see motion_graphics_vocabulary."
                f"OUT_OF_VOCABULARY), not to a text layer"
            )


def project_titles(document: dict[str, Any], comp: CompSpec, fps: float,
                   safe: SafeAreaInsets,
                   comp_start_seconds: float = 0.0) -> TitleProjection:
    """A titles document into ``AddText``/``Keyframe`` ops.

    ``document`` carries ``timed_text`` (props with ``moments``,
    ``fontFamily`` and optional ``fontFile``) and/or
    ``motion_moments`` (resolved moments). Either may be absent; both
    absent is an empty projection, refused - an overlay that draws
    nothing is not rendered.
    """
    if not isinstance(document, dict):
        raise TitleProjectionError(
            f"titles document must be a mapping, got "
            f"{type(document).__name__}")
    _reject_caption_keys(document)
    out = TitleProjection()
    timed = document.get("timed_text") or {}
    for index, moment in enumerate(timed.get("moments") or []):
        out.extend(project_timed_text_moment(
            moment, index, timed["fontFamily"], timed.get("fontFile"),
            comp, fps, safe, comp_start_seconds))
    for moment in document.get("motion_moments") or []:
        out.extend(project_motion_moment(
            moment, comp, fps, safe, comp_start_seconds))
    if not out.operations:
        raise TitleProjectionError(
            "titles document projects no text layer: no timed-text "
            "moments and no motion moments")
    return out


def build_titles_request(document: dict[str, Any], comp: CompSpec,
                         fps: float, safe: SafeAreaInsets,
                         renders: list[dict[str, Any]],
                         comp_start_seconds: float = 0.0,
                         project_id: str | None = None) -> dict[str, Any]:
    """A standalone driver-shaped request carrying only titles.

    The overlay-companion shape: titles render as their own alpha
    overlay (like every Remotion segment), composited downstream, so a
    titles-only comp needs no footage. Returns the request dict plus
    the identity records and losses beside it.
    """
    if not renders:
        raise TitleProjectionError(
            "at least one render spec is required: a titles request "
            "with no render draws nothing"
        )
    projection = project_titles(document, comp, fps, safe,
                                comp_start_seconds)
    operations: list[dict[str, Any]] = [{
        "op": "new_comp",
        "name": comp.name,
        "width": comp.width,
        "height": comp.height,
        "frame_rate": comp.frame_rate,
        "duration": comp.duration_seconds,
    }]
    operations.extend(projection.operations)
    return {
        "version": 1,
        "project_id": project_id or "titles-effectcraft",
        "operations": operations,
        "renders": renders,
        "identity_map": {
            "titles": [record.as_json()
                       for record in projection.titles],
        },
        "losses": projection.losses,
    }
