"""Per-deliverable QC: one file judged against its own spec. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation. The agency addition to this lane derives a dependency graph
so shared work is done once; this module is the QC half of each graph
leaf - duration, black frames and loudness measured against the
deliverable's SELECTED spec, reusing render QA and the grade delivery
gate, with the existing caption check called as-is.

What was wrong before
--------------------
The gate's loudness default came from the registry, but nothing bound
all three per-deliverable measurements to one spec object: duration was
checked against the timeline, black frames against declared beats, and
loudness against a default that could drift from both. A render
mastered to the wrong spec's target passed two of three checks, and the
grade landing was judged on a different path from the file checks, so
no single verdict said "this deliverable meets its platform's spec".

So this module runs the three file checks against the SAME
:class:`DeliverySpec` - its loudness target, tolerance and true-peak
ceiling go to the loudness check - then the grade delivery gate and,
when caption data is supplied, the existing subtitle-timing check. One
verdict per deliverable, each check's own pass/fail preserved.

What this module does not do
----------------------------
It does not select the spec: the caller passes the :class:`DeliverySpec`
the project's named platform resolved (``delivery_spec`` owns that
choice), so this module never defaults, guesses or falls back. It does
not render, reframe or master anything. The grade finding and the
caption findings REPORT, never gate - ``measure_grade_delivery`` says
so itself (a shortfall confounds grade, pipeline and encode), and the
caption check is called exactly as it exists, never re-implemented as
a second gate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from library.tools.delivery_spec import DeliverySpec
from library.tools.render_qa import RenderQAResult


@dataclass(frozen=True)
class DeliverableQCVerdict:
    """One deliverable's QC verdict, against its own spec.

    ``platform`` names the spec row judged. ``passed`` is the file
    checks only - duration, black frames and loudness. ``grade`` and
    ``captions`` are the reporting halves: present, said loudly, never
    gating. ``captions`` is None when no caption data was supplied, and
    a supplied-but-empty caption document is judged as-is by the
    existing check, not skipped.
    """

    platform: str
    passed: bool
    duration: RenderQAResult
    black_frames: RenderQAResult
    loudness: RenderQAResult
    grade: RenderQAResult
    captions: Optional[List[RenderQAResult]]


def measure_loudness(video_path: str, spec: DeliverySpec) -> RenderQAResult:
    """The delivered file's loudness, against the spec's own target.

    The one measurement this module exists to make, exposed on its own
    so that the delivery VERDICT (`library.tools.reel_deliver`) grades
    the same number by the same rule instead of writing a second one.
    """
    from library.tools import render_qa

    return render_qa.measure_lufs(
        video_path,
        target_lufs=spec.loudness_target_lufs,
        tolerance=spec.loudness_tolerance_lufs,
        true_peak_ceiling=spec.true_peak_ceiling_dbtp)


def run_deliverable_qc(
        video_path: str,
        spec: DeliverySpec,
        expected_duration: float,
        grade_spans: Optional[Sequence] = None,
        subtitle_data: Optional[List[Dict]] = None,
        spine_blocks: Optional[Sequence] = None,
        declared_black_beats: Optional[List] = None) -> DeliverableQCVerdict:
    """Judge one rendered deliverable against its spec.

    Duration, black frames and loudness GATE. The grade delivery gate
    (``render_qa.measure_grade_delivery``) and, when ``subtitle_data``
    is supplied, the existing subtitle-timing check
    (``subtitle_qa.verify_subtitle_timing``) REPORT. An absent file
    raises ``FileNotFoundError`` - an absent render is not a passing
    one.
    """
    from library.tools import render_qa
    from library.tools.subtitle_qa import verify_subtitle_timing

    if not video_path or not os.path.isfile(video_path):
        raise FileNotFoundError(
            f"deliverable QC needs a rendered file, got: {video_path!r}")

    duration = render_qa.verify_duration(video_path, expected_duration)
    black_frames = render_qa.detect_black_frames(
        video_path, declared_beats=declared_black_beats)
    loudness = measure_loudness(video_path, spec)
    grade = render_qa.measure_grade_delivery(video_path, grade_spans)
    captions = (verify_subtitle_timing(
        subtitle_data, total_duration=expected_duration,
        spine_blocks=spine_blocks)
        if subtitle_data is not None else None)

    return DeliverableQCVerdict(
        platform=spec.platform,
        passed=bool(duration.passed and black_frames.passed
                    and loudness.passed),
        duration=duration,
        black_frames=black_frames,
        loudness=loudness,
        grade=grade,
        captions=captions,
    )
