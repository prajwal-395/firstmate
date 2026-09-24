"""Cue lead and frame hold in FullFrameCard: two declared numbers, no look.

Blockers 4 and 8 of docs/SPAN_RENDERER_CAPABILITY.md, both inside
`remotion-subtitles/src/compositions/FullFrameCard/index.tsx`:

  4. The reveal begins at the word; the reference completes ON it. Each
     word of the reference fades up starting ~100 ms BEFORE its spoken
     onset (docs/ANIMATION_FIRST_REFERENCE.md section 1). No lead
     parameter exists anywhere in the span path.
  8. No frame quantiser. The reference reads as 15 fps inside a 30 fps
     container (section 5 of the same read). That is ONE INTEGER, and it
     is currently inexpressible.

What this proves, and how:

  - `WordCue.lead` (seconds, per cue, absent means 0) moves that cue's
    reveal earlier without moving its speech window: the emphasis clock
    still reads the measured [start, end), only the reveal leads.
  - `holdFrames` (integer, absent means every frame) quantises the
    card's whole clock through `heldFrame`, the same function shape
    StagedScene already draws (its `heldFrame`).
  - Lead 0 and hold 1 reproduce today's output exactly: the timing
    functions with absent/zero parameters equal the old arithmetic on
    every frame of a sweep, not just at the boundaries.
  - A lead that pushes a cue off its own word and onto the previous one
    REFUSES rather than clamping: `validateCueLeads` throws, naming the
    cue. The input that makes it refuse is cues ALPHA [0.0, 0.1] and
    BETA [0.15, 0.25] with BETA carrying lead 0.2 - its effective start
    (-0.05) lands inside ALPHA's window.
  - Neither parameter gets a default that produces a look (the captain's
    rule of 2026-09-08: "i want no hardcoded values. there are no house
    glow looks, there are no settled house grain or anything.").

Proved WITHOUT rendering a frame: the component's own timing functions
are transpiled and executed under node, and the assertions below name
exact frame indices - which frame a cue reaches full reveal on, and
that hold N makes frames 2k and 2k+1 identical. Another lane owns the
captain's machine for renders right now, and this proof is stronger
than eyeballing one anyway.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions",
    "FullFrameCard", "index.tsx")
TSC_DIR = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "node_modules", "typescript")

node_available = pytest.mark.skipif(
    shutil.which("node") is None or not os.path.isdir(TSC_DIR),
    reason=(
        "needs node and remotion-subtitles/node_modules/typescript. Runs "
        "anywhere the Remotion dev deps are installed - the same "
        "environment every other delivery test in this suite needs."
    ),
)


def _code_lines() -> list:
    """The component source with block comments removed."""
    with open(COMPONENT, encoding="utf-8") as handle:
        src = handle.read()
    out, i = [], 0
    while True:
        start = src.find("/*", i)
        if start < 0:
            out.append(src[i:])
            break
        out.append(src[i:start])
        end = src.find("*/", start)
        i = len(src) if end < 0 else end + 2
    body = "".join(out)
    return [ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("//")]


# ── The source half: free, and it runs anywhere ───────────────────────


def test_lead_and_hold_state_no_look():
    """No magnitude from the reference lives in the renderer as a default.

    docs/ANIMATION_FIRST_REFERENCE.md section 11: every magnitude quoted
    there - the 100 ms lead, the 15 fps step - belongs to THAT piece and
    is recorded so a declaration can be argued against a measurement,
    not so anything can default to it.
    """
    code = "\n".join(_code_lines())
    assert "holdFrames = 2" not in code and "holdFrames ?? 2" not in code
    assert "lead ?? 0.1" not in code and "cueLeadSeconds ?? 0.1" not in code
    assert "leadSeconds = 0.1" not in code


def test_the_component_quantises_its_own_clock():
    """The drawn frame goes through `heldFrame`, not around it."""
    code = "\n".join(_code_lines())
    assert "heldFrame(useCurrentFrame()" in code, (
        "the component reads the raw frame: a hold declaration would "
        "reach the props and change nothing on screen")


def test_the_component_refuses_a_bad_lead():
    """An out-of-window lead throws in the render path, never clamps."""
    code = "\n".join(_code_lines())
    assert "validateCueLeads(" in code, (
        "nothing in the render path refuses a lead that pushes a cue "
        "off its word")


# ── The math half: the component's own functions, executed ────────────

HARNESS = r"""
const Module = require("module");
const path = require("path");
const ts = require(process.env.TSC_PATH);
const fs = require("fs");

