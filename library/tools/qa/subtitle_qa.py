"""QA on a rendered subtitle overlay.

Two halves, and only one of them decides anything.

**The frames it looks at.**  A subtitle overlay is transparent between
captions.  The gate used to sample two fixed instants - 0.5s and 1.5s
into the segment - and hand whatever came back to a vision model.  On
project 001's `sub_block_10` both land in an ordinary pause: the first
caption is the single word "i", ending 0.70s in, and the next does not
arrive until 1.58s.  Frames are now chosen by measuring the overlay's own
alpha channel and taking the instants with the most ink on them.

**What decides.**  The mechanical checks below, because they read the
pixels.  A segment must draw something somewhere, and what it draws must
sit inside the frame with a margin and in the lower half where a subtitle
belongs.  Those are the failures this gate exists to catch and they are
deterministic.

**What does not decide, and why.**  The vision model's typography verdict
is recorded and printed, not enforced.  Measured on 2026-08-20 against
gemma-4-12b-it-4bit, its answer is not correlated with the picture:

* Shown the two BLANK frames above, it answered PASS (phase 2's run) and
  later FAIL - a coin toss on an empty image.
* Shown four frames that are demonstrably clean - "casey neistat," at
  alpha bbox (288, 1694, 776, 1770) in a 1080x1920 frame, and "myself
  this is the" at (258, 1697, 808, 1770) - it answered FAIL three times
  out of three, claiming the text was "cut off by the bottom edge" and
  that "the letters are overlapping and distorted".  Both claims are
  false: there are 150 clear rows below the type and the kerning is
  Montserrat's own.

A gate that fails correct output on a fabricated reason is not coverage,
it is a blocked render; a gate that passes a blank frame is not coverage
either.  The rule this file follows is the one in CLAUDE.md - judge a
check by what it actually reads.  So the mechanical half blocks, the
model's opinion is written down for a human, and the specific complaint
the model kept inventing (text clipped at an edge) is now checked for
real, in `check_caption_geometry`.
"""

import os
import sys
import subprocess

# Add tools to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# How many instants to measure across the segment before choosing.
ALPHA_PROBE_SAMPLES = 16

# The alpha plane is measured on a small grid and judged on its MAXIMUM,
# not its mean: caption text covers on the order of 1% of a 1080x1920
# frame, so a mean rounds to zero whether the words are there or not.
ALPHA_PROBE_GRID = (32, 57)

# Below this, the instant carries no legible mark. Antialiased edges of
# real text reach 255; an empty frame measures 0.
ALPHA_INK_THRESHOLD = 32

# Caption ink must stay this far inside the frame. Wide enough that a
# genuinely clipped caption is caught, loose enough that a full-width
# line of type is not called clipped for reaching the safe area.
EDGE_MARGIN_FRACTION = 0.02

# Subtitles are bottom-positioned. Ink whose vertical centre sits above
# this fraction of the frame is not a subtitle, it is a layout fault.
CAPTION_TOP_LIMIT_FRACTION = 0.5


def _probe_alpha(mov_path: str, timestamp: float) -> int:
    """Peak alpha at one instant, 0-255. 0 means nothing is drawn."""
    width, height = ALPHA_PROBE_GRID
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{timestamp:.3f}",
                "-i", mov_path, "-frames:v", "1",
                "-vf", f"alphaextract,scale={width}:{height}",
                "-f", "rawvideo", "-pix_fmt", "gray", "-",
            ],
            capture_output=True, timeout=60,
        )
    except Exception:
        return 0
    return max(result.stdout) if result.stdout else 0


def find_inked_timestamps(mov_path: str, duration: float, count: int = 2) -> list:
    """The instants in a segment that actually have captions drawn on them.

    Returns up to `count` timestamps, most ink first. An empty list means
    the overlay draws nothing anywhere in the segment.
    """
    if duration <= 0:
        return []

    # Probe on an interior grid - the very first and last frames of a
    # segment are lead-in and lead-out padding by construction.
    step = duration / (ALPHA_PROBE_SAMPLES + 1)
    measured = []
    for i in range(1, ALPHA_PROBE_SAMPLES + 1):
        t = step * i
        alpha = _probe_alpha(mov_path, t)
        if alpha >= ALPHA_INK_THRESHOLD:
            measured.append((alpha, t))

    measured.sort(reverse=True)
    return [t for _, t in measured[:count]]


def _alpha_bbox(frame_path: str):
    """(left, top, right, bottom) of everything drawn, or None if blank."""
    from PIL import Image

    with Image.open(frame_path) as im:
        return im.convert("RGBA").getchannel("A").getbbox()


def check_caption_geometry(frame_path: str) -> list:
    """Everything mechanically wrong with one caption frame.

    This is the half of the gate that reads real state: is anything
    drawn, is it clipped at an edge, is it where a subtitle goes.
    Returns a list of sentences; empty means the frame is sound.
    """
    from PIL import Image

    with Image.open(frame_path) as im:
        width, height = im.size
    bbox = _alpha_bbox(frame_path)
    if bbox is None:
        return [f"{os.path.basename(frame_path)} draws nothing at all."]

    left, top, right, bottom = bbox
    x_margin = width * EDGE_MARGIN_FRACTION
    y_margin = height * EDGE_MARGIN_FRACTION
    problems = []

    clipped = []
    if left < x_margin:
        clipped.append(f"left (x={left}, margin {x_margin:.0f})")
    if right > width - x_margin:
        clipped.append(f"right (x={right} of {width})")
    if top < y_margin:
        clipped.append(f"top (y={top}, margin {y_margin:.0f})")
    if bottom > height - y_margin:
        clipped.append(f"bottom (y={bottom} of {height})")
    if clipped:
        problems.append(
            f"caption ink reaches the frame edge on {', '.join(clipped)} - "
            f"the text is being clipped."
        )

    centre_y = (top + bottom) / 2
    if centre_y < height * CAPTION_TOP_LIMIT_FRACTION:
        problems.append(
            f"caption sits at y={centre_y:.0f} of {height}, in the upper "
            f"half of the frame. Subtitles are bottom-positioned."
        )

    return problems


