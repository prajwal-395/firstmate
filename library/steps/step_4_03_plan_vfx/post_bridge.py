#!/usr/bin/env python3
"""
Step 4.3 Bridge: Resolve VFX Creative Plan to Execution Data

Takes the LLM's creative VFX selections (effect_type, params,
target_block_position) and resolves:
- target_block_position → timeline_start/end from timed spine
- params → checked against the parameter names the renderer reads
- Optionally: OpenCV motion detection to skip already-dynamic clips

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import sys
from library.tools.pipeline_validation import require_keys
from library.tools.plan_keys import refuse_unknown_keys
from library.tools.sub_block_anchor import (
    ANCHOR_ENTRY_KEYS,
    AnchorRefused,
    resolve_anchor,
)
from library.tools.vfx_plan_basis import (
    DroppedEntry,
    PlanBasis,
    basis_summary,
)
from library.tools.native_ops import (
    NATIVE_SPEED_EFFECTS,
    SPEED_RAMP_PARAM_KEYS,
    refuse_speed_curve,
)


# The step's own effect toolkit, and the parameter NAMES each one is
# drawn from. This is a CAPABILITY statement, not a creative one: it
# carries no value, no default and no bound. How far a zoom travels and
# how hard a shake hits are the planner's decisions, and an `INTENSITY_MAP`
# resolving `subtle|moderate|strong` into fixed numbers here was removed
# on the captain's ruling of 2026-09-02 - it hardcoded creativity, and it
# justified its ceiling by citing AGENTS.md, a document this step never
# reads.
#
# What the map DID guarantee has to survive it. The renderer
# (`library/tools/fusion/comp_builder.build_effect_comp`) dispatches on
# parameter NAMES, so a plan emitting a name nothing reads produces a comp
# without that effect in it and NO WARNING (AGENTS.md §10.2) - which is
# exactly how `zoom_percent`, `intensity_px` and `scale_factor` left three
# of the five advertised effects rendering nothing while the manifest
# recorded them as planned. So the names are enumerated here, checked
# against the renderer's own dispatch by
# tests/test_vfx_reaches_the_manifest.py, and an entry carrying none of
# its effect's names is DROPPED with the reason rather than passed on to
# draw nothing - `no_readable_parameters`, which is the drop reason
# `vfx_plan_basis` records.
#
# And the VALUES in a readable name are never checked, clamped or
# substituted. That is the other half of the captain's ruling and it is
# stated here because this is the only place that could do any of the
# three: how far a zoom travels is a magnitude, a magnitude is taste,
# and an engine that bounded one would be offering the scale the ruling
# removed (AGENTS.md 10.5).
TOOLKIT_PARAMETERS = {
    # Ken Burns drift across the clip: fx.zoom's start and end, plus an
    # optional static re-centre. `pan_start` is deliberately ABSENT:
    # `fx.zoom` accepts it and draws nothing with it - animated Center
    # drift needs a `Path{}`, which AGENTS.md §5 bans wherever a Merge
    # exists downstream - so advertising it would be advertising a name
    # with no reader, which is the whole defect this enumeration exists
    # to prevent.
    "slow_zoom_in": ("zoom_start", "zoom_end", "pan_end"),
    "slow_zoom_out": ("zoom_start", "zoom_end", "pan_end"),
    # Punch in and settle back - fx.zoom's three-point spline, so the
    # emphasis reads as a push rather than a permanent reframe.
    "zoom_emphasis": ("zoom_start", "zoom_mid", "zoom_end"),
    # fx.shake offsets the frame centre as a FRACTION of frame width.
    # `shake_x`/`shake_y` are what reach the dispatch; `shake_decay_frames`
    # MODIFIES the shake they start and draws nothing on its own, which is
    # why an entry must carry at least one readable name rather than a
    # particular one.
    "screen_shake": ("shake_x", "shake_y", "shake_decay_frames"),
    # A static reframe: a constant Size on the Transform, held for the
    # clip, so all three points of the spline carry the same value.
    "cut_in": ("zoom_start", "zoom_mid", "zoom_end"),
    # cut_out: DELETED per captain's ruling 2026-08-17.
    # Superseded by the framing parameter (PR 109); a pull-back is now a
    # lower framing value, so a separate sub-1.0 zoom effect is redundant.
}


# Spellings that mean an existing effect. An alias may only RENAME an
# effect, never decide one: `push_in` and `zoom_emphasis` are two names
# for the same punch-and-settle, so the mapping states a fact.
EFFECT_ALIASES = {
    "push_in": "zoom_emphasis",
}

# Aliases that chose a direction the planner had not stated. Kept so the
# withdrawal is visible: a name that says only "zoom" does not say which
# way, and answering "in" on the planner's behalf is taste. A plan naming
# one of these now gets dropped with the toolkit listed, which tells the
# editor to say which they meant.
#
# `ken_burns` used to sit here for the same reason, and no longer does:
# the captain asks for Ken Burns BY NAME (2026-09-09, "a lot of ken burns
# to emphasize points"), so the spelling is accepted with the direction
# read off the params the plan chose - `zoom_end` above `zoom_start` is a
# push in, below is a pull out.  An entry whose params state neither is
# still dropped, as `ken_burns_without_direction`: the direction is
# DERIVED, never defaulted.  This EXTENDS the 2026-09-08 drift ruling
# rather than sitting beside it - the renderer, the toolkit params and
# the rationale refusal are all unchanged; only the spelling is new.
WITHDRAWN_ALIASES = {
    "slow_zoom": "names no direction; `slow_zoom_in` and `slow_zoom_out` "
                 "are different effects and the pipeline may not pick",
}

# The captain's name for the drift move.  Not a second motion system:
# a `ken_burns` entry resolves to the `slow_zoom_in` / `slow_zoom_out`
# its own params describe, and travels the rest of the path - rationale
# refusal included - as that effect.
KEN_BURNS = "ken_burns"

# The drift moves: gradual motion that puts life on a STATIC hold.  A
# push that arrives because every static shot gets a push is exactly
# what the captain's ruling of 2026-09-08 refuses - motion on a static
# shot is allowed only where the plan states, per shot, why that shot
# wants it, and a shot with no reason gets no motion.  So an entry
# naming one of these with a missing or blank `rationale` is dropped
# with `no_stated_reason` (library/tools/vfx_plan_basis.py) rather than
# given motion.  Emphasis effects (`zoom_emphasis`, `screen_shake`,
# `cut_in`) are a different decision with their own grounds and their
# path is unchanged by this.
DRIFT_EFFECTS = ("slow_zoom_in", "slow_zoom_out")

# There is no default zoom, and there must not be one again. A
# `inject_default_ken_burns` here used to add `slow_zoom_in`/`slow_zoom_out`
# to every speech block over three seconds that the plan had deliberately
# left alone, "because the style spec requires subtle motion on all A-roll
# clips >3s". That is a creative floor, removed by the captain's ruling of
# 2026-08-20, and it is the one that survived because
# `tests/test_no_creative_floors.py` guarded only the PROMPTS.
# See docs/RULE_EVIDENCE.md#the-default-that-outvoted-the-plan.


# Native speed effects: timeline operations, not Fusion comps. A ramp is
# a SEQUENCE of constant-speed steps - Resolve 21.1 carries no
# speed-curve API (measured 2026-09-24), so the plan names `segments`
# and each step is one constant `SetSpeed` judged by `GetSpeed` during
# the timeline build (`library/tools/native_ops_apply.py`). A freeze is
# `freeze_frame`, never a 0% step. Entries resolve with
# `"route": "native_resolve"` so `compile_manifest` carries them to the
# build instead of the comp engine.
def _validate_native_speed(raw_type, effect_type, params, pos, _drop):
    """Check a native speed entry's params; return step percents or None.

    Returns the list of step percents for `speed_ramp`, [] for
    `freeze_frame`. Returns None when the entry is dropped (recorded).
    A `curve`/`easing`/`bezier` param RAISES (`NativeSpeedRefused`):
    rounding a curve to constants would invent pacing the plan declined
    to step out, so the model re-plans with `segments`.
    """
    if effect_type == "freeze_frame":
        # A freeze takes no params: the span (anchors or block) is what
        # freezes. Anything carried is ignored, never read.
        return []
    for curve_key in ("curve", "easing", "bezier"):
        if params.get(curve_key) is not None:
            raise refuse_speed_curve(raw_type, pos)
    segments = params.get("segments")
    if not isinstance(segments, list) or not segments:
        _drop(
            pos, raw_type, "not_a_speed_step",
            f"Dropped VFX {raw_type!r} on block {pos!r}: `speed_ramp` "
            f"needs `params.segments`, a non-empty list of percents "
            f"above 0 - Resolve 21.1 draws stepped constant segments, "
            f"never a curve.",
        )
        return None
    percents = []
    for seg in segments:
        pct = seg.get("percent") if isinstance(seg, dict) else seg
        if (isinstance(pct, bool) or not isinstance(pct, (int, float))
                or pct <= 0):
            _drop(
                pos, raw_type, "not_a_speed_step",
                f"Dropped VFX {raw_type!r} on block {pos!r}: segment "
                f"{seg!r} is not a percent above 0 - a freeze is "
                f"`freeze_frame`, never a 0% step.",
            )
            return None
        percents.append(float(pct))
    return percents


# The entry keys this step reads. Anything else on an entry is
# REFUSED by `refuse_unknown_keys` below, never dropped: an unread key
# is how a probe's SFX `at_word` landed 3.06 s early on the block
# start, and a dropped VFX entry is recorded in `planning_basis`
# rather than re-planned - which is right for a bad VALUE (a
# withdrawn alias, an effect the toolkit has not got) and wrong for a
# key nothing reads. `segment_id` is the legacy spelling of
# `target_block_position`; `composite_mode` is read on the generator
# overlay path only. `anchor` / `anchor_end` are the sub-block address
# (library/tools/sub_block_anchor.py): `anchor` moves the effect's
# start onto a word, beat or frame inside the block, `anchor_end` its
# end - a punch that spans one word carries both. Either alone leaves
# the other end on the block boundary.
VFX_ENTRY_KEYS = frozenset({
    "target_block_position",
    "segment_id",
    "effect_type",
    "params",
    "rationale",
    "composite_mode",
} | ANCHOR_ENTRY_KEYS)


def _builtin_effect_names() -> set:
    """The built-in Fusion clip effects the handoff offers, by name.

    Only returns presets that declare an image input (clip effects).
    Generator presets (particles, backgrounds, standalone lens flares,
    etc.) are excluded - they produce content from nothing and belong
    on an overlay track, not as a clip effect.

    Empty when the preset index is unreadable, which makes an unknown
    effect_type a drop rather than an import that would fail later.
    """
    try:
        from library.tools.builtin_effect_loader import list_clip_effects
        return set(list_clip_effects() or {})
    except Exception as exc:  # pragma: no cover - index missing
        print(f"  Built-in effect index unavailable: {exc}", file=sys.stderr)
        return set()


def _generator_effect_names() -> set:
    """Generator presets that CANNOT be used as clip effects.

    These have no image input and would cover the picture rather than
    modify it. They are routed to an overlay track instead.
    """
    try:
        from library.tools.builtin_effect_loader import list_generator_effects
        return set(list_generator_effects() or {})
    except Exception:  # pragma: no cover
        return set()


def _stated_reason(vfx: dict) -> bool:
    """Whether the entry states a per-shot reason for the motion.

    A missing key, a non-string, and a blank string are all no reason:
    a rationale of whitespace states nothing about the shot.
    """
    rationale = vfx.get("rationale")
    return isinstance(rationale, str) and bool(rationale.strip())


def _derive_ken_burns_direction(params: dict):
    """The drift direction a `ken_burns` entry's own params describe.

    `zoom_end` above `zoom_start` is a push in, below is a pull out;
    equal, non-numeric or missing names neither and answers None.  The
    direction is READ, never defaulted: a Ken Burns move without one is
    dropped as `ken_burns_without_direction`.
    """
    if not isinstance(params, dict):
        return None
    start = params.get("zoom_start")
    end = params.get("zoom_end")
    if (isinstance(start, bool) or isinstance(end, bool)
            or not isinstance(start, (int, float))
            or not isinstance(end, (int, float))):
        return None
    if end > start:
        return "slow_zoom_in"
    if end < start:
        return "slow_zoom_out"
    return None


def resolve_vfx(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
    dropped: list = None,
    music_analysis: dict | None = None,
    music_selection: dict | None = None,
) -> list:
    """Resolve creative VFX plan to execution specs.

    `dropped` is an optional out-parameter: pass a list and every entry
    this function discards is appended to it as a
    `vfx_plan_basis.DroppedEntry`.  Without it the drops go only to
    stderr, which is where they used to go exclusively - and a plan whose
    every entry was dropped came out byte-identical to a plan the model
    deliberately left empty.  See `library/tools/vfx_plan_basis.py`.

    `music_analysis` / `music_selection` route the beat grid beat
    anchors resolve against; word and frame anchors need only the
    spine. Absent, a beat anchor refuses naming the missing grid.
    """
    # An entry key nothing here reads is refused before anything
    # resolves - the refusal travels the post-bridge retry path so the
    # model re-plans, which a recorded drop cannot do.
    refuse_unknown_keys(creative_plan, VFX_ENTRY_KEYS,
                         step="plan_vfx", plan="vfx_creative")
    def _drop(pos, effect_type, reason, detail):
        print(f"  {detail}", file=sys.stderr)
        if dropped is not None:
            dropped.append(DroppedEntry(
                target_block_position=pos,
                effect_type=effect_type or "",
                reason=reason,
                detail=detail,
            ))

    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {str(b["position"]): b for b in spine_blocks if "position" in b}

    resolved = []
    covered_positions = set()
    for vfx in creative_plan:
        pos = vfx.get("target_block_position", vfx.get("segment_id"))
        block = block_lookup.get(str(pos))
        if block is None:
            # An entry that names no real spine block used to resolve to
            # {} and land at 0.0-5.0, so several of them stacked into one
            # identical effect. Drop it loudly instead.
            _drop(
                pos, vfx.get("effect_type", ""), "not_a_spine_block",
                f"Dropped VFX {vfx.get('effect_type', '?')}: "
                f"target_block_position {pos!r} is not a spine block",
            )
            continue

        # One effect per timeline span. Two entries resolving onto the
        # identical span are duplicates, not stacked effects - but two
        # entries on the same block with different sub-block anchors
        # are two different moments (a punch on each of two words),
        # so the cover is keyed by span, not by block.
        span_key = None
        if vfx.get("anchor") is None and vfx.get("anchor_end") is None:
            span_key = str(pos)
        if span_key is not None and span_key in covered_positions:
            _drop(
                pos, vfx.get("effect_type", ""), "duplicate_block",
                f"Dropped duplicate VFX on block {pos!r} "
                f"({vfx.get('effect_type', '?')})",
            )
            continue
        if span_key is not None:
            covered_positions.add(span_key)

        tl_start = block["timeline_start"]
        tl_end = block["timeline_end"]

        # An entry that names no effect used to become a `slow_zoom_in`,
        # so a malformed plan entry put a zoom on the picture that no
        # editor asked for. Which effect a block gets is the decision the
        # step exists to make; there is nothing to fall back to.
        raw_type = vfx.get("effect_type")
        if not raw_type:
            _drop(
                pos, raw_type, "no_effect_type",
                f"Dropped VFX on block {pos!r}: it names no effect_type. "
                f"No effect is substituted - choose one of "
                f"{', '.join(sorted(TOOLKIT_PARAMETERS))}.",
            )
            covered_positions.discard(str(pos))
            continue
        effect_type = EFFECT_ALIASES.get(raw_type, raw_type)
        params = vfx.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # Ken Burns is the captain's name for the drift move, not a
        # second motion system: the entry resolves to the
        # `slow_zoom_in` / `slow_zoom_out` its own params describe and
        # travels the rest of this path - rationale refusal included -
        # as that effect, so the renderer reads it unchanged.
        if raw_type == KEN_BURNS:
            derived = _derive_ken_burns_direction(params)
            if derived is None:
                _drop(
                    pos, raw_type, "ken_burns_without_direction",
                    f"Dropped VFX {raw_type!r} on block {pos!r}: its params "
                    f"state no direction (`zoom_end` above `zoom_start` is "
                    f"a push in, below is a pull out). The direction is "
                    f"read off the values, never defaulted.",
                )
                covered_positions.discard(str(pos))
                continue
            effect_type = derived

        # Native speed effects travel the anchor path below (a ramp or a
        # freeze may span a word, not the block) but NOT the Fusion param
        # path: there is no comp to check names against. Validated here;
        # the resolved entry is built after the span is known.
        native_step_percents = None
        is_native_speed = effect_type in NATIVE_SPEED_EFFECTS
        if is_native_speed:
            native_step_percents = _validate_native_speed(
                raw_type, effect_type, params, pos, _drop)
            if native_step_percents is None:
                covered_positions.discard(str(pos))
                continue
        elif effect_type in _builtin_effect_names():
            # A built-in Fusion clip effect, imported whole by the renderer.
            # It is applied whole and takes no parameters.
            params = {}
        elif effect_type in _generator_effect_names():
            # Generator preset: produces pixels from nothing, has no image
            # input. Cannot be used as a clip effect - it would cover the
            # shot rather than modify it. Route to the overlay track via
            # resolve_generator_overlays instead.
            _drop(
                pos, raw_type, "generator_not_a_clip_effect",
                f"Rejected generator preset {raw_type!r} on block {pos!r}: "
                f"this preset has no image input and cannot modify the "
                f"picture. Generator presets are routed to the overlay "
                f"track (V5) via resolve_generator_overlays.",
            )
            covered_positions.discard(str(pos))
            continue
        elif effect_type in TOOLKIT_PARAMETERS:
            # Kinetic motion is allowed only where the plan states, per
            # shot, why that shot wants it (captain's ruling 2026-09-08).
            # A drift entry with no stated reason gets no motion - it is
            # dropped, never given a default move. This is checked before
            # the parameter names because whether motion applies at all
            # comes before how it is drawn.
            if effect_type in DRIFT_EFFECTS and not _stated_reason(vfx):
                _drop(
                    pos, raw_type, "no_stated_reason",
                    f"Dropped VFX {raw_type!r} on block {pos!r}: it states "
                    f"no reason. Motion on a static shot needs a per-shot "
                    f"rationale saying why that shot wants it; a shot with "
                    f"no reason gets no motion.",
                )
                covered_positions.discard(str(pos))
                continue
            # The plan supplies the values; this checks only that the
            # NAMES reach a reader. A name the renderer does not dispatch
            # on draws nothing and says nothing (AGENTS.md §10.2), so an
            # entry carrying none of its effect's names is dropped with
            # the reason rather than recorded as a planned effect the
            # viewer never sees. Nothing is substituted and no value is
            # bounded - what a name is set TO is the plan's decision.
            readable = TOOLKIT_PARAMETERS[effect_type]
            params = {k: v for k, v in params.items() if k in readable}
            if not params:
                _drop(
                    pos, raw_type, "no_readable_parameters",
                    f"Dropped VFX {raw_type!r} on block {pos!r}: its "
                    f"params name nothing the renderer reads. "
                    f"{raw_type!r} is drawn from "
                    f"{', '.join(readable)}; nothing is substituted.",
                )
                covered_positions.discard(str(pos))
                continue
        else:
            withdrawn = WITHDRAWN_ALIASES.get(raw_type)
            _drop(
                pos, raw_type,
                "withdrawn_alias" if withdrawn else "unknown_effect_type",
                f"Dropped VFX {raw_type!r} on block {pos!r}: "
                + (f"{withdrawn}. " if withdrawn else "")
                + f"not in the effect toolkit "
                f"({', '.join(sorted(TOOLKIT_PARAMETERS))}), not a "
                f"native speed effect ({', '.join(NATIVE_SPEED_EFFECTS)}) "
                f"and not a built-in Fusion clip effect",
            )
            covered_positions.discard(str(pos))
            continue

        # Sub-block span: the anchor moves the effect's start onto a
        # word, beat or frame inside the block, the end anchor its end.
        # Either alone leaves the other end on the block boundary - so a
        # punch that spans one word carries both, and an effect with
        # neither spans the block exactly as before. An end at or before
        # the start refuses: a span is not a point.
        span_start, span_end = tl_start, tl_end
        anchor_method = None
        if vfx.get("anchor") is not None:
            hit = resolve_anchor(
                vfx["anchor"], block=block,
                music_analysis=music_analysis,
                music_selection=music_selection,
                frame_rate=frame_rate, step="plan_vfx",
                plan="vfx_creative", index=len(resolved))
            span_start = hit["timeline_seconds"]
            anchor_method = hit["method"]
        if vfx.get("anchor_end") is not None:
            hit = resolve_anchor(
                vfx["anchor_end"], block=block,
                music_analysis=music_analysis,
                music_selection=music_selection,
                frame_rate=frame_rate, step="plan_vfx",
                plan="vfx_creative", index=len(resolved),
                end="anchor_end")
            span_end = hit["timeline_seconds"]
            anchor_method = (f"{anchor_method} to {hit['method']}"
                             if anchor_method is not None
                             else f"block start to {hit['method']}")
        if span_end <= span_start:
            raise AnchorRefused(
                what=(f"step plan_vfx plan entry {len(resolved)} anchor "
                      f"span ends at {span_end:.3f}s, at or before its "
                      f"start {span_start:.3f}s"),
                why=(f"an effect's extent is a span: entry "
                     f"{len(resolved)} of `vfx_creative` anchors a span "
                     f"that is not a span, and drawing it would put an "
                     f"effect on the picture nobody placed."),
                fix=(f"re-plan entry {len(resolved)} of `vfx_creative` "
                     f"with `anchor_end` after `anchor` (a punch that "
                     f"spans one word anchors the word's start and its "
                     f"end), or drop one of the two anchors."),
            )
        if anchor_method is not None:
            span_key = (str(pos), round(span_start, 3),
                        round(span_end, 3))
            if span_key in covered_positions:
                _drop(
                    pos, vfx.get("effect_type", ""), "duplicate_block",
                    f"Dropped duplicate VFX on block {pos!r} "
                    f"({vfx.get('effect_type', '?')}) spanning the same "
                    f"anchored range",
                )
                continue
            covered_positions.add(span_key)

        if is_native_speed:
            # The steps divide the resolved span equally: a ramp is
            # stepped segments, and the plan states the percents, not
            # the split. A freeze carries no steps - the whole span
            # freezes as one op.
            steps = []
            count = len(native_step_percents)
            for i, pct in enumerate(native_step_percents):
                step_start = span_start + (span_end - span_start) * i / count
                step_end = (span_start + (span_end - span_start)
                            * (i + 1) / count)
                steps.append({
                    "percent": pct,
                    "timeline_start": round(step_start, 3),
                    "timeline_end": round(step_end, 3),
                })
            resolved.append({
                "vfx_id": f"vfx_{len(resolved)+1:03d}",
                "target_block_position": block["position"],
                "timeline_start": round(span_start, 3),
                "timeline_end": round(span_end, 3),
                "effect_type": effect_type,
                "params": ({"segments": steps} if steps else {}),
                "rationale": vfx.get("rationale", ""),
                "route": "native_resolve",
            })
            if anchor_method is not None:
                resolved[-1]["anchor_method"] = anchor_method
            continue

        resolved.append({
            "vfx_id": f"vfx_{len(resolved)+1:03d}",
            "target_block_position": block["position"],
            "timeline_start": round(span_start, 3),
            "timeline_end": round(span_end, 3),
            "effect_type": effect_type,
            "params": params,
            "rationale": vfx.get("rationale", ""),
        })
        if anchor_method is not None:
            resolved[-1]["anchor_method"] = anchor_method

    resolved.sort(key=lambda v: v["timeline_start"])
    for i, v in enumerate(resolved, start=1):
        v["vfx_id"] = f"vfx_{i:03d}"

    _assert_vfx_distinct(resolved)
    return resolved


def _assert_vfx_distinct(resolved: list) -> None:
    """Fail when several VFX cover the identical timeline range.

    Five `slow_zoom_in` entries all spanning 2.682-4.067s is a collapse:
    only one is visible and the other four are dead weight in the manifest.
    """
    if len(resolved) < 2:
        return
    ranges = {(v["timeline_start"], v["timeline_end"]) for v in resolved}
    if len(ranges) < len(resolved):
        raise ValueError(
            f"{len(resolved)} VFX resolved to only {len(ranges)} distinct "
            f"timeline range(s): {sorted(ranges)}. Each VFX must span its "
            f"own timeline range - two entries on one block need "
            f"different sub-block anchors."
        )


def resolve_generator_overlays(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
) -> list:
    """Extract generator presets from the creative plan and resolve them
    to overlay track entries.

    Generator presets produce content from nothing (no image input) and
    belong on the overlay track, composited over the picture. This
    function is the planning-side pair of the clip-effect rejection in
    resolve_vfx: that function rejects generators from V1, this one
    routes them to the overlay track.

    Returns a list of overlay entries, each with:
      - overlay_id: unique identifier
      - effect_name: the generator preset's snake_case name
      - timeline_start / timeline_end: seconds
      - target_block_position: spine block position
      - composite_mode: how to composite (default: "screen")
      - rationale: from the creative plan
    """
    spine_blocks = timed_spine.get(
        "structure",
        timed_spine.get("audio_spine", {}).get("structure", []),
    )
    block_lookup = {
        str(b["position"]): b for b in spine_blocks if "position" in b
    }

    generator_names = _generator_effect_names()
    if not generator_names:
        return []

    overlays = []
    covered_positions = set()
    for entry in creative_plan:
        pos = entry.get("target_block_position", entry.get("segment_id"))
        raw_type = entry.get("effect_type", "")
        effect_type = EFFECT_ALIASES.get(raw_type, raw_type)

        if effect_type not in generator_names:
            continue

        block = block_lookup.get(str(pos))
        if block is None:
            print(
                f"  Dropped generator overlay {raw_type!r}: "
                f"target_block_position {pos!r} is not a spine block",
                file=sys.stderr,
            )
            continue

        if str(pos) in covered_positions:
            print(
                f"  Dropped duplicate generator overlay on block {pos!r} "
                f"({raw_type!r})",
                file=sys.stderr,
            )
            continue
        covered_positions.add(str(pos))

        overlays.append({
            "overlay_id": f"gen_{len(overlays)+1:03d}",
            "effect_name": effect_type,
            "target_block_position": block["position"],
            "timeline_start": round(block["timeline_start"], 3),
            "timeline_end": round(block["timeline_end"], 3),
            "composite_mode": entry.get("composite_mode", "screen"),
            "rationale": entry.get("rationale", ""),
        })

    overlays.sort(key=lambda o: o["timeline_start"])
    for i, o in enumerate(overlays, start=1):
        o["overlay_id"] = f"gen_{i:03d}"

    if overlays:
        print(
            f"  Generator overlays: {len(overlays)} presets routed to "
            f"overlay track",
            file=sys.stderr,
        )

    return overlays


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")
    require_keys(data, ["a_roll_assignments"], "step_4_03_plan_vfx/post_bridge.py")
    if "timed_spine" in data and not isinstance(data["timed_spine"], dict):
        raise ValueError("timed_spine must be a dictionary")

    creative = data.get("vfx_creative")
    if not creative and "llm_raw_response" in data:
        try:
            parsed = json.loads(data["llm_raw_response"])
            creative = parsed if isinstance(parsed, list) else parsed.get("vfx_creative", [])
        except Exception:
            creative = data["llm_raw_response"]

    if not isinstance(creative, list):
        print(f"  Warning: LLM returned invalid response for plan_vfx. Defaulting to empty list. Response was: {str(creative)[:100]}", file=sys.stderr)
        creative = []

    creative = [v for v in creative if isinstance(v, dict)]

    spine = data.get("timed_spine", {})
    fps = data.get("frame_rate", 30.0)
    music_analysis = data.get("music_analysis", {})
    music_selection = data.get("music_selection", {})

    # An empty plan is a legitimate answer - the handoff says so in as many
    # words ("an empty list is a legitimate answer for a piece that wants
    # stillness"), and this is where the code used to disagree with it: it
    # padded the plan up to every eligible block and then failed the step
    # outright if the padding left it empty.

    dropped = []
    result = resolve_vfx(creative, spine, fps, dropped=dropped,
                         music_analysis=music_analysis,
                         music_selection=music_selection)

    # Extract generator presets for the overlay track.
    # resolve_vfx rejects these from the clip-effect path; this routes
    # them to the overlay track instead of discarding them.
    gen_overlays = resolve_generator_overlays(creative, spine, fps)

    # A generator that REACHED the overlay track was routed, not dropped:
    # its pixels are on V5.  Only one the overlay path could not place is
    # a casualty, so the clip-effect rejection is withdrawn from the drop
    # list for every position the overlays cover.  Recording a routed
    # entry as dropped would make `every_entry_dropped` fire on a plan
    # that is fully delivered.
    routed = {str(o["target_block_position"]) for o in gen_overlays}
    dropped = [
        d for d in dropped
        if not (d.reason == "generator_not_a_clip_effect"
                and str(d.target_block_position) in routed)
    ]

    # Why this plan is the length it is.  `{"visual_effects": []}` alone
    # says the same thing whether the planner deliberately chose
    # stillness or named four effects that were all discarded, and only
    # the first of those is a decision.  See
    # `library/tools/vfx_plan_basis.py`.
    basis = PlanBasis(
        proposed=len(creative),
        resolved=len(result) + len(gen_overlays),
        dropped=dropped,
    ).as_dict()
    print(f"  {basis_summary(basis)}", file=sys.stderr)

    # C5 fix: Output key must be enhancement_spec to match manifest contract.
    # The DAG edge plan_vfx -> compile_manifest maps enhancement_spec.
    output = {"enhancement_spec": {
        "visual_effects": result,
        "planning_basis": basis,
    }}
    if gen_overlays:
        output["enhancement_spec"]["generator_overlays"] = gen_overlays
    json.dump(output, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
