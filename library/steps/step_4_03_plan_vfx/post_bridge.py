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
from library.tools.vfx_plan_basis import (
    DroppedEntry,
    PlanBasis,
    basis_summary,
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
WITHDRAWN_ALIASES = {
    "slow_zoom": "names no direction; `slow_zoom_in` and `slow_zoom_out` "
                 "are different effects and the pipeline may not pick",
    "ken_burns": "same: a Ken Burns move has a direction and this alias "
                 "did not carry it",
}

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


def resolve_vfx(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
    dropped: list = None,
) -> list:
    """Resolve creative VFX plan to execution specs.

    `dropped` is an optional out-parameter: pass a list and every entry
    this function discards is appended to it as a
    `vfx_plan_basis.DroppedEntry`.  Without it the drops go only to
    stderr, which is where they used to go exclusively - and a plan whose
    every entry was dropped came out byte-identical to a plan the model
    deliberately left empty.  See `library/tools/vfx_plan_basis.py`.
    """
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

        # One effect per block. A second entry on the same block is a
        # duplicate, not a stacked effect.
        if str(pos) in covered_positions:
            _drop(
                pos, vfx.get("effect_type", ""), "duplicate_block",
                f"Dropped duplicate VFX on block {pos!r} "
                f"({vfx.get('effect_type', '?')})",
            )
            continue
        covered_positions.add(str(pos))

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

        if effect_type in _builtin_effect_names():
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
                f"({', '.join(sorted(TOOLKIT_PARAMETERS))}) and not a "
                f"built-in Fusion clip effect",
            )
            covered_positions.discard(str(pos))
            continue

        resolved.append({
            "vfx_id": f"vfx_{len(resolved)+1:03d}",
            "target_block_position": block["position"],
            "timeline_start": round(tl_start, 3),
            "timeline_end": round(tl_end, 3),
            "effect_type": effect_type,
            "params": params,
            "rationale": vfx.get("rationale", ""),
        })

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
            f"timeline range(s): {sorted(ranges)}. Each VFX must target "
            f"its own spine block."
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

    # An empty plan is a legitimate answer - the handoff says so in as many
    # words ("an empty list is a legitimate answer for a piece that wants
    # stillness"), and this is where the code used to disagree with it: it
    # padded the plan up to every eligible block and then failed the step
    # outright if the padding left it empty.

    dropped = []
    result = resolve_vfx(creative, spine, fps, dropped=dropped)

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
