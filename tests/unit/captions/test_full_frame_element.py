"""The full-frame element: its roster, its declaration, and its refusals.

Every gate here is proved in BOTH directions.  A declaration that should
be refused is asserted to raise AND the neighbouring valid one is
asserted to pass, because a validator that refuses everything reads
exactly like a validator that works (AGENTS.md 10.4).

The timeline half - what the conformance verifier does when picture is a
graphic rather than footage - is in
``tests/unit/captions/test_full_frame_element.py``.
"""
from __future__ import annotations
import copy
import json
from dataclasses import replace
import pytest
from library.tools import full_frame_element as ffe
import os
import shutil
import subprocess
import sys
from library.tools import explainer_plan as ex
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelTimeline,
    TimelineItem,
    _snapshot_to_reel_timeline,
    check_explainer,
)
from library.tools.reel_conformance_verifier import (
    PlannedCard,
    PlannedPlacement,
    card_items,
    check_delivered_framing,
    check_full_frame_cards,
    check_item_count,
    check_picture_holes,
    check_plan_describes_timeline,
)


# ── The roster ───────────────────────────────────────────────────────


def test_the_roster_is_well_formed_and_the_gate_can_fail():
    """The gate refuses an entry stating only what a thing IS."""
    ffe.assert_roster_is_well_formed()
    bad = replace(ffe.ROSTER[0], never=())
    original = ffe.ROSTER
    try:
        ffe.ROSTER = original + (replace(bad, key="silent_entry"),)
        with pytest.raises(ffe.FullFrameVocabularyError, match="no refusals"):
            ffe.assert_roster_is_well_formed()
    finally:
        ffe.ROSTER = original
    ffe.assert_roster_is_well_formed()


def test_the_boundary_names_the_module_that_owns_each_near_miss():
    """A near miss is REDIRECTED, never merely refused."""
    for key, why in ffe.OUT_OF_VOCABULARY.items():
        assert ".py" in why or "AGENTS.md" in why, (
            f"{key} is refused without naming who owns it: {why!r}")


# ── The declaration ──────────────────────────────────────────────────


VALID = {
    "element": "full_frame_card",
    "placement": "head",
    "duration_seconds": 2.0,
    "background": "#101014",
    "entrance": "blur",
    "exit": "fade",
    "font_family": "Montserrat",
    "runs": [
        {"bind": "opening_line", "type_role": "display", "colour": "#FFFFFF"},
    ],
}


def declare(**overrides):
    entry = copy.deepcopy(VALID)
    entry.update(overrides)
    return {"full_frame_elements": [entry]}


def test_a_project_that_declares_nothing_gets_nothing():
    assert ffe.declared_elements(None) == []
    assert ffe.declared_elements({}) == []
    assert ffe.declared_elements({"full_frame_elements": []}) == []


def test_the_valid_declaration_is_accepted():
    """The other half of every refusal below."""
    normalised = ffe.declared_elements(declare())
    assert len(normalised) == 1
    assert normalised[0]["placement"] == "head"
    assert normalised[0]["duration_seconds"] == 2.0
    assert normalised[0]["runs"][0]["bind"] == "opening_line"


MALFORMED_DECLARATIONS = [
    ({"element": None}, "names no `element`"),
    ({"element": "intro_card"}, "bookend"),
    ({"element": "title_lockup"}, "overlay roster"),
    ({"element": "not_a_thing"}, "does not draw"),
    ({"placement": "middle"}, "no default"),
    ({"placement": None}, "no default"),
    ({"duration_seconds": 0}, "lasts no time"),
    ({"duration_seconds": -1}, "lasts no time"),
    ({"duration_seconds": True}, "lasts no time"),
    ({"duration_seconds": ffe.MAX_CARD_SECONDS + 0.1}, "not a card"),
    ({"background": ""}, "no `background`"),
    ({"background": None}, "no `background`"),
    ({"entrance": "shimmer"}, "motion characters"),
    ({"exit": "shimmer"}, "motion characters"),
    ({"font_family": ""}, "no `font_family`"),
    ({"font_family": "Nonesuch Display"}, "cannot deliver"),
    ({"runs": []}, "no `runs`"),
    ({"runs": None}, "no `runs`"),
    ({"y": 1.4}, "between 0 and 1"),
    ({"y": "middle"}, "between 0 and 1"),
    ({"opening_seconds": 0}, "positive number"),
]


def test_a_malformed_declaration_is_refused_by_name():
    for overrides, expect in MALFORMED_DECLARATIONS:
        with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
            ffe.declared_elements(declare(**overrides))
            pytest.fail(f"accepted {overrides}")


MALFORMED_RUNS = [
    ({"text": "A", "bind": "speakers", "type_role": "display",
      "colour": "#FFF"}, "both `text` and `bind`"),
    ({"type_role": "display", "colour": "#FFF"}, "neither `text` nor `bind`"),
    ({"bind": "episode_title", "type_role": "display", "colour": "#FFF"},
     "does not produce"),
    ({"text": "  ", "type_role": "display", "colour": "#FFF"},
     "empty `text`"),
    ({"text": "A", "type_role": "headline", "colour": "#FFF"},
     "typographic weights"),
    ({"text": "A", "type_role": "display"}, "no `colour`"),
    ({"text": "A", "type_role": "display", "colour": "#FFF",
      "font_size": -4}, "positive number"),
]


def test_a_malformed_run_is_refused_by_name():
    for run, expect in MALFORMED_RUNS:
        with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
            ffe.declared_elements(declare(runs=[run]))
            pytest.fail(f"accepted {run}")
    # The other direction: literal copy is legal and is not a binding.
    normalised = ffe.declared_elements(declare(runs=[
        {"text": "Season two", "type_role": "supporting", "colour": "#FFF"}]))
    assert normalised[0]["runs"][0]["text"] == "Season two"
    assert normalised[0]["runs"][0]["bind"] is None


