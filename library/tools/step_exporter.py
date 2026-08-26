"""
step_exporter.py - Exports step outputs as individual reviewable files.

After each step completes, this module:
1. Writes the raw output to pipeline_output/step_<id>.json
2. Generates a human-readable markdown summary for the dashboard

The summaries are step-type-aware: a creative_direction step gets a
formatted brief, a speech_sequence step gets a script layout, etc.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from library.tools.project_layout import Area, ProjectLayout


def export_step_output(
    project_dir: str,
    step_id: str,
    step_name: str,
    output: Dict[str, Any],
) -> Dict[str, str]:
    """Export a step's output as individual files.

    Returns dict with paths: {"json": "...", "summary_md": "..."}
    """
    layout = ProjectLayout(project_dir)

    # Write raw JSON output
    json_path = layout.step_output_json(step_id)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    # Generate human-readable summary
    summary_md = generate_summary(step_id, step_name, output)
    md_path = layout.step_output_summary(step_id)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(summary_md)

    return {"json": str(json_path), "summary_md": str(md_path)}


def load_step_output(project_dir: str, step_id: str) -> Optional[Dict[str, Any]]:
    """Load a previously exported step output."""
    json_path = ProjectLayout(project_dir).read_path(
        Area.OUTPUT_ROOT, f"{step_id}.json")
    if not json_path.exists():
        return None
    with open(json_path) as f:
        return json.load(f)


def load_step_summary(project_dir: str, step_id: str) -> str:
    """Load a previously generated step summary."""
    md_path = ProjectLayout(project_dir).read_path(
        Area.OUTPUT_ROOT, f"{step_id}.summary.md")
    if not md_path.exists():
        return ""
    with open(md_path) as f:
        return f.read()


def list_exported_steps(project_dir: str) -> list[str]:
    """List all step IDs that have exported outputs."""
    output_dir = ProjectLayout(project_dir).read_dir(Area.OUTPUT_ROOT)
    if not output_dir.exists():
        return []
    return sorted(
        p.stem
        for p in output_dir.glob("*.json")
        if not p.name.endswith(".summary.json")
        and p.stem not in ("gates",)
        and not p.name.startswith("gate_")
    )


# ── Summary Generators ─────────────────────────────────────────────

def generate_summary(step_id: str, step_name: str, output: Dict[str, Any]) -> str:
    """Generate a human-readable markdown summary for a step output.

    Dispatches to step-specific formatters where available, falls back
    to a generic JSON summary.
    """
    # Map step IDs to specialized formatters
    formatters = {
        "scan": _summary_scan,
        "catalog": _summary_catalog,
        "semantic_analysis": _summary_semantic,
        "temporal_index": _summary_temporal,
        "prosody_analysis": _summary_prosody,
        "creative_direction": _summary_creative_direction,
        "speech_sequence": _summary_speech_sequence,
        "music_selection": _summary_music_selection,
        "mesh_spine": _summary_mesh_spine,
        "assign_aroll": _summary_aroll,
        "select_broll": _summary_broll,
        "review_rough_cut": _summary_rough_cut,
        "plan_subtitles": _summary_plan_subtitles,
        "plan_transitions": _summary_plan_transitions,
        "plan_vfx": _summary_plan_vfx,
        "plan_sfx": _summary_plan_sfx,
        "color_grade": _summary_color_grade,
        "audio_mix": _summary_audio_mix,
        "creative_cohesion": _summary_creative_cohesion,
        "compile_manifest": _summary_compile_manifest,
        "render": _summary_render,
        "validate": _summary_validate,
        "render_subtitles": _summary_render_subtitles,
        "render_motion_graphics": _summary_render_motion_graphics,
        "object_segmentation": _summary_object_segmentation,
        "ocr_extraction": _summary_ocr_extraction,
    }

    formatter = formatters.get(step_id, _summary_generic)
    try:
        return formatter(step_name, output)
    except Exception as e:
        return f"# {step_name}\n\nError generating summary: {e}\n\n```json\n{json.dumps(output, indent=2)[:2000]}\n```\n"


def _summary_scan(name: str, out: dict) -> str:
    files = out.get("raw_footage_files", [])
    lines = [f"# {name}", "", f"Found **{len(files)} raw footage files**:", ""]
    for f in files[:20]:
        basename = os.path.basename(f) if isinstance(f, str) else str(f)
        lines.append(f"- `{basename}`")
    if len(files) > 20:
        lines.append(f"- ... and {len(files) - 20} more")
    return "\n".join(lines)


def _summary_catalog(name: str, out: dict) -> str:
    clips = out.get("clip_catalog", [])
    if isinstance(clips, dict):
        clips = list(clips.values())
    lines = [f"# {name}", "", f"Cataloged **{len(clips)} clips**:", ""]
    lines.append("| Clip ID | Duration | Resolution | FPS |")
    lines.append("|---------|----------|------------|-----|")
    for c in clips[:30]:
        cid = c.get("clip_id", c.get("filename", "?"))
        dur = c.get("duration_seconds", c.get("duration_s", "?"))
        w = c.get("width", "")
        h = c.get("height", "")
        res = f"{w}x{h}" if w and h else c.get("resolution", "?")
        fps = c.get("frame_rate", c.get("fps", "?"))
        lines.append(f"| {cid} | {dur}s | {res} | {fps} |")
    return "\n".join(lines)


def _summary_semantic(name: str, out: dict) -> str:
    docs = out.get("semantic_analysis_documents", [])
    if isinstance(docs, dict):
        docs = list(docs.values())
    lines = [f"# {name}", "", f"Analyzed **{len(docs)} clips** with vision model:", ""]
    for doc in docs[:15]:
        cid = doc.get("clip_id", "?")
        mood = doc.get("mood", doc.get("overall_mood", ""))
        energy = doc.get("energy", doc.get("overall_energy", ""))
        score = doc.get("interest_score", "")
        summary = doc.get("summary", doc.get("editorial_assessment", ""))
        if isinstance(summary, dict):
            summary = summary.get("summary", str(summary)[:100])
        lines.append(f"### {cid}")
        if mood:
            lines.append(f"- **Mood**: {mood}")
        if energy:
            lines.append(f"- **Energy**: {energy}")
        if score:
            lines.append(f"- **Interest Score**: {score}")
        if summary:
            lines.append(f"- {str(summary)[:200]}")
        lines.append("")
    return "\n".join(lines)


def _summary_temporal(name: str, out: dict) -> str:
    index = out.get("temporal_index", {})
    if isinstance(index, list):
        clips = index
    elif isinstance(index, dict):
        clips = list(index.values()) if index else []
    else:
        clips = []
    lines = [f"# {name}", "", f"Transcribed **{len(clips)} clips** with word-level alignment:", ""]
    for clip in clips[:10]:
        cid = clip.get("clip_id", "?")
        regions = clip.get("speech_regions", clip.get("segments", []))
        word_count = sum(len(r.get("words", [])) for r in regions) if regions else 0
        text = " ".join(
            r.get("text", "") for r in (regions[:3] if regions else [])
        )[:200]
        lines.append(f"### {cid}")
        lines.append(f"- **Regions**: {len(regions)}, **Words**: {word_count}")
        if text:
            lines.append(f"- *\"{text}...\"*")
        lines.append("")
    return "\n".join(lines)


def _summary_prosody(name: str, out: dict) -> str:
    analysis = out.get("prosody_analysis", {})
    if isinstance(analysis, list):
        clips = analysis
    elif isinstance(analysis, dict):
        clips = list(analysis.values()) if analysis else []
    else:
        clips = []
    lines = [f"# {name}", "", f"Analyzed prosody for **{len(clips)} clips**:", ""]
    for clip in clips[:10]:
        cid = clip.get("clip_id", "?")
        energy = clip.get("average_energy", clip.get("energy", ""))
        tempo = clip.get("speech_rate", clip.get("tempo", ""))
        lines.append(f"- **{cid}**: energy={energy}, rate={tempo}")
    return "\n".join(lines)


def _summary_creative_direction(name: str, out: dict) -> str:
    cd = out.get("creative_direction", out)
    lines = [f"# {name}", ""]
    lines.append(f"**Narrative Theme**: {cd.get('narrative_theme', 'N/A')}")
    lines.append("")
    lines.append(f"**Target Mood**: {cd.get('target_mood', 'N/A')}")
    lines.append("")
    lines.append(f"**Target Energy**: {cd.get('target_energy', 'N/A')}")
    lines.append("")
    lines.append(f"**Energy Arc**: {cd.get('energy_arc', 'N/A')}")
    lines.append("")
    lines.append(f"**Emotional Landscape**: {cd.get('emotional_landscape', 'N/A')}")
    lines.append("")
    lines.append(f"**Audience Emotion**: {cd.get('audience_emotion', 'N/A')}")
    lines.append("")
    moments = cd.get("key_moments", [])
    if moments:
        lines.append("**Key Moments**:")
        for m in moments:
            lines.append(f"- {m}")
        lines.append("")
    lines.append(f"**Rationale**: {cd.get('rationale', 'N/A')}")
    return "\n".join(lines)


def _summary_speech_sequence(name: str, out: dict) -> str:
    seq = out.get("speech_sequence", out)
    hook = seq.get("hook", {})
    body = seq.get("body", [])
    excluded = seq.get("excluded_passages", [])
    lines = [f"# {name}", ""]
    if hook:
        lines.append("## Hook")
        lines.append(f"**Clip**: {hook.get('clip_id', '?')} | "
                     f"**Time**: {hook.get('start', '?')}s - {hook.get('end', '?')}s")
        lines.append(f"> {hook.get('text', '')}")
        lines.append("")
    lines.append("## Body Sequence")
    lines.append("")
    for i, block in enumerate(body):
        role = block.get("role", "")
        cid = block.get("clip_id", "?")
        text = block.get("text", "")
        start = block.get("start", "?")
        end = block.get("end", "?")
        flow = block.get("flow_note", "")
        lines.append(f"### {i+1}. [{role.upper()}] {cid} ({start}s - {end}s)")
        lines.append(f"> {text}")
        if flow:
            lines.append(f"*Flow*: {flow}")
        lines.append("")
    if excluded:
        lines.append("## Excluded Passages")
        for ex in excluded[:5]:
            lines.append(f"- {ex.get('clip_id', '?')}: {ex.get('reason', '')}")
    return "\n".join(lines)


def _summary_music_selection(name: str, out: dict) -> str:
    sel = out.get("music_selection", out)
    lines = [f"# {name}", ""]
    track = sel.get("selected_track", sel.get("track", {}))
    if isinstance(track, dict):
        lines.append(f"**Selected Track**: {track.get('title', track.get('filename', 'N/A'))}")
        lines.append(f"**BPM**: {track.get('bpm', 'N/A')}")
        lines.append(f"**Key**: {track.get('key', 'N/A')}")
        lines.append(f"**Mood**: {track.get('mood', 'N/A')}")
    else:
        lines.append(f"**Selected**: {track}")
    rationale = sel.get("rationale", sel.get("selection_rationale", ""))
    if rationale:
        lines.append(f"\n**Rationale**: {rationale}")
    return "\n".join(lines)


def _summary_mesh_spine(name: str, out: dict) -> str:
    spine = out.get("audio_spine", out)
    blocks = spine.get("blocks", spine.get("segments", []))
    lines = [f"# {name}", "", f"**{len(blocks)} spine blocks**:", ""]
    lines.append("| # | Type | Duration | Text Excerpt |")
    lines.append("|---|------|----------|--------------|")
    for i, b in enumerate(blocks[:20]):
        btype = b.get("type", b.get("block_type", "?"))
        dur = b.get("duration", b.get("duration_s", "?"))
        text = b.get("text", "")[:60]
        lines.append(f"| {i+1} | {btype} | {dur}s | {text} |")
    return "\n".join(lines)


def _summary_aroll(name: str, out: dict) -> str:
    assignments = out.get("a_roll_assignments", [])
    if isinstance(assignments, dict):
        assignments = list(assignments.values())
    lines = [f"# {name}", "", f"**{len(assignments)} A-roll assignments**:", ""]
    for a in assignments[:20]:
        cid = a.get("clip_id", "?")
        src_in = a.get("source_in", "?")
        src_out = a.get("source_out", "?")
        tl_start = a.get("timeline_start", "?")
        tl_end = a.get("timeline_end", "?")
        lines.append(f"- **{cid}**: src [{src_in}s - {src_out}s] -> timeline [{tl_start}s - {tl_end}s]")
    return "\n".join(lines)


def _summary_broll(name: str, out: dict) -> str:
    assignments = out.get("b_roll_assignments", [])
    if isinstance(assignments, dict):
        assignments = list(assignments.values())
    lines = [f"# {name}", "", f"**{len(assignments)} B-roll assignments**:", ""]
    for b in assignments[:20]:
        cid = b.get("clip_id", "?")
        placement = b.get("placement_type", b.get("type", "?"))
        rationale = b.get("rationale", "")[:80]
        lines.append(f"- **{cid}** ({placement}): {rationale}")
    return "\n".join(lines)


def _summary_rough_cut(name: str, out: dict) -> str:
    review = out.get("rough_cut_review", out)
    passed = review.get("passed", None)
    lines = [f"# {name}", ""]
    if passed is not None:
        status = "PASSED" if passed else "FAILED"
        lines.append(f"## Result: **{status}**")
    lines.append("")
    mech = review.get("mechanical_checks", {})
    if mech:
        lines.append("## Mechanical Checks")
        for check_name, check_data in mech.items():
            if isinstance(check_data, dict):
                cp = "PASS" if check_data.get("passed", True) else "FAIL"
                lines.append(f"- {check_name}: **{cp}**")
        lines.append("")
    narr = review.get("narrative_review", {})
    script = narr.get("reconstructed_script", "")
    if script:
        lines.append("## Reconstructed Script")
        lines.append(f"> {script[:500]}")
        lines.append("")
    reasons = review.get("rejection_reasons", [])
    if reasons:
        lines.append("## Rejection Reasons")
        for r in reasons:
            lines.append(f"- {r}")
    return "\n".join(lines)


def _summary_plan_subtitles(name: str, out: dict) -> str:
    plan = out.get("subtitle_plan", out)
    groups = plan.get("groups", plan.get("subtitle_groups", []))
    lines = [f"# {name}", "", f"**{len(groups)} subtitle groups** planned", ""]
    for i, g in enumerate(groups[:10]):
        text = g.get("text", "")[:80]
        lines.append(f"- Group {i+1}: \"{text}\"")
    return "\n".join(lines)


def _summary_plan_transitions(name: str, out: dict) -> str:
    spec = out.get("transition_spec", out)
    transitions = spec.get("transitions", [])
    lines = [f"# {name}", "", f"**{len(transitions)} transitions** planned:", ""]
    lines.append("| # | Type | Between | Duration |")
    lines.append("|---|------|---------|----------|")
    for i, t in enumerate(transitions[:15]):
        ttype = t.get("type", t.get("transition_type", "?"))
        between = f"{t.get('from_clip', '?')} -> {t.get('to_clip', '?')}"
        dur = t.get("duration_frames", t.get("duration", "?"))
        lines.append(f"| {i+1} | {ttype} | {between} | {dur}f |")
    return "\n".join(lines)


def _summary_plan_vfx(name: str, out: dict) -> str:
    spec = out.get("enhancement_spec", out)
    effects = spec.get("enhancements", spec.get("effects", []))
    lines = [f"# {name}", "", f"**{len(effects)} VFX enhancements** planned:", ""]
    for e in effects[:15]:
        etype = e.get("type", e.get("effect_type", "?"))
        target = e.get("clip_id", e.get("target", "?"))
        desc = e.get("description", "")[:80]
        lines.append(f"- **{etype}** on {target}: {desc}")
    return "\n".join(lines)


def _summary_plan_sfx(name: str, out: dict) -> str:
    spec = out.get("sfx_spec", out)
    placements = spec.get("placements", spec.get("sfx_placements", []))
    lines = [f"# {name}", "", f"**{len(placements)} SFX placements** planned:", ""]
    for p in placements[:15]:
        sfx = p.get("sfx_file", p.get("name", "?"))
        at = p.get("timeline_start", p.get("at", "?"))
        lines.append(f"- `{sfx}` at {at}s")
    return "\n".join(lines)


def _summary_color_grade(name: str, out: dict) -> str:
    spec = out.get("color_grade_spec", out)
    lines = [f"# {name}", ""]
    # `house_look` is what step_5_01 emits. This used to look for
    # `target_look`/`look`/`lut`, none of which any step has ever
    # written, so the summary never named the grade it was summarising.
    look = spec.get("house_look_title") or spec.get("house_look")
    if look:
        lines.append(f"**House Look**: {look}")
    else:
        lines.append("**House Look**: none named - exposure normalisation only")
    notes = spec.get("look_notes", "")
    if notes:
        lines.append(f"\n{notes}")
    lines.append(f"\n```json\n{json.dumps(spec, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_audio_mix(name: str, out: dict) -> str:
    spec = out.get("audio_mix_spec", out)
    lines = [f"# {name}", ""]
    voice_db = spec.get("voice_level_db", "")
    music_db = spec.get("music_level_db", "")
    if voice_db:
        lines.append(f"**Voice Level**: {voice_db} dB")
    if music_db:
        lines.append(f"**Music Level**: {music_db} dB")
    lines.append(f"\n```json\n{json.dumps(spec, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_creative_cohesion(name: str, out: dict) -> str:
    review = out.get("cohesion_review", out)
    lines = [f"# {name}", ""]
    passed = review.get("passed", review.get("cohesive", None))
    if passed is not None:
        lines.append(f"**Result**: {'COHESIVE' if passed else 'NEEDS REVISION'}")
    notes = review.get("notes", review.get("feedback", ""))
    if notes:
        lines.append(f"\n{notes}")
    return "\n".join(lines)


def _summary_compile_manifest(name: str, out: dict) -> str:
    manifest = out.get("assembly_manifest", out)
    lines = [f"# {name}", ""]
    clips = manifest.get("clips", manifest.get("video_segments", []))
    lines.append(f"**{len(clips)} clips** in final manifest")
    total_dur = manifest.get("total_duration_s", "")
    if total_dur:
        lines.append(f"**Total Duration**: {total_dur}s")
    return "\n".join(lines)


def _summary_render(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    if "output_file" in out:
        lines.append(f"**Output File**: `{out['output_file']}`")
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_validate(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    if "is_valid" in out:
        lines.append(f"**Valid**: {out['is_valid']}")
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_render_subtitles(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_render_motion_graphics(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_object_segmentation(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_ocr_extraction(name: str, out: dict) -> str:
    lines = [f"# {name}", ""]
    lines.append(f"```json\n{json.dumps(out, indent=2)[:800]}\n```")
    return "\n".join(lines)


def _summary_generic(name: str, out: dict) -> str:
    """Fallback: render the output as formatted JSON."""
    lines = [f"# {name}", ""]
    # Show top-level keys
    keys = list(out.keys())
    if keys:
        lines.append(f"**Output keys**: {', '.join(keys)}")
        lines.append("")
    # Truncated JSON preview
    preview = json.dumps(out, indent=2)
    if len(preview) > 2000:
        preview = preview[:2000] + "\n... (truncated)"
    lines.append(f"```json\n{preview}\n```")
    return "\n".join(lines)