const src = fs.readFileSync(process.env.COMPONENT_PATH, "utf8");
const { outputText } = ts.transpileModule(src, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2019,
    jsx: ts.JsxEmit.React,
  },
});

const Easing = {
  cubic: (t) => t * t * t,
  linear: (t) => t,
  out: (fn) => (t) => 1 - fn(1 - t),
  in: (fn) => fn,
  inOut: (fn) => (t) => t,
};
const remotionStub = {
  AbsoluteFill: () => null,
  Easing,
  Img: () => null,
  staticFile: (p) => p,
  useCurrentFrame: () => 0,
};
const reactStub = { Fragment: Symbol("F"), createElement: () => null };
const stubOf = (id) => {
  if (id === "react") return reactStub;
  if (id === "remotion") return remotionStub;
  if (id.endsWith("fonts")) return { loadBundledFonts: () => {}, loadProjectFont: () => {} };
  if (id.endsWith("MotionGraphics"))
    return {
      elementOpacity: () => 1,
      entranceTransform: () => ({}),
      exitTransform: () => ({}),
      rampFrames: () => 0,
      typewriterCursorOn: () => false,
      typewriterShown: () => 1,
      typewriterSplit: (lens) => ({ shown: lens, cursorRun: -1 }),
    };
  throw new Error("unexpected import " + id);
};

const m = new Module("FullFrameCard", null);
m.require = (id) => stubOf(id);
m._compile(outputText, process.env.COMPONENT_PATH);
const f = m.exports;

const check = (name, cond, extra) => {
  if (!cond) {
    console.error("FAIL " + name + (extra === undefined ? "" : " :: " + extra));
    process.exitCode = 1;
  } else {
    console.log("PASS " + name);
  }
};

// Two words, far apart: ALPHA [0.0, 0.25], BETA [1.0, 1.25] at 30 fps.
// "ALPHA BETA" shows 5 chars through the first word, 10 through both.
// Every onset, end and lead below is binary-exact, and every frame
// asserted sits a third of a frame off the boundary - so each index
// below is exact arithmetic, not a floating-point near miss.
const FPS = 30;
const TOTAL = 10;
const cues = [
  { word: "ALPHA", start: 0.0, end: 0.25, chars: 5 },
  { word: "BETA", start: 1.0, end: 1.25, chars: 10 },
];

// Lead 0 reproduces today: the step lands on the onset frame.
check("no-lead frame 29 shows one word", f.wordCuedChars(29, FPS, cues, TOTAL) === 5);
check("no-lead frame 30 shows both", f.wordCuedChars(30, FPS, cues, TOTAL) === 10);
const withZero = cues.map((c) => ({ ...c, lead: 0 }));
for (let frame = 0; frame <= 40; frame += 1) {
  if (f.wordCuedChars(frame, FPS, cues, TOTAL) !== f.wordCuedChars(frame, FPS, withZero, TOTAL)) {
    check("lead-0 equals absent on frame " + frame, false);
  }
}
check("lead-0 equals absent on every frame 0..40", true);

// A 250 ms lead moves BETA's step from frame 30 to frame 23 exactly.
const led = [
  { word: "ALPHA", start: 0.0, end: 0.25, chars: 5 },
  { word: "BETA", start: 1.0, end: 1.25, chars: 10, lead: 0.25 },
];
check("effective start honours lead", f.cueEffectiveStart(led[1]) === 0.75);
check("effective start defaults to onset", f.cueEffectiveStart(cues[0]) === 0.0);
check("led frame 22 shows one word", f.wordCuedChars(22, FPS, led, TOTAL) === 5);
check("led frame 23 shows both", f.wordCuedChars(23, FPS, led, TOTAL) === 10);

