"""The render review's verdict, and where it is read.

Step 6.01's deterministic half builds the timeline and exports the
file; its model half WATCHES the export (frame strips drawn off the
file itself behind `PIPELINE_PERCEPTUAL_QA`, manifest and measurements
otherwise) and writes `render_review`. That verdict has no downstream
step to act on it - 6.02's own validation is the gate with teeth - so
its reader is the run summary, the way the rough-cut review's
unplaced findings are (`cut_verdicts.summary_lines`). Reading is not
gating: a review that disliked the render cannot fail it here; 6.02
decides that under its own classification.

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
        if isinstance(note, str) and note.strip():
            lines.append(f"    - {note.strip()[:400]}")
    return lines
