"""The planning step is actually ASKED for semantic visuals.

The motion-graphics planner is ASKED for anchored semantic visuals: the
machine-readable output schema names the anchor keys, no worked example
teaches the `conflicting_timing` shape, and a planner-authored entry
lands on its measured word. Incident: `docs/evidence/semantic_visual.md`.
"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.bridge import (
    timeline_rows,
)
from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (
    generate_motion_props,
)

STEP_DIR = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics")


def _manifest():
    with open(os.path.join(STEP_DIR, "manifest.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _handoff():
    with open(os.path.join(STEP_DIR, "handoff.md"), encoding="utf-8") as f:
        return f.read()


def test_the_machine_readable_schema_names_the_anchor_keys():
    """The `llm_outputs` description is the Required Output Format block.

    `run_pipeline.generate_output_schema_text` renders it verbatim into
    the prompt and the agent request file carries it as `expected_schema`.
    If `subject`/`anchor_phrase`/`hold_seconds` are named nowhere in it,
    the model is not asked - handoff prose notwithstanding.
    """
    manifest = _manifest()
    llm_outputs = manifest["interface"]["llm_outputs"]
    plan = next(o for o in llm_outputs if o["name"] == "motion_graphics_plan")
    for key in ("subject", "anchor_phrase", "hold_seconds"):
        assert key in plan["description"], (
            f"{key!r} is named nowhere in the motion_graphics_plan "
            f"llm_outputs description, so the rendered Required Output "
            f"Format block never asks for it")


def test_no_worked_example_mixes_anchor_with_declared_seconds():
    """No single example entry may carry both timings.

    An entry naming `anchor_phrase` beside `start_seconds` or
    `duration_seconds` is dropped as `conflicting_timing` - the engine
    picks neither. An example showing both teaches the failing shape.
    """
    blocks = re.findall(r"```json\n(.*?)```", _handoff(), re.DOTALL)
    assert blocks, "the handoff carries no JSON answer template at all"
    checked = 0
    for block in blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if not isinstance(entry, dict) or "element" not in entry:
                continue
            checked += 1
            if str(entry.get("anchor_phrase") or "").strip():
                assert entry.get("start_seconds") is None, (
                    "worked example names anchor_phrase beside "
                    "start_seconds - resolve_plan drops that shape as "
                    "conflicting_timing")
                assert entry.get("duration_seconds") is None, (
                    "worked example names anchor_phrase beside "
                    "duration_seconds - resolve_plan drops that shape "
                    "as conflicting_timing")
    assert checked, "no example entry with an element key was found"


def _spine():
    """One block of speech, with source-clocked word measurements.

    The words say the rent is due; the planner's subject below is one
    nobody enumerated - no table maps these words to anything.
    """
    words = ["the", "rent", "is", "due", "on", "friday"]
    return {"structure": [{
        "position": 1,
        "block_type": "body",
        "clip_id": "clip_001",
        "timeline_start": 40.0,
        "timeline_end": 46.0,
        "source_start": 10.0,
        "source_end": 16.0,
        "content": {"text": "the rent is due on friday no exceptions"},
        "word_timestamps": [
            {"word": w, "start": 10.0 + i * 0.4, "end": 10.3 + i * 0.4}
            for i, w in enumerate(words)
        ],
    }]}


def test_a_planner_authored_entry_quoted_from_bridge_context_lands():
    """Context the bridge built, entry the planner reasoned, landing real.

    The anchor phrase is quoted from the bridge's own
    `timeline_context_toon` table - the only speech the prompt shows -
    and the entry carries no `start_seconds`/`duration_seconds`, the
    shape the fixed handoff teaches. `generate_motion_props` is the
    runner's own resolution half, so a landing here is a landing on a
    run.
    """
    spine = _spine()
    rows = timeline_rows(spine)
    says = " ".join(r["says"] for r in rows)
    assert "rent is due" in says

    entry = {
        "element": "subject_emblem",
        "subject": "deadline pressure - the rent lands friday",
        "anchor_phrase": "rent is due",
        "hold_seconds": 2.0,
        "anchor": "middle_right",
        "copy": {"display": "FRI", "supporting": "rent due friday"},
        "color": "#F5C518",
        "why": "answers when the deadline the speech names actually is",
    }
    segments, resolved = generate_motion_props(
        [entry], spine, fps=30, width=1080, height=1920,
        project_folder="")
    assert resolved.basis == "elements_planned", (
        f"planner-authored entry did not survive: "
        f"{[(d.element, d.reason) for d in resolved.dropped]}")
    moment = resolved.moments[0]
    assert moment["timing_basis"] == "word_window:rent is due"
    assert moment["subject"] == "deadline pressure - the rent lands friday"
    assert moment["timeline_start"] == segments[0]["timeline_start"]
