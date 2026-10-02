"""Copy renders in the case stated; uppercase is declared, never default.

Finding 22, execution-frontier report 2026-09-24: motion-graphics text
was force-uppercased whatever case the copy stated - the MG2.3 run's
`title_lockup` copy 'link in bio' rendered 'LINK IN BIO'. A run now
renders as typed unless it declares `uppercase: true`, and the plan
(`motion_graphics_plan._copy_runs`), the renderer (the Remotion
composition) and the tight-box measurement (`mg_tight_box._run_width`)
all read that one flag - so the measured union is the drawn one.

No Resolve, no render: plan resolution, a recording fitter, and the
composition source.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import mg_tight_box as box  # noqa: E402
from library.tools import motion_graphics_plan as mgp  # noqa: E402

FPS = 30
DURATION = 12.0

COMPOSITION = (REPO_ROOT / "remotion-subtitles" / "src" / "compositions"
               / "MotionGraphics" / "index.tsx")


def _entry(**kw):
    base = {
        "element": "title_lockup",
        "start_seconds": 0.5,
        "duration_seconds": 2.0,
        "anchor": "top_left",
        "color": "#F5F5F0",
    }
    base.update(kw)
    return base


def _resolve(plan):
    return mgp.resolve_plan(plan, timeline_duration=DURATION, fps=FPS,
                            palette_roles={})


# ── The plan carries the declared style through ─────────────────────

def test_copy_renders_in_the_case_stated_by_default():
    """A lowercase display run stays lowercase unless the plan says shout."""
    resolved = _resolve([_entry(copy=[
        {"text": "link in bio", "type_role": "display"}])])
    run = resolved.moments[0]["runs"][0]
    assert run["text"] == "link in bio"
    assert not run["uppercase"]

    shouted = _resolve([_entry(copy=[
        {"text": "LINK IN BIO", "type_role": "display",
         "uppercase": True}])])
    assert shouted.moments[0]["runs"][0]["uppercase"] is True


# ── The measurement draws what the renderer draws ───────────────────

class _RecordingFitter:
    def __init__(self):
        self.seen = []

    def word_width(self, word):
        self.seen.append(word)
        return float(len(word))


def _cache_for(role="display", scale=1.0):
    size = box.TYPE_SIZE[role] * scale
    weight = box.TYPE_WEIGHT[role]
    fitter = _RecordingFitter()
    return {(round(size, 3), weight): fitter}, fitter


def test_measurement_honours_case_unless_the_flag_is_set():
    cache, fitter = _cache_for()
    box._run_width("link in bio", "display", 1.0, "", cache)
    assert fitter.seen[0] == "link in bio"

    cache, fitter = _cache_for()
    box._run_width("link in bio", "display", 1.0, "", cache,
                   uppercase=True)
    assert fitter.seen[0] == "LINK IN BIO"


# ── The renderer reads the flag, not the tier ───────────────────────

def test_no_display_tier_forces_uppercase_in_the_composition():
    """The two unconditional `type_role === "display" ? "uppercase"`
    transforms were the defect. Every uppercase transform now answers
    to the run's own declared flag."""
    source = COMPOSITION.read_text(encoding="utf-8")
    offenders = [line for line in source.splitlines()
                 if 'type_role === "display" ? "uppercase"' in line]
    assert not offenders, "composition still force-uppercases a display run"
    assert "run.uppercase" in source
