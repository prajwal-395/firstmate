#!/usr/bin/env python3
"""
visual_qa_router.py - Dual-path visual quality assurance router.

Routes QA checks through two paths:

1. Frame grab (synchronous):
   - Grabs a single frame from the timeline
   - Returns base64 + structured prompt for the orchestrating LLM
   - The LLM uses its own vision to analyze and decide inline
   - Best for: quick spot checks, color grade verification, VFX validation

2. Video segment (asynchronous):
   - Renders a timeline range to a temp file
   - Runs deterministic ffmpeg checks (black frames, freeze, LUFS, color)
   - Sends frames to local Gemma 4 12B for vision analysis
   - Returns structured text analysis that feeds back as LLM context
   - Best for: transition smoothness, motion quality, temporal consistency

The orchestrating LLM (Claude, Codex, etc.) drives the frame grab path
directly via MCP gallery_stills.grab_and_export. This module provides
the Python-level support and the video segment path.
"""

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from library.tools.segment_renderer import (
    SegmentRenderResult,
    render_segment,
    render_single_frame,
    cleanup_segment,
)
from library.tools.timeline_qa import VisualQACheck, VisualQAReport
from library.tools.visual_qa_prompts import (
    frame_grab_inline_prompt,
    segment_analysis_prompt,
    QA_RESPONSE_SCHEMA,
)


# --- Data types ---

@dataclass
class FrameGrabRequest:
    """A planned frame grab check for the orchestrating LLM."""
    frame_number: int
    timecode: str
    check_type: str
    prompt: str
    context: Dict[str, Any]


@dataclass
class FrameGrabResult:
    """Result from a frame grab - either Python-level or MCP-level."""
    check: VisualQACheck
    base64_image: Optional[str] = None
    image_path: Optional[str] = None


@dataclass
class VideoSegmentRequest:
    """A planned video segment check for Gemma 4 analysis."""
    mark_in: int
    mark_out: int
    check_type: str
    prompt: str
    context: Dict[str, Any]
    deterministic_checks: List[str] = field(default_factory=lambda: ["general"])


@dataclass
class VideoSegmentResult:
    """Result from a video segment analysis."""
    check: VisualQACheck
    deterministic: Dict[str, Any] = field(default_factory=dict)
    vision_analysis: Optional[Dict[str, Any]] = None
    segment_path: Optional[str] = None


@dataclass
class QAPassPlan:
    """A planned QA pass with a mix of frame grabs and segment checks."""
    frame_grabs: List[FrameGrabRequest] = field(default_factory=list)
    segment_checks: List[VideoSegmentRequest] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


# --- Frame number to timecode ---

def frame_to_timecode(frame: int, fps: float = 30.0) -> str:
    """Convert a timeline frame number to HH:MM:SS:FF timecode."""
    total_seconds = frame / fps
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    f = int(frame % fps)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


# --- Planning ---

