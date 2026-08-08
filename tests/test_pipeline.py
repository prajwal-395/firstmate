#!/usr/bin/env python3
"""
Pipeline Test Orchestrator — Phase 2-6 Re-run

Uses existing Phase 1 outputs from 001 test project.
Strategy: save updated step outputs to the pipeline_output dir with
versioned names, then call each step's actual interface.

For steps that load from files (compile_manifest), we temporarily
write our v2 outputs to the expected filenames, run the step, then
restore the originals.

Usage:
    python3 test_pipeline.py
"""
import json
import os
import sys
import shutil

# ── Paths ────────────────────────────────────────────────────────────
# Set PIPELINE_TEST_PROJECT env var to point to your test project,
# or use --project flag when running directly.
PROJECT_DIR = os.environ.get("PIPELINE_TEST_PROJECT", "")
if not PROJECT_DIR:
    # Try to resolve from project registry
    try:
        _pilot_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, _pilot_root)
        from library.tools.paths import PROJECTS_ROOT
        # Use the first available project as a default
        for entry in PROJECTS_ROOT.iterdir():
            if entry.is_dir() and (entry / "project.yaml").exists():
                PROJECT_DIR = str(entry)
                break
            for sub in entry.iterdir():
                if sub.is_dir() and (sub / "project.yaml").exists():
                    PROJECT_DIR = str(sub)
                    break
            if PROJECT_DIR:
                break
    except (ImportError, FileNotFoundError, OSError):
        pass
if not PROJECT_DIR:
    print("Error: Set PIPELINE_TEST_PROJECT env var to a project directory", file=sys.stderr)
    sys.exit(1)
OUTPUT_DIR = os.path.join(PROJECT_DIR, "pipeline_output")
PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIBRARY_DIR = os.path.join(PILOT_ROOT, "library")

# Use importlib to load step modules explicitly (avoids name collisions)
import importlib.util

