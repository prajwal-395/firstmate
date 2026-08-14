#!/usr/bin/env python3
"""
Step 4.3 Bridge: Resolve VFX Creative Plan to Execution Data

Takes the LLM's creative VFX selections (effect_type, intensity,
target_block_position) and resolves:
- target_block_position → timeline_start/end from timed spine
- intensity → concrete zoom parameters from style spec ranges
- Optionally: OpenCV motion detection to skip already-dynamic clips

Classification: Deterministic / Data Transformation
Idempotent: Yes
"""
import json
import sys
from library.tools.pipeline_validation import require_keys


# Style spec ranges for VFX parameters.
#
# EVERY parameter name here must be one the renderer reads. The renderer
# (library/tools/execution/apply_fusion_comps.py) dispatches on parameter
# NAMES, so `zoom_percent`, `intensity_px` and `scale_factor` - the names
# this map used to emit for zoom_emphasis, screen_shake and cut_in - had
# no reader at all: three of the five advertised effects rendered nothing
# while the manifest recorded them as planned.
#
# AGENTS.md: "NEVER set transition zoom > 1.04". That bound is about
# transitions between shots; cut_in/cut_out are framing changes on one
# shot, which is the whole point of them, so they are not clamped to it.
INTENSITY_MAP = {
    # Ken Burns drift across the clip.
    "slow_zoom_in": {
        "subtle": {"zoom_start": 1.0, "zoom_end": 1.03},
        "moderate": {"zoom_start": 1.0, "zoom_end": 1.04},
        "strong": {"zoom_start": 1.0, "zoom_end": 1.04},
    },
    "slow_zoom_out": {
        "subtle": {"zoom_start": 1.03, "zoom_end": 1.0},
        "moderate": {"zoom_start": 1.04, "zoom_end": 1.0},
        "strong": {"zoom_start": 1.04, "zoom_end": 1.0},
    },
    # Punch in and settle back - fx.zoom's three-point spline, so the
    # emphasis reads as a push rather than a permanent reframe.
    "zoom_emphasis": {
        "subtle": {"zoom_start": 1.0, "zoom_mid": 1.03, "zoom_end": 1.0},
        "moderate": {"zoom_start": 1.0, "zoom_mid": 1.04, "zoom_end": 1.0},
        "strong": {"zoom_start": 1.0, "zoom_mid": 1.04, "zoom_end": 1.0},
    },
    # fx.shake offsets the frame centre as a FRACTION of frame width, so
    # the old pixel counts are converted here: 2/3/4 px of a 1080-wide
    # frame. shake_decay_frames makes it an impact that settles, which is
    # what the old (unread) duration_frames was asking for.
    "screen_shake": {
        "subtle": {"shake_x": 0.00185, "shake_y": 0.00185, "shake_decay_frames": 3},
        "moderate": {"shake_x": 0.00278, "shake_y": 0.00278, "shake_decay_frames": 4},
        "strong": {"shake_x": 0.0037, "shake_y": 0.0037, "shake_decay_frames": 5},
    },
    # Static reframes: a constant Size on the Transform, held for the clip.
    "cut_in": {
        "subtle": {"zoom_start": 1.15, "zoom_mid": 1.15, "zoom_end": 1.15},
        "moderate": {"zoom_start": 1.25, "zoom_mid": 1.25, "zoom_end": 1.25},
        "strong": {"zoom_start": 1.4, "zoom_mid": 1.4, "zoom_end": 1.4},
    },
    "cut_out": {
        "subtle": {"zoom_start": 0.95, "zoom_mid": 0.95, "zoom_end": 0.95},
        "moderate": {"zoom_start": 0.9, "zoom_mid": 0.9, "zoom_end": 0.9},
        "strong": {"zoom_start": 0.85, "zoom_mid": 0.85, "zoom_end": 0.85},
    },
}

# Spellings that mean an existing effect. `slow_zoom` was advertised in
# the handoff without a direction and fell through to the default, which
# is how six of eight effects came out as the same 3% zoom.
EFFECT_ALIASES = {
    "slow_zoom": "slow_zoom_in",
    "ken_burns": "slow_zoom_in",
    "push_in": "zoom_emphasis",
}

# Minimum A-roll clip duration (seconds) to receive default Ken Burns zoom.
# Clips shorter than this are too brief for a slow zoom to be perceptible.
KEN_BURNS_MIN_DURATION_S = 3.0


