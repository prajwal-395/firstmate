import sys
import re
import os
import json

file_path = "/Users/prajwal/.treehouse/video_editing_pilot-9487f5/3/video_editing_pilot/library/steps/step_6_01_render/resolve_build_timeline.py"

with open(file_path, "r") as f:
    content = f.read()

# 1. Update _connect_resolve
old_connect = """def _connect_resolve():
    \"\"\"Connect to running DaVinci Resolve instance.\"\"\"
    api_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    lib_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

    if api_path not in sys.path:
        sys.path.append(os.path.join(api_path, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = api_path
    os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise ConnectionError("Cannot connect to DaVinci Resolve. Is it running?")
    return resolve"""

new_connect = """def _connect_resolve():
    \"\"\"Connect to running DaVinci Resolve instance.\"\"\"
    api_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    lib_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

    if api_path not in sys.path:
        sys.path.append(os.path.join(api_path, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = api_path
    os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    try:
        from library.tools.resolve_health import check_resolve_connection
        health = check_resolve_connection()
        if not health.get("success"):
            raise ConnectionError(health.get("error"))
        return health["resolve"]
    except ImportError:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            raise ConnectionError("Cannot connect to DaVinci Resolve. Is it running?")
        return resolve"""

content = content.replace(old_connect, new_connect)

# 2. Extract Fusion comp processing into a new function
fusion_start_marker = "    # APPLY FUSION .comp FILES (animated VFX + transitions per clip)"
fusion_end_marker = "    # NEURAL ENGINE DIRECTIVES (Per-Clip)"

idx_start = content.find(fusion_start_marker)
idx_end = content.find(fusion_end_marker)

fusion_block = content[idx_start:idx_end]

new_fusion_caller = """    # APPLY FUSION .comp FILES (animated VFX + transitions per clip)
    # ══════════════════════════════════════════════════════════
    # We spawn a subprocess here to apply Fusion comps because calling
    # ImportFusionComp in the same Python process that created the timeline
    # causes stale clip references and crashes.
    
    has_any_effects = bool(
        manifest.get('fusion_effects', {}).get('per_clip') or 
        manifest.get('fusion_effects', {}).get('transitions') or
        manifest.get('vfx', [])
    )

    if has_any_effects:
        import tempfile
        import subprocess
        
        checkpoint_data = {
            "manifest": manifest,
            "timeline_name": timeline_name,
            "project_name": project_name or project.GetName()
        }
        
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.json') as tf:
            json.dump(checkpoint_data, tf)
            checkpoint_path = tf.name
            
        print(f"\\n── Fusion .comp (Pass 2 Process Isolation) ──", file=sys.stderr)
        print(f"  Spawning subprocess for Fusion comps...", file=sys.stderr)
        
        cmd = [sys.executable, __file__, "--pass2", checkpoint_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            pass2_result = json.loads(proc.stdout)
            
            results["warnings"].extend(pass2_result.get("warnings", []))
            results["errors"].extend(pass2_result.get("errors", []))
            
            if proc.stderr:
                print(proc.stderr.strip(), file=sys.stderr)
        except subprocess.CalledProcessError as e:
            results["errors"].append(f"Pass 2 Fusion comp application failed: {e}")
            if e.stderr:
                print(e.stderr.strip(), file=sys.stderr)
        except json.JSONDecodeError as e:
            results["errors"].append(f"Pass 2 Fusion returned invalid JSON: {e}")
            if 'proc' in locals() and proc.stderr:
                print(proc.stderr.strip(), file=sys.stderr)
        finally:
            if os.path.exists(checkpoint_path):
                os.unlink(checkpoint_path)

"""

content = content[:idx_start - 35] + new_fusion_caller + content[idx_end - 35:]

pass2_func = """
def run_pass2_fusion(checkpoint_path: str) -> dict:
    \"\"\"Pass 2: Apply Fusion Comps in an isolated process.\"\"\"
    import json
    import os
    import sys
    
    with open(checkpoint_path) as f:
        data = json.load(f)
        
    manifest = data["manifest"]
    timeline_name = data["timeline_name"]
    project_name = data["project_name"]
    
    results = {"warnings": [], "errors": []}
    
    try:
        resolve = _connect_resolve()
    except Exception as e:
        results["errors"].append(str(e))
        return results
        
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if project.GetName() != project_name:
        project = pm.LoadProject(project_name)
        
    fps = manifest.get('project', {}).get('frame_rate', 30)
    
    timeline = None
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == timeline_name:
            timeline = tl
            break
            
    if not timeline:
        results["errors"].append(f"Timeline {timeline_name} not found in Pass 2")
        return results
        
    project.SetCurrentTimeline(timeline)
    
"""

lines = fusion_block.split('\\n')
fusion_code = '\\n'.join(lines) + "\\n    return results\\n\\n"

# Use regex to strip verify_fusion_comps instead of fragile string replace
import re
fusion_code = re.sub(r'if verify_fusion_comps:.*?_run_qa\(verify_fusion_comps.*?\)','', fusion_code, flags=re.DOTALL)

fusion_code = fusion_code.replace("for ci, clip_spec in enumerate(v1_clips):", 
"v1_clips = manifest.get('tracks', {}).get('V1', {}).get('clips', [])\\n        for ci, clip_spec in enumerate(v1_clips):")

content = content.replace("def build_timeline(", pass2_func + fusion_code + "def build_timeline(")

cli_part = """    if manifest is None:
        # CLI mode: parse arguments
        parser = argparse.ArgumentParser(description="Build Resolve timeline from manifest")
        parser.add_argument("manifest", help="Path to assembly_manifest.json", nargs='?')
        parser.add_argument("--subtitle-overlay", help="Path to Remotion subtitle overlay (.mov)")
        parser.add_argument("--motion-graphics", help="Path to Remotion motion graphics overlay (.mov)")
        parser.add_argument("--project", help="Resolve project name")
        parser.add_argument("--keep-existing", action="store_true",
                            help="Don't delete existing timelines with same name")
        parser.add_argument("--pass2", help="Internal use: run Pass 2 Fusion comps on checkpoint")
        args = parser.parse_args()

        if args.pass2:
            res = run_pass2_fusion(args.pass2)
            json.dump(res, sys.stdout)
            sys.exit(0)

        if not args.manifest:
            parser.error("manifest is required unless --pass2 is provided")

        with open(args.manifest) as f:"""

old_cli_part = """    if manifest is None:
        # CLI mode: parse arguments
        parser = argparse.ArgumentParser(description="Build Resolve timeline from manifest")
        parser.add_argument("manifest", help="Path to assembly_manifest.json")
        parser.add_argument("--subtitle-overlay", help="Path to Remotion subtitle overlay (.mov)")
        parser.add_argument("--motion-graphics", help="Path to Remotion motion graphics overlay (.mov)")
        parser.add_argument("--project", help="Resolve project name")
        parser.add_argument("--keep-existing", action="store_true",
                            help="Don't delete existing timelines with same name")
        args = parser.parse_args()

        with open(args.manifest) as f:"""

content = content.replace(old_cli_part, cli_part)

with open(file_path, "w") as f:
    f.write(content)
print("Patch script completed.")
