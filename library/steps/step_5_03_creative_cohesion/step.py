#!/usr/bin/env python3
"""
Step 5.03: Creative Cohesion Review
Reviews creative decisions across plans for cohesion and consistency.
"""
import json
import sys

def map_energy(energy_str):
    energy_str = str(energy_str).lower()
    if any(w in energy_str for w in ["high", "building", "dynamic", "fast", "intense"]):
        return "high"
    elif any(w in energy_str for w in ["low", "calm", "slow", "reflective"]):
        return "calm"
    return "moderate"

def extract_cuts_per_minute(creative_direction):
    pacing = creative_direction.get("pacing", {})
    if isinstance(pacing, dict) and "cuts_per_minute" in pacing:
        return pacing["cuts_per_minute"]
    return None

def review_creative_cohesion(inputs: dict) -> dict:
    creative_direction = inputs.get("creative_direction", {})
    transition_spec_raw = inputs.get("transition_spec", [])
    if isinstance(transition_spec_raw, dict):
        transitions = transition_spec_raw.get("transitions", transition_spec_raw.get("transition_spec", []))
    else:
        transitions = transition_spec_raw if isinstance(transition_spec_raw, list) else []

    sfx_spec_raw = inputs.get("sfx_spec", [])
    if isinstance(sfx_spec_raw, dict):
        sfx = sfx_spec_raw.get("sfx_plan", sfx_spec_raw.get("sfx_events", sfx_spec_raw.get("sfx_spec", [])))
    else:
        sfx = sfx_spec_raw if isinstance(sfx_spec_raw, list) else []

    color_grade_spec = inputs.get("color_grade_spec", {})
    speech_sequence = inputs.get("speech_sequence", {})
    project_config = inputs.get("project_config", {})

    warnings = []
    adjustments = []
    score = 100

    # 1. Energy Alignment Check
    raw_energy = creative_direction.get("target_energy", "moderate")
    energy = map_energy(raw_energy)

    # Transition Check
    for t in transitions:
        dur_frames = t.get("duration_frames", 15)
        # assuming 30fps -> ms = (frames / 30) * 1000
        dur_ms = (dur_frames / 30.0) * 1000
        ttype = t.get("transition_type", t.get("type", ""))
        
        if energy == "high":
            if dur_ms >= 500 and ttype not in ["cut", "hard_cut", ""]:
                warnings.append(f"High energy but found slow transition ({dur_ms}ms)")
                score -= 5
                adjustments.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 10, # ~333ms
                    "reason": "High energy requires faster transitions (<500ms)",
                    "target_index": transitions.index(t)
                })
        elif energy == "calm":
            if dur_ms <= 1000 and ttype in ["cross_dissolve", "fade_in", "fade_out", "dip_to_black"]:
                warnings.append(f"Calm energy but found fast dissolve ({dur_ms}ms)")
                score -= 5
                adjustments.append({
                    "target_step": "transition_spec",
                    "field": "duration_frames",
                    "current_value": dur_frames,
                    "suggested_value": 30, # 1000ms
                    "reason": "Calm energy should have slower dissolves (>=1000ms)",
                    "target_index": transitions.index(t)
                })

    # SFX Check (density estimation)
    # Get total duration if possible
    total_duration = 60.0 # fallback
    if isinstance(speech_sequence, dict) and "body_sequence" in speech_sequence:
        body = speech_sequence["body_sequence"]
        if body and "end_time" in body[-1] and body[-1]["end_time"] is not None:
            total_duration = body[-1]["end_time"]

    sfx_density_per_min = (len(sfx) / total_duration) * 60 if total_duration > 0 else 0
    if energy == "high" and sfx_density_per_min < 10:
        warnings.append(f"High energy but sparse SFX ({sfx_density_per_min:.1f} per min)")
        score -= 5
        adjustments.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "sparse",
            "suggested_value": "dense",
            "reason": "High energy requires dense SFX"
        })
    elif energy == "calm" and sfx_density_per_min > 15:
        warnings.append(f"Calm energy but dense SFX ({sfx_density_per_min:.1f} per min)")
        score -= 5
        adjustments.append({
            "target_step": "sfx_spec",
            "field": "density",
            "current_value": "dense",
            "suggested_value": "sparse",
            "reason": "Calm energy requires sparse SFX"
        })

    # Color Grade Check
    color_mood = str(color_grade_spec.get("mood", color_grade_spec.get("grade_name", ""))).lower()
    if energy == "high" and any(w in color_mood for w in ["soft", "muted", "faded", "calm"]):
        warnings.append("High energy but color grade is soft/muted")
        score -= 5
    elif energy == "calm" and any(w in color_mood for w in ["high contrast", "vibrant", "punchy", "saturated"]):
        warnings.append("Calm energy but color grade is highly saturated/contrasty")
        score -= 5

    # 2. Pacing Consistency Check
    cuts_per_min_target = extract_cuts_per_minute(creative_direction)
    if cuts_per_min_target is not None:
        actual_cuts_per_min = (len(transitions) / total_duration) * 60 if total_duration > 0 else 0
        diff = abs(actual_cuts_per_min - cuts_per_min_target)
        if diff > 5:
            warnings.append(f"Pacing mismatch: target {cuts_per_min_target} cuts/min, actual {actual_cuts_per_min:.1f}")
            score -= 5
            
    # Engagement score check
    if isinstance(speech_sequence, dict):
        hook = speech_sequence.get("hook_segment") or {}
        body = speech_sequence.get("body_sequence", [])
        
        hook_eng = hook.get("engagement", 0)
        max_body_eng = max([b.get("engagement", 0) for b in body]) if body else 0
        
        if max_body_eng > hook_eng + 10:
            warnings.append(f"Hook engagement ({hook_eng}) is lower than peak body engagement ({max_body_eng})")
            score -= 10
            adjustments.append({
                "target_step": "speech_sequence",
                "field": "segment_order",
                "current_value": "current",
                "suggested_value": "front_loaded",
                "reason": "Highest engagement segments should be front-loaded for hooks"
            })

    # 3. Duration Warning
    # Check actual duration against project_config.target_duration_seconds
    if isinstance(project_config, dict):
        target_dur = project_config.get("target_duration_seconds")
        if target_dur is not None:
            target_dur = float(target_dur)
            # Warn if more than 50% over or under target
            if total_duration > target_dur * 1.5:
                warnings.append(
                    f"Duration warning: actual duration ({total_duration:.1f}s) "
                    f"exceeds target ({target_dur:.0f}s) by more than 50%"
                )
                score -= 3
            elif total_duration < target_dur * 0.5:
                warnings.append(
                    f"Duration warning: actual duration ({total_duration:.1f}s) "
                    f"is less than half the target ({target_dur:.0f}s)"
                )
                score -= 3

    # 4. Output Adjustments
    score = max(0, score)

    # 5. Apply Adjustments (optional)
    applied_adjustments = []
    if score < 70:
        for adj in adjustments:
            if adj["target_step"] == "transition_spec" and "target_index" in adj:
                idx = adj["target_index"]
                transitions[idx][adj["field"]] = adj["suggested_value"]
                applied_adjustments.append(f"Adjusted transition {idx} {adj['field']} to {adj['suggested_value']}")
            elif adj["target_step"] == "sfx_spec" and adj["field"] == "density":
                # We can't really generate new SFX here, but we can set a flag
                applied_adjustments.append("Flagged SFX density adjustment needed")
            elif adj["target_step"] == "speech_sequence" and adj["field"] == "segment_order":
                applied_adjustments.append("Flagged speech sequence for reordering (too destructive to auto-apply)")

    # Construct review output
    cohesion_review = {
        "cohesion_score": score,
        "warnings": warnings,
        "adjustments": adjustments,
        "applied_adjustments": applied_adjustments
    }

    # If we modified transition_spec in-place, we should output the modified version so 
    # the orchestrator or compile_manifest can use it. But our output is just cohesion_review.
    # compile_manifest will read cohesion_review and apply it.

    return cohesion_review

def main():
    data = json.loads(sys.stdin.read())
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    review = review_creative_cohesion(data)

    json.dump({
        "step": "5.03_creative_cohesion",
        "cohesion_review": review
    }, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