def test_the_project_yaml_wins_over_the_brand_template(tmp_path):
    """A card is copy the viewer reads, so the project owns it."""
    (tmp_path / "project.yaml").write_text(
        "effect:\n"
        "  full_frame_elements:\n"
        "    - element: full_frame_card\n"
        "      placement: tail\n"
        "      duration_seconds: 1.0\n"
        "      background: '#000000'\n"
        "      font_family: Montserrat\n"
        "      runs:\n"
        "        - text: from the project\n"
        "          type_role: micro\n"
        "          colour: '#FFFFFF'\n",
        encoding="utf-8")
    template_slot = {"full_frame_elements": [dict(VALID)]}
    resolved = ffe.resolve_declaration(template_slot, str(tmp_path))
    elements = ffe.declared_elements(resolved)
    assert len(elements) == 1
    assert elements[0]["placement"] == "tail"
    assert elements[0]["runs"][0]["text"] == "from the project"


# ── The facts a card may quote ───────────────────────────────────────


class _Moment:
    number = 7
    speakers = ("SpeakerOne", "SpeakerTwo")


def _transcript():
    return {"segments": [{
        "speaker": "SpeakerOne", "timeline_start": 10.0, "timeline_end": 16.0,
        "text": "So ranking number one on Google but invisible to AI",
        "words": [
            {"word": w, "start": 10.0 + i * 0.4, "end": 10.35 + i * 0.4,
             "timed": True}
            for i, w in enumerate(
                "So ranking number one on Google but invisible to AI".split())
        ],
    }]}


def test_the_opening_line_is_quoted_from_the_ranges_the_reel_plays_or_refused():
    facts = ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=3.0)
    assert facts.opening_line().startswith("So ranking number one")
    assert facts.reel_number == 7
    # Never a quotation quietly shorter than the one declared.
    facts = ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=2.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="measured over"):
        facts.opening_line(6.0)


def test_a_binding_with_nothing_behind_it_refuses_the_card():
    """An empty run is never drawn and never substituted."""
    declarations = ffe.declared_elements(declare())
    empty = ffe.ReelFacts(reel_number=3, speakers=(), opening=(),
                          opening_window=3.0)
    with pytest.raises(ffe.FullFrameDeclarationError, match="nothing there"):
        ffe.plan_reel_cards(declarations, empty, 960, 24000 / 1001, width=1080, height=1920)


# ── Planning ─────────────────────────────────────────────────────────


FPS = 24000 / 1001


def _facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), [(10.0, 16.0)], _transcript(), opening_seconds=3.0)


def test_a_head_card_starts_at_reel_zero_and_a_tail_card_after_the_body():
    head = copy.deepcopy(VALID)
    tail = copy.deepcopy(VALID)
    tail["placement"] = "tail"
    tail["duration_seconds"] = 1.5
    tail["runs"] = [{"bind": "speakers", "type_role": "micro",
                     "colour": "#FFF"}]
    declarations = ffe.declared_elements(
        {"full_frame_elements": [head, tail]})
    body_frames = 960
    cards = ffe.plan_reel_cards(declarations, _facts(), body_frames, FPS, width=1080, height=1920)
    assert [c.placement for c in cards] == ["head", "tail"]
    assert cards[0].reel_start_frame == 0
    # The tail card starts on the frame after the last one of picture -
    # integer arithmetic, so there is no rounding gap to be an F1 hole.
    assert cards[1].reel_start_frame == cards[0].duration_frames + body_frames
    assert cards[0].reel_end_frame == cards[0].duration_frames


def test_the_props_carry_the_declaration_and_nothing_the_engine_chose():
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    props = cards[0].props
    assert props["background"] == "#101014"
    assert props["entrance"] == "blur"
    assert props["fontFamily"] == "Montserrat"
    assert props["width"] == 1080 and props["height"] == 1920
    assert props["safeArea"]["top"] > 0
    assert props["durationInFrames"] == int(round(2.0 * FPS))
    # The engine states no position when the declaration states none.
    assert "y" not in props
    assert props["runs"][0]["colour"] == "#FFFFFF"
    assert props["runs"][0]["text"].startswith("So ranking")


def test_a_card_that_rounds_to_no_frames_is_refused():
    declarations = ffe.declared_elements(declare(duration_seconds=0.001))
    with pytest.raises(ffe.FullFrameDeclarationError, match="at least one"):
        ffe.plan_reel_cards(declarations, _facts(), 960, FPS, width=1080, height=1920)


# ── Rendering ────────────────────────────────────────────────────────


def _fake_batch_success(seen):
    """A stand-in for the shared batch renderer that draws every job."""
    def fake_batch(jobs, **kwargs):
        seen["calls"] = seen.get("calls", 0) + 1
        seen["composition"] = kwargs.get("composition")
        seen["job_count"] = len(jobs)
        results = []
        for job in jobs:
            with open(job.out_path, "wb") as handle:
                handle.write(b"not empty")
            results.append({"ok": True, "out": job.out_path})
        return results
    return fake_batch


def test_the_render_batches_opaque_through_the_shared_renderer(
        monkeypatch, tmp_path):
    """One batch for every card, on the card's own declared ground.

    The one command-line difference between this layer and the overlay
    layer used to be asserted off the argv (no ``--transparent``); now
    that every card goes through the shared batch renderer, what is
    asserted is the route itself - one ``render_batch`` call carrying
    every card under the FullFrameCard composition - and the opacity
    half is the props: the card carries its own background ground
    rather than relying on black showing through an alpha channel that
    nothing is beneath.
    """
    seen = {}
    monkeypatch.setattr(ffe, "render_batch", _fake_batch_success(seen))
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    rendered = ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))

    assert seen["calls"] == 1, (
        "every card in one batch: one bundle-and-launch, not one per card")
    assert seen["composition"] == ffe.FULL_FRAME_COMPOSITION
    assert seen["job_count"] == len(cards)
    assert rendered[0].rendered_path.endswith(".mov")
    # The props really reached disk beside the render.
    props = json.loads(
        (tmp_path / f"{cards[0].render_name}_props.json").read_text())
    assert props["background"] == "#101014"


