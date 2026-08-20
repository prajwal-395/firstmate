"""Perceptual quality QA: a model that WATCHES the render.

Every gate in this pipeline is technical - resolution, fps, duration,
black frames, LUFS, audio streams - and not one of them would catch a
letterboxed edit with the subject's head cropped off. That gap is Q8, and
the captain's answer (2026-08-16) is to feed the render to the local
Gemma 4 12B vision model and ask it what is wrong.

What this does and does not settle
----------------------------------
It genuinely closes the gap: framing, letterboxing, caption legibility and
colour are all things a model that looks at a frame can catch and a
technical gate cannot. It does NOT settle whose taste the bar is
calibrated to - it moves it from the agent's taste to the model's. That is
an improvement, because a model verdict is reproducible, inspectable and
cheap to run on every render, but it still needs the captain to look once
and say whether they agree. Until they have, nothing here is anybody's
standard.

Four constraints, all deliberate
--------------------------------
**Structured, not prose.** Verdicts have to be comparable across renders,
and prose cannot be diffed.

**Ask what is WRONG and WHY, never for a score.** A number invites tuning
toward the number. This module has no score and should not grow one.

**Every dimension must discriminate.** A dimension that returns the same
answer on every frame ranks nothing and is dropped. `fills_frame` was
dropped for exactly that: it answered `true` on a letterboxed frame that
plainly did not fill the frame, and `true` on the two that did.

**Observation only.** Nothing here fails a render. There is no evidence
yet about the false-positive rate, and making it fatal without that
evidence would repeat the mistake this project keeps undoing.

What the first real verdicts taught
-----------------------------------
Asked openly - "report only what is wrong" - the model returned
`{"issues": []}` on a frame whose subject was visibly cut in half. Asked
the grounded questions below, it got all three calibration frames right,
including naming which side the subject was cut off on. What the model
notices depends almost entirely on being asked something concrete, which
is why the dimensions here are narrow, closed questions rather than an
invitation to critique.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# The model already wired into visual_qa_router; see library/tools/vision_model.py.
MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"


@dataclass(frozen=True)
class Dimension:
    """One closed question about a frame.

    `key` is the JSON field the model fills in. `bad_when` decides whether
    an answer is a finding - it takes the parsed value and returns True
    when the frame is wrong on this dimension.
    """

    key: str
    question: str
    kind: str  # "bool" | "enum" | "text"
    options: tuple = ()
    verified: bool = False   # seen to discriminate on real frames
    note: str = ""

    def is_finding(self, value: Any) -> bool:
        if value is None:
            return False
        if self.kind == "bool":
            # Phrased so that False is the problem: "fully visible", "legible".
            return value is False
        if self.kind == "enum":
            return str(value) not in ("none", "no", "")
        return bool(str(value).strip())


DIMENSIONS: List[Dimension] = [
    Dimension(
        key="black_bars",
        question=('"black_bars": "none|top_and_bottom|left_and_right" - are '
                  'there black bars around the picture?'),
        kind="enum",
        options=("none", "top_and_bottom", "left_and_right"),
        verified=True,
        note=("Caught the letterboxed frame - 'large black bars at the top "
              "and bottom' - which is the loudest defect in the plan and the "
              "one no technical gate can see."),
    ),
    Dimension(
        key="main_subject_fully_visible",
        question=('"main_subject_fully_visible": true|false - is the main '
                  'subject complete, or cut off by the edge of the frame?'),
        kind="bool",
        verified=True,
        note=("Caught the dead-centre crop and named the side: 'The main "
              "subject is cut off on the left side of the frame.'"),
    ),
    Dimension(
        key="text_legible",
        question=('"text_legible": true|false - is any on-screen text easy to '
                  'read against what is behind it?'),
        kind="bool",
        verified=False,
        note="Not yet seen to discriminate; drop it if it never does.",
    ),
    Dimension(
        key="what_is_wrong",
        question=('"what_is_wrong": "<one sentence naming the single worst '
                  'problem, or empty if nothing is wrong>"'),
        kind="text",
        verified=True,
        note="The why. Empty on the clean frame, specific on both bad ones.",
    ),
]

# Dropped, and recorded rather than deleted so nobody re-adds it:
#   fills_frame (bool) - answered `true` on all three calibration frames,
#   including the letterboxed one where it contradicted the model's own
#   black_bars answer. It ranked nothing.
DROPPED_DIMENSIONS = {
    "fills_frame": (
        "Answered true on every frame including a letterboxed one, "
        "contradicting its own black_bars answer on the same frame. A "
        "dimension that cannot discriminate ranks nothing."
    ),
}

PROMPT_HEADER = (
    "This is one frame of a vertical short-form video, 1080x1920, meant to "
    "fill a phone screen.\n"
    "Report only what is WRONG and why. Do not give a score or a rating.\n"
    "Answer as JSON only:\n"
)


def build_prompt(dimensions: Optional[List[Dimension]] = None) -> str:
    """The grounded, closed-question prompt.

    Deliberately not an open invitation to critique: asked openly the
    model reported no issues on a frame whose subject was cut in half.
    """
    dims = dimensions if dimensions is not None else DIMENSIONS
    body = ",\n ".join(d.question for d in dims)
    return f"{PROMPT_HEADER}{{{body}}}"


@dataclass
class Finding:
    dimension: str
    value: Any
    detail: str = ""


@dataclass
class FrameVerdict:
    """One frame's verdict. Observation only - nothing here fails a render.

    `unanswered` is the list of dimensions the model did not return a
    usable key for. It exists because the alternative was silence: the
    parser used to `continue` past an absent key, so a dimension the model
    garbled simply produced no finding and the frame read CLEAN. Measured
    on a real frame, the model answered `main__subject_fully_visible` -
    two underscores - and that dimension vanished from the verdict without
    a word. A gate that reports "nothing wrong" when it did not get an
    answer is the vacuous-gate pattern this project keeps removing.
    """

    frame: int
    raw: str = ""
    answers: Dict[str, Any] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    parse_error: str = ""
    unanswered: List[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return (not self.findings and not self.parse_error
                and not self.unanswered)


def parse_verdict(raw: str, frame: int = 0,
                  dimensions: Optional[List[Dimension]] = None) -> FrameVerdict:
    """Parse the model's reply into a comparable verdict.

    The model fences its JSON in ```json blocks about half the time, so
    the fence is stripped rather than fought.
    """
    dims = dimensions if dimensions is not None else DIMENSIONS
    verdict = FrameVerdict(frame=frame, raw=raw or "")

    text = (raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    else:
        brace = re.search(r"\{.*\}", text, re.S)
        if brace:
            text = brace.group(0)

    try:
        data = json.loads(text)
    except (ValueError, TypeError) as e:
        verdict.parse_error = f"could not parse a verdict: {e}"
        return verdict

    if not isinstance(data, dict):
        verdict.parse_error = "verdict was not a JSON object"
        return verdict

    verdict.answers = data
    for dim in dims:
        if dim.key not in data:
            verdict.unanswered.append(dim.key)
            continue
        value = data[dim.key]
        if dim.is_finding(value):
            detail = str(data.get("what_is_wrong") or "").strip()
            verdict.findings.append(
                Finding(dimension=dim.key, value=value, detail=detail))
    return verdict


def dimension_variance(verdicts: List[FrameVerdict]) -> Dict[str, int]:
    """How many DISTINCT answers each dimension gave.

    The variance discipline, made measurable. A dimension scoring 1 across
    a real set of renders answers the same thing every time, ranks nothing
    and should move to DROPPED_DIMENSIONS with its evidence.
    """
    seen: Dict[str, set] = {}
    for v in verdicts:
        for key, value in (v.answers or {}).items():
            seen.setdefault(key, set()).add(json.dumps(value, sort_keys=True))
    return {k: len(v) for k, v in sorted(seen.items())}


def summarise(verdicts: List[FrameVerdict]) -> Dict[str, Any]:
    """A render's perceptual observation, in one comparable dict.

    `unanswered` is reported per dimension rather than folded into the
    frame count, because "the model skipped this question on 4 of 6
    frames" and "the model said the frames were fine" have to be
    distinguishable at a glance. They were not: an unanswered dimension
    used to count as clean.
    """
    findings = [
        {"frame": v.frame, "dimension": f.dimension,
         "value": f.value, "detail": f.detail}
        for v in verdicts for f in v.findings
    ]
    unanswered: Dict[str, int] = {}
    for v in verdicts:
        for key in v.unanswered:
            unanswered[key] = unanswered.get(key, 0) + 1
    return {
        "model": MODEL_ID,
        "observation_only": True,
        "frames_examined": len(verdicts),
        "frames_clean": sum(1 for v in verdicts if v.clean),
        "parse_failures": sum(1 for v in verdicts if v.parse_error),
        "unanswered_dimensions": dict(sorted(unanswered.items())),
        "findings": findings,
        "dimension_variance": dimension_variance(verdicts),
    }