def _builtin_effect_names() -> set:
    """The built-in Fusion effects the handoff also offers, by name.

    Empty when the preset index is unreadable, which makes an unknown
    effect_type a drop rather than an import that would fail later.
    """
    try:
        from library.tools.builtin_effect_loader import list_builtin_effects
        return set(list_builtin_effects() or {})
    except Exception as exc:  # pragma: no cover - index missing
        print(f"  Built-in effect index unavailable: {exc}", file=sys.stderr)
        return set()


def resolve_vfx(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
) -> list:
    """Resolve creative VFX plan to execution specs."""
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
            print(
                f"  Dropped VFX {vfx.get('effect_type', '?')}: "
                f"target_block_position {pos!r} is not a spine block",
                file=sys.stderr,
            )
            continue

        # One effect per block. A second entry on the same block is a
        # duplicate, not a stacked effect.
        if str(pos) in covered_positions:
            print(
                f"  Dropped duplicate VFX on block {pos!r} "
                f"({vfx.get('effect_type', '?')})",
                file=sys.stderr,
            )
            continue
        covered_positions.add(str(pos))

        tl_start = block["timeline_start"]
        tl_end = block["timeline_end"]

        raw_type = vfx.get("effect_type", "slow_zoom_in")
        effect_type = EFFECT_ALIASES.get(raw_type, raw_type)
        intensity = vfx.get("intensity", "moderate")

        # Resolve intensity → concrete parameters
        type_map = INTENSITY_MAP.get(effect_type)
        if type_map:
            params = dict(type_map.get(intensity, type_map["moderate"]))
        elif effect_type in _builtin_effect_names():
            # A built-in Fusion effect, imported whole by the renderer.
            # It takes no intensity parameters.
            params = {}
        else:
            # No silent default. An unknown type used to become the
            # default 3% zoom while keeping its own name, so the manifest
            # claimed an effect the viewer never saw.
            print(
                f"  Dropped VFX {raw_type!r} on block {pos!r}: not in the "
                f"effect toolkit ({', '.join(sorted(INTENSITY_MAP))}) and "
                f"not a built-in Fusion effect",
                file=sys.stderr,
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


def inject_default_ken_burns(
    creative_plan: list,
    timed_spine: dict,
) -> list:
    """Add default subtle Ken Burns zoom to A-roll clips that have no VFX.

    The style spec mandates that nearly every A-roll talking head clip >3s
    should have subtle Ken Burns motion. This function provides a
    deterministic guarantee: if the LLM didn't assign VFX to a qualifying
    clip, we inject the default.

    Args:
        creative_plan: List of VFX entries from the LLM (may be empty).
        timed_spine: The timed audio spine with block positions.

    Returns:
        Updated creative_plan with defaults injected for uncovered blocks.
    """
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))

    # Find which spine positions already have VFX assigned
    covered_positions = set()
    for vfx in creative_plan:
        pos = vfx.get("target_block_position", vfx.get("segment_id"))
        if pos is not None:
            covered_positions.add(str(pos))

    # Inject defaults for uncovered A-roll blocks > KEN_BURNS_MIN_DURATION_S
    injected_count = 0
    for block in spine_blocks:
        if block.get("block_type") not in ("speech", "hook"):
            continue

        pos = block.get("position")
        if str(pos) in covered_positions:
            continue

        duration = block.get("timeline_end", 0) - block.get("timeline_start", 0)
        if duration < KEN_BURNS_MIN_DURATION_S:
            continue

        try:
            pos_int = int(pos)
        except (ValueError, TypeError):
            pos_int = hash(pos)

        # Alternate between zoom_in and zoom_out for visual variety
        effect = "slow_zoom_in" if (pos_int % 2 == 0) else "slow_zoom_out"

        creative_plan.append({
            "target_block_position": pos,
            "effect_type": effect,
            "intensity": "subtle",
            "rationale": "Default Ken Burns - style spec requires subtle"
                         " motion on all A-roll clips >3s.",
        })
        injected_count += 1

    if injected_count > 0:
        print(
            f"  Ken Burns: injected {injected_count} default zoom effects"
            f" on uncovered A-roll clips",
            file=sys.stderr,
        )

    return creative_plan


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

    # Inject default Ken Burns on uncovered A-roll clips before resolving
    creative = inject_default_ken_burns(creative, spine)

    if not creative and not data.get("vfx_plan"):
        print(json.dumps({"error": "No VFX planned. You MUST plan at least 3-7 VFX items.", "step": "4.03_bridge"}))
        sys.exit(1)

    result = resolve_vfx(creative, spine, fps)
    # C5 fix: Output key must be enhancement_spec to match manifest contract.
    # The DAG edge plan_vfx -> compile_manifest maps enhancement_spec.
    json.dump({"enhancement_spec": {"visual_effects": result}}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
