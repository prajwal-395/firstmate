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

The AUDIO spec, verified on the file itself
--------------------------------------------
Loudness alone is not the audio spec. Integrated loudness is a GATED
measurement (BS.1770 drops everything below the absolute gate), so a
delivered file that LOST its dialogue still measures on target: the
silence is not counted, the words that survived are. Measured on this
repo's own delivery path (2026-10-20), a file whose last 16 of 20
seconds were digital silence came back ``passed: True`` from BOTH
``verify_deliverable`` and ``run_deliverable_qc`` - the delivery state
read "delivered" over a file with no dialogue in it.

So the audio spec is three measurements of the DELIVERED FILE:

* ``loudness`` - the spec's own target, tolerance and true-peak
  ceiling, measured on the file that is handed over;
* ``silence`` - digital silence under picture, which is the half that
  sees MISSING DIALOGUE. The engine already owns that measurement
  (``render_qa.measure_silence_under_picture``) and the declared
  silence that excuses it, so this module calls that and does not
  write a second silence rule;
* ``channels`` - channel presence: a file that carries a channel with
  no signal in it lost that channel, and a file with no audio stream
  at all is not a delivery.

INTENTIONAL SILENCE PASSES. A file with no audible content has no
loudness to be wrong - its integrated loudness is ``-inf``, and
"-inf LUFS is inf dB off the target" is not a loudness failure, it is
a silence question. So the loudness leg is NOT CHECKED on such a file
(``measure_lufs`` says so in its own detail) and the SILENCE leg
decides: a declared silent ending - Reel 13's silent end card - is
excused by the declaration, an undeclared silence is not.

The CAPTION spec, verified on the file itself
---------------------------------------------
A caption can be right in the plan and wrong in the file: the card
never reached the export, it reached it late, it was replaced by a card
that was never re-rendered, or another graphic was drawn over it. None
of that is visible to a plan-side check, because the plan is not what
was delivered.

So the caption spec is measured on the DELIVERED FILE, the way the
audio spec is: the words the export carries, the frames the caption is
visible for, where it is drawn, whether it is legible, how it wraps,
whether the emphasis reached the export, and whether a caption nobody
declared is still on the file. The plan supplies only the INTENT
(``library/tools/delivered_caption_intent.py``); every measurement is
taken from the file. It GATES when it ran, and reports itself
unmeasured when it could not - an unmeasured caption is never a
silently passing one.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

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
    #: The delivered FILE's audio against its spec - loudness, the
    #: silence under picture that catches missing dialogue, and the
    #: channel presence. GATES with the rest: a file that fails its
    #: audio spec is not a deliverable, whatever its picture measures.
    audio: "AudioSpecVerdict" = None
    #: The delivered FILE's CAPTIONS against what the plan declared for
    #: them - the words, the visible span, the placement, the
    #: readability, the wrapping, the emphasis, the collision with
    #: other graphics, and the card nobody declared (a replacement that
    #: never reached the export). None when no caption intent was
    #: supplied: an unmeasured caption is not a passing caption, and the
    #: verdict says so rather than passing it.
    caption_spec: Any = None


@dataclass(frozen=True)
class AudioSpecVerdict:
    """The delivered FILE's audio, against the spec it ships under.

    ``loudness`` is the spec's integrated target, tolerance and
    true-peak ceiling, measured on the file that is handed over.
    ``silence`` is the file's own digital silence under picture - the
    half that sees MISSING DIALOGUE, which the loudness measurement
    cannot see (BS.1770 gates the silence out, so a file that lost its
    dialogue still measures on target). ``channels`` is the channel
    presence: a file carrying a channel with no signal in it lost that
    channel.

    ``passed`` is all three. A file with no audible content has no
    loudness to be wrong: its loudness leg is NOT CHECKED (the detail
    says so) and the silence leg decides - a DECLARED silent ending
    passes, an undeclared silence does not.
    """

    loudness: RenderQAResult
    silence: RenderQAResult
    channels: RenderQAResult
    passed: bool


def _measured_lufs(result: RenderQAResult) -> Optional[bool]:
    """Whether the file carries audible content, or None when unknown.

    ``True`` - the file's integrated loudness is a number.
    ``False`` - the file carries NO audible content: its integrated
    loudness is -inf, which is not a loudness to be wrong.
    ``None`` - the measurement carries no loudness at all, so the
    instrument's own verdict stands (an unmeasured file is never
    turned into a passing one).
    """
    value = result.value if isinstance(result.value, dict) else {}
    if "input_i" not in value:
        return None
    try:
        measured = float(value["input_i"])
    except (TypeError, ValueError):
        return None
    return math.isfinite(measured)