def test_a_render_that_produces_no_file_raises(monkeypatch, tmp_path):
    def fake_batch(jobs, **kwargs):
        return [{"ok": True, "out": job.out_path} for job in jobs]

    monkeypatch.setattr(ffe, "render_batch", fake_batch)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements(declare()), _facts(), 960, FPS, width=1080, height=1920)
    with pytest.raises(ffe.FullFrameRenderError, match="missing or empty"):
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))


# ── The composition really exists ────────────────────────────────────


# --------------------------------------------------------------------------
# From test_fullframe_cue_lead_hold.py
#
# Cue lead and frame hold in FullFrameCard: two declared numbers, no look.
#
# Blockers 4 and 8 of docs/SPAN_RENDERER_CAPABILITY.md, both inside
# `remotion-subtitles/src/compositions/FullFrameCard/index.tsx`:
#
#   4. The reveal begins at the word; the reference completes ON it. Each
#      word of the reference fades up starting ~100 ms BEFORE its spoken
#      onset (docs/ANIMATION_FIRST_REFERENCE.md section 1). No lead
#      parameter exists anywhere in the span path.
#   8. No frame quantiser. The reference reads as 15 fps inside a 30 fps
#      container (section 5 of the same read). That is ONE INTEGER, and it
#      is currently inexpressible.
#
# What this proves, and how:
#
#   - `WordCue.lead` (seconds, per cue, absent means 0) moves that cue's
#     reveal earlier without moving its speech window: the emphasis clock
#     still reads the measured [start, end), only the reveal leads.
#   - `holdFrames` (integer, absent means every frame) quantises the
#     card's whole clock through `heldFrame`, the same function shape
#     StagedScene already draws (its `heldFrame`).
#   - Lead 0 and hold 1 reproduce today's output exactly: the timing
#     functions with absent/zero parameters equal the old arithmetic on
#     every frame of a sweep, not just at the boundaries.
#   - A lead that pushes a cue off its own word and onto the previous one
#     REFUSES rather than clamping: `validateCueLeads` throws, naming the
#     cue. The input that makes it refuse is cues ALPHA [0.0, 0.1] and
#     BETA [0.15, 0.25] with BETA carrying lead 0.2 - its effective start
#     (-0.05) lands inside ALPHA's window.
#   - Neither parameter gets a default that produces a look (the captain's
#     rule of 2026-09-08: "i want no hardcoded values. there are no house
#     glow looks, there are no settled house grain or anything.").
#
# Proved WITHOUT rendering a frame: the component's own timing functions
# are transpiled and executed under node, and the assertions below name
# exact frame indices - which frame a cue reaches full reveal on, and
# that hold N makes frames 2k and 2k+1 identical. Another lane owns the
# captain's machine for renders right now, and this proof is stronger
# than eyeballing one anyway.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
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


# --------------------------------------------------------------------------
# From test_explainer_plan.py
#
# The animated explainer: what it refuses, and that every refusal fires.
#
# A gate that cannot fail is worse than no gate (AGENTS.md 10.4), so most
# of this file is the refusals rather than the happy path.  Each one is
# provoked by data a real reading could really produce.
#
# `tests/unit/captions/test_full_frame_element.py` is the other half - F21,
# which grades a built timeline against the plan the build recorded.

# ── Lines a reel really plays ────────────────────────────────────────

def _line(at, says, words=None):
    line = {"at": at, "speaker": "SpeakerOne", "says": says}
    if words is not None:
        line["words"] = words
    return line


def _timed(at, text, gap=0.4):
    """A line whose words carry their own reel seconds."""
    words = []
    second = at
    for token in text.split(" "):
        words.append({"word": token, "at": round(second, 3)})
        second += gap
    return _line(at, text, words)


LINES = [
    _timed(10.0, "AI is going to see your LinkedIn your Crunchbase"),
    _timed(14.0, "Reddit threads your Instagram press mentions"),
]

PARTS = [
    {"part": "LinkedIn", "quote": "your LinkedIn"},
    {"part": "Crunchbase", "quote": "your Crunchbase"},
    {"part": "Reddit", "quote": "Reddit threads"},
]

DECLARATION = {
    "element": "list_build",
    "band": "above",
    "anchor": "bottom_left",
    "hold_seconds": 2.0,
    "colour": "#FFB8D4",
    "type_role": "supporting",
}


# ── The roster subset ────────────────────────────────────────────────


# ── Anchoring ────────────────────────────────────────────────────────

def test_a_stage_is_anchored_to_the_word_its_quote_begins_on():
    out = ex.anchor_stages(PARTS, LINES, reel_seconds=30.0)
    assert not out.refused
    assert [s.text for s in out.stages] == ["LinkedIn", "Crunchbase",
                                            "Reddit"]
    assert all(s.precision == ex.WORD for s in out.stages)
    # "your LinkedIn" begins on the sixth word of the first line.
    assert out.stages[0].at_seconds == pytest.approx(10.0 + 5 * 0.4)
    assert out.stages[1].at_seconds == pytest.approx(10.0 + 7 * 0.4)
    assert out.stages[2].at_seconds == pytest.approx(14.0)


