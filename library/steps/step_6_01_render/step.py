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

if PILOT_ROOT not in sys.path:
    sys.path.insert(0, PILOT_ROOT)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.brand_registry import DEFAULT_TIMELINE_NAME  # noqa: E402


def _perceptual_watch_enabled() -> bool:
    """Whether this build draws watch strips of its export.

    The same switch `visual_qa_router.perceptual_qa_enabled()` reads,
    repeated inline - with the same accepted values - so the check
    costs nothing and never imports the vision stack on a run that
    leaves the flag off. If the accepted values ever change there,
    this must follow.
    """
    return os.environ.get(
        "PIPELINE_PERCEPTUAL_QA", "").strip().lower() in (
            "1", "true", "yes", "on")


def _render_watch_block(video_path: str, project_folder: str) -> str:
    """Draw the strips the LLM review WATCHES, and map them for the prompt.

    The same route `step_6_02_validate_output`'s bridge takes, off the
    file this step exported rather than off the timeline: the strips
    are of the COMPOSITE - everything drawn over everything else -
    which exists only once the export is on disk. Drawing is
    measurement, not judgement, and it runs only when
    `PIPELINE_PERCEPTUAL_QA` is set; the flag stays off by default, so
    a default build attaches nothing and the handoff says the picture
    was not seen.

    Returns "" when nothing could be drawn. Best-effort throughout: a
    watch that breaks must not break a render.
    """
    if not _perceptual_watch_enabled():
        return ""
    if not video_path or not os.path.exists(video_path):
        return ""
    if not project_folder:
        print("  No project_folder: no watch frames drawn", file=sys.stderr)
        return ""
    try:
        from library.tools import render_watch
        frames_dir, _record_path = render_watch.watch_paths(
            project_folder, video_path)
        drawn = render_watch.draw_watch_strips(
            video_path, frames_dir, label="render")
        print(f"  {len(drawn['rows'])} watch strip(s) at {frames_dir}"
              + (f"; {len(drawn['missing'])} span(s) not drawn"
                 if drawn["missing"] else ""), file=sys.stderr)
        if not drawn["rows"]:
            return ""
        return render_watch.build_watch_block(
            drawn["directory"], drawn["rows"], drawn["missing"],
            subject="the exported render this step just built",
            duration=drawn["duration"])
    except Exception as exc:  # noqa: BLE001 - drawing is best-effort
        print(f"  ⚠ Render watch unavailable: {exc}", file=sys.stderr)
        return ""


def _visual_qa_prompt_addition(visual_qa) -> str:
    """The text that replaces `<!-- VISUAL_QA_INSTRUCTIONS -->`.

    Always returns a replacement, so the marker never reaches a prompt
    raw. When the grabs ran it describes the table; when they did not -
    the default, with the flag off - it records that absence, so the
    review is never instructed to use a table that is not there.
    """
    if visual_qa:
        return (
            "### Visual QA Findings\n\n"
            "The `visual_qa` table contains observations from a local vision model "
            "that watched the render. Use these findings to evaluate visual correctness, "
            "such as framing, subject visibility, and transition boundaries."
        )
    return (
        "### Visual QA Findings\n\n"
        "No frame-grab pass ran on this build (`PIPELINE_PERCEPTUAL_QA` "
        "unset): there is no `visual_qa` table. The "
        "`render_watch_frames` section states whether strips of the "
        "export were drawn - judge the picture from those, or say it "
        "was not seen."
    )


def _resolve_build_project_name(manifest: dict) -> str:
    """The Resolve PROJECT a build opens, off the manifest.

    ``manifest.project.resolve_project_name`` - the exact listed name
    from the project's ``resolve.project_name`` - never the timeline
    name beside it. Passing the timeline's name opened a project of
    that name on every run ("No project named exactly 'Main Edit'").
    Empty refuses naming the declaration: a build with nowhere bound
    must not open whatever project happens to be current.
    """
    from library.tools.ren_refusal import RenRefusal

    name = ((manifest.get("project") or {}).get(
        "resolve_project_name") or "").strip()
    if not name:
        raise RenRefusal(
            what=("compile_manifest recorded no resolve_project_name, "
                  "so the build has no Resolve project bound"),
            why=("the project declares no resolve.project_name - and a "
                 "build must not open whatever project happens to be "
                 "current, least of all on the captain's machine"),
            fix=("declare resolve.project_name in project.yaml (the "
                 "exact name as Resolve's project list reports it) and "
                 "recompile"))
    return name


