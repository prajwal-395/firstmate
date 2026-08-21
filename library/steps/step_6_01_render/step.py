#!/usr/bin/env python3
"""
Step 6.1: Render Final Video

Assembles the final video in DaVinci Resolve.
Calls resolve_build_timeline.py's main function with the correct arguments.

Input: { "assembly_manifest": {...}, ... }
Output: {
    "render_output": {
        "timeline_name": "...",
        "status": "success",
        ...
    }
}
"""
import json
import subprocess
import sys
import os
from resolve_build_timeline import build_timeline

# step.py lives at <repo>/library/steps/step_6_01_render/step.py
PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
RENDER_SCRIPT = os.path.join(
    PILOT_ROOT, "library", "tools", "execution", "resolve_render.py")


def _export_timeline(timeline_name: str, inputs: dict, manifest: dict) -> dict:
    """Render the built timeline to a file and return the render report.

    Runs in a separate process: clip references go stale after timeline
    creation (AGENTS.md section 5), and the same isolation rule that
    applies to ImportFusionComp applies to driving the Deliver page.
    """
    project_folder = inputs.get("project_folder", "")
    if not project_folder:
        raise ValueError("project_folder is required to place the export")

    output_dir = os.path.join(project_folder, "exports")
    output_name = manifest.get("project", {}).get("name", "Pipeline_Edit")

    cmd = [
        sys.executable, RENDER_SCRIPT,
        "--timeline", timeline_name or "",
        "--output-dir", output_dir,
        "--name", output_name,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2400)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Export failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[-800:]}"
        )
    return json.loads(proc.stdout)


def run(inputs: dict) -> dict:
    import sys
    manifest = inputs.get("assembly_manifest", {})
    if not manifest:
        # Sometimes orchestrator passes it as the root or under another key
        # Check if project is in inputs, which means it might be the manifest itself
        if "project" in inputs and "tracks" in inputs:
            manifest = inputs
        else:
            raise ValueError("assembly_manifest missing from inputs")
            
    try:
        # Build timeline (this connects to Resolve)
        result = build_timeline(
            manifest=manifest,
            subtitle_overlay_path=inputs.get("subtitle_overlay_path"),
            motion_graphics_path=inputs.get("motion_graphics_path"),
            project_name=manifest.get("project", {}).get("name", "Pipeline_Edit"),
            delete_existing=True,
            project_folder=inputs.get("project_folder", ""),
        )
        
        if not result.get("success") and result.get("errors"):
            if any("Cannot connect to DaVinci Resolve" in str(e) for e in result.get("errors", [])):
                raise ConnectionError("DaVinci Resolve is not running or not accessible.")
            raise RuntimeError(f"Timeline build failed: {result.get('errors')}")
            
        # Run Timeline Sync QA
        try:
            PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
            if os.path.join(PILOT_ROOT, "library") not in sys.path:
                sys.path.insert(0, os.path.join(PILOT_ROOT, "library"))
            from tools.qa.timeline_sync_qa import run_timeline_sync_qa
            run_timeline_sync_qa(manifest, manifest.get("project", {}).get("name", "Pipeline_Edit"), result.get("timeline_name"))
        except Exception as e:
            raise RuntimeError(f"Timeline Sync QA Validation Failed: {str(e)}")
            
        # ── Export ──
        # Building the timeline is not shipping the video. Render it to a
        # real file so step 6.02 has something to validate; without this
        # every run ended at distribution_ready: false.
        export = _export_timeline(result.get("timeline_name"), inputs, manifest)

        output_payload = {
            "render_output": {
                "timeline_name": result.get("timeline_name"),
                "status": "success" if result.get("success", True) else "failed",
                "success": result.get("success", True),
                "errors": result.get("errors", []),
                "tracks": result.get("tracks", {}),
                "warnings": result.get("warnings", []),
                # Error-severity QA station failures, forwarded so they
                # land in pipeline_data.json. This payload hand-picks its
                # keys, so anything not named here is dropped before the
                # ledger ever sees it - and this list is the evidence
                # channel for whether a failing station should become
                # fatal. Without it that question can never be answered
                # from real runs. See docs/PIPELINE_PLAN.md.
                "qa_failures": result.get("qa_failures", []),
                "output_path": export["output_path"],
                "output_size_bytes": export["size_bytes"],
                "render_job": {
                    "job_id": export["job_id"],
                    "job_status": export["job_status"],
                    "format": export["format"],
                    "codec": export["codec"],
                },
            }
        }

        if "visual_qa" in result:
            output_payload["visual_qa"] = result["visual_qa"]

        return output_payload
        
    except ConnectionError as e:
        # Fail fast if Resolve isn't running
        raise RuntimeError(f"ConnectionError: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Render failed: {str(e)}")


def main():
    import sys
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}
        
    try:
        result = run(input_data)
        json.dump(result, sys.stdout, indent=2)
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "step": "6.1_render"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