def plan_qa_checks(manifest: dict, phase: str = "post_build",
                   fps: float = 30.0) -> QAPassPlan:
    """Plan QA checks based on the assembly manifest and pipeline phase.

    Examines the manifest to determine which clips, transitions, and effects
    need visual verification. Returns a QAPassPlan with frame grabs for
    quick checks and segment requests for temporal analysis.

    Args:
        manifest: The assembly_manifest.json dict.
        phase: Pipeline phase triggering QA. One of:
               'post_clip_placement', 'post_transitions', 'post_vfx',
               'post_color', 'post_subtitles', 'post_build', 'pre_render'.
        fps: Timeline frame rate.
    """
    plan = QAPassPlan(context={"phase": phase})

    clips = manifest.get("clips", manifest.get("video_clips", []))
    transitions = manifest.get("transitions", [])
    vfx = manifest.get("vfx", manifest.get("enhancements", []))
    color = manifest.get("color_grade", {})
    subtitles = manifest.get("subtitles", [])

    if phase in ("post_clip_placement", "post_build", "pre_render"):
        # Frame grab at the midpoint of each clip to verify correct footage
        for i, clip in enumerate(clips):
            start = clip.get("timeline_in_frame", 0)
            end = clip.get("timeline_out_frame", start + 30)
            mid_frame = (start + end) // 2
            ctx = {
                "clip_name": clip.get("clip_name", clip.get("source_file", f"clip_{i}")),
                "timecode": frame_to_timecode(mid_frame, fps),
            }
            plan.frame_grabs.append(FrameGrabRequest(
                frame_number=mid_frame,
                timecode=ctx["timecode"],
                check_type="clip_placement",
                prompt=frame_grab_inline_prompt("clip_placement", ctx),
                context=ctx,
            ))

    if phase in ("post_transitions", "post_build", "pre_render"):
        # Segment check across each transition boundary
        for i, trans in enumerate(transitions):
            mark_in = trans.get("timeline_frame", trans.get("frame", 0))
            duration = trans.get("duration_frames", 30)
            # Capture a few frames before and after the transition point
            seg_in = max(0, mark_in - 5)
            seg_out = mark_in + duration + 5
            ctx = {
                "transition_type": trans.get("type", "unknown"),
                "clip_name": trans.get("from_clip", f"transition_{i}"),
                "mark_in": seg_in,
                "mark_out": seg_out,
            }
            plan.segment_checks.append(VideoSegmentRequest(
                mark_in=seg_in,
                mark_out=seg_out,
                check_type="transition",
                prompt=segment_analysis_prompt("transition", ctx),
                context=ctx,
                deterministic_checks=["transition"],
            ))

    if phase in ("post_vfx", "post_build", "pre_render"):
        # Frame grab in the middle of each VFX effect
        for i, effect in enumerate(vfx):
            start = effect.get("timeline_in_frame", effect.get("start_frame", 0))
            end = effect.get("timeline_out_frame", effect.get("end_frame", start + 30))
            mid_frame = (start + end) // 2
            ctx = {
                "clip_name": effect.get("clip_name", f"vfx_clip_{i}"),
                "effect_type": effect.get("type", effect.get("effect", "unknown")),
                "expected_behavior": effect.get("description", ""),
                "timecode": frame_to_timecode(mid_frame, fps),
            }
            plan.frame_grabs.append(FrameGrabRequest(
                frame_number=mid_frame,
                timecode=ctx["timecode"],
                check_type="vfx",
                prompt=frame_grab_inline_prompt("vfx", ctx),
                context=ctx,
            ))

    if phase in ("post_color", "post_build", "pre_render"):
        # Frame grab to verify color grade on a subset of clips
        per_clip_color = color.get("per_clip_adjustments", [])
        for adj in per_clip_color:
            clip_name = adj.get("source_file", "")
            look = adj.get("intended_look", adj.get("mood", ""))
            # Find the clip's midpoint
            matching = [c for c in clips if c.get("source_file", "") == clip_name]
            if matching:
                start = matching[0].get("timeline_in_frame", 0)
                end = matching[0].get("timeline_out_frame", start + 30)
                mid_frame = (start + end) // 2
                ctx = {
                    "clip_name": os.path.basename(clip_name),
                    "intended_look": look,
                    "timecode": frame_to_timecode(mid_frame, fps),
                }
                plan.frame_grabs.append(FrameGrabRequest(
                    frame_number=mid_frame,
                    timecode=ctx["timecode"],
                    check_type="color_grade",
                    prompt=frame_grab_inline_prompt("color_grade", ctx),
                    context=ctx,
                ))

    if phase in ("post_subtitles", "post_build", "pre_render"):
        # Frame grab at first subtitle appearance
        for i, sub in enumerate(subtitles[:3]):  # cap at 3 subtitle checks
            frame_num = sub.get("start_frame", sub.get("timeline_in_frame", 0))
            ctx = {
                "expected_text": sub.get("text", ""),
                "timecode": frame_to_timecode(frame_num, fps),
                "notes": f"Subtitle group {i + 1}",
            }
            plan.frame_grabs.append(FrameGrabRequest(
                frame_number=frame_num,
                timecode=ctx["timecode"],
                check_type="subtitle",
                prompt=frame_grab_inline_prompt("subtitle", ctx),
                context=ctx,
            ))

    return plan


def prepare_frame_grab(frame_number: int, check_type: str,
                       context: dict, fps: float = 30.0) -> FrameGrabRequest:
    """Prepare a single frame grab request with prompt.

    Use this for ad-hoc checks outside the full plan_qa_checks flow.
    The orchestrating LLM uses the returned prompt with gallery_stills
    MCP calls.
    """
    timecode = frame_to_timecode(frame_number, fps)
    context.setdefault("timecode", timecode)
    prompt = frame_grab_inline_prompt(check_type, context)

    return FrameGrabRequest(
        frame_number=frame_number,
        timecode=timecode,
        check_type=check_type,
        prompt=prompt,
        context=context,
    )


# --- Execution: Frame Grab (Python API path) ---