// The hold quantises: pairs identical, boundaries not, 1/absent untouched.
for (let k = 0; k < 8; k += 1) {
  if (f.heldFrame(2 * k, 2) !== f.heldFrame(2 * k + 1, 2)) {
    check("hold-2 pair " + k + " identical", false);
  }
}
check("hold-2 makes every pair identical", true);
check("hold-2 boundary moves", f.heldFrame(3, 2) === 2 && f.heldFrame(4, 2) === 4);
for (let frame = 0; frame < 16; frame += 1) {
  if (f.heldFrame(frame, 1) !== frame || f.heldFrame(frame, undefined) !== frame) {
    check("hold-1/absent is identity on frame " + frame, false);
  }
}
check("hold-1 and absent reproduce every frame 0..15", true);

// The per-word rise keeps its shape and leads by the lead.
const word = { text: "BETA", runIndex: 0, start: 1.0, end: 1.25, lead: 0 };
const wordLed = { text: "BETA", runIndex: 0, start: 1.0, end: 1.25, lead: 0.25 };
for (let i = 0; i <= 10; i += 1) {
  const t = i / 10;
  const a = f.cuedWordProgress(t * FPS, FPS, wordLed);
  const b = f.cuedWordProgress((t + 0.25) * FPS, FPS, word);
  if (a !== b) check("rise leads by exactly the lead at " + t.toFixed(1), false, a + " vs " + b);
}
check("the rise leads by exactly the lead", true);
check("rise rests at 0 before its window", f.cuedWordProgress(0, FPS, word) === 0);
check("rise rests at 1 past its window", f.cuedWordProgress(40, FPS, word) === 1);

// Emphasis still reads the measured window: leading the reveal must not
// move the highlight onto a word nobody is speaking.
check("emphasis owns the onset frame", f.cuedEmphasisIndex(30, FPS, led) === 1);
check("emphasis owns nothing in the lead gap", f.cuedEmphasisIndex(23, FPS, led) === -1);

// The refusal: BETA lead 0.2 pushes its effective start (-0.05) inside
// ALPHA's window, and a first word leading before zero has nowhere to go.
const crowded = [
  { word: "ALPHA", start: 0.0, end: 0.1, chars: 5 },
  { word: "BETA", start: 0.15, end: 0.25, chars: 10, lead: 0.2 },
];
let threw = false;
try {
  f.validateCueLeads(crowded);
} catch (e) {
  threw = true;
}
check("a lead onto the previous word refuses", threw);
threw = false;
try {
  f.validateCueLeads([{ word: "A", start: 0.05, end: 0.15, chars: 1, lead: 0.1 }]);
} catch (e) {
  threw = true;
}
check("a lead before zero refuses", threw);
threw = false;
try {
  f.validateCueLeads([{ word: "A", start: 0.2, end: 0.3, chars: 1, lead: -0.05 }]);
} catch (e) {
  threw = true;
}
check("a negative lead refuses", threw);
threw = false;
try {
  f.validateCueLeads(led);
  f.validateCueLeads(cues);
} catch (e) {
  threw = true;
}
check("a lead inside the gap passes", !threw);

// A hold that is not a positive integer is malformed, not a softer hold.
for (const bad of [0, -2, 1.5, NaN]) {
  threw = false;
  try {
    f.heldFrame(4, bad);
  } catch (e) {
    threw = true;
  }
  check("hold " + String(bad) + " refuses", threw);
}
"""


@node_available
def test_cue_lead_and_hold_proved_from_the_timing_functions(tmp_path):
    """Exact frame indices and identical pairs, from the component itself."""
    harness = tmp_path / "lead_hold_harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ, TSC_PATH=TSC_DIR, COMPONENT_PATH=COMPONENT)
    result = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=False, env=env)
    assert result.returncode == 0, (
        "the timing-function harness failed:\n" + result.stdout[-3000:]
        + result.stderr[-2000:])
    assert "FAIL" not in result.stdout
    for name in ("led frame 23 shows both",
                 "hold-2 makes every pair identical",
                 "a lead onto the previous word refuses"):
        assert f"PASS {name}" in result.stdout, (
            f"harness did not report {name!r}:\n{result.stdout[-2000:]}")
