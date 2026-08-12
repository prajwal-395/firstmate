#!/usr/bin/env python3
"""
qa_feedback_loop.py - Retry/adjustment coordinator for visual QA.

Runs visual QA checks, evaluates verdicts, and coordinates retries.
Works with both the frame grab (sync) and video segment (async) paths.

Usage from the orchestrating LLM:

    loop = QAFeedbackLoop(resolve, project, timeline, config)
    result = loop.run(plan)

    # result.passed indicates if all checks passed
    # result.iterations shows the history of check-adjust-recheck cycles
    # result.llm_context_summary() gives a formatted dict for LLM context

Usage from pipeline steps:

    loop = QAFeedbackLoop(resolve, project, timeline)
    result = loop.run_single_check(
        frame_number=450, check_type="color_grade",
        context={"intended_look": "warm cinematic"},
    )
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from library.tools.timeline_qa import VisualQACheck, VisualQAReport
from library.tools.visual_qa_prompts import adjustment_suggestion_prompt
from library.tools.visual_qa_router import (
    FrameGrabRequest,
    FrameGrabResult,
    VideoSegmentRequest,
    VideoSegmentResult,
    QAPassPlan,
    execute_frame_grab,
    execute_video_segment_check,
    analyze_frame_locally,
    prepare_frame_grab,
    format_frame_grab_for_llm,
    format_segment_result_for_llm,
    parse_qa_response,
)


# --- Configuration ---

@dataclass
class QAFeedbackConfig:
    """Configuration for the QA feedback loop."""
    max_retries: int = 3
    frame_grab_enabled: bool = True
    video_segment_enabled: bool = True
    # Confidence threshold below which a "passed" verdict is still retried
    min_confidence: float = 0.6
    # Whether to run local Gemma analysis on frame grabs
    # (vs. just returning base64 for the orchestrating LLM)
    local_frame_analysis: bool = True
    # Segment render settings
    segment_resolution: str = "720p"
    segment_sample_count: int = 5

    @classmethod
    def from_manifest(cls, manifest: dict) -> "QAFeedbackConfig":
        """Load config from the process manifest's visual_qa block."""
        vqa = manifest.get("visual_qa", {})
        return cls(
            max_retries=vqa.get("max_retries", 3),
            frame_grab_enabled=vqa.get("frame_grab_enabled", True),
            video_segment_enabled=vqa.get("video_segment_enabled", True),
        )


# --- Iteration tracking ---

@dataclass
class FeedbackIteration:
    """Record of a single check-evaluate-adjust cycle."""
    attempt: int
    check_type: str
    route: str  # "frame_grab" or "video_segment"
    check_result: VisualQACheck
    passed: bool
    adjustment_prompt: Optional[str] = None
    adjustments_applied: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0


@dataclass
class FeedbackLoopResult:
    """Final result from a complete feedback loop run."""
    passed: bool
    total_attempts: int
    iterations: List[FeedbackIteration] = field(default_factory=list)
    final_report: Optional[VisualQAReport] = None

    def llm_context_summary(self) -> dict:
        """Format the loop result as structured context for the orchestrating LLM."""
        return {
            "type": "qa_feedback_loop_result",
            "passed": self.passed,
            "total_attempts": self.total_attempts,
            "checks": [
                {
                    "attempt": it.attempt,
                    "check_type": it.check_type,
                    "route": it.route,
                    "passed": it.passed,
                    "confidence": it.check_result.confidence,
                    "issues": it.check_result.issues,
                    "detail": it.check_result.detail,
                    "adjustments": it.adjustments_applied,
                }
                for it in self.iterations
            ],
        }

    def failed_checks(self) -> List[FeedbackIteration]:
        """Return only the iterations that failed."""
        return [it for it in self.iterations if not it.passed]


# --- Feedback Loop ---

