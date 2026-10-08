"""The render review's verdict, and where it is read.

Step 6.01's deterministic half builds the timeline and exports the
file; its model half WATCHES the export (frame strips drawn off the
file itself behind `PIPELINE_PERCEPTUAL_QA`, manifest and measurements
otherwise) and writes `render_review`. Its summary reader keeps the
operator informed. Span-grounded notes also feed assignment creative QA,
which carries them into the assignment revision record for the creator
and slice 3. Subjective notes never gate delivery; 6.02 owns the
deterministic file checks and their gate classification.

This module is also what makes the step's ask honest. The model used
to be asked for `render_watch_frames` and `visual_qa` - both measured
by the build, neither authored by any model - because the manifest
declared no `llm_outputs` and the runner asked for every declared
output the step had not already produced (finding 11,
execution-frontier report 2026-09-24). The manifest now declares
`llm_outputs: [render_review]`: the one key the model writes, read
here.
"""

from typing import Any


def subjective_observations(render_review: Any) -> list[dict]:
    """Return only notes with an explicit measured span and intent reference.

    Old free-text notes remain printable as historical advisory output, but
    cannot enter assignment QA because neither their span nor target is known.
    """
    if not isinstance(render_review, dict):
        return []
    notes = render_review.get("notes") or []
    observations = []
    for note in notes:
        if not isinstance(note, dict):
            continue
        required = {"detail", "span", "intent"}
        if required.issubset(note):
            observations.append({
                "metric": str(note.get("metric", "render_observation")),
                "detail": str(note["detail"]),
                "span": dict(note["span"]),
                "intent": dict(note["intent"]),
                "passed": bool(note.get("passed", False)),
                "severity": str(note.get("severity", "review")),
            })
    return observations


def summary_lines(render_review: Any) -> list:
    """What the run summary prints about the render review.

    Empty when the review wrote nothing, so a run with no review is
    silent rather than printing a heading with nothing under it.
    """
    if not isinstance(render_review, dict):
        return []
    overall = render_review.get("overall") or ""
    notes = render_review.get("notes") or []
    if not overall and not notes:
        return []
    lines = [f"  Render review: {overall or 'no overall verdict'}"]
    for note in notes:
        if isinstance(note, dict) and note.get("detail"):
            detail = str(note["detail"]).strip()
            if detail:
                span = note.get("span") or {}
                if "start" in span and "end" in span:
                    detail = f"{float(span['start']):.2f}-{float(span['end']):.2f}s: {detail}"
                lines.append(f"    - {detail[:400]}")
        elif isinstance(note, str) and note.strip():
            lines.append(f"    - {note.strip()[:400]}")
    return lines