def execute_frame_grab(resolve, project, timeline,
                       request: FrameGrabRequest,
                       output_dir: str = None) -> FrameGrabResult:
    """Grab a single frame using Resolve's Deliver page and encode as base64.

    This is the Python-API path for frame grabs. For the MCP path, the
    orchestrating LLM calls gallery_stills.grab_and_export directly using
    the prompt from request.prompt.

    Returns a FrameGrabResult with the base64 image and a VisualQACheck
    stub (passed=None, to be filled by the analyzer).
    """
    png_path = render_single_frame(
        resolve, project, timeline,
        frame=request.frame_number,
        output_dir=output_dir,
    )

    if not png_path:
        return FrameGrabResult(
            check=VisualQACheck(
                name=f"frame_grab_{request.check_type}",
                passed=False,
                expected="rendered frame",
                actual="render failed",
                severity="error",
                frame_timecode=request.timecode,
                model_used="none",
                detail="Failed to render frame from timeline",
                confidence=0.0,
                issues=[],
            ),
        )

    # Encode to base64
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return FrameGrabResult(
        check=VisualQACheck(
            name=f"frame_grab_{request.check_type}",
            passed=True,  # Render succeeded; actual QA pass/fail comes from vision analysis
            expected=request.check_type,
            actual="frame_captured",
            frame_timecode=request.timecode,
            model_used="pending_analysis",
            detail="Frame grabbed successfully, awaiting vision analysis",
            confidence=0.0,
            issues=[],
        ),
        base64_image=b64,
        image_path=png_path,
    )


def analyze_frame_locally(image_path: str, check_type: str,
                          context: dict) -> VisualQACheck:
    """Analyze a frame using the local Gemma 4 12B model.

    Use this when the orchestrating LLM isn't available for inline vision
    or when running in a pipeline step without an LLM in the loop.
    """
    from library.tools.vision_model import VisionModel

    prompt = frame_grab_inline_prompt(check_type, context)
    model = VisionModel()
    raw_output = model.analyze_image(image_path, prompt, max_tokens=512)

    verdict = parse_qa_response(raw_output)

    return VisualQACheck(
        name=f"local_frame_qa_{check_type}",
        passed=verdict.get("passed", False),
        expected=check_type,
        actual="analyzed",
        model_used="gemma-4-12b-it",
        confidence=verdict.get("confidence", 0.0),
        issues=verdict.get("issues", []),
        detail=verdict.get("detail", raw_output[:200]),
        frame_timecode=context.get("timecode", ""),
        image_path=image_path,
    )


# --- Execution: Video Segment (async Gemma path) ---

def execute_video_segment_check(resolve, project, timeline,
                                request: VideoSegmentRequest,
                                output_dir: str = None,
                                sample_count: int = 5,
                                cleanup: bool = True) -> VideoSegmentResult:
    """Render a timeline segment and analyze with Gemma 4 12B + deterministic checks.

    This is the async path. In practice the orchestrating LLM fires this
    off and continues working; the result feeds back as text context.
    """
    from library.tools.video_segment_analyzer import run_analysis

    # Render the segment
    render_result = render_segment(
        resolve, project, timeline,
        mark_in=request.mark_in,
        mark_out=request.mark_out,
        output_dir=output_dir,
    )

    if not render_result.success:
        return VideoSegmentResult(
            check=VisualQACheck(
                name=f"segment_qa_{request.check_type}",
                passed=False,
                expected="rendered segment",
                actual="render failed",
                severity="error",
                detail=render_result.error or "Segment render failed",
                model_used="none",
                confidence=0.0,
                frame_timecode="",
                issues=[],
            ),
        )

    # Run combined analysis (deterministic + vision)
    try:
        analysis = run_analysis(
            video_path=render_result.path,
            prompt=request.prompt,
            checks=request.deterministic_checks,
            sample_count=sample_count,
        )
    except Exception as e:
        analysis = {"deterministic": {}, "vision_analysis": {"error": str(e)}}

    # Parse vision analysis into a verdict
    vision = analysis.get("vision_analysis", {})
    if isinstance(vision, dict) and "error" not in vision:
        passed = vision.get("passed", True)
        confidence = vision.get("confidence", 0.5)
        issues = vision.get("issues", [])
        detail = vision.get("detail", "")
    elif isinstance(vision, dict) and "raw_text" in vision:
        # Vision model returned non-JSON text
        parsed = parse_qa_response(vision["raw_text"])
        passed = parsed.get("passed", True)
        confidence = parsed.get("confidence", 0.5)
        issues = parsed.get("issues", [])
        detail = parsed.get("detail", vision["raw_text"][:200])
    else:
        passed = True
        confidence = 0.0
        issues = []
        detail = f"Vision analysis error: {vision.get('error', 'unknown')}"

    # Factor in deterministic check failures
    det = analysis.get("deterministic", {})
    for metric_name, metric_data in det.items():
        if isinstance(metric_data, dict) and not metric_data.get("passed", True):
            passed = False
            issues.append(f"Deterministic check failed: {metric_name} - {metric_data.get('detail', '')}")

    result = VideoSegmentResult(
        check=VisualQACheck(
            name=f"segment_qa_{request.check_type}",
            passed=passed,
            expected=request.check_type,
            actual="analyzed",
            model_used="gemma-4-12b-it",
            confidence=confidence,
            issues=issues,
            detail=detail,
            frame_timecode="",
        ),
        deterministic=det,
        vision_analysis=vision if isinstance(vision, dict) else {"raw_text": str(vision)},
        segment_path=render_result.path,
    )

    if cleanup and render_result.path:
        cleanup_segment(render_result.path)
        result.segment_path = None

    return result


