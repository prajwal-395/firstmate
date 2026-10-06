"""verify_render: the deterministic gate over a rendered file.

Wraps the file-only half of `library/tools/render_qa.py` - the checks
that read the master ffmpeg produced and need no Resolve, no manifest
and no model. A skill entry point, so a step (or the pipeline on its
behalf) can invoke it by import or by shell:

    python3 -m library.skills.verify_render.skill \
        --video /path/to/master.mp4 --project-folder /path/to/project \
        --step-id validate [--expected-duration 63.2]
        [--expected-resolution 1080 1920] [--expected-fps 23.976]
        [--dirty-receipt <touch receipt> ...]

`--dirty-receipt` scopes the run to what those touches changed
(`library/tools/dirty_regions.py`): black, freeze and silence re-read only
the dirty spans of their domain, loudness runs only when audio is dirty,
and the stream probes always run. What was not re-checked is NAMED in
`not_rechecked` - it is not passed. A receipt with no dirty block, or a
render older than the touch, falls back to the whole file.

The scoped re-check is not only technical QA (gap E8). `run_scoped_recheck`
re-runs, on the touched spans only, the three checks a touch could have
broken: caption pairing (`reel_hearing`), B-roll correspondence
(`broll_correspondence`) and caption quality (`render_qa`'s legibility and
obscuring). All three REPORT - they do not block, the same ruling every
report-only check in this pipeline carries. A check whose inputs are absent
(a scoped re-check with no timeline or transcript to read) is NAMED in
`not_rechecked`, never passed.

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/verify_render.json`
carrying the per-check verdicts. The receipt is what the must-check
rule reads back: a model asserting it checked writes no receipt, so a
self-reported check fails the gate the way a gate that cannot fail
should. See `library/tools/pipeline_skills.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

SKILL_NAME = "verify_render"


def _measured_project_fps(project_folder: str) -> Optional[float]:
    """The frame rate the catalog measured off the footage, or None."""
    import os

    from library.tools import capability_outputs
    from library.tools.project_layout import PIPELINE_DATA_FILE
    path = os.path.join(project_folder or "", PIPELINE_DATA_FILE)
    if not project_folder or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    fps = capability_outputs.value(state, "footage.catalog", "project_fps")
    return float(fps) if fps else None


def run(video_path: str,
        project_folder: str,
        step_id: str,
        expected_duration: Optional[float] = None,
        expected_resolution: Optional[List[int]] = None,
        expected_fps: Optional[float] = None,
        declared_black_beats: Optional[List] = None,
        dirty_receipts: Optional[List[str]] = None,
        declared_ending_black_spans: Optional[List] = None,
        declared_silence_spans: Optional[List] = None,
        assembly_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run the deterministic render checks and record the receipt.

    Returns a verdict dict with `passed`, per-check `checks`, and the
    `receipt` path. `passed` is False when any check fails - this skill
    GATES. Raises FileNotFoundError when the video is absent (an absent
    render is not a passing one) and records the refusal in the receipt.
    """
    from library.tools import pipeline_skills, render_qa

    import os
    if not video_path or not os.path.exists(video_path):
        verdict: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "checks": [],
            "issues": [f"render file not found: {video_path}"],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    results = []
    not_rechecked: List[str] = []
    scope = "whole file"
    try:
        if assembly_manifest is not None:
            from library.tools.spine_contract import declared_black_beat_ranges

            ending_spans = render_qa.declared_ending_spans(assembly_manifest)
            if declared_black_beats is None:
                declared_black_beats = declared_black_beat_ranges(
                    assembly_manifest.get("_spine_blocks") or [])
            if declared_ending_black_spans is None:
                declared_ending_black_spans = ending_spans["black"]
            if declared_silence_spans is None:
                declared_silence_spans = ending_spans["silence"]
        from library.tools.dirty_regions import scope_for
        dirty = scope_for(video_path, dirty_receipts)
        if dirty is None or dirty["whole_reel"]:
            if dirty is not None:
                scope = f"whole file: {dirty['whole_reel_reason']}"
            results.append(render_qa.measure_lufs(video_path))
            results.append(render_qa.detect_black_frames(
                video_path, declared_beats=declared_black_beats,
                declared_ending_spans=declared_ending_black_spans))
            results.append(render_qa.detect_freeze_frames(video_path))
            results.append(render_qa.measure_silence_under_picture(
                video_path, declared_spans=declared_silence_spans))
        else:
            from library.tools.dirty_regions import AUDIO, describe
            scope = f"scoped to the touch: {describe(dirty)}"
            if AUDIO in dirty["dirty_domains"]:
                results.append(render_qa.measure_lufs(video_path))
            else:
                not_rechecked.append("lufs")
            scoped, skipped = render_qa.run_scoped_render_qa(
                video_path, dirty, declared_black_beats,
                declared_ending_black_spans, declared_silence_spans)
            results += scoped
            not_rechecked += skipped
            # Gap E8: the scoped re-check is not only technical QA. Re-run,
            # on the touched spans only, the caption pairing, B-roll
            # correspondence and caption quality checks a touch could have
            # broken. Report-only - they do not block.
            recheck, recheck_skipped = run_scoped_recheck(
                video_path, dirty, assembly_manifest=assembly_manifest,
                project_folder=project_folder or "")
            results += recheck
            not_rechecked += recheck_skipped
        # The frame the render was built at: stated by the caller, else
        # read off the project's own declaration
        # (`library/tools/delivery_format.py` - project override over
        # template over vertical). Never a shape literal here: a
        # `.get`-style fallback to vertical would grade a render
        # against a guessed frame.
        frame = list(expected_resolution) if expected_resolution else None
        if frame is None:
            from library.tools.delivery_format import (
                resolve_delivery_format)
            frame = resolve_delivery_format(project_folder or None)
        width, height = frame[:2]
        results.append(render_qa.verify_resolution(
            video_path, expected_width=width, expected_height=height))
        # The rate, the same way: stated by the caller, else the rate the
        # project's catalog MEASURED off its footage. A hardcoded 30 here
        # failed a correct 23.976 reel (Reel 01, 2026-10-03).
        if expected_fps is None:
            expected_fps = _measured_project_fps(project_folder)
        if expected_fps is None:
            results.append(render_qa.framerate_not_checked(
                "no --expected-fps and no measured project_fps in the "
                "project's catalog"))
        else:
            results.append(render_qa.verify_framerate(
                video_path, expected_fps=expected_fps))
        if expected_duration is not None:
            results.append(render_qa.verify_duration(
                video_path, expected_duration))
        results.append(render_qa.verify_audio_streams(video_path))
    except FileNotFoundError:
        verdict: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "checks": [],
            "issues": [f"render file not found: {video_path}"],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    checks = [
        {"name": r.metric, "passed": r.passed, "detail": r.detail}
        for r in results
    ]
    issues = [f"{r.metric}: {r.detail}" for r in results if not r.passed]
    verdict = {
        "skill": SKILL_NAME,
        "passed": all(r.passed for r in results),
        "video_path": video_path,
        "checks": checks,
        "issues": issues,
        "scope": scope,
        "not_rechecked": not_rechecked,
    }
    verdict["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, verdict)
    return verdict


def _locate_plan(project_folder: str, timeline=None, transcript=None):
    """The timeline and transcript docs for this project, or (None, None).

    The timeline is the newest serialized timeline `build_reels` wrote
    under `pipeline_output/review/`; the transcript is
    `timeline_transcript.transcript_path`. Either is None when it cannot
    be read - a scoped re-check then NAMES the check rather than guessing.
    """
    import glob
    import os

    if timeline is None and project_folder:
        from library.tools.project_layout import Area, ProjectLayout
        review = ProjectLayout(project_folder).read_dir(Area.REVIEW)
        if review.is_dir():
            candidates = sorted(
                glob.glob(os.path.join(str(review), "*.timeline.json")),
                key=os.path.getmtime, reverse=True)
            if candidates:
                with open(candidates[0], encoding="utf-8") as handle:
                    timeline = json.load(handle)
    if transcript is None and project_folder:
        from library.tools.timeline_transcript import transcript_path
        path = transcript_path(project_folder)
        if path and os.path.exists(str(path)):
            with open(str(path), encoding="utf-8") as handle:
                transcript = json.load(handle)
    return timeline, transcript


def _overlay_segments(manifest: Optional[Dict[str, Any]]):
    """The rendered overlays the manifest places over the picture.

    The same segments `step_6_02_validate_output` builds for the whole-file
    gate, read here so the scoped re-check measures the same geometry. None
    when the manifest names no overlay track at all - a different claim
    from one that carries the tracks and declares them empty.
    """
    from library.tools.render_qa import OverlaySegment

    keys = ("subtitle_overlay", "motion_graphics_overlay", "timed_text_overlay")
    if not any(key in (manifest or {}) for key in keys):
        return None
    fps = float(((manifest or {}).get("project") or {}).get("frame_rate") or 30.0)
    segments = []
    for key in keys:
        track = (manifest or {}).get(key) or {}
        for seg in track.get("segments") or []:
            path = seg.get("overlay_path")
            start = seg.get("timeline_start")
            end = seg.get("timeline_end")
            if not path or start is None or end is None or end <= start:
                continue
            segments.append(OverlaySegment(
                str(path), float(start), float(end),
                float(seg.get("source_in_frame") or 0.0) / fps,
                kind=key))
    return segments


def run_scoped_recheck(video_path: str, dirty: Dict[str, Any], *,
                       assembly_manifest: Optional[Dict[str, Any]] = None,
                       project_folder: str = "", model=None, timeline=None,
                       transcript=None, overlay_segments=None
                       ) -> Tuple[List, List]:
    """Re-run, on the dirty spans only, the checks a touch could have broken.

    Gap E8: the scoped re-check was technical-QA only (black, freeze,
    silence - `run_scoped_render_qa`). This extends the dirty-region seam
    to the three checks a touch could have broken that are NOT located
    detectors:

    - caption pairing (`reel_hearing.pairing_rows`) - a card the touch
      moved off the speech its own filename declares.
    - B-roll correspondence (`broll_correspondence`) - a cutaway the touch
      swapped in that does not illustrate the speech it covers.
    - caption quality (`render_qa`'s legibility and obscuring) - a caption
      the touch made illegible or obscuring.

    Each is scoped to the dirty PICTURE spans' CORE - the frames the touch
    changed - so a defect the touch did not cause is not this touch's. All
    three REPORT (`passed` stays True); whether one blocks is the captain's
    call, the same ruling `reel_hearing.GATES`, `BROLL_CORRESPONDENCE_GATES`
    and `CAPTION_QUALITY_GATES` carry.

    Returns `(results, not_rechecked)`. A check whose inputs are absent is
    named in `not_rechecked`, never passed.
    """
    from library.tools import broll_correspondence, dirty_regions, \
        reel_hearing, render_qa

    results, not_rechecked = [], []
    core = [(s[2], s[3]) for s in
            dirty_regions.spans_for(dirty, [dirty_regions.PICTURE])]

    def in_dirty(start: float, end: float) -> bool:
        return any(start < hi and end > lo for lo, hi in core)

    if not core:
        # The touch changed no picture, so no caption, cutaway or overlay
        # is one it could have broken.
        return results, ["caption_pairing", "broll_correspondence",
                         "caption_quality"]

    # ── Caption pairing (reel_hearing) and B-roll correspondence (E3) ──
    # Both read the timeline and the transcript; neither is measurable
    # without them.
    if timeline is None or transcript is None:
        timeline, transcript = _locate_plan(project_folder,
                                            timeline, transcript)
    if timeline is None or transcript is None:
        not_rechecked.append("caption_pairing")
        not_rechecked.append("broll_correspondence")
    else:
        try:
            pairing = reel_hearing.pairing_rows(timeline, transcript)
            mispaired = [row for row in pairing["mispaired"]
                         if in_dirty(row["reel_start"], row["reel_end"])]
            detail = (f"{len(mispaired)} of {pairing['caption_cards']} "
                      f"caption card(s) are mispaired in the dirty span(s)")
            if mispaired:
                first = mispaired[0]
                detail += (f" - first at {first['reel_start']:.2f}s: "
                           f"{first['detail']}")
                detail += " - REPORTED ONLY; see reel_hearing.GATES"
            results.append(render_qa.RenderQAResult(
                reel_hearing.PAIRING_METRIC, True, {
                    "caption_cards": pairing["caption_cards"],
                    "mispaired": mispaired,
                }, {"gates": False, "spans": [list(s) for s in core]},
                "warning" if mispaired else "info", detail))
        except Exception:
            not_rechecked.append("caption_pairing")
        try:
            cutaways = broll_correspondence.placed_cutaways(
                assembly_manifest or {})
            dirty_cutaways = [c for c in cutaways
                              if in_dirty(c.timeline_in, c.timeline_out)]
            if not dirty_cutaways:
                not_rechecked.append("broll_correspondence")
            else:
                results.append(
                    broll_correspondence.measure_broll_correspondence(
                        video_path, assembly_manifest or {}, timeline,
                        transcript, model=model, project_folder=project_folder,
                        cutaways=dirty_cutaways))
        except Exception:
            not_rechecked.append("broll_correspondence")

    # ── Caption quality (E4): legibility and obscuring ────────────────
    if overlay_segments is None and assembly_manifest is not None:
        overlay_segments = _overlay_segments(assembly_manifest)
    caption_segs = render_qa._subtitle_overlay_segments(overlay_segments)
    dirty_segs = [s for s in caption_segs if in_dirty(s.start, s.end)]
    if not dirty_segs:
        not_rechecked.append("caption_quality")
    else:
        results.append(render_qa.measure_caption_legibility(
            video_path, dirty_segs))
        obscuring = render_qa.measure_caption_obscuring(
            video_path, dirty_segs)
        results.append(obscuring)
        # Close the loop (evaluation track): a caption the touch made
        # obscuring is not only reported - the correction loop moves it by
        # the measured clearance, writes the corrected placement onto the
        # manifest segment the build reads (`tight_box.placement`, which
        # `place_overlay_segment` reads and refuses when absent), and
        # re-measures on an offline re-render of the overlay with the
        # correction applied.  The write lands on the manifest dict the
        # pipeline holds, so the post-step state dump carries it to the
        # next build; the correction record below carries the placement
        # too, so the finding survives even where the inputs were a
        # copy.  The face detections are reused from
        # measure_caption_obscuring - Haar over a span is the expensive
        # part and running it twice is the defect this removes.
        from library.tools.caption_correction import correct_caption_obscuring

        def _write_placement(seg, placement):
            track = (assembly_manifest or {}).get("subtitle_overlay", {})
            for manifest_seg in track.get("segments", []):
                if manifest_seg.get("overlay_path") == seg.path:
                    tight = manifest_seg.setdefault("tight_box", {})
                    tight["placement"] = placement
                    return

        obscuring_value = obscuring.value
        corrections = correct_caption_obscuring(
            video_path, dirty_segs,
            obscuring_result=(obscuring_value
                              if isinstance(obscuring_value, dict) else None),
            write_placement=_write_placement)
        if corrections:
            results.append(render_qa.RenderQAResult(
                "caption_obscuring_correction",
                all(c["passed"] for c in corrections),
                {"corrections": corrections},
                {"gates": False},
                "warning" if not all(c["passed"] for c in corrections)
                else "info",
                f"{len(corrections)} obscuring caption(s) corrected by "
                f"the loop" if all(c["passed"] for c in corrections)
                else f"{len(corrections)} obscuring caption(s) could not "
                     f"be fully corrected - see the loop record"))

    return results, not_rechecked


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic render gate (skill: verify_render).")
    parser.add_argument("--video", required=True)
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--expected-duration", type=float, default=None)
    parser.add_argument("--expected-resolution", type=int, nargs=2,
                        default=None, metavar=("W", "H"))
    parser.add_argument("--expected-fps", type=float, default=None)
    parser.add_argument(
        "--assembly-manifest", default=None,
        help="compiled reel manifest; render QA allowances are derived "
             "from its ending effect and silent end card")
    parser.add_argument("--dirty-receipt", action="append", default=None,
                        help="a touch receipt; re-check only what it "
                             "changed (repeatable)")
    args = parser.parse_args(argv)

    assembly_manifest = None
    if args.assembly_manifest:
        with open(args.assembly_manifest, encoding="utf-8") as handle:
            assembly_manifest = json.load(handle)
        if not isinstance(assembly_manifest, dict):
            parser.error("--assembly-manifest must contain a JSON object")
    verdict = run(args.video, args.project_folder, args.step_id,
                  expected_duration=args.expected_duration,
                  expected_resolution=args.expected_resolution,
                  expected_fps=args.expected_fps,
                  assembly_manifest=assembly_manifest,
                  dirty_receipts=args.dirty_receipt)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
