"""Camera steadiness has ONE reading, and it says which signal answered.

Two signals in the temporal index bear on how steady a shot is, and they
are not equivalent:

- the **optical-flow residual**, written by step 1.04 under
  ``camera_motion_decomposition.values[].residual``. It is derived from
  the block-matched global translation between consecutive samples, so
  it measures how much the WHOLE FRAME moved. That is the signal
  designed for this job.
- the **standard deviation of ``motion_energy``**, which is frame
  differencing. It rises for a gesturing subject in front of a locked-off
  camera exactly as it does for a shaking camera, so it cannot tell the
  two apart.

`compute_deterministic_assessment` used to read the residual from
``temporal_index["camera_motion"]["residual"]``. Nothing has ever written
that key: the container is ``camera_motion_decomposition`` and the
residual lives per sample inside ``values``. On project 001,
``ti.get("camera_motion")`` was ``None`` on 17 of 17 clips, so the
residual had never been read once and every label the project ever
shipped came from frame differencing. It called clip_017 - the steadiest
clip in the edit, measured at 0.25 px/frame of global translation against
clip_012's 2.86 - ``unstable``, while the VLM in the same run called it
``stable`` and the VFX planner (which reads the VLM) put both of the
video's effects on it for being still. See AGENTS.md 10.3 and
docs/RULE_EVIDENCE.md.

**The thresholds are read off the INSTRUMENT, not off one project.**
Step 1.04 block-matches a 160x90 luma frame at 5 Hz over a search grid of
``dx, dy in {-8, -6, ..., 8}``, then divides by 8. So:

- the smallest displacement the search can report on one axis is 2 px of
  160 - 1.25% of the frame width, per 0.2 s sample - which arrives here
  as ``dx = 0.25``;
- ``residual = max(0, magnitude - 0.5 * (|dx| + |dy|))``, so that one
  grid step yields a residual of ``0.125`` and a sample where the search
  found no displacement at all yields exactly ``0.0``.

``STABLE_BELOW`` and ``HANDHELD_BELOW`` are therefore half a grid step
and one grid step of MEAN global displacement per sample. They are not
fitted to a distribution; ``MEASURED_ON_001`` records what that project's
17 clips look like against them, and against the VLM's own per-window
verdict, as a check rather than as the derivation.

The old numbers - 0.02 and 0.08 - were written for a signal that never
arrived and have no such derivation. Against the residual's real scale
they sit at 0.16 and 0.64 of a single grid step, which is below the
instrument's own resolution: they would call 12 of 001's 17 clips
``unstable`` including the two the render measurement says are the
steadiest.
"""

from __future__ import annotations

# One grid step of the 160-wide block search, expressed as a residual.
GRID_STEP_RESIDUAL = 0.125

# Under half a grid step of mean displacement: the search found nothing
# to match in the majority of samples.
STABLE_BELOW = GRID_STEP_RESIDUAL / 2      # 0.0625

# Under a full grid step: displacement is detected but averages less than
# the smallest step the search can resolve.
HANDHELD_BELOW = GRID_STEP_RESIDUAL        # 0.125

# Fewer than this and the mean is not a measurement of the clip.
MIN_RESIDUAL_SAMPLES = 10

# The whole of what may answer, and every label carries which one did.
# AGENTS.md 10.3: a method field travels with the number it qualifies.
METHODS = {
    "optical_flow_residual":
        "mean of camera_motion_decomposition.values[].residual - the "
        "block-matched global translation between 5 Hz samples",
    "motion_energy_std":
        "standard deviation of motion_energy.values - frame differencing, "
        "which cannot separate a moving subject from a moving camera",
    "unmeasured":
        "neither signal had enough samples; the label is 'unknown'",
}

# Recorded rather than deleted: the fallback stays, because a clip whose
# optical flow failed still needs an answer, and it is weaker on purpose.
WHY_THE_FALLBACK_IS_WEAKER = (
    "motion_energy is a frame difference, so a gesturing subject in front "
    "of a locked-off camera reads the same as a shaking camera. It answers "
    "only when the residual has nothing to say, and it says so."
)

# The check, not the derivation. Project 001, 17 clips, run 20260829.
MEASURED_ON_001 = {
    "residual_mean_range": (0.0051, 0.3706),
    "residual_mean_quartiles": (0.0769, 0.2382, 0.3094),
    "under_these_thresholds": {"stable": 3, "handheld": 2, "unstable": 12},
    "residual_read_on": "17 of 17 clips (it was 0 of 17)",
    "hard_disagreements_with_the_vlm": {
        # One signal says steady and the other does not, counted the same
        # way on both sides. A clip whose windows disagree with EACH
        # OTHER (clip_008) is not counted either way.
        "before": 9,   # frame differencing: 001 003 004 006 009 010 014 015 017
        "after": 5,    # residual:           001 004 006 010 014
    },
    # Both say steady, or both say moving.
    "outright_agreements_with_the_vlm": {"before": 1, "after": 10},
    "caveat":
        "one project is a thin basis for a threshold, and no render has "
        "been made against these numbers. They are grounded in the search "
        "grid rather than in this distribution, which is why they are "
        "stated as the grid and checked against the distribution.",
}