def test_a_stage_the_speech_cannot_place_is_refused_with_its_reason():
    out = ex.anchor_stages(
        [{"part": "Facebook", "quote": "your Facebook page"}],
        LINES, reel_seconds=30.0)
    assert not out.stages
    assert out.refused[0]["reason"] == ex.UNGROUNDED
    # The SPEECH is the order, not the list: revealing part three while
    # part one is being said is refused.
    out = ex.anchor_stages(
        [{"part": "Reddit", "quote": "Reddit threads"},
         {"part": "LinkedIn", "quote": "your LinkedIn"}],
        LINES, reel_seconds=30.0)
    assert [s.text for s in out.stages] == ["Reddit"]
    assert out.refused[0]["reason"] == ex.OUT_OF_ORDER
    out = ex.anchor_stages(PARTS, LINES, reel_seconds=11.0)
    assert ex.OFF_THE_END in {r["reason"] for r in out.refused}


# ── The declaration ──────────────────────────────────────────────────


MALFORMED = [
    ("element", "", "names no `element`"),
    ("element", "quote_card", "an element that stages"),
    ("band", "", "band"),
    ("band", "beside", "band"),
    ("anchor", "nowhere", "anchor"),
    ("hold_seconds", None, "hold_seconds"),
    ("hold_seconds", -1.0, "cannot be negative"),
    ("colour_role", "chartreuse", "colour_role"),
    ("type_role", "enormous", "type_role"),
    ("entrance", "explode", "entrance"),
    ("exit", "implode", "exit"),
]


def test_a_malformed_declaration_raises_by_name():
    """RAISES rather than being dropped: a declaration that vanishes
    into a log line is how the 4th Wall end card survived four months."""
    for field, value, message in MALFORMED:
        declaration = dict(DECLARATION)
        declaration[field] = value
        with pytest.raises(ex.ExplainerError, match=message):
            ex.normalise_declaration(declaration)
            pytest.fail(f"accepted {field}={value!r}")


# ── The plan ─────────────────────────────────────────────────────────

def test_the_plan_is_one_entry_whose_runs_are_the_stages():
    """ONE entry, not one per stage: the staging is INSIDE the element,
    so an entry per stage would draw three separate lists."""
    anchored = ex.anchor_stages(PARTS, LINES, reel_seconds=30.0)
    entries = ex.plan_entries(anchored, ex.normalise_declaration(DECLARATION))
    assert len(entries) == 1
    assert [run["text"] for run in entries[0]["copy"]] == [
        "LinkedIn", "Crunchbase", "Reddit"]


# ── The picture bands ────────────────────────────────────────────────


# ── Authoring, end to end ────────────────────────────────────────────

JUDGEMENT = {"readings": [{"reel": 7, "claim_parts": PARTS}]}


def test_authoring_names_the_basis_of_every_outcome():
    """`[]` for a reel with no parts is NOT 'nobody was asked': each
    outcome carries its own basis from `BASES`."""
    plan = ex.author_explainer("Reel 07", 7, 30.0, JUDGEMENT, DECLARATION,
                               lines=LINES)
    assert plan.basis == ex.PLANNED
    assert len(plan.entries) == 1
    assert len(plan.anchored.stages) == 3

    plan = ex.author_explainer("Reel 07", 7, 30.0, JUDGEMENT, None)
    assert plan.basis == ex.NOT_DECLARED
    assert plan.entries == []

    plan = ex.author_explainer("Reel 07", 7, 30.0,
                               {"readings": [{"reel": 7}]}, DECLARATION)
    assert plan.basis == ex.NO_PARTS

    plan = ex.author_explainer(
        "Reel 07", 7, 30.0,
        {"readings": [{"reel": 7,
                       "claim_parts": [{"part": "X", "quote": "not said"}]}]},
        DECLARATION, lines=LINES)
    assert plan.basis == ex.ALL_REFUSED
    assert plan.anchored.refused


def test_parts_for_reel_reads_the_reel_it_was_asked_for():
    judgement = {"readings": [{"reel": 1, "claim_parts": [{"part": "a"}]},
                              {"reel": 7, "claim_parts": PARTS}]}
    assert ex.parts_for_reel(judgement, 7) == PARTS
    assert ex.parts_for_reel(judgement, 99) == []
    assert ex.parts_for_reel(None, 7) == []


# ── The recorded plan ────────────────────────────────────────────────

def test_the_build_records_every_reel_including_the_empty_ones(tmp_path):
    plans = [
        ex.ExplainerPlan(reel_name="Reel 01", declared=False,
                         basis=ex.NOT_DECLARED),
        ex.ExplainerPlan(reel_name="Reel 07", declared=True,
                         basis=ex.PLANNED,
                         segments=[{"overlay_path": "/x.mov",
                                    "timeline_start": 1.0,
                                    "timeline_end": 3.0,
                                    "total_frames": 48,
                                    "elements": ["list_build"]}]),
    ]
    ex.write_plans(str(tmp_path), plans)
    back = ex.read_plans(str(tmp_path))
    assert len(back["plans"]) == 2
    assert ex.plan_for_reel(back, "Reel 07")["segments"][0]["total_frames"] == 48
    assert ex.plan_for_reel(back, "Reel 01")["segments"] == []
    assert ex.plan_for_reel(back, "Reel 99") is None
    assert ex.read_plans(str(tmp_path / "none")) == {}


def test_a_segment_name_carries_its_reel():
    """AGENTS.md 5: prefix an overlay filename with its context. Two
    reels writing one filename is one reel's graphic on another reel's
    timeline."""
    first = ex.segment_name("Reel 01 - a-slug", 0)
    second = ex.segment_name("Reel 02 - a-slug", 0)
    assert first != second
    assert first.startswith(ex.RENDER_PREFIX)


# ── The one field, in the one contract ───────────────────────────────

def test_claim_parts_is_in_the_judge_contract_and_is_optional():
    from library.tools.reel_quality_bar import READING_FIELDS, READING_SCHEMA
    field = [f for f in READING_FIELDS if f.name == ex.CLAIM_PARTS_KEY]
    assert field, "claim_parts is not in the judge's ask"
    assert field[0].required is False
    assert field[0].grounding == "contains"
    assert ex.CLAIM_PARTS_KEY in READING_SCHEMA["readings"][0]