def _render_output_payload(result: dict, export: dict,
                           resolve_project_name: str = "") -> dict:
    """The render record 6.02 validates, as the `render_output` state key.

    Hand-picks its keys (see `run`): anything not named here is dropped
    before the ledger ever sees it. Three of them are the approve gate's
    address of the timeline it must read back: `resolve_project_name`
    is the exact Resolve project the build opened (refused unless
    exact, so this spelling is the listed one), `timeline_name` is the
    timestamped name the build placed (not the manifest's base project
    name), and `track_plan` is the plan that build laid out, so the
    `verify_timeline` gating skill on 6.02 can run its link and stream
    checks instead of skipping them openly. The manifest itself never
    reaches 6.02's prompt (its context drops it whole), so the address
    has to travel here - a skill whose inputs are not in context cannot
    be invoked. A build that recorded no plan carries no `track_plan`
    key - an absent plan is not an empty one, and the skill says which
    it got.
    """
    result = result or {}
    export = export or {}
    payload = {
        "timeline_name": result.get("timeline_name"),
        "resolve_project_name": resolve_project_name or "",
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
        # One record per transition, including an exact requested frame
        # and the reason a plan was compiled as a hard cut. The manifest
        # alone is not the build report, and this payload hand-picks the
        # fields that reach the run ledger.
        "transition_items": result.get("transition_items", []),
        "output_path": export.get("output_path"),
        "output_size_bytes": export.get("size_bytes"),
        "render_job": {
            "job_id": export.get("job_id"),
            "job_status": export.get("job_status"),
            "format": export.get("format"),
            "codec": export.get("codec"),
        },
    }
    # `master_render_report` measures the encoded delivery file. Preserve
    # that proof through the hand-picked render payload so the ledger and
    # render review can distinguish its measured result from the Resolve
    # request that produced it.
    if "mastering" in export:
        payload["mastering"] = export["mastering"]
        payload["raw_output_path"] = export["raw_output_path"]
    track_plan = result.get("track_plan")
    if track_plan is not None:
        payload["track_plan"] = track_plan
    return payload


def _transition_items_for_report(manifest: dict) -> list[dict]:
    """Summarise every compiled transition for the render/build report."""
    items = []
    for row in manifest.get("transitions", []) or []:
        if not isinstance(row, dict):
            continue
        requested_type = row.get("requested_type") or row.get(
            "transition_type", "")
        compiled_type = row.get("transition_type", "")
        reason = row.get("downgrade_reason") or ""
        duration_source = row.get("duration_source")
        requested_frames = row.get("requested_duration_frames")
        if (requested_frames is None and duration_source in (
                "frames", "stated_frames", "stated_frames_and_seconds")):
            requested_frames = row.get("duration_frames")
        requested_seconds = row.get("requested_duration_seconds")
        if (requested_seconds is None and duration_source in (
                "seconds", "stated_seconds", "stated_frames_and_seconds")):
            requested_seconds = row.get("duration_seconds")
        downgraded = bool(reason) or requested_type != compiled_type
        items.append({
            "transition_id": row.get("transition_id"),
            "requested_type": requested_type,
            "compiled_type": compiled_type,
            "cut_point_frame": row.get("cut_point_frame"),
            "cut_point_timeline": row.get("cut_point_timeline"),
            "duration_source": duration_source,
            "requested_duration_frames": requested_frames,
            "requested_duration_seconds": requested_seconds,
            "requested_duration_feel": row.get("duration_feel"),
            "compiled_duration_frames": row.get("duration_frames"),
            "status": "downgraded" if downgraded else "compiled",
            "reason": reason,
        })
    return items