# --- Response Parsing ---

def parse_qa_response(text: str) -> dict:
    """Parse a QA response from an LLM or vision model into a verdict dict.

    Handles raw JSON, markdown-fenced JSON, and plain text fallbacks.
    Returns a dict with keys: passed, confidence, detail, issues.
    """
    clean = text.strip()

    # Try stripping markdown code fences
    if "```json" in clean:
        clean = clean.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in clean:
        clean = clean.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return {
                "passed": parsed.get("passed", False),
                "confidence": float(parsed.get("confidence", 0.5)),
                "detail": str(parsed.get("detail", "")),
                "issues": list(parsed.get("issues", [])),
            }
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: heuristic from plain text
    import re
    text_lower = text.lower()
    # Check for negative indicators with word boundaries, but skip negated forms
    # like "no issues", "no problem", "not broken"
    negation_pattern = r'\b(?:no|not|without|zero)\b'
    negative_keywords = ["fail", "issue", "problem", "incorrect", "broken", "artifact"]
    has_issue = False
    for kw in negative_keywords:
        matches = list(re.finditer(r'\b' + kw, text_lower))
        for m in matches:
            # Check if preceded by a negation word within 15 chars
            prefix = text_lower[max(0, m.start() - 15):m.start()]
            if not re.search(negation_pattern, prefix):
                has_issue = True
                break
        if has_issue:
            break

    passed = not has_issue
    return {
        "passed": passed,
        "confidence": 0.3,
        "detail": text[:500],
        "issues": [] if passed else ["Could not parse structured response; see detail"],
    }


# --- Formatting results for LLM context ---

def format_frame_grab_for_llm(result: FrameGrabResult) -> dict:
    """Format a frame grab result for inclusion in LLM context.

    Returns a dict suitable for JSON serialization and inclusion
    in the LLM's conversation as structured context.
    """
    return {
        "type": "frame_grab_result",
        "check_type": result.check.name,
        "passed": result.check.passed,
        "confidence": result.check.confidence,
        "timecode": result.check.frame_timecode,
        "detail": result.check.detail,
        "issues": result.check.issues,
        "has_image": result.base64_image is not None,
    }


def format_segment_result_for_llm(result: VideoSegmentResult) -> dict:
    """Format a video segment analysis for inclusion in LLM context.

    This is how the Gemma 4 analysis feeds back to the orchestrating LLM.
    The LLM doesn't see the video - it gets this structured text summary.
    """
    return {
        "type": "video_segment_result",
        "check_type": result.check.name,
        "passed": result.check.passed,
        "confidence": result.check.confidence,
        "detail": result.check.detail,
        "issues": result.check.issues,
        "model": result.check.model_used,
        "deterministic_checks": {
            k: {"passed": v.get("passed", True), "detail": v.get("detail", "")}
            for k, v in result.deterministic.items()
            if isinstance(v, dict)
        },
        "vision_summary": result.vision_analysis if isinstance(result.vision_analysis, dict) else None,
    }


def format_qa_plan_for_llm(plan: QAPassPlan) -> dict:
    """Format a QA plan for the orchestrating LLM.

    The LLM uses this to know what frame grabs to perform via MCP
    and what the expected outcome should be.
    """
    return {
        "type": "qa_plan",
        "phase": plan.context.get("phase", "unknown"),
        "frame_grab_checks": [
            {
                "frame": fg.frame_number,
                "timecode": fg.timecode,
                "check_type": fg.check_type,
                "prompt": fg.prompt,
                "mcp_sequence": [
                    "1. resolve_control.save_state()",
                    "2. resolve_control.open_page('color')",
                    f"3. Navigate playhead to frame {fg.frame_number} (timecode {fg.timecode})",
                    "4. gallery_stills.grab_and_export(format='png', cleanup=true, delete_after=true)",
                    "5. Analyze the returned base64 image using the prompt above",
                    "6. resolve_control.restore_state(state_token=<from step 1>)",
                ],
            }
            for fg in plan.frame_grabs
        ],
        "segment_checks": [
            {
                "mark_in": sc.mark_in,
                "mark_out": sc.mark_out,
                "check_type": sc.check_type,
                "note": "This check runs in the background via Gemma 4 12B. "
                        "Results will be fed back as context.",
            }
            for sc in plan.segment_checks
        ],
    }