def test_claim_parts_ground_the_reading_or_refuse_it():
    """The quote is the only thing that puts a part in time, so an
    ungrounded or quote-less part refuses the whole reading; the field
    is OPTIONAL, so a reading without it still grounds."""
    from library.tools.reel_quality_bar import check_reading
    said = "AI is going to see your LinkedIn"
    base = {"reel": 1, "claim_quote": "your LinkedIn",
            "opening_quote": "AI", "closing_quote": "your LinkedIn",
            "assumes_known": []}
    ungrounded, _ = check_reading(
        {**base, "closing_quote": "your Crunchbase",
         "claim_parts": [{"part": "Facebook", "quote": "your Facebook"}]},
        said + " your Crunchbase")
    assert any("claim_parts" in u for u in ungrounded)
    ungrounded, _ = check_reading(
        {**base, "claim_parts": [{"part": "LinkedIn", "quote": ""}]}, said)
    assert any("no quote" in u for u in ungrounded)
    ungrounded, _ = check_reading(
        {**base, "claim_parts": [{"part": "LinkedIn",
                                  "quote": "your LinkedIn"}]}, said)
    assert ungrounded == []
    assert check_reading(base, said)[0] == []


def test_the_ask_naming_claim_parts_is_still_uncontaminated():
    """Adding a field must not hand the judge the answer sheet."""
    from library.tools.reel_quality_bar import (
        READING_SCHEMA, assert_ask_is_uncontaminated)
    assert_ask_is_uncontaminated(json.dumps(READING_SCHEMA), "schema")
    for field in __import__(
            "library.tools.reel_quality_bar", fromlist=["x"]).READING_FIELDS:
        assert_ask_is_uncontaminated(field.asks, field.name)


# ── Words, and where they may go ─────────────────────────────────────

def test_an_omitted_optional_quote_does_not_refuse_the_reading():
    """The defect this test names: `normalise(None)` was the word
    "none", so a judge that answered an optional field by leaving the
    key out - rather than sending an empty string - had its whole
    reading refused.  A gate that fails correct output (AGENTS.md
    10.4), and it never fired only because every reading on disk
    happens to carry the key."""
    from library.tools.reel_quality_bar import normalise
    assert normalise(None) == ""


# ── Looking at what was drawn ────────────────────────────────────────

def _alpha_clip(path, width, height, rect, frames=4):
    """A ProRes 4444 clip with alpha, opaque in exactly `rect`.

    Built with ffmpeg, which the CI runner installs (AGENTS.md 9): 27
    library files shell out to it and every audio and video measurement
    path skipped itself without it.

    An OPAQUE box padded onto a transparent canvas rather than a
    `drawbox` over one - `drawbox` blends into the existing alpha and
    leaves the plane transparent, which produces a clip that measures as
    empty and would make this whole test read as a pass for the wrong
    reason.
    """
    import subprocess
    left, top, right, bottom = rect
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=white:s={right - left}x{bottom - top}:d=1",
         "-vf", (f"format=rgba,pad={width}:{height}:{left}:{top}"
                 f":color=0x00000000"),
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-y", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False)
    return result.returncode == 0


def test_measure_render_finds_the_ink_and_the_frame_edges_it_touches(tmp_path):
    clip = tmp_path / "ink.mov"
    if not _alpha_clip(clip, 1080, 1920, (90, 300, 500, 600)):
        raise AssertionError(
            "ffmpeg could not build an alpha clip; the CI runner installs "
            "ffmpeg (AGENTS.md 9) and this measurement is meaningless "
            "without it")
    measured = ex.measure_render(str(clip))
    assert measured["frame"] == [1080, 1920]
    assert measured["ink_pixels"] > 0
    # ProRes is chroma-subsampled 4:4:4 but the alpha plane is exact;
    # allow one row of codec rounding at each edge rather than pretending
    # a video codec is lossless.
    top, bottom = measured["rows"]
    assert abs(top - 300) <= 2 and abs(bottom - 599) <= 2
    assert measured["touches_frame_edge"] == []

    clip = tmp_path / "clipped.mov"
    if not _alpha_clip(clip, 1080, 1920, (0, 0, 400, 500)):
        raise AssertionError("ffmpeg could not build an alpha clip")
    measured = ex.measure_render(str(clip))
    assert "top" in measured["touches_frame_edge"]
    assert "left" in measured["touches_frame_edge"]


def test_measure_render_refuses_a_file_it_cannot_read(tmp_path):
    missing = tmp_path / "nothing.mov"
    with __import__("pytest").raises(ex.ExplainerError, match="could not read"):
        ex.measure_render(str(missing))


def _bands():
    from library.tools.safe_area import safe_area_for_frame
    return ex.picture_bands((0, 656, 1080, 1264), 1080, 1920,
                            safe_area_for_frame(1080, 1920))


def test_render_findings_grade_the_ink_against_the_band():
    """Edge ink is an error (a graphic inside a 90px inset cannot reach
    column zero unless the frame cut it off); one row of shadow past the
    shared band boundary is a warning, not an error (reel 21's real
    render: ink rows 320..657 against a band of 120..656 - failing it
    would fail correct output, AGENTS.md 10.4); ink inside the band says
    nothing; no ink at all is an error (AGENTS.md 10.2)."""
    def findings(ink, rows, edges):
        return ex.render_findings(
            {"ink_pixels": ink, "rows": rows,
             "cols": [90, 560] if rows else None,
             "touches_frame_edge": edges, "frame": [1080, 1920]},
            _bands(), {"band": "above"})

    edge = findings(10, [0, 657], ["top"])
    errors = [f for f in edge if f["severity"] == "error"]
    assert errors and errors[0]["code"] == ex.FRAME_EDGE_CLIPPED

    shadow = findings(61778, [320, 657], [])
    assert [f["severity"] for f in shadow] == ["warning"]
    assert "1 row(s) below" in shadow[0]["message"]

    assert findings(500, [320, 600], []) == []

    empty = findings(0, None, [])
    assert empty[0]["severity"] == "error"
    assert empty[0]["code"] == ex.NOTHING_TO_DRAW