def _export_timeline(timeline_name: str, inputs: dict, manifest: dict) -> dict:
    """Render the built timeline to a file and return the render report.

    Runs in a separate process: clip references go stale after timeline
    creation (AGENTS.md section 5), and the same isolation rule that
    applies to ImportFusionComp applies to driving the Deliver page.
    """
    project_folder = inputs.get("project_folder", "")
    if not project_folder:
        raise ValueError("project_folder is required to place the export")

    output_dir = str(ProjectLayout(project_folder).write_dir(Area.EXPORTS, step="render"))
    scratch_dir = str(ProjectLayout(project_folder).write_dir(
        Area.SCRATCH, step="render"))
    # The export is the deliverable OF this timeline, so it carries the
    # timeline's own name - including PR 460's timestamp/duration/draft
    # suffix. Recomputing it from the manifest's base project name is how
    # every run overwrote exports/<base>.mp4 in place while the timestamped
    # timelines accumulated beside it (D9). Identity, not a second
    # timestamp computation: a fresh strftime here could tick over a second
    # boundary and name a file no timeline holds.
    output_name = timeline_name or manifest.get("project", {}).get("name", DEFAULT_TIMELINE_NAME)

    cmd = [
        sys.executable, RENDER_SCRIPT,
        "--timeline", timeline_name or "",
        "--output-dir", scratch_dir,
        "--name", f"{output_name}_pre_master",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2400)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Export failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[-800:]}"
        )
    rendered = json.loads(proc.stdout)
    raw_path = rendered.get("output_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeError("Resolve render returned no output_path")
    extension = os.path.splitext(raw_path)[1] or ".mp4"
    final_path = os.path.join(output_dir,
                              os.path.basename(output_name) + extension)
    from library.tools.master_loudness import (
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
        DELIVERY_LUFS_TARGET,
        master_render_report,
    )
    audio_mix = manifest.get("audio_mix", {}) or {}
    return master_render_report(
        rendered, final_path,
        target_lufs=float(audio_mix.get(
            "delivery_lufs_target", DELIVERY_LUFS_TARGET)),
        true_peak_ceiling=float(audio_mix.get(
            "delivery_true_peak_ceiling_dbtp",
            DEFAULT_TRUE_PEAK_CEILING_DBTP)),
    )


def _skip_authorized_stabilization(manifest: dict) -> int:
    """Apply the explicit emergency opt-out to stabilization alone."""
    if os.environ.get("PIPELINE_SKIP_STABILIZATION", "").strip().lower() not in (
            "1", "true", "yes"):
        return 0
    directives = manifest.get("neural_engine_directives") or {}
    skipped = 0
    for label, values in list(directives.items()):
        if isinstance(values, dict) and "stabilize" in values:
            del values["stabilize"]
            skipped += 1
            if not values:
                directives.pop(label)
    if skipped:
        print(
            f"  PIPELINE_SKIP_STABILIZATION set: skipping {skipped} "
            f"authorized stabilization directive(s) for this build. "
            f"The manifest on disk keeps their authorization records; "
            f"other neural directives remain active.",
            file=sys.stderr,
        )
    return skipped


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
            
    # Keep the memory-ceiling escape hatch, but let it suppress only
    # stabilization and leave other Neural Engine work intact.
    _skip_authorized_stabilization(manifest)

    try:
        # Build timeline (this connects to Resolve)
        result = build_timeline(
            manifest=manifest,
            subtitle_overlay_path=inputs.get("subtitle_overlay_path"),
            motion_graphics_path=inputs.get("motion_graphics_path"),
            project_name=_resolve_build_project_name(manifest),
            project_folder=inputs.get("project_folder", ""),
        )
        result["transition_items"] = _transition_items_for_report(manifest)
        
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
            run_timeline_sync_qa(manifest, _resolve_build_project_name(manifest), result.get("timeline_name"), result.get("track_plan"))
        except Exception as e:
            raise RuntimeError(f"Timeline Sync QA Validation Failed: {str(e)}")
            
        # ── Export ──
        # Building the timeline is not shipping the video. Render it to a
        # real file so step 6.02 has something to validate; without this
        # every run ended at distribution_ready: false.
        export = _export_timeline(result.get("timeline_name"), inputs, manifest)

        # The exact Resolve project the build opened: `build_timeline`
        # refuses anything but the exact listed name, so the manifest's
        # base project name is the listed one. 6.02's prompt never sees
        # the manifest, so the skill's `--project` travels here.
        output_payload = {
            "render_output": _render_output_payload(
                result, export,
                resolve_project_name=_resolve_build_project_name(manifest)),
        }

        if "visual_qa" in result and result["visual_qa"]:
            output_payload["visual_qa"] = result["visual_qa"]
        output_payload["__prompt_additions"] = {
            "<!-- VISUAL_QA_INSTRUCTIONS -->": _visual_qa_prompt_addition(
                output_payload.get("visual_qa"))
        }

        # ── The frames the LLM review WATCHES ──
        #
        # Emitted as its own key rather than folded into the verdict:
        # the deterministic half MEASURES and this block is what the
        # review SEES. Absent on a run that drew none - the flag off,
        # no export, or a failed draw - and the handoff then says the
        # picture was not seen rather than claiming it was. The same
        # shape `step_6_02_validate_output` carries as
        # `render_watch_frames`, so the runner's withholding for a
        # harness with no eyes (`window_frames.FRAME_INPUTS`) covers
        # this key with no runner change.
        watch_block = _render_watch_block(
            export.get("output_path", ""),
            inputs.get("project_folder", ""))
        if watch_block:
            output_payload["render_watch_frames"] = watch_block

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
        # The reason goes to STDERR, and the traceback with it.
        #
        # This used to `print()` the reason - to STDOUT - and exit 1. The
        # runner reports STDERR on a non-zero exit and discards stdout,
        # so the one channel carrying the reason was the one the failure
        # path does not read. A build could refuse for a stated cause and
        # arrive as "Step failed (exit 1)" with stderr truncated mid-log
        # and nothing in step_errors, pipeline_log.jsonl or the run
        # summary. Same family as a success tick that reports what it
        # wanted rather than what happened (AGENTS.md 5): the report was
        # decoupled from the event.
        #
        # `str(e)` alone loses where it came from, and these failures are
        # Resolve calls a dozen frames deep, so the traceback goes too.
        import traceback
        print(f"RENDER FAILED: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.stderr.flush()
        # Still on stdout for anything that parses it.
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
            "step": "6.1_render"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