def run_subtitle_qa(mov_path: str, project_folder: str = None,
                      harness: str = None) -> dict:
    """Check a rendered subtitle segment. Raises on a mechanical failure.

    `harness` selects who looks at the sampled frames for the
    advisory typography observation: a driving host with vision
    answers first, else gemma (`library/tools/still_vision.py`).
    Unset reads `PIPELINE_HOST_HARNESS`, so a bare run keeps the
    gemma behaviour exactly.
    """
    if os.environ.get("SKIP_QA_CHECKS") == "1":
        print("Skipping subtitle QA check (SKIP_QA_CHECKS=1)", file=sys.stderr)
        return {"passed": True, "reason": "skipped"}

    print(f"Running subtitle QA on {os.path.basename(mov_path)}...", file=sys.stderr)

    # Get project folder to put temp frames in
    if not project_folder:
        # try to derive from mov_path, assuming it's in pipeline_output/subtitle_segments/
        project_folder = os.path.dirname(os.path.dirname(os.path.dirname(mov_path)))

    from library.tools.project_layout import Area, ProjectLayout
    qa_frames_dir = str(ProjectLayout(project_folder).write_dir(Area.QA_FRAMES, step="validate"))
    os.makedirs(qa_frames_dir, exist_ok=True)

    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', mov_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
        )
        duration = float(result.stdout.strip())
    except Exception as e:
        print(f"Warning: could not get duration of {mov_path}, defaulting to 2s: {e}", file=sys.stderr)
        duration = 2.0

    # Choose frames that HAVE captions on them, rather than two fixed
    # instants that may both land in a pause between captions.
    timestamps = find_inked_timestamps(mov_path, duration, count=2)
    if not timestamps:
        raise RuntimeError(
            f"Subtitle QA Failed:\nFAIL\n{os.path.basename(mov_path)} draws "
            f"nothing anywhere in its {duration:.2f}s: every one of "
            f"{ALPHA_PROBE_SAMPLES} probes across the segment measured an "
            f"empty alpha channel. The overlay is blank, not merely paused."
        )
    print(
        "  Sampling at "
        + ", ".join(f"{t:.2f}s" for t in timestamps)
        + " (measured to carry caption pixels)",
        file=sys.stderr,
    )

    basename = os.path.basename(mov_path).replace('.mov', '')
    frame_paths = []
    try:
        for i, t in enumerate(timestamps, start=1):
            frame_path = os.path.join(qa_frames_dir, f"{basename}_frame{i}.png")
            subprocess.run(
                ['ffmpeg', '-y', '-ss', f"{t:.3f}", '-i', mov_path,
                 '-frames:v', '1', frame_path],
                capture_output=True, check=True,
            )
            frame_paths.append(frame_path)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to extract frames for QA: {e.stderr.decode()}")

    # ── The half that decides ──────────────────────────────────────────
    geometry_problems = []
    for frame_path in frame_paths:
        geometry_problems.extend(check_caption_geometry(frame_path))

    if geometry_problems:
        for frame_path in frame_paths:
            try:
                os.remove(frame_path)
            except OSError:
                pass
        raise RuntimeError(
            "Subtitle QA Failed:\nFAIL\n"
            + "\n".join(f"  - {p}" for p in geometry_problems)
        )
    print("  Caption geometry OK: inside the frame, bottom-positioned.",
          file=sys.stderr)

    # ── The half that is recorded, not enforced ────────────────────────
    # See this module's docstring for the measurement that demoted it.
    observation = _vision_observation(
        frame_paths, harness=harness, project_folder=project_folder)

    for frame_path in frame_paths:
        try:
            os.remove(frame_path)
        except OSError:
            pass

    print("Subtitle QA passed.", file=sys.stderr)
    return {
        "passed": True,
        "reason": "caption geometry checks passed",
        "sampled_at": [round(t, 3) for t in timestamps],
        "vision_observation": observation,
    }


def _vision_observation(frame_paths: list, harness: str = None,
                          project_folder: str = None) -> str:
    """The model's opinion on typography, for a human to read.

    Never raises and never blocks: its verdict has been measured against
    known-good and known-blank frames and does not track either.
    Who looks is the still-vision route (`library/tools/still_vision.py`):
    the driving host first, gemma fallback. (This also retires the old
    `from tools.vision_model import get_model`, which only resolved when
    the caller's sys.path happened to cooperate.)
    """
    prompt_text = """This is a rendered subtitle overlay for vertical shortform video (1080x1920). Check:
(1) Are words properly spaced and readable?
(2) Is text positioned at the bottom of the frame?
(3) Is the font style clean and professional?
(4) Are there any obvious rendering issues?
Report pass/fail with specific issues.
Format your response starting with exactly "PASS" or "FAIL", followed by a newline and your explanation."""

    try:
        from library.tools.still_vision import inspect_stills
        print("Calling still-vision route (advisory)...", file=sys.stderr)
        response = inspect_stills(
            prompt_text, list(frame_paths), harness=harness,
            project_folder=project_folder, step_id="subtitle_qa",
            label="subtitle_qa")
    except Exception as e:
        return f"vision observation unavailable: {e}"

    if not response:
        return "vision observation unavailable: empty response"

    verdict = response.strip().splitlines()[0] if response.strip() else "?"
    print(f"  Vision observation (advisory, not a gate): {verdict}",
          file=sys.stderr)
    return response