def _silence_has_no_loudness(result: RenderQAResult) -> RenderQAResult:
    """The loudness leg of a file that carries no audible content.

    Not a pass and not a failure: the file has no loudness to be
    wrong, and the SILENCE leg owns the question it does have. The
    detail says that in words, because "-inf LUFS is inf dB off the
    target" is not a thing a reader can act on.
    """
    return RenderQAResult(
        metric=result.metric,
        passed=True,
        value=result.value,
        threshold=result.threshold,
        severity="info",
        detail=("the file carries no audible content, so there is no "
                "loudness to be wrong - the silence check owns this file, "
                "and a DECLARED silent ending passes it"))


def verify_audio_spec(video_path: str, spec: DeliverySpec, *,
                      declared_silence_spans: Optional[Sequence] = None,
                      loudness: Optional[RenderQAResult] = None
                      ) -> AudioSpecVerdict:
    """The delivered file's audio, against the spec it ships under.

    The one measurement the delivery VERDICT and the QC gate share, so
    the two cannot grade the same file two ways.

    `declared_silence_spans` are the `(start, end)` seconds where
    silence under picture is DELIBERATE - a silent end card, a
    TV-switch-off tail. They come from the project's own declarations
    (`library/tools/declared_silence.py`), so the file is excused only
    where somebody said it should be.

    `loudness` is a measurement the caller already made against the
    same spec: passing it in keeps one measurement of one number, and
    the loudness half of the file is measured ONCE.
    """
    from library.tools import render_qa

    if loudness is None:
        loudness = measure_loudness(video_path, spec)
    if _measured_lufs(loudness) is False:
        loudness = _silence_has_no_loudness(loudness)
    silence = render_qa.measure_silence_under_picture(
        video_path, declared_spans=declared_silence_spans)
    channels = render_qa.verify_channel_presence(video_path)
    return AudioSpecVerdict(
        loudness=loudness,
        silence=silence,
        channels=channels,
        passed=bool(loudness.passed and silence.passed
                    and channels.passed))


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
        declared_black_beats: Optional[List] = None,
        declared_silence_spans: Optional[Sequence] = None,
        caption_intents: Optional[Sequence] = None,
        caption_spans: Optional[Sequence] = None
        ) -> DeliverableQCVerdict:
    """Judge one rendered deliverable against its spec.

    Duration, black frames and the AUDIO SPEC GATE. The grade delivery
    gate (``render_qa.measure_grade_delivery``) and, when
    ``subtitle_data`` is supplied, the existing subtitle-timing check
    (``subtitle_qa.verify_subtitle_timing``) REPORT. An absent file
    raises ``FileNotFoundError`` - an absent render is not a passing
    one.

    The audio spec is the delivered FILE's own: its loudness against
    the spec, the digital silence under picture that catches missing
    dialogue, and the channel presence. ``declared_silence_spans``
    excuse the silence a project DECLARED (a silent end card); nothing
    else is excused.

    THE CAPTION SPEC. ``caption_intents`` is what the captions were
    supposed to be - the plan's side of the comparison. The caption
    spec is measured on the DELIVERED FILE (``library/tools/
    delivered_caption_spec.py``): its words, the frames it is visible
    for, where it is drawn, whether it is legible, how it wraps,
    whether the emphasis reached the export, and whether another
    graphic is drawn over it. It GATES when it ran; when it could not
    run (no intent, or no way to read the delivered frames) it is
    REPORTED as unmeasured and does not gate - an unmeasured caption is
    never silently a passing one.
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
    audio = verify_audio_spec(
        video_path, spec,
        declared_silence_spans=declared_silence_spans,
        loudness=loudness)
    grade = render_qa.measure_grade_delivery(video_path, grade_spans)
    captions = (verify_subtitle_timing(
        subtitle_data, total_duration=expected_duration,
        spine_blocks=spine_blocks)
        if subtitle_data is not None else None)
    caption_spec = _measure_caption_spec(
        video_path, caption_intents, caption_spans)

    return DeliverableQCVerdict(
        platform=spec.platform,
        passed=bool(duration.passed and black_frames.passed
                    and audio.passed
                    and (caption_spec is None or caption_spec.passed)),
        duration=duration,
        black_frames=black_frames,
        loudness=audio.loudness,
        grade=grade,
        captions=captions,
        audio=audio,
        caption_spec=caption_spec,
    )


def _measure_caption_spec(video_path: str,
                           caption_intents: Optional[Sequence],
                           caption_spans: Optional[Sequence]):
    """The delivered file's captions, measured on the delivered file.

    None when no intent was supplied: nothing was asked, so nothing was
    measured, and the verdict says so rather than passing a file it
    could not judge.
    """
    if caption_intents is None:
        return None
    from library.tools.delivered_caption_spec import verify_delivered_captions

    return verify_delivered_captions(
        video_path, caption_intents,
        declared_spans=list(caption_spans or ()))