class QAFeedbackLoop:
    """Coordinates visual QA check-adjust-recheck cycles.

    The loop runs checks, evaluates whether they pass, and if not,
    generates an adjustment suggestion prompt. The adjustment is either
    applied by the orchestrating LLM (MCP path) or by a provided
    callback (pipeline path).
    """

    def __init__(self, resolve, project, timeline,
                 config: QAFeedbackConfig = None):
        self.resolve = resolve
        self.project = project
        self.timeline = timeline
        self.config = config or QAFeedbackConfig()
        self._iterations: List[FeedbackIteration] = []

    def run(self, plan: QAPassPlan,
            on_adjustment: Callable[[str, dict], None] = None) -> FeedbackLoopResult:
        """Execute a full QA plan with retry loops.

        Args:
            plan: QA pass plan from visual_qa_router.plan_qa_checks().
            on_adjustment: Optional callback invoked when an adjustment is
                          needed. Receives (adjustment_prompt, context).
                          If None, adjustments are logged but not applied.

        Returns:
            FeedbackLoopResult with all iterations and final verdict.
        """
        self._iterations = []
        all_passed = True

        # Run frame grab checks
        if self.config.frame_grab_enabled:
            for request in plan.frame_grabs:
                passed = self._run_frame_grab_loop(request, on_adjustment)
                if not passed:
                    all_passed = False

        # Run video segment checks
        if self.config.video_segment_enabled:
            for request in plan.segment_checks:
                passed = self._run_segment_loop(request, on_adjustment)
                if not passed:
                    all_passed = False

        # Build final report
        visual_checks = [it.check_result for it in self._iterations]
        report = VisualQAReport(
            station="visual_qa_feedback_loop",
            passed=all_passed,
            visual_checks=visual_checks,
        )

        return FeedbackLoopResult(
            passed=all_passed,
            total_attempts=len(self._iterations),
            iterations=list(self._iterations),
            final_report=report,
        )

    def run_single_check(self, frame_number: int, check_type: str,
                         context: dict, fps: float = 30.0,
                         on_adjustment: Callable[[str, dict], None] = None) -> FeedbackLoopResult:
        """Run a single frame grab check with retry loop.

        Convenience method for ad-hoc checks outside a full QA plan.
        """
        request = prepare_frame_grab(frame_number, check_type, context, fps)
        self._iterations = []
        passed = self._run_frame_grab_loop(request, on_adjustment)

        report = VisualQAReport(
            station=f"single_check_{check_type}",
            passed=passed,
            visual_checks=[it.check_result for it in self._iterations],
        )

        return FeedbackLoopResult(
            passed=passed,
            total_attempts=len(self._iterations),
            iterations=list(self._iterations),
            final_report=report,
        )

    # --- Internal loop implementations ---

    def _run_frame_grab_loop(self, request: FrameGrabRequest,
                             on_adjustment: Callable = None) -> bool:
        """Run a frame grab check with up to max_retries attempts."""
        for attempt in range(1, self.config.max_retries + 1):
            t0 = time.time()

            # Execute the frame grab
            grab_result = execute_frame_grab(
                self.resolve, self.project, self.timeline, request,
            )

            # If the render itself failed, don't retry
            if not grab_result.check.passed and grab_result.base64_image is None:
                iteration = FeedbackIteration(
                    attempt=attempt,
                    check_type=request.check_type,
                    route="frame_grab",
                    check_result=grab_result.check,
                    passed=False,
                    duration_seconds=time.time() - t0,
                )
                self._iterations.append(iteration)
                return False

            # Analyze the frame
            if self.config.local_frame_analysis and grab_result.image_path:
                check = analyze_frame_locally(
                    grab_result.image_path, request.check_type, request.context,
                )
            else:
                # When local analysis is off, the check stub from execute_frame_grab
                # indicates capture succeeded. The orchestrating LLM handles analysis.
                check = grab_result.check

            # Evaluate
            passed, should_retry = self._evaluate(check)

            # Build adjustment prompt if needed
            adj_prompt = None
            adjustments = []
            if not passed and should_retry and attempt < self.config.max_retries:
                adj_prompt = adjustment_suggestion_prompt(
                    request.check_type, check.issues,
                    attempt, self.config.max_retries,
                )
                if on_adjustment:
                    on_adjustment(adj_prompt, request.context)
                    adjustments.append(f"adjustment_callback_invoked_attempt_{attempt}")

            iteration = FeedbackIteration(
                attempt=attempt,
                check_type=request.check_type,
                route="frame_grab",
                check_result=check,
                passed=passed,
                adjustment_prompt=adj_prompt,
                adjustments_applied=adjustments,
                duration_seconds=time.time() - t0,
            )
            self._iterations.append(iteration)

            if passed or not should_retry:
                return passed

        return False

    def _run_segment_loop(self, request: VideoSegmentRequest,
                          on_adjustment: Callable = None) -> bool:
        """Run a video segment check with up to max_retries attempts."""
        for attempt in range(1, self.config.max_retries + 1):
            t0 = time.time()

            result = execute_video_segment_check(
                self.resolve, self.project, self.timeline,
                request,
                sample_count=self.config.segment_sample_count,
                cleanup=True,
            )

            check = result.check
            passed, should_retry = self._evaluate(check)

            adj_prompt = None
            adjustments = []
            if not passed and should_retry and attempt < self.config.max_retries:
                adj_prompt = adjustment_suggestion_prompt(
                    request.check_type, check.issues,
                    attempt, self.config.max_retries,
                )
                if on_adjustment:
                    on_adjustment(adj_prompt, request.context)
                    adjustments.append(f"adjustment_callback_invoked_attempt_{attempt}")

            iteration = FeedbackIteration(
                attempt=attempt,
                check_type=request.check_type,
                route="video_segment",
                check_result=check,
                passed=passed,
                adjustment_prompt=adj_prompt,
                adjustments_applied=adjustments,
                duration_seconds=time.time() - t0,
            )
            self._iterations.append(iteration)

            if passed or not should_retry:
                return passed

        return False

    def _evaluate(self, check: VisualQACheck) -> Tuple[bool, bool]:
        """Evaluate a QA check result.

        Returns:
            (passed, should_retry): passed is True if the check passes.
            should_retry is True if a retry might help (e.g., low confidence
            pass, or fixable issues).
        """
        if not check.passed:
            # Failed - retry if there are specific issues to fix
            should_retry = len(check.issues) > 0
            return False, should_retry

        if check.confidence < self.config.min_confidence:
            # Passed but with low confidence - worth a retry
            return False, True

        return True, False

class LLMStepQA:
    """Coordinates QA checks and retries for generic LLM pipeline steps."""
    
    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries
        
    def run_checks(self, node_id: str, output: dict, manifest: dict, validate_fn: Callable) -> Tuple[bool, str]:
        """
        Run QA checks for an LLM step's output.
        Returns (passed, feedback_string).
        """
        try:
            # 1. Run schema validation (which raises RuntimeError on failure)
            if validate_fn:
                validate_fn(node_id, output, manifest)
                
            # Future: Dynamically load and run any other checks from library.tools.qa
            # if they apply to this node_id.
            
            return True, ""
        except Exception as e:
            return False, str(e)