# --------------------------------------------------------------------------
# From test_explainer_reel_conformance.py
#
# F21: the animated explainer, graded against the plan the build wrote.
#
# Both directions.  A check that only catches an absence reads as coverage
# while an out-of-band append walks past it (AGENTS.md 10.4), and the
# master already refuses that one by name
# (`bookends.assert_no_invented_bookends`).
#
# The other half - what an explainer refuses before it is ever built - is
# `tests/unit/captions/test_full_frame_element.py`.

def _item(start, frames, track=ex.EXPLAINER_TRACK, name="explainer_r07_00.mov"):
    return TimelineItem(
        track_type="video", track_index=track,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file="/x.mov", speaker=None, name=name,
        unique_id=f"id-{start}")


def _plan(*segments, basis=ex.PLANNED):
    return {"reel": "Reel 07", "basis": basis, "declared": True,
            "segments": [{"timeline_start": s, "total_frames": f,
                          "timeline_end": s + f / FPS,
                          "overlay_path": "/x.mov",
                          "elements": ["list_build"]}
                         for s, f in segments]}


# ── The record survives partial builds and rebuilds ──────────────────

def _stored_plans(project, plans):
    import json

    from library.tools.project_layout import Area, ProjectLayout
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / ex.PLAN_FILENAME).write_text(
        json.dumps({"format": "explainer_plans/1", "plans": plans}),
        encoding="utf-8")
    return review


def _read_plans(project):
    import json

    from library.tools.project_layout import Area, ProjectLayout
    path = (project / "pipeline_output" / "review" / ex.PLAN_FILENAME)
    return json.loads(path.read_text(encoding="utf-8"))["plans"]


def _explainer(reel, basis=ex.PLANNED):
    return ex.ExplainerPlan(reel_name=reel, declared=True, basis=basis)


# ── It passes what is right ──────────────────────────────────────────

def test_a_placed_explainer_matching_its_plan_passes():
    start = int(round(40.707 * FPS))
    findings = check_explainer(
        "Reel 07", [_item(start, 197)], _plan((40.707, 197)), FPS)
    assert findings == []


# ── It fails what is wrong, in both directions ───────────────────────

def test_a_planned_explainer_the_timeline_does_not_carry_fails():
    findings = check_explainer("Reel 07", [], _plan((40.707, 197)), FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F21]
    assert "no item" in findings[0].message


def test_an_item_no_plan_accounts_for_fails():
    """The out-of-band append. `bookends.assert_no_invented_bookends`
    refuses this on the master; here it is F21."""
    findings = check_explainer(
        "Reel 07", [_item(100, 50)], _plan(basis=ex.NO_PARTS), FPS)
    assert findings and findings[0].finding_class == FindingClass.F21
    assert "no explainer" in findings[0].message


# ── The track the verifier had been dropping ─────────────────────────

class _Clip:
    def __init__(self, track_type, track_index, start, end, name):
        self.track_type = track_type
        self.track_index = track_index
        self.timeline_start = start
        self.timeline_end = end
        self.duration = end - start
        self.source_in_frame = 0
        self.source_out_frame = 10
        self.source_file = "/x.mov"
        self.speaker = None
        self.name = name
        self.resolve_item_id = name
        self.transform = {}


class _Snapshot:
    timeline_name = "Reel 07"
    fps = FPS
    start_frame = 0
    end_frame = 1000
    width = 1080
    height = 1920

    def __init__(self, clips):
        self.clips = clips


def test_the_explainer_track_is_no_longer_dropped_on_the_floor():
    """`_snapshot_to_reel_timeline` classified by `track <= 2`,
    `== 3`, `audio`, and an implicit else that dropped the item. A clip
    on the explainer track was not read as a duplicate placement and
    not read as a picture hole - it was not read at all, which is the
    gate-that-cannot-fail shape wearing a different hat."""
    snapshot = _Snapshot([
        _Clip("video", 1, 0.0, 4.0, "LC4932.MXF"),
        _Clip("video", 3, 0.5, 2.0, "sub_x.mov"),
        _Clip("video", ex.EXPLAINER_TRACK, 1.0, 3.0, "explainer_r07_00.mov"),
        _Clip("audio", 1, 0.0, 4.0, "LC4932.MXF"),
    ])
    reel = _snapshot_to_reel_timeline(snapshot)
    assert len(reel.explainer_items) == 1
    assert reel.explainer_items[0].name == "explainer_r07_00.mov"
    # And it is NOT counted as picture, which would make F4 report an
    # extra item on a correct build.
    assert all(i.track_index <= 2 for i in reel.video_items)
    assert len(reel.video_items) == 1


# --------------------------------------------------------------------------
# From test_reel_conformance_full_frame.py
#
# What the conformance checks do when picture is a GRAPHIC, not footage.
#
# A full-frame element replaces picture on V1 for its own stretch of reel
# time (``library/tools/full_frame_element.py``).  Three checks in
# ``reel_conformance_verifier`` were written when every V1 item was a
# frame of the master, and all three would report a FALSE ERROR on a
# correct build:
#
# - ``check_item_count`` (F4) counts V1/V2 items against the plan's
#   placements, so the card reads as an extra item and its seconds land on
#   whichever speaker owns V1;
# - ``check_delivered_framing`` (F12) looks each item's source up in the
#   catalog, and a rendered card is not in it;
# - ``check_plan_describes_timeline`` is frame-exact, and the card's frames
#   come from no keep range.
#
# This file plants each of those and asserts the check now reads it right -
# and, in the same breath, that the NEW gate (F13) can still fail in both
# directions.  A check taught to ignore a card would be a check that cannot
# see one, which is the trade this file exists to refuse (AGENTS.md 10.4).

