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


# Style spec ranges for VFX parameters
INTENSITY_MAP = {
    "slow_zoom_in": {
        "subtle": {"zoom_start": 1.0, "zoom_end": 1.03},
        "moderate": {"zoom_start": 1.0, "zoom_end": 1.05},
        "strong": {"zoom_start": 1.0, "zoom_end": 1.08},
    },
    "slow_zoom_out": {
        "subtle": {"zoom_start": 1.03, "zoom_end": 1.0},
        "moderate": {"zoom_start": 1.05, "zoom_end": 1.0},
        "strong": {"zoom_start": 1.08, "zoom_end": 1.0},
    },
    "zoom_emphasis": {
        "subtle": {"zoom_percent": 3.0},
        "moderate": {"zoom_percent": 5.0},
        "strong": {"zoom_percent": 8.0},
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


def resolve_vfx(
    creative_plan: list,
    timed_spine: dict,
    frame_rate: float = 30.0,
) -> list:
    """Resolve creative VFX plan to execution specs."""
    spine_blocks = timed_spine.get("audio_spine", {}).get("structure", [])
    block_lookup = {b["position"]: b for b in spine_blocks}

    resolved = []
    for vfx in creative_plan:
        pos = vfx.get("target_block_position")
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


def main():
    data = json.loads(sys.stdin.read())
    creative = data.get("vfx_creative", [])
    spine = data.get("timed_spine", {})
    fps = data.get("frame_rate", 30.0)

    result = resolve_vfx(creative, spine, fps)
    json.dump({"vfx_spec": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