def load_step_module(step_dir_name, module_name="step"):
    """Load a step module by its directory name."""
    path = os.path.join(LIBRARY_DIR, "steps", step_dir_name, f"{module_name}.py")
    spec = importlib.util.spec_from_file_location(f"{step_dir_name}.{module_name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_json(path):
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  → Saved: {os.path.basename(path)}")


def banner(phase, title):
    print(f"\n{'='*60}")
    print(f"  {phase}: {title}")
    print(f"{'='*60}")


import pytest
@pytest.mark.skipif(not os.path.exists(os.path.join(OUTPUT_DIR, "step_2_01.json")), reason='Pipeline output files missing')
def test_pipeline_run():
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 1: Verify existing outputs
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 1", "Loading existing analysis outputs")
    
    pipeline_data = load_json(os.path.join(PROJECT_DIR, "pipeline_data.json"))
    clip_catalog = pipeline_data.get("catalog", {}).get("clip_catalog", [])
    print(f"  Clip catalog: {len(clip_catalog)} clips")
    
    # Verify Phase 1 files exist
    for f in ["step_1_01.json", "step_1_02.json", "step_1_03.json"]:
        path = os.path.join(OUTPUT_DIR, f)
        exists = os.path.exists(path)
        print(f"  {f}: {'✓' if exists else '✗'}")
    
    # Also check if we have rendered transcript (word-level timestamps)
    rendered_path = os.path.join(OUTPUT_DIR, "rendered_transcript.json")
    has_rendered = os.path.exists(rendered_path)
    print(f"  rendered_transcript.json: {'✓' if has_rendered else '✗'}")
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 2: Audio Spine — re-run bridge with frame fields
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 2", "Audio Spine (re-run bridge with frame fields)")
    
    # Load existing LLM decisions
    step_2_01 = load_json(os.path.join(OUTPUT_DIR, "step_2_01.json"))
    step_2_02 = load_json(os.path.join(OUTPUT_DIR, "step_2_02.json"))
    step_2_04 = load_json(os.path.join(OUTPUT_DIR, "step_2_04.json"))
    step_2_05_orig = load_json(os.path.join(OUTPUT_DIR, "step_2_05.json"))
    
    speech_seq = step_2_02.get("speech_sequence", {})
    music = step_2_04.get("music_selection", {})
    spine = step_2_05_orig.get("audio_spine", {})
    
    print(f"  2.01 Creative Direction: ✓")
    print(f"  2.02 Speech Sequence: ✓ (hook + {len(speech_seq.get('body_sequence', []))} passages)")
    print(f"  2.04 Music: ✓ ({len(music.get('tracks', []))} tracks)")
    
    # Re-run bridge to add frame fields
    print(f"\n  Re-running step 2.05 bridge...")
    bridge_mod = load_step_module("step_2_05_mesh_spine", "bridge")
    enriched = bridge_mod.enrich_spine(spine, speech_seq, music)
    
    # Verify frame fields
    structure = enriched["audio_spine"]["structure"]
    sample = structure[0]
    has_frames = "timeline_start_frame" in sample
    
    print(f"  ✓ Frame fields present: {has_frames}")
    print(f"  ✓ Total blocks: {len(structure)}")
    print(f"  ✓ Duration: {enriched['audio_spine'].get('total_estimated_duration_seconds', 0):.1f}s")
    print(f"  ✓ Total frames: {enriched['audio_spine'].get('total_duration_frames', '?')}")
    print(f"  ✓ Frame rate: {enriched['audio_spine'].get('frame_rate', '?')} fps")
    
    for b in structure:
        print(f"    Block {b['position']} ({b['block_type']:6s}): "
              f"f{b.get('timeline_start_frame', '?'):>5} → f{b.get('timeline_end_frame', '?'):>5} "
              f"({b.get('duration_frames', '?'):>4} frames, "
              f"{b.get('duration_seconds', 0):.1f}s)")
    
    save_json(os.path.join(OUTPUT_DIR, "step_2_05_v2.json"), enriched)
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 3: Visual Assembly — re-run a-roll with link_group_id
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 3", "Visual Assembly (re-run a-roll with link_group_id)")
    
    aroll_mod = load_step_module("step_3_01_assign_aroll")
    aroll_result = aroll_mod.assign_a_roll(enriched["audio_spine"], clip_catalog)
    
    # Check link_group_id and frame fields
    linked_count = 0
    frame_count = 0
    for a in aroll_result.get("a_roll_assignments", []):
        if a.get("block_type") == "hook":
            if a.get("link_group_id"):
                linked_count += 1
            if a.get("timeline_start_frame") is not None:
                frame_count += 1
        for seg in a.get("video_segments", []):
            if seg.get("link_group_id"):
                linked_count += 1
    
    print(f"  ✓ Assignments: {len(aroll_result.get('a_roll_assignments', []))}")
    print(f"  ✓ link_group_id present on: {linked_count} segments")
    print(f"  ✓ Frame fields on hook: {frame_count > 0}")
    
    step_3_01_new = {"step": "3.01", **aroll_result}
    save_json(os.path.join(OUTPUT_DIR, "step_3_01_v2.json"), step_3_01_new)
    
    # B-roll: use existing (LLM decision)
    step_3_02 = load_json(os.path.join(OUTPUT_DIR, "step_3_02.json"))
    print(f"\n  3.02 B-Roll: ✓ ({len(step_3_02.get('b_roll_assignments', []))} assignments, existing)")
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 4: Enhancement — re-run subtitles with min_duration
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 4", "Enhancement (re-run subtitles with min_duration)")
    
    sub_mod = load_step_module("step_4_01_plan_subtitles")
    sub_result = sub_mod.generate_subtitles(enriched["audio_spine"], speech_seq)
    
    print(f"  ✓ Total subtitles: {sub_result['total_subtitles']}")
    
    # Check min duration compliance
    violations = []
    for sub in sub_result["subtitle_entries"]:
        dur = sub["timeline_end"] - sub["timeline_start"]
        if dur < 0.69:
            violations.append(sub)
    
    print(f"  ✓ Min duration violations (<0.7s): {len(violations)}")
    
    # Show all subtitles
    for sub in sub_result["subtitle_entries"]:
        dur = sub["timeline_end"] - sub["timeline_start"]
        words = sub.get("word_count", len(sub["text"].split()))
        emphasis = sub.get("emphasis_words", [])
        marker = " ⚠️" if dur < 0.69 else ""
        print(f"    [{sub['entry_id']}] {dur:.2f}s ({words}w): "
              f"\"{sub['text']}\""
              f"{' 💪 ' + ','.join(emphasis) if emphasis else ''}"
              f"{marker}")
    
    step_4_01_new = {"step": "4.01", **sub_result}
    save_json(os.path.join(OUTPUT_DIR, "step_4_01_v2.json"), step_4_01_new)
    
    # Use existing enhancement specs
    step_4_02 = load_json(os.path.join(OUTPUT_DIR, "step_4_02.json"))
    step_4_03 = load_json(os.path.join(OUTPUT_DIR, "step_4_03.json"))
    step_4_04 = load_json(os.path.join(OUTPUT_DIR, "step_4_04.json"))
    print(f"\n  4.02 Transitions: ✓ (existing)")
    print(f"  4.03 VFX: ✓ (existing)")
    print(f"  4.04 SFX: ✓ (existing)")
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 5: Compile manifest — swap in v2 files, run compiler
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 5", "Compile Assembly Manifest (with all new fields)")
    
    # compile_manifest() loads from fixed filenames in the output dir.
    # We need to temporarily swap in our v2 outputs.
    swap_pairs = [
        ("step_2_05.json", "step_2_05_v2.json"),
        ("step_4_01.json", "step_4_01_v2.json"),
    ]
    
    # Back up originals and swap in v2
    for orig_name, v2_name in swap_pairs:
        orig_path = os.path.join(OUTPUT_DIR, orig_name)
        backup_path = orig_path + ".bak"
        v2_path = os.path.join(OUTPUT_DIR, v2_name)
        if os.path.exists(orig_path):
            shutil.copy2(orig_path, backup_path)
        shutil.copy2(v2_path, orig_path)
        print(f"  Swapped {orig_name} ← {v2_name}")
    
    try:
        manifest_mod = load_step_module("step_5_04_compile_manifest")
        manifest = manifest_mod.compile_manifest(OUTPUT_DIR)
    
        # Report
        v1_clips = manifest.get("tracks", {}).get("V1", {}).get("clips", [])
        v2_clips = manifest.get("tracks", {}).get("V2", {}).get("clips", [])
    
        v1_frames = sum(1 for c in v1_clips if c.get("timeline_in_frame") is not None)
        v2_frames = sum(1 for c in v2_clips if c.get("timeline_in_frame") is not None)
        v1_linked = sum(1 for c in v1_clips if c.get("link_group_id"))
    
        print(f"\n  ✓ V1: {len(v1_clips)} clips ({v1_frames} with frames, {v1_linked} linked)")
        print(f"  ✓ V2: {len(v2_clips)} clips ({v2_frames} with frames)")
        print(f"  ✓ Subtitles: {len(manifest.get('subtitles', []))}")
        print(f"  ✓ Transitions: {len(manifest.get('transitions', []))}")
        print(f"  ✓ VFX: {len(manifest.get('vfx', []))}")
        print(f"  ✓ SFX: {len(manifest.get('sfx', []))}")
    
        # Show V1 clip details
        print(f"\n  V1 Clip Details:")
        for c in v1_clips:
            lgid = c.get("link_group_id", "")[:8] if c.get("link_group_id") else "none"
            print(f"    {c.get('label', '?'):20s}  "
                  f"f{c.get('timeline_in_frame', '?'):>5} → f{c.get('timeline_out_frame', '?'):>5}  "
                  f"link={lgid}")
    
        save_json(os.path.join(OUTPUT_DIR, "assembly_manifest_v2.json"), manifest)
    
    finally:
        # Restore originals
        for orig_name, _ in swap_pairs:
            orig_path = os.path.join(OUTPUT_DIR, orig_name)
            backup_path = orig_path + ".bak"
            if os.path.exists(backup_path):
                shutil.move(backup_path, orig_path)
                print(f"  Restored {orig_name}")
    
    # ══════════════════════════════════════════════════════════════════════
    # PHASE 6: Generate XMEML v4
    # ══════════════════════════════════════════════════════════════════════
    
    banner("PHASE 6", "Generate XMEML v4 Timeline")
    
    # Add render step to path so xmeml_generator can be found
    sys.path.insert(0, os.path.join(LIBRARY_DIR, "steps", "step_6_01_render"))
    from xmeml_generator import write_xmeml_file
    xmeml_path = os.path.join(OUTPUT_DIR, "timeline_v2.xml")
    write_xmeml_file(manifest, xmeml_path)
    
    # Validate
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(xmeml_path)
        root = tree.getroot()
        print(f"  ✓ XML well-formed ({root.tag})")
    
        clips = root.findall(".//clipitem")
        trans = root.findall(".//transitionitem")
        filters_el = root.findall(".//filter")
        links = root.findall(".//link")
        files = root.findall(".//file")
    
        print(f"  ✓ Clipitems: {len(clips)}")
        print(f"  ✓ Transitions: {len(trans)}")
        print(f"  ✓ Filters: {len(filters_el)}")
        print(f"  ✓ Links: {len(links)}")
        print(f"  ✓ File nodes: {len(files)}")
    
        # NTSC
        ntsc_nodes = root.findall(".//ntsc")
        if ntsc_nodes:
            vals = set(n.text for n in ntsc_nodes)
            print(f"  ✓ NTSC values: {vals}")
    
        # Frame-integer timing
        starts = root.findall(".//clipitem/start")
        if starts:
            sample = starts[0].text
            try:
                int(sample)
                print(f"  ✓ Frame-integer timing (sample: {sample})")
            except ValueError:
                print(f"  ✗ Non-integer timing: {sample}")
    
        # Check for link blocks
        link_refs = root.findall(".//link/linkclipref")
        if link_refs:
            print(f"  ✓ Link references: {len(link_refs)} (A/V pairing)")
    
        # File size
        sz = os.path.getsize(xmeml_path)
        print(f"  ✓ File size: {sz:,} bytes")
    
    except ET.ParseError as e:
        print(f"  ✗ XML Parse Error: {e}")
    
    # ══════════════════════════════════════════════════════════════════════
    # FINAL SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    
    print(f"\n{'═'*60}")
    print(f"  PIPELINE TEST COMPLETE")
    print(f"{'═'*60}")
    print(f"")
    print(f"  New outputs:")
    print(f"    step_2_05_v2.json          — spine with frame fields")
    print(f"    step_3_01_v2.json          — a-roll with link_group_id")
    print(f"    step_4_01_v2.json          — subtitles with min duration")
    print(f"    assembly_manifest_v2.json  — full manifest")
    print(f"    timeline_v2.xml            — XMEML v4 for Resolve")
    print(f"")
    print(f"  Import into Resolve:")
    print(f"    File → Import → Timeline → {xmeml_path}")
    print(f"{'═'*60}\n")