CARD_FRAMES = 53          # 2.2s at 23.976, as the planner rounds it
CARD_NAME = "reel_07_card_01"
CARD_FILE = f"/p/pipeline_output/scratch/reel_cards/{CARD_NAME}.mov"
FOOTAGE = "/m/LCATL0013.MXF"


def _item_2(track_index: int, start: int, frames: int,
          source_file: str = FOOTAGE, speaker="SpeakerOne",
          transform=None) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=track_index,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file=source_file, speaker=speaker,
        name=source_file.rsplit("/", 1)[-1],
        transform=dict(transform or {}))


def _card_item(start: int = 0, frames: int = CARD_FRAMES,
               track_index: int = 1, transform=None) -> TimelineItem:
    return _item_2(track_index, start, frames, source_file=CARD_FILE,
                 speaker=None, transform=transform)


def _card(start_frame: int = 0,
          frames: int = CARD_FRAMES) -> PlannedCard:
    return PlannedCard(render_name=CARD_NAME, placement="head",
                       reel_start_frame=start_frame, duration_frames=frames)


IDENTITY = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
            "CropLeft": 0.0, "CropRight": 0.0,
            "CropTop": 0.0, "CropBottom": 0.0}


# ── Identifying a card ───────────────────────────────────────────────


def test_a_card_is_identified_by_its_render_name_not_by_a_catalog_miss():
    """Positive identification. "Not in the catalog" would make every
    relink gap look like a full-frame element."""
    items = [_card_item(), _item_2(1, CARD_FRAMES, 100),
             _item_2(1, CARD_FRAMES + 100, 100, source_file="/m/unknown.MXF")]
    found = card_items(items, [_card()])
    assert list(found) == [CARD_NAME]
    assert found[CARD_NAME].source_file == CARD_FILE


# ── F13, both directions ─────────────────────────────────────────────