def residual_samples(temporal_index) -> list:
    """Every per-sample residual step 1.04 wrote, under the real key.

    Returns ``[]`` when the index is absent or carries no decomposition -
    never a fabricated value.
    """
    if not isinstance(temporal_index, dict):
        return []
    decomposition = temporal_index.get("camera_motion_decomposition")
    if not isinstance(decomposition, dict):
        return []
    values = decomposition.get("values")
    if not isinstance(values, list):
        return []
    out = []
    for value in values:
        if isinstance(value, dict) and isinstance(
                value.get("residual"), (int, float)):
            out.append(float(value["residual"]))
    return out


def label_for_residual_mean(mean: float) -> str:
    if mean < STABLE_BELOW:
        return "stable"
    if mean < HANDHELD_BELOW:
        return "handheld"
    return "unstable"


def read_camera_stability(temporal_index):
    """``(label, method, mean_residual)``, and it never guesses.

    ``("unknown", "unmeasured", None)`` when neither signal has enough
    samples. The mean is returned only when the residual answered.
    """
    residuals = residual_samples(temporal_index)
    if len(residuals) > MIN_RESIDUAL_SAMPLES:
        mean = sum(residuals) / len(residuals)
        return label_for_residual_mean(mean), "optical_flow_residual", mean

    motion = (temporal_index or {}).get("motion_energy")
    if isinstance(motion, dict):
        values = [v for v in (motion.get("values") or [])
                  if isinstance(v, (int, float))]
        if len(values) > 30:
            mean = sum(values) / len(values)
            std = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
            if std < 0.05:
                label = "stable"
            elif std < 0.15:
                label = "handheld"
            else:
                label = "unstable"
            return label, "motion_energy_std", None

    return "unknown", "unmeasured", None


# ── The two signals disagree, and that is DATA ──────────────────────────
#
# The VLM's per-window `camera[].stability` and the deterministic
# per-clip `assessment.camera_stability` are different measurements of
# different things: the model looks at the picture over a window, the
# residual measures global translation over the whole clip. They can
# both be right and still differ, and on 001 they differ on clips the
# planning steps then reason about in opposite directions.
#
# Nothing here picks a winner. Whatever picked a winner would become the
# measurement (AGENTS.md 10.5).

# The VLM's vocabulary, mapped onto the same axis as the deterministic
# one, for the purpose of saying whether the two POINT THE SAME WAY.
# Anything outside these two sets is `unrecognised` and is carried
# verbatim, never mapped onto the nearest word.
_STEADY_WORDS = {"stable", "static", "locked", "tripod", "smooth"}
_MOVING_WORDS = {"shaky", "unstable", "handheld", "jittery", "unsteady"}

AGREEMENTS = {
    "agree": "both signals point the same way",
    "disagree": "one says steady and the other says moving",
    "partial": "one of them is 'handheld', which is neither end",
    "one_sided": "only one of the two measured anything",
    "unrecognised": "a word outside either vocabulary; carried verbatim",
}

STABILITY_LEGEND = {
    "deterministic_stability":
        "assessment.camera_stability - one verdict for the whole clip, "
        "from the signal named in deterministic_method.",
    "deterministic_method":
        "which signal produced it: optical_flow_residual (global "
        "translation between 5 Hz samples), motion_energy_std (frame "
        "differencing, which cannot separate subject motion from camera "
        "motion), unmeasured, or unrecorded - the last meaning the document "
        "predates the method field, so the label exists and the signal "
        "behind it does not.",
    "vlm_stability":
        "camera[].stability as the vision model wrote it, per window. "
        "Several windows of one clip may disagree with each other; every "
        "distinct word the clip's windows carry is listed.",
    "signals_agree":
        "agree / disagree / partial / one_sided / unrecognised. A "
        "disagreement is a fact about the two measurements, not a fault "
        "in either, and NOTHING in the pipeline resolves it. It is here "
        "so a step reasoning from one of them knows the other exists.",
}


def _side(word):
    if not isinstance(word, str):
        return None
    w = word.strip().lower()
    if w in _STEADY_WORDS:
        return "steady"
    if w in _MOVING_WORDS:
        return "moving" if w != "handheld" else "middle"
    return None


def compare_stability_signals(deterministic, vlm_words):
    """How the two signals stand to each other. It resolves nothing."""
    words = [w for w in (vlm_words or []) if isinstance(w, str) and w.strip()]
    det_side = _side(deterministic)
    if deterministic in (None, "", "unknown") or not words:
        return "one_sided"
    sides = {_side(w) for w in words}
    if None in sides or det_side is None:
        return "unrecognised"
    if det_side == "middle" or sides == {"middle"}:
        return "partial"
    if len(sides) > 1:
        return "partial"
    return "agree" if sides == {det_side} else "disagree"
