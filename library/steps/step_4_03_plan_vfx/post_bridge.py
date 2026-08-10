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
# AGENTS.md: "NEVER set transition zoom > 1.04" - all zoom values
# are clamped to this limit.
INTENSITY_MAP = {
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
    "zoom_emphasis": {
        "subtle": {"zoom_percent": 3.0},
        "moderate": {"zoom_percent": 4.0},
        "strong": {"zoom_percent": 4.0},
    },
    "screen_shake": {
        "subtle": {"intensity_px": 2, "duration_frames": 3},
        "moderate": {"intensity_px": 3, "duration_frames": 4},
        "strong": {"intensity_px": 4, "duration_frames": 5},
    },
    "cut_in": {
        "subtle": {"scale_factor": 1.15},
        "moderate": {"scale_factor": 1.25},
        "strong": {"scale_factor": 1.4},
    },
}

# Minimum A-roll clip duration (seconds) to receive default Ken Burns zoom.
# Clips shorter than this are too brief for a slow zoom to be perceptible.
KEN_BURNS_MIN_DURATION_S = 3.0


def resolve_vfx(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
) -> list:
    """Resolve creative VFX plan to execution specs."""
    spine_blocks = timed_spine.get("structure", timed_spine.get("audio_spine", {}).get("structure", []))
    block_lookup = {b["position"]: b for b in spine_blocks}

    resolved = []
    for vfx in creative_plan:
        pos = vfx.get("target_block_position", vfx.get("segment_id"))
        if pos is not None:
            try:
                pos = int(pos)
            except ValueError:
                pass
        block = block_lookup.get(pos, {})
        tl_start = block.get("timeline_start", 0.0)
        tl_end = block.get("timeline_end", tl_start + 5.0)

        effect_type = vfx.get("effect_type", "slow_zoom_in")
        intensity = vfx.get("intensity", "moderate")

        # Resolve intensity → concrete parameters
        params = {}
        type_map = INTENSITY_MAP.get(effect_type, {})
        if type_map:
            params = dict(type_map.get(intensity, type_map.get("moderate", {})))
        else:
            params = {"zoom_start": 1.0, "zoom_end": 1.03}

        resolved.append({
            "vfx_id": f"vfx_{len(resolved)+1:03d}",
            "timeline_start": round(tl_start, 3),
            "timeline_end": round(tl_end, 3),
            "effect_type": effect_type,
            "params": params,
            "rationale": vfx.get("rationale", ""),
        })

    return resolved


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
            try:
                pos = int(pos)
            except ValueError:
                pass
            covered_positions.add(pos)

    # Inject defaults for uncovered A-roll blocks > KEN_BURNS_MIN_DURATION_S
    injected_count = 0
    for block in spine_blocks:
        if block.get("block_type") not in ("speech", "hook"):
            continue

        pos = block.get("position")
        if pos in covered_positions:
            continue

        duration = block.get("timeline_end", 0) - block.get("timeline_start", 0)
        if duration < KEN_BURNS_MIN_DURATION_S:
            continue

        # Alternate between zoom_in and zoom_out for visual variety
        effect = "slow_zoom_in" if (pos % 2 == 0) else "slow_zoom_out"

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