def test_f13_fails_a_card_missing_undeclared_or_above_the_picture():
    findings = check_full_frame_cards(
        "Reel 07", [_card()], [_item_2(1, 0, 400)], 1080, 1920, FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "no item on the timeline is it" in findings[0].message
    # The out-of-band append `bookends` refuses on the master, caught on
    # the reels path.
    findings = check_full_frame_cards(
        "Reel 07", [], [_card_item(), _item_2(1, CARD_FRAMES, 400)],
        1080, 1920, FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "no declaration accounts for it" in findings[0].message
    # The replace-versus-overlay ruling, as a gate.
    findings = check_full_frame_cards(
        "Reel 07", [_card()], [_card_item(track_index=4)], 1080, 1920, FPS)
    assert any("belongs on V1" in f.message for f in findings)
    assert any("cannot turn down" in f.message for f in findings)


# ── F4: the card is not an unplanned picture item ────────────────────


def _placements(n: int, frames: int) -> list:
    return [PlannedPlacement(track_index=1, speaker="SpeakerOne",
                             record_seconds=i * frames / FPS,
                             source_in=0.0, source_out=frames / FPS,
                             source_file=FOOTAGE)
            for i in range(n)]


def test_f4_counts_the_card_as_planned_and_still_catches_a_dropped_clip():
    items = [_card_item()] + [_item_2(1, CARD_FRAMES + i * 400, 400)
                              for i in range(2)]
    findings = check_item_count("Reel 07", _placements(2, 400), items, FPS,
                                cards=[_card()])
    assert findings == [], [f.message for f in findings]
    # Taught to ignore the card, not taught to ignore everything.
    items = [_card_item(), _item_2(1, CARD_FRAMES, 400)]
    findings = check_item_count("Reel 07", _placements(2, 400), items, FPS,
                                cards=[_card()])
    assert any("planned 2 picture items, found 1" in f.message
               for f in findings)


# ── F1: a card abutting the first clip is not a hole ─────────────────


def test_a_one_frame_gap_after_the_card_is_still_a_hole():
    findings = check_picture_holes(
        "Reel 07", [_card_item(), _item_2(1, CARD_FRAMES + 1, 400)])
    assert [f.finding_class for f in findings] == [FindingClass.F1]


# ── F12: the card is graded against the whole frame ──────────────────


LETTERBOX_SIZES = {FOOTAGE: {"width": 3840, "height": 2160, "rotation": 0}}


def test_f12_grades_a_full_frame_card_against_the_whole_frame():
    """A card drawn at the delivery size with an identity transform fills
    the frame, whatever the project declares about its FOOTAGE."""
    findings = check_delivered_framing(
        "Reel 07", [_card_item(transform=IDENTITY),
                    _item_2(1, CARD_FRAMES, 400, transform=IDENTITY)],
        1080, 1920, source_sizes=LETTERBOX_SIZES, declared_intent=0.0,
        cards=[_card()])
    assert findings == [], [f.message for f in findings]
    # The other direction: the gate is not an exemption.
    shrunk = dict(IDENTITY, ZoomX=0.5, ZoomY=0.5)
    findings = check_delivered_framing(
        "Reel 07", [_card_item(transform=shrunk)],
        1080, 1920, source_sizes={}, declared_intent=0.0, cards=[_card()])
    assert [f.finding_class for f in findings] == [FindingClass.F12]
    assert findings[0].severity == "error"


# ── PLAN-MISMATCH: the card's frames come from no keep range ─────────


def test_the_plan_mismatch_gate_still_fires_on_a_real_difference():
    keep = [(100.0, 120.0)]
    body = round(120.0 * FPS) - round(100.0 * FPS)
    findings = check_plan_describes_timeline(
        "Reel 07", keep, body + CARD_FRAMES - 9, FPS,
        card_frames=CARD_FRAMES)
    assert [f.finding_class for f in findings] == [FindingClass.PLAN_MISMATCH]
    assert "-9 frames" in findings[0].message


# ── The lead: picture and captions move together, or not at all ──────


def test_placements_shift_by_the_lead_in_whole_frames():
    from library.tools.reel_build import placements

    class _Clip:
        track_type = "video"
        track_index = 1
        speaker = "SpeakerOne"
        source_file = FOOTAGE
        timeline_start = 10.0
        timeline_end = 20.0
        source_in = 100.0

    plain = placements([(10.0, 20.0)], [_Clip()], FPS)
    shifted = placements([(10.0, 20.0)], [_Clip()], FPS,
                         lead_frames=CARD_FRAMES)
    assert plain[0]["snapped_record"] == 0
    assert shifted[0]["snapped_record"] == CARD_FRAMES
    # Which frames PLAY is unchanged - a card in front moves where a clip
    # lands, never what is in it.
    assert plain[0]["source_in"] == shifted[0]["source_in"]
    assert plain[0]["source_out"] == shifted[0]["source_out"]


def test_the_spine_moves_the_reel_clock_and_leaves_the_source_clock_alone():
    """A word's place in the raw clip does not move when something is
    placed in front of it (AGENTS.md 6)."""
    from library.tools.reel_spine import spine_for_reel

    transcript = {"segments": [{
        "speaker": "SpeakerOne", "timeline_start": 10.0, "timeline_end": 14.0,
        "text": "ranking number one on Google but invisible to AI entirely",
        "clip_id": "clip_001", "source_file": FOOTAGE,
        "source_start": 100.0, "source_end": 104.0,
        "words": [{"word": w, "start": 10.0 + i * 0.4,
                   "end": 10.35 + i * 0.4, "timed": True}
                  for i, w in enumerate(
                      "ranking number one on Google but invisible to AI "
                      "entirely".split())],
    }]}

    class _Moment:
        number = 7
        timeline_start = 10.0
        timeline_end = 14.0
        speakers = ("SpeakerOne",)

    plain = spine_for_reel(_Moment(), transcript, [(10.0, 14.0)])
    shifted = spine_for_reel(_Moment(), transcript, [(10.0, 14.0)],
                             lead_seconds=2.2)
    assert plain["structure"] and shifted["structure"]
    for before, after in zip(plain["structure"], shifted["structure"]):
        assert after["timeline_start"] == pytest.approx(
            before["timeline_start"] + 2.2)
        assert after["timeline_end"] == pytest.approx(
            before["timeline_end"] + 2.2)
        assert after["source_start"] == before["source_start"]
        assert after["source_end"] == before["source_end"]
        assert after["word_timestamps"] == before["word_timestamps"]


def test_a_tail_card_abuts_the_last_clip_on_a_range_that_does_not_round_evenly():
    """The head card's one-frame bug, checked at the OTHER end.

    A tail card placed from `round(seconds * fps)` lands a frame away
    from the last clip on ranges whose edges do not round evenly - a
    black hole if it is late, an overlap if it is early. Both sides are
    integer frame arithmetic instead, so the ranges below still abut
    exactly.

    Routed through `reel_build.plan_cards` rather than
    `plan_reel_cards` directly, because the number that can regress is
    the one the CALLER computes: summing the ranges' seconds and
    rounding once is the mistake, and a test that passes the correct
    frame count in cannot see it (AGENTS.md 10.4).
    """
    from library.tools.reel_build import placements, plan_cards
    from library.tools import full_frame_element as ffe

    # CHOSEN so the two arithmetics disagree by a frame: summing the
    # ranges' SECONDS and rounding once gives 562 frames, while rounding
    # each edge - which is what `placements` does - gives 563. Without
    # that disagreement this test could not fail.
    ranges = [(29.881, 42.397), (134.695, 145.611)]
    per_edge = sum(int(round(b * FPS)) - int(round(a * FPS))
                   for a, b in ranges)
    summed_seconds = int(round(sum(b - a for a, b in ranges) * FPS))
    assert per_edge == 563 and summed_seconds == 562

    class _Clip:
        track_type = "video"
        track_index = 1
        speaker = "SpeakerOne"
        source_file = FOOTAGE
        timeline_start = 0.0
        # Spans BOTH ranges: a clip that covered only the first would
        # place 301 frames of picture and let the assertion below pass
        # for the wrong reason.
        timeline_end = 200.0
        source_in = 0.0

    class _Moment:
        number = 7
        speakers = ("SpeakerOne",)

    declarations = ffe.declared_elements({"full_frame_elements": [{
        "element": "full_frame_card", "placement": "tail",
        "duration_seconds": 1.37, "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"text": "the end", "type_role": "micro",
                  "colour": "#FFFFFF"}]}]})

    cards = plan_cards(_Moment(), {"segments": []}, ranges, "", FPS,
                       declarations=declarations, width=1080, height=1920)
    assert [c.placement for c in cards] == ["tail"]

    placed = placements(ranges, [_Clip()], FPS, lead_frames=0)
    last_end = max(p["snapped_record"]
                   + int(round((p["source_out"] - p["source_in"]) * FPS))
                   for p in placed)
    assert last_end == per_edge, (
        "the picture itself must end on the per-edge frame count, or "
        "this test is measuring the wrong thing")
    assert cards[0].reel_start_frame == last_end, (
        "the tail card must start on the frame the picture ends, not a "
        "rounding away from it")

    items = [_item_2(1, p["snapped_record"],
                   int(round((p["source_out"] - p["source_in"]) * FPS)))
             for p in placed]
    items.append(_card_item(start=cards[0].reel_start_frame,
                            frames=cards[0].duration_frames))
    assert check_picture_holes("Reel 07", items) == []
