"""The two creative floors are gone, and must stay gone.

Captain's ruling 2026-08-20 (`decision-creative-floors.md`): remove the
B-roll minimum and the SFX minimum entirely - not warnings, not a
reconciled range, not per-template minimums. The creative direction
decides how many B-roll cuts and how many sound effects a piece gets;
nothing is padded to satisfy a number. The accepted consequence, in the
captain's own words, is that a thin edit will no longer be caught by a
mechanical check.

These tests fail if either floor comes back, under any name, as a
rejection OR as a warning - and if the prompts start demanding a count
again, because a prompt-level quota pads the edit just as effectively as
a bridge-level one. That is what put two B-roll cuts and one sound effect
into the shipped project 001 with rationales that said so.

The ruling is about creative floors, not about B-roll and SFX
specifically, and two prompt-level quotas survived it because this file
only guarded the two steps the ruling was WRITTEN about:

* `step_4_03_plan_vfx/handoff.md` demanded at least three to seven VFX
  items and a slow zoom on every talking-head clip over three seconds.
  001 produced exactly eight effects on exactly eight clips, one each,
  alternating direction.
* `step_2_02_speech_sequence/handoff.md` demanded "strictly select
  exactly 10-15" body passages, contradicting the 75%-of-target-duration
  rule in the same file. The model obeyed the duration and produced 7 -
  it was right, and the prompt was wrong.

Both are gone, and every creative-planning prompt is now under the guard
rather than the two that were named on the day.

2026-08-26: and the guard now reads CODE as well as prompts, because
reading only prompts is how the VFX pair survived, and reading only the
two steps the ruling named is how a third floor survived in
`step_4_02_plan_transitions/post_bridge.py` for longer still:

* `min_trans = max(1, total_cuts // 3)`, an injection loop appending
  `{"type": "defocus", "duration_feel": "medium"}` at any boundary where
  the semantic mood or the keyword tags differed, and a `sys.exit(1)`
  reading "You MUST plan at least N transitions at DISTINCT cut points".
* `library/tools/transition_selector.select_transition` invented a DRAWN
  `defocus`/`flash`/`fade_to_black` for any cut the plan had left alone,
  once per twenty seconds.
* `library/tools/audio_reactive_sfx.scale_sfx_density` DELETED plan
  entries - half the impacts on "moderate" - judged by an energy word
  read from a `creative_direction` key that does not exist, so the
  constant "moderate" decided it every time.
* `library/tools/audio_reactive_sfx.align_sfx_to_prosody` MOVED a plan
  entry - a whoosh onto the nearest pause, an impact onto the nearest
  emphasis peak, inside a hardcoded 2.0 s window - off a measurement
  this pipeline has never taken.  It never fired on any run.

A floor is a floor whether it pads, rejects, warns, or cuts. The code
guard below is deliberately narrow - it drives the real bridges and
asserts on their real output rather than grepping for the word "default",
which would fail on every legitimate frame rate in the tree.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**There are NO creative floors, and there must not be again.**
[why](docs/RULE_EVIDENCE.md#no-creative-floors)
- **A floor in the PROMPT is a floor.** `tests/test_no_creative_floors.py` guards every creative-planning prompt (`CREATIVE_PLANNING_STEPS`), DERIVED from `undetermined.DECLARING_STEPS` so a new model-reaching step is covered whether or not anybody remembers to add it.
- **A floor in a BRIDGE is a floor.** [why](docs/RULE_EVIDENCE.md#the-default-that-outvoted-the-plan)
- **A floor that CUTS is still a floor.** `audio_reactive_sfx.scale_sfx_density` deleted half the plan's impacts because a constant said the piece was "moderate". Deleted, not unwired.
- **A floor that MOVES a plan entry is still a floor.** `audio_reactive_sfx.align_sfx_to_prosody` snapped a sound to a pause or an emphasis peak inside a 2.0 s window. Deleted, not unwired - and it had never once fired.
- **`tests/test_no_creative_floors.py` reads CODE as well as prompts.** It drives the real bridges of every step in `CREATIVE_PLANNING_STEPS` and asserts on their output.
- A COVERAGE requirement is not a floor: "every non-speech block MUST have B-roll" stays, because an uncovered block fails `_assert_timeline_fully_covered`.
- `_assert_sfx_distributed` stays: it catches a collapse (every SFX on one frame), not a sparse plan.
- **A floor in a REVIEW step is still a floor.** `creative_cohesion` (5.03) reports the counts under `cohesion_review.measurements` and judges none of them; a pace check there needs a pace the creative direction DECLARED, which no step emits. **The step is therefore a pure OBSERVER**: every proposal it can still make routes to `OWNED_UPSTREAM`, so `adjustments` is empty for every input at every energy (#272). `ACTIONABLE_AT_COHESION` has an applier and no producer, which `library/tools/cohesion_scope.py` states and `tests/test_cohesion_scope.py::test_the_step_is_a_pure_observer` pins off the step's own source. **Do not read an empty `adjustments` as a clean bill of health.**
- **How long a drawn transition holds comes from the PLAN.** The handoff asks for a `duration_feel` on every one; step 4.02's post-bridge renders that word into frames. A brand template's `transition_duration_ms` `{min, max}` is a RANGE, so it BOUNDS that choice and never replaces it; a scalar is a declared length. A drawn transition that neither declares is DROPPED with the reason, not held for a constant.

WP3b (2026-09-14) binds the same principle beyond the incidents: shape 1
sweeps every creative `.get()` fallback in `library/` (the seven
literals above are the incident-shaped subset), shape 2 fingerprints
catalogue-order picks and fetch truncations, and shape 3-of-three
drives a sparse plan through EVERY declaring step's bridge. Each
exemption names its category; each pattern ships with the removed
line that proves it fires.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools.creative_floors import (
    QUOTA_PATTERNS,
    QUOTA_PHRASES,
    find_floors,
)

REPO = Path(__file__).resolve().parents[1]
STEPS = REPO / "library" / "steps"
BROLL = STEPS / "step_3_02_select_broll"
SFX = STEPS / "step_4_04_plan_sfx"
VFX = STEPS / "step_4_03_plan_vfx"
SPEECH = STEPS / "step_2_02_speech_sequence"

# Every step whose prompt asks a model HOW MANY of something to plan.
# Add a creative-planning step here when you add one; the ruling is about
# floors, not about the two steps it was written about.
TRANSITIONS = STEPS / "step_4_02_plan_transitions"
# 5.01 joined on 2026-09-03: it stopped writing the identity CDL whenever
# no template declared an exposure reference and started asking a
# colourist what the footage needs, which makes it a creative-planning
# step and puts its prompt under this guard.
COLOR = STEPS / "step_5_01_color_grade"
def _creative_planning_steps():
    """DERIVED from `undetermined.DECLARING_STEPS`, not listed here.

    This was a hardcoded tuple of six, and a hardcoded roster is a guard
    whose coverage depends on somebody remembering. It had already
    drifted: `creative_direction`, `music_selection`, `mesh_spine` and
    `render_motion_graphics` all reach a model and none of them was in
    it, so their prompts were never read for a floor. The step written
    the day this was found, `select_reels`, was not in it either.

    `craft_role` already borrows the same list from the same place, with
    the stated reason that the two cannot drift apart. This does the
    same, so adding a model-reaching step puts its prompt under this
    guard whether or not anybody remembers to.

    `validate` is excluded: it checks a finished render and plans
    nothing.
    """
    from library.tools.project_layout import STEPS as STEP_DIRS
    from library.tools.undetermined import DECLARING_STEPS

    by_node = {d.node_id: STEPS / f"step_{d.dirname}" for d in STEP_DIRS}
    return tuple(sorted(
        (by_node[node] for node in DECLARING_STEPS
         if node != "validate" and node in by_node),
        key=lambda path: path.name))


CREATIVE_PLANNING_STEPS = _creative_planning_steps()


def _run_bridge(script: Path, payload: dict, extra_env: dict = None):
    """Run a bridge the way the runner does: repo root on the path."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )


# ── A sound library the SFX bridges can resolve against ───────────────
#
# Step 4.04 resolves an `sfx_id` against the real catalogue, so these
# tests need a library of their own. It is two entries under tmp_path -
# never the captain's, and never the shared one at PIPELINE_SFX_LIBRARY.

SFX_ID = "test_whoosh.wav"


@pytest.fixture(scope="module")
def sfx_library(tmp_path_factory):
    lib = tmp_path_factory.mktemp("sfx_library")
    audio = lib / "test_whoosh.wav"
    audio.write_bytes(b"RIFF....WAVEfmt ")
    (lib / "sfx_index.json").write_text(json.dumps([{
        "file": SFX_ID,
        "path": str(audio),
        "folder_category": "Accents",
        "description": "a soft air movement",
        "technical": {
            "basic": {"duration": 0.4},
            "energy_profile": {"envelope_shape": "fading"},
        },
        "transient_offset_sec": 0.05,
    }]))
    return {"PIPELINE_SFX_LIBRARY": str(lib)}


# ── The prompts must not demand a count ───────────────────────────────
#
# The vocabulary lives in `library/tools/creative_floors.py`, once: the
# floors test reads steps for it, and a project-declared creative task is
# refused at declaration time against the same list. A guard whose
# vocabulary exists in only one place cannot reach a second prompt
# without being restated.


@pytest.mark.parametrize(
    "path",
    [step / name
     for step in CREATIVE_PLANNING_STEPS
     for name in ("handoff.md", "manifest.json")],
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_prompt_surfaces_demand_no_count(path):
    if not path.exists():
        pytest.skip(f"{path.name} does not exist for this step")
    text = path.read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in QUOTA_PHRASES if phrase in text]
    hits += [m.group(0) for pattern in QUOTA_PATTERNS
             for m in re.finditer(pattern, text)]
    assert not hits, (
        f"{path.relative_to(REPO)} demands a count again ({hits}). The "
        f"floors were removed by ruling; a prompt-level quota "
        f"reintroduces exactly the padding they caused."
    )


# ── A floor in the ROLE BLOCK is a floor ──────────────────────────────
#
# From 2026-09-03 the runner PREPENDS a role statement to a step's
# handoff (library/tools/craft_role.py), so a quota written there reaches
# the model exactly as a quota in `handoff.md` would - and the file-based
# parametrisation above cannot see it, because the text is in a Python
# module and not in the step's directory. Reading only the files on disk
# is how the two VFX quotas survived the ruling for five days.

def _declared_roles():
    from library.tools import craft_role
    return sorted(craft_role.ROLES)


@pytest.mark.parametrize("step_id", _declared_roles())
def test_the_role_block_demands_no_count(step_id):
    """Every declared role, not only the creative-planning ones.

    A role for a review or QA step that demanded a count would be just as
    much a floor, and parametrising over the roles that exist means no
    case here is vacuous and none is skipped.
    """
    from library.tools import craft_role

    text = craft_role.prompt_block(step_id).lower()
    assert text, f"{step_id} is in ROLES and renders no block"
    hits = [phrase for phrase in QUOTA_PHRASES if phrase in text]
    hits += [m.group(0) for pattern in QUOTA_PATTERNS
             for m in re.finditer(pattern, text)]
    assert not hits, (
        f"the craft role for {step_id} demands a count ({hits}). A role "
        f"hands over capability and authority; a role that also says how "
        f"much of something to plan is a floor arriving one level up."
    )


def test_the_guard_can_actually_fire():
    """A gate that cannot fail reads as coverage. These are the literal
    lines removed on 2026-08-25."""
    removed = [
        "you must plan at least 3-7 vfx items across the video. an empty "
        "list is a failure.",
        "target body passages | 10-15 (strictly select exactly 10-15 of "
        "the strongest passages)",
        "every a-roll talking head clip >3 seconds must have at least "
        "`slow_zoom_in` or `slow_zoom_out`",
    ]
    for line in removed:
        hits = [p for p in QUOTA_PHRASES if p in line]
        hits += [m.group(0) for pattern in QUOTA_PATTERNS
                 for m in re.finditer(pattern, line)]
        assert hits, f"the guard does not catch {line!r}"


# ── The bridges must not reject a sparse plan ─────────────────────────


def test_select_broll_accepts_a_single_cutaway():
    """One B-roll clip is a legitimate edit, not an error."""
    payload = {
        "clip_catalog": [
            {
                "clip_id": "clip_001",
                "source_file": "/tmp/a.mov",
                "duration_seconds": 30.0,
                "width": 1080,
                "height": 1920,
            },
            {
                "clip_id": "clip_002",
                "source_file": "/tmp/b.mov",
                "duration_seconds": 30.0,
                "width": 1080,
                "height": 1920,
            },
        ],
        "semantic_analysis_documents": [],
        "temporal_event_indices": [],
        "timed_spine": {
            "structure": [
                {
                    "position": 1,
                    "block_type": "speech",
                    "clip_id": "clip_001",
                    "timeline_start": 0.0,
                    "timeline_end": 4.0,
                }
            ]
        },
        "broll_creative": [
            {
                "clip_id": "clip_002",
                "spine_block_position": 1,
                "preferred_moment": "the wide establishing shot",
                "selection_rationale": "illustrates the line about the shop",
            }
        ],
        "b_roll_interjections": [],
    }
    proc = _run_bridge(BROLL / "post_bridge.py", payload)
    assert proc.returncode == 0, (
        f"one B-roll clip was rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    result = json.loads(proc.stdout)
    assert len(result["b_roll_assignments"]) == 1
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("minimum", "at least", "you must select"):
        assert word not in combined, (
            f"the bridge warns about count ({word!r}). The ruling declined "
            f"a warning as well as a rejection."
        )


def _sfx_payload(n_sfx: int):
    spine = {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": "clip_001",
                "source_start": i * 2.0,
                "source_end": i * 2.0 + 2.0,
                "timeline_start": i * 2.0,
                "timeline_end": i * 2.0 + 2.0,
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(max(n_sfx, 1))
        ]
    }
    return {
        "sfx_creative": [
            {
                "spine_block_position": i + 1,
                "sfx_id": SFX_ID,
                "volume_db": -18,
                "rationale": "marks the cut",
            }
            for i in range(n_sfx)
        ],
        "timed_spine": spine,
        "temporal_event_indices": [],
        "music_analysis": {},
        # The bar grid is in the music file's clock; the selection carries
        # the section offset that makes it a timeline clock.
        "music_selection": {"audio_path": ""},
        "project_fps": 30.0,
        "creative_direction": {},
    }


@pytest.mark.parametrize("n_sfx", [1, 2])
def test_plan_sfx_accepts_a_sparse_plan(n_sfx, sfx_library):
    """A one- or two-sound edit passes; there is no minimum."""
    proc = _run_bridge(SFX / "post_bridge.py", _sfx_payload(n_sfx),
                       sfx_library)
    assert proc.returncode == 0, (
        f"{n_sfx} SFX were rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    result = json.loads(proc.stdout)
    assert len(result["sfx_spec"]["sfx_list"]) == n_sfx
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("minimum", "at least", "you must plan"):
        assert word not in combined, (
            f"the bridge warns about count ({word!r}). The ruling declined "
            f"a warning as well as a rejection."
        )


def test_sfx_collapse_is_still_caught():
    """Removing the floor must not remove the collapse check.

    Every SFX landing on one timeline position is a broken plan, not a
    sparse one, and the ruling says nothing about it.
    """
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    with pytest.raises(ValueError, match="collapse"):
        _assert_sfx_distributed(
            [
                {"timeline_in": 0.0},
                {"timeline_in": 0.0},
            ]
        )


def test_layered_sfx_at_same_position_are_not_a_collapse():
    """Two sounds at the same position is layering, not a collapse.

    Whoosh + bass hit on an important transition is standard sound design.
    The old check rejected ANY duplicate positions; the new one only catches
    a full collapse (every sound on one frame).
    """
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    # Two sounds layered at position 1.0, one sound alone at 5.0 -
    # three SFX, two distinct positions: legitimate layering.
    _assert_sfx_distributed(
        [
            {"timeline_in": 1.0},
            {"timeline_in": 1.0},
            {"timeline_in": 5.0},
        ]
    )


def test_a_single_layered_moment_is_not_a_collapse():
    """The whole plan as one layered moment: two entries naming the SAME
    spine block and resolving to the same span is the layering the schema
    invites, not a collapse. The old rule counted positions and refused
    it for having only one."""
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    # No raise: this is the corrected behaviour.
    _assert_sfx_distributed(
        [
            {"label": "sfx_001", "timeline_in": 6.0,
             "spine_block_position": 2},
            {"label": "sfx_002", "timeline_in": 6.0,
             "spine_block_position": 2},
        ]
    )


def test_moments_planned_apart_landing_together_are_a_collapse():
    """The narrowed check still catches the true collapse: entries naming
    SEVERAL spine positions that landed on one timeline position."""
    from library.steps.step_4_04_plan_sfx.post_bridge import (
        _assert_sfx_distributed,
    )

    with pytest.raises(ValueError, match="collapse"):
        _assert_sfx_distributed(
            [
                {"label": "sfx_001", "timeline_in": 6.0,
                 "spine_block_position": 1},
                {"label": "sfx_002", "timeline_in": 6.0,
                 "spine_block_position": 4},
            ]
        )


# ── The VFX post-bridge must not pad the plan ─────────────────────────
#
# The third floor, and the one that outlived the ruling by hiding in code
# rather than in a prompt. `inject_default_ken_burns` added a
# `slow_zoom_in`/`slow_zoom_out` to every speech block over three seconds
# that the plan had deliberately left alone, "because the style spec
# requires subtle motion on all A-roll clips >3s", and a second guard
# failed the step outright when the plan was empty. Observed on the run of
# 2026-08-26: a three-effect plan came out of the bridge with seven, three
# of them on blocks the spine had marked "no effect", and the resulting
# manifest failed P7 for putting one zoom family on every V1 clip.


def _vfx_payload(positions, n_blocks=4):
    spine = {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": "clip_001",
                "source_start": i * 5.0,
                "source_end": i * 5.0 + 5.0,
                "timeline_start": i * 5.0,
                "timeline_end": i * 5.0 + 5.0,
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(n_blocks)
        ]
    }
    return {
        "vfx_creative": [
            {
                "target_block_position": pos,
                "effect_type": "slow_zoom_in",
                "params": {"zoom_start": 1.0, "zoom_end": 1.03},
                "rationale": "the shot is held long enough to go dead",
            }
            for pos in positions
        ],
        "a_roll_assignments": [],
        "timed_spine": spine,
        "frame_rate": 30.0,
        "creative_direction": {},
    }


def test_plan_vfx_leaves_the_blocks_the_plan_left_alone():
    """One effect on four eligible blocks stays one effect."""
    proc = _run_bridge(VFX / "post_bridge.py", _vfx_payload([1]))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    effects = json.loads(proc.stdout)["enhancement_spec"]["visual_effects"]
    assert [e["target_block_position"] for e in effects] == [1], (
        f"the bridge padded the plan back up - a floor is back: {effects}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("ken burns", "default", "requires subtle"):
        assert word not in combined, (
            f"the bridge still talks about a default ({word!r})"
        )


def test_plan_vfx_accepts_an_empty_plan():
    """The handoff says so in as many words: 'an empty list is a
    legitimate answer for a piece that wants stillness'. The bridge used
    to exit 1 with 'You MUST plan at least 3-7 VFX items'."""
    proc = _run_bridge(VFX / "post_bridge.py", _vfx_payload([]))
    assert proc.returncode == 0, (
        "an empty VFX plan was rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert json.loads(proc.stdout)["enhancement_spec"]["visual_effects"] == []


# ── The guard reaches CODE, not only prompts ──────────────────────────
#
# Everything above this line drives a bridge or reads a prompt. What
# follows drives the bridges the ruling had never been applied to, and
# reads the modules where a floor can live without a prompt saying so.


def _spine(n_blocks: int, clip_ids=None) -> dict:
    """A spine of `n_blocks` speech blocks, five seconds each."""
    ids = clip_ids or [f"clip_{i+1:03d}" for i in range(n_blocks)]
    return {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": ids[i],
                "source_start": i * 5.0,
                "source_end": i * 5.0 + 5.0,
                "timeline_start": i * 5.0,
                "timeline_end": i * 5.0 + 5.0,
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(n_blocks)
        ]
    }


def _transitions_payload(creative: list, n_blocks: int = 9) -> dict:
    return {
        "transition_creative": creative,
        "timed_spine": _spine(n_blocks),
        "music_selection": {"audio_path": ""},
        "music_analysis": {},
        "temporal_event_indices": [],
        "frame_rate": 30.0,
        "creative_direction": {},
        "brand_effect": {},
        "semantic_analysis": [],
    }


def test_plan_transitions_accepts_an_empty_plan():
    """No transition is a legitimate edit: a cut draws nothing.

    The post-bridge used to exit 1 with "You MUST plan at least N
    transitions at DISTINCT cut points" whenever the plan covered fewer
    than a third of the spine's boundaries.
    """
    proc = _run_bridge(TRANSITIONS / "post_bridge.py", _transitions_payload([]))
    assert proc.returncode == 0, (
        "an empty transition plan was rejected - a floor is back.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert json.loads(proc.stdout)["transition_spec"] == []


def test_plan_transitions_does_not_pad_the_plan():
    """One transition on eight boundaries stays one transition."""
    creative = [{"cut_point_position": 2, "type": "flash",
                 "duration_feel": "quick",
                 "rationale": "the topic really does change here"}]
    proc = _run_bridge(TRANSITIONS / "post_bridge.py",
                       _transitions_payload(creative))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    resolved = json.loads(proc.stdout)["transition_spec"]
    assert len(resolved) == 1, (
        f"the bridge padded the plan back up - a floor is back: {resolved}"
    )
    combined = (proc.stdout + proc.stderr).lower()
    for word in ("default defocus", "you must plan", "at least"):
        assert word not in combined, (
            f"the bridge still talks about a floor ({word!r})"
        )


def test_the_transition_selector_invents_no_drawn_transition():
    """A cut the plan did not decorate draws nothing.

    `select_transition` used to answer `defocus` (or `flash`, or
    `fade_to_black`) for any cut across two source clips more than twenty
    seconds after the last drawn one - taste, chosen by a constant, for a
    cut nobody asked to decorate.
    """
    from library.tools.transition_selector import select_transition
    from library.tools.transition_vocabulary import is_drawn

    for to_clip in (
        {"clip_id": "clip_002", "timeline_start": 25.0},
        {"clip_id": "clip_002", "timeline_start": 90.0,
         "block_type": "breather"},
        {"clip_id": "clip_002", "timeline_start": 90.0,
         "music_behavior": "step_up"},
        {"clip_id": "clip_002", "timeline_start": 90.0,
         "block_type": "transition_slot"},
    ):
        res = select_transition({"clip_id": "clip_001"}, to_clip, {}, {})
        assert not is_drawn(res["type"]), (
            f"an undecorated cut produced a drawn {res['type']!r} - a "
            f"creative default is back: {to_clip}"
        )


def test_the_transition_selector_still_honours_a_request():
    """Removing the invented defaults must not remove the editor's call."""
    from library.tools.transition_selector import select_transition

    res = select_transition(
        {"clip_id": "clip_001"}, {"clip_id": "clip_002"},
        {}, {}, requested_type="defocus",
    )
    assert res["type"] == "defocus"


def test_nothing_scales_the_sfx_plan_by_energy():
    """`scale_sfx_density` deleted plan entries; it is gone, not unwired.

    Left in the module it would still state, to the next reader, that a
    "moderate" piece keeps half its impacts.
    """
    import library.tools.audio_reactive_sfx as ars

    assert not hasattr(ars, "scale_sfx_density"), (
        "scale_sfx_density is back. How many sound effects a piece gets "
        "is the creative direction's call, and that holds for cutting "
        "them as much as for padding them."
    )
    sfx_source = (SFX / "post_bridge.py").read_text(encoding="utf-8")
    assert "scale_sfx_density(" not in sfx_source, (
        "the SFX post-bridge scales the plan by energy again"
    )


def test_nothing_snaps_the_sfx_plan_to_a_prosody_window():
    """`align_sfx_to_prosody` is gone, not unwired, and for the same reason.

    It moved a whoosh onto a pause and an impact onto an emphasis peak
    inside a hardcoded 2.0 s window - taste stated as fact - and it never
    fired on any run, for five independent reasons recorded in
    `library/tools/audio_reactive_sfx.py`. What it claimed to do, the SFX
    post-bridge already does from measured onsets and real word
    boundaries.
    """
    import library.tools.audio_reactive_sfx as ars

    assert not hasattr(ars, "align_sfx_to_prosody"), (
        "align_sfx_to_prosody is back. It never fired, and where a sound "
        "lands is decided by the sound's measured envelope and the "
        "spine's own word times - not by a 2.0 s window around a "
        "measurement this pipeline does not take."
    )
    sfx_source = (SFX / "post_bridge.py").read_text(encoding="utf-8")
    assert "align_sfx_to_prosody(" not in sfx_source, (
        "the SFX post-bridge aligns the plan to prosody again"
    )


# ── No creative value is substituted for one the plan omitted ─────────
#
# The other half of the captain's 2026-08-26 ruling: the pipeline never
# invents a creative judgement on the model's behalf. A plan entry that
# names no effect, no parameter the renderer reads, no sound or no level
# is DROPPED with the reason, never completed from a constant.

CREATIVE_SUBSTITUTIONS = [
    # (file, the literal that must not be a fallback, what it decided)
    (VFX / "post_bridge.py", '"effect_type", "slow_zoom_in"',
     "which effect a block gets"),
    # The `INTENSITY_MAP` that resolved subtle|moderate|strong into fixed
    # zoom numbers is REMOVED (captain, 2026-09-02): a scale of three is
    # still the engine choosing how strong an effect is, and its ceiling
    # cited AGENTS.md, which this step never reads. The values are the
    # plan's; only the parameter NAMES are checked.
    (VFX / "post_bridge.py", '"intensity", "moderate"',
     "how strong that effect is"),
    (SFX / "post_bridge.py", '"sfx_type", "whoosh"',
     "which sound plays"),
    # The `VOLUME_MAP` that resolved subtle|low|medium|prominent into
    # -18|-14|-10|-6 dB is REMOVED, on the same ruling that removed
    # `INTENSITY_MAP`. The level is the plan's own number in dB.
    (SFX / "post_bridge.py", '"volume_db", -14',
     "how loud it plays"),
    (TRANSITIONS / "post_bridge.py", '"duration_feel", "medium"',
     "how long a transition holds"),
    (STEPS / "step_5_04_compile_manifest" / "step.py", '"sfx_type", "whoosh"',
     "which sound reaches track A3"),
    (STEPS / "step_5_03_creative_cohesion" / "step.py",
     '"target_energy", "moderate"',
     "the energy the whole edit is scored against"),
]


@pytest.mark.parametrize(
    "path,literal,decided",
    CREATIVE_SUBSTITUTIONS,
    ids=lambda v: v if isinstance(v, str) else v.parent.name,
)
def test_no_creative_value_is_substituted_for_a_missing_one(
        path, literal, decided):
    source = path.read_text(encoding="utf-8")
    # The literal may appear in a comment recording its removal; what must
    # not come back is a `.get(...)` handing it to live code.
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert f".get({literal}" not in stripped, (
            f"{path.relative_to(REPO)} substitutes a creative value for a "
            f"missing one: {literal}. That constant decides {decided}, "
            f"which is the model's call. Drop the entry with the reason "
            f"instead."
        )


def test_plan_vfx_drops_an_entry_that_names_no_effect():
    """No effect_type used to mean `slow_zoom_in`."""
    payload = _vfx_payload([])
    payload["vfx_creative"] = [
        {"target_block_position": 1,
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "rationale": "the shot is held"}
    ]
    proc = _run_bridge(VFX / "post_bridge.py", payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["enhancement_spec"]["visual_effects"] == [], (
        "an entry naming no effect was completed from a constant"
    )
    assert "names no effect_type" in proc.stderr


def test_plan_sfx_refuses_an_entry_that_names_no_playable_sound(sfx_library):
    """WHICH sound plays is refused loudly, never substituted.

    Naming no `sfx_id`, or one the library has no file for, fails the
    step where the plan is written - not three steps later inside
    `compile_manifest`, and not by quietly dropping the entry, which
    ships an edit missing a sound nobody decided to cut.
    """
    for entry in (
        {"spine_block_position": 1, "volume_db": -18,
         "rationale": "marks the cut"},
        {"spine_block_position": 1, "sfx_id": "not_in_the_library.wav",
         "volume_db": -18, "rationale": "marks the cut"},
    ):
        payload = _sfx_payload(1)
        payload["sfx_creative"] = [entry]
        proc = _run_bridge(SFX / "post_bridge.py", payload, sfx_library)
        assert proc.returncode == 1, proc.stdout + proc.stderr
        error = json.loads(proc.stdout)["error"]
        assert "sfx_id" in error
        assert "cannot be played" in error


def test_plan_sfx_drops_an_entry_that_names_no_level(sfx_library):
    """A level is a decision too, and no dB is substituted for one."""
    payload = _sfx_payload(1)
    payload["sfx_creative"] = [{
        "spine_block_position": 1, "sfx_id": SFX_ID,
        "rationale": "marks the cut",
    }]
    proc = _run_bridge(SFX / "post_bridge.py", payload, sfx_library)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed == [], f"a level was substituted: {placed}"


# ── The bridges must not inject creative defaults ─────────────────────
#
# 2026-09-02 sweep.  The gap this closes: the ONLY guard was
# test_prompt_surfaces_demand_no_count, which read prompts (handoff.md,
# manifest.json).  A creative default or floor living in bridge.py or
# post_bridge.py was invisible to it - and that is exactly how
# `inject_default_ken_burns` survived in step_4_03_plan_vfx/post_bridge.py
# after the first removal attempt.  This guard reads CODE, and a creative
# floor hiding in code will fail it.
#
# What counts as a creative default in a bridge:
#   - A function that injects a creative value the plan did not name
#     (inject_default_*, *_default_*, DEFAULT_*)
#   - A hardcoded count that rejects or warns on a plan size
#     ("Recommended is N", "at least N", min_* = N where N is a count)
#   - A hardcoded energy/density/style word used as a creative choice
#     (but NOT as a technical lookup key or a schema constant)

# Bridge files that must not contain creative defaults.
# Every bridge and post_bridge under the creative-planning steps.
BRIDGE_FILES = [
    step / script
    for step in CREATIVE_PLANNING_STEPS
    for script in ("bridge.py", "post_bridge.py")
    if (step / script).exists()
]

# Patterns that indicate an injected creative default in bridge code.
# These are searched ONLY in non-comment lines.
BRIDGE_DEFAULT_PATTERNS = [
    # A function that injects defaults the plan didn't ask for.
    (r"\bdef\s+inject_default_", "inject_default_* function"),
    # A hardcoded plan-size recommendation.
    (r"recommended is \d+", "hardcoded plan-size recommendation"),
    # A creative density scaler (the pattern that scale_sfx_density used).
    (r"\bdef\s+scale_\w+_density\b", "density scaling function"),
]


@pytest.mark.parametrize(
    "path",
    BRIDGE_FILES,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_bridges_inject_no_creative_default(path):
    """Bridge and post-bridge code must not inject creative defaults.

    This is the gap that let `inject_default_ken_burns` survive: the
    prompt guard read handoff.md but not the code that ran after it.
    A floor that lives in code pads the edit identically to one in a
    prompt.
    """
    source = path.read_text(encoding="utf-8")
    hits = []
    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern, label in BRIDGE_DEFAULT_PATTERNS:
            if re.search(pattern, stripped, re.IGNORECASE):
                hits.append(f"L{i}: {label}: {stripped[:120]}")
    assert not hits, (
        f"{path.relative_to(REPO)} contains a creative default in code:\n"
        + "\n".join(hits)
    )


# Also guard against the prompt-level patterns IN bridge code (not just
# handoff.md).  A bridge that prints "you MUST plan at least N" to stderr
# is a floor wearing a warning's clothes.
@pytest.mark.parametrize(
    "path",
    BRIDGE_FILES,
    ids=lambda p: f"{p.parent.name}/{p.name}",
)
def test_bridges_demand_no_count(path):
    """Bridge code must not demand a creative count, even in warnings."""
    source = path.read_text(encoding="utf-8").lower()
    hits = []
    for i, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for phrase in QUOTA_PHRASES:
            if phrase in stripped:
                hits.append(f"L{i}: phrase {phrase!r}: {stripped[:120]}")
        for pattern in QUOTA_PATTERNS:
            m = re.search(pattern, stripped)
            if m:
                hits.append(f"L{i}: pattern {m.group(0)!r}: {stripped[:120]}")
    assert not hits, (
        f"{path.relative_to(REPO)} demands a count in code:\n"
        + "\n".join(hits)
    )


def test_bridge_guard_can_fire():
    """The bridge guard is not a tautology - these are the literal patterns
    that survived before it existed."""
    removed_lines = [
        # inject_default_ken_burns lived in post_bridge.py
        "def inject_default_ken_burns(creative_plan, spine_blocks):",
        # The passage-count recommendation that lived in 2.02 post_bridge
        'print(f"WARNING: Selected {len(body)} speech passages. Recommended is 10-15.")',
        # scale_sfx_density that deleted plan entries
        "def scale_sfx_density(sfx_plan, energy_label):",
    ]
    for line in removed_lines:
        hits = []
        stripped = line.strip().lower()
        for pattern, label in BRIDGE_DEFAULT_PATTERNS:
            if re.search(pattern, stripped):
                hits.append(label)
        for phrase in QUOTA_PHRASES:
            if phrase in stripped:
                hits.append(phrase)
        for pattern in QUOTA_PATTERNS:
            m = re.search(pattern, stripped)
            if m:
                hits.append(m.group(0))
        assert hits, (
            f"the bridge guard does not catch {line!r} - add a pattern"
        )


# ── WP3b: the guard binds the principle, not the incidents ──────────
#
# 2026-09-14 (vision principle 2, enforcement half). The scout
# (`data/vep-vision-wp3-scripts-adjudicating-meaning/report.md`, section
# 5) showed every surface above binds the one module its originating
# incident landed in: the seven-literal `CREATIVE_SUBSTITUTIONS` list
# sees only the seven lines it was written about, the bridge patterns
# see quota language only, and ordering/truncation plus the six
# un-driven creative bridges pass through untouched. PR 1138 removed
# the five constants this extension would otherwise fail on (F1/F2
# transition holds, F3 timed-text look, F4 b-roll substitution, F7
# copy tiers), so the wider guard goes green on landing.
#
# Three shapes below (the scout's shapes 1, 2 and 4; shape 3 - numbers
# without declared provenance - is out of scope on five open captain
# holds). Every swept set is DERIVED, never listed: new files and new
# model-reaching steps are covered whether or not anybody remembers to
# add them. Every new pattern ships with the removed line that proves
# it fires. Each registry entry names the exemption category it
# honours, from the scout's own list: absence-sentinel,
# absence-of-decoration, validity-floor-that-refuses,
# closed-behind-refusal, closed-route, report-only,
# admitted-absence-in-context, measurement-lookup,
# reported-load-bearing-default, needs-captain.


# ── Shape 1: a creative `.get()` with a literal fallback, everywhere ─
#
# The seven-literal list above is incident-shaped: F2
# (`t.get("duration_frames", int(0.5 * fps))`) and F3
# (`m.get("font_size", 42)` and five siblings) are this exact shape on
# lines the list never named. So the guard sweeps every `.py` under
# `library/tools` and `library/steps` for `.get(<creative key>,
# <fallback>)` - keyed by the KEY (the decision), not by the literal
# (the incident) - plus numeric completion of `duration_frames` by
# subscript assignment (F1's `t['duration_frames'] = 15` shape; the
# surviving `= 0` lines are the absence-of-decoration the cut branch
# correctly writes, and are registry entries, not violations).

# Keys whose fallback decides what the viewer sees or hears. Technical
# keys (frame rates, timeouts, codecs, paths, measurements) are never
# in this set: a grep for "default" fails on every legitimate frame
# rate, which is the constraint that killed every naive version.
CREATIVE_GET_KEYS = (
    "effect_type",
    "intensity",
    "sfx_type",
    "sfx_id",
    "volume_db",
    "duration_feel",
    "duration_frames",
    "target_energy",
    "font_size",
    "font_weight",
    "text_shadow",
    "fade_in_frames",
    "fade_out_frames",
    "font_family",
    "text_align",
    "type_role",
    # F3 completed card position from 0.5 constants: a centred card is
    # a look, and no live `.get("x"/"y")` remains to exempt.
    "x",
    "y",
)

_GET_KEY_PATTERN = re.compile(
    r"\.get\(\s*['\"](?P<key>" + "|".join(CREATIVE_GET_KEYS) + r")['\"]\s*,"
)
_GET_COMMA_AT_EOL = re.compile(
    r"\.get\(\s*['\"](?P<key>" + "|".join(CREATIVE_GET_KEYS) + r")['\"]\s*,\s*$"
)
_DURATION_ASSIGN_PATTERN = re.compile(
    r"['\"]duration_frames['\"]\s*\]\s*=\s*(?P<value>\d+)"
)

# (relative path, key, normalised fallback) -> (exemption category, why
# this fallback is not engine taste). A hit without an entry fails; an
# entry matching nothing fails as stale. Fail-closed both directions.
CREATIVE_GET_EXEMPTIONS = {
    # Drop/refuse paths read the missing value only to name it.
    ("library/steps/step_4_03_plan_vfx/post_bridge.py", "effect_type", '""'):
        ("absence-sentinel",
         "feeds not_a_spine_block/duplicate_block drop records, never a render"),
    ("library/steps/step_4_03_plan_vfx/post_bridge.py", "effect_type", "'?'"):
        ("report-only",
         "names the dropped entry in stderr, never completes it"),
    ("library/steps/step_4_04_plan_sfx/post_bridge.py", "sfx_id", "'?'"):
        ("report-only",
         "names the dropped entry in stderr, never completes it"),
    ("library/steps/step_5_04_compile_manifest/step.py", "effect_type", '""'):
        ("absence-sentinel",
         "empty means undecided upstream; the entry is dropped, never drawn"),
    ("library/steps/step_5_04_compile_manifest/step.py", "effect_type", "'?'"):
        ("report-only",
         "names malformed entries in refusal messages"),
    ("library/steps/step_5_04_compile_manifest/step.py", "effect_type",
     "v.get('type', '?')"):
        ("report-only",
         "falls back to a second key, then to an unknown marker in a refusal"),
    # A cut IS zero-length; 0 draws nothing.
    ("library/steps/step_5_04_compile_manifest/step.py", "duration_frames:assign", "0"):
        ("absence-of-decoration",
         "the cut branch (F1/F2 fix): a cut is 0, a drawn hold without one is refused"),
    ("library/steps/step_5_04_compile_manifest/step.py", "duration_frames", "0"):
        ("validity-floor-that-refuses",
         "probes absence in the refusal that replaced the 15-frame completion"),
    # The fusion emit path downgrades duration-less drawn effects to a
    # hard cut with the reason recorded (F2 fix); each `= 0` there is
    # that cut, and all three share the entry above.
    # The renderer's last-resort hold sits behind a validator that
    # refuses duration-less drawn effects, so it never fires on
    # validated data. Removing it is a separate change, not this guard.
    ("library/tools/execution/apply_fusion_comps.py", "duration_frames", "12"):
        ("closed-behind-refusal",
         "unreachable behind compile_manifest's refusal; recorded, not removed"),
    ("library/tools/execution/apply_fusion_comps.py", "effect_type",
     "vfx.get('preset', '')"):
        ("absence-sentinel",
         "forwards a second key, then empty; applied only `if preset:`"),
    # DRP project-file surgery: the closed route (AGENTS.md 5), read by
    # its own test only.
    ("library/tools/execution/apply_native_transitions.py", "duration_frames", "24"):
        ("closed-route",
         "unwired legacy path; nothing in the pipeline imports it"),
    # Same family as F7 at declaration level: an absent explainer tier
    # renders at "supporting". The per-entry F7 fix (drop as
    # no_type_role_declared) does not reach this line; the constant is
    # unchanged here (out of scope) and the hold belongs to the captain.
    ("library/tools/explainer_plan.py", "type_role", '"supporting"'):
        ("needs-captain",
         "surviving F7-family default, surfaced not silently kept"),
    # 0 fade draws no fade.
    ("library/tools/fusion/comp_builder.py", "fade_in_frames", "0"):
        ("absence-of-decoration", "no fade in"),
    ("library/tools/fusion/comp_builder.py", "fade_out_frames", "0"):
        ("absence-of-decoration", "no fade out"),
    ("library/tools/manifest_validator.py", "effect_type", "'?'"):
        ("report-only", "names the entry a finding is about"),
    # Sizing already-placed runs in pixels. The placed tier is decided
    # upstream - and since the F7 fix an undeclared one is dropped
    # before it reaches any measurement - so these fallbacks never
    # choose emphasis, only measure it.
    ("library/tools/mg_tight_box.py", "type_role", '"supporting"'):
        ("measurement-lookup",
         "measures runs the plan tiered; chooses none"),
    ("library/tools/mg_tight_box.py", "type_role", '"micro"'):
        ("measurement-lookup",
         "measures runs the plan tiered; chooses none"),
    # Exporters and QA describe; they never reach a timeline.
    ("library/tools/step_exporter.py", "duration_frames", 't.get("duration", "?")'):
        ("report-only", "export placeholder for an absent number"),
    ("library/tools/step_exporter.py", "effect_type", '"?"'):
        ("report-only", "export placeholder for an absent effect"),
    ("library/tools/visual_qa_router.py", "duration_frames", "30"):
        ("report-only",
         "QA segment-check window around a transition, never placed"),
    # The one surviving engine default, load-bearing for the shipped
    # Night card which omits alignment (STYLE_MOMENT_KEYS). Reported in
    # timed_text_overlay.py, not silently kept.
    ("library/tools/timed_text_overlay.py", "text_align", '"center"'):
        ("reported-load-bearing-default",
         "the shipped card omits it and renders today"),
    # The SFX pre-bridge's prompt table: "" is an admitted absence in a
    # cell the model reads, never a placed value.
    ("library/steps/step_4_04_plan_sfx/bridge.py", "duration_frames", '""'):
        ("admitted-absence-in-context",
         "prompt-table cell saying the transition states no hold"),
}


def _normalise_fallback(raw: str) -> str:
    """One spelling for a fallback, so the registry cannot drift from it."""
    return re.sub(r"\s+", " ", raw.strip().lower())


def _fallback_after(line: str, match_end: int) -> str:
    """The fallback expression after `.get(key,`, balanced.

    Stops at the first depth-0 comma or closing paren, honouring
    nesting and quotes - so `v.get('type', '?')` reads whole and a
    trailing `, "next_arg"` is never glued onto the fallback.
    """
    depth = 0
    quote = None
    escaped = False
    for pos in range(match_end, len(line)):
        char = line[pos]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char in "([":
            depth += 1
        elif char in ")]":
            if depth == 0:
                return line[match_end:pos]
            depth -= 1
        elif char == "," and depth == 0:
            return line[match_end:pos]
    return line[match_end:]


def _swept_source_files():
    """DERIVED: every `.py` under `library/tools` and `library/steps`.

    No list to remember: a new module is swept whether or not anybody
    adds it anywhere.
    """
    for base in (REPO / "library" / "tools", REPO / "library" / "steps"):
        yield from sorted(base.rglob("*.py"))


def _iter_code_lines(path: Path):
    """Physical lines that can execute, without line numbers attached.

    Skips full-line `#` comments and triple-quoted docstring bodies:
    prose recording a removal (sfx_level's withdrawn VOLUME_MAP,
    music_selection_contract's sorted-pick incident record) is not a
    fallback the renderer reads. Yields `(lineno, text)`; a `.get(`
    whose fallback continues on the next line is joined into one
    logical line at the opening lineno, because F3 once wore exactly
    that shape.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    in_docstring = False
    i = 0
    while i < len(lines):
        line = lines[i]
        # Toggle on an ODD count of triple quotes: a one-line docstring
        # opens and closes without changing state.
        quotes = line.count('"""') + line.count("'''")
        if in_docstring:
            if quotes % 2 == 1:
                in_docstring = False
            i += 1
            continue
        stripped = line.strip()
        if not stripped.startswith("#"):
            if _GET_COMMA_AT_EOL.search(line):
                # Join continuation lines until the call closes.
                joined = [line.rstrip()]
                depth = line.count("(") - line.count(")")
                j = i + 1
                while depth > 0 and j < len(lines):
                    joined.append(lines[j].strip())
                    depth += lines[j].count("(") - lines[j].count(")")
                    j += 1
                yield i + 1, " ".join(joined)
                if quotes % 2 == 1:
                    in_docstring = True
                i = j
                continue
            yield i + 1, line
        if quotes % 2 == 1:
            in_docstring = True
        i += 1


def _creative_get_hits():
    """Every creative-key `.get()` fallback and duration completion.

    Returns `(relpath, key, fallback, lineno, text)` rows. `None` is
    never a hit: an explicit None is an admitted absence, not taste.
    """
    hits = []
    for path in _swept_source_files():
        rel = str(path.relative_to(REPO))
        for lineno, line in _iter_code_lines(path):
            for match in _GET_KEY_PATTERN.finditer(line):
                key = match.group("key")
                norm = _normalise_fallback(_fallback_after(line, match.end()))
                if norm in ("none", ""):
                    continue
                hits.append((rel, key, norm, lineno, line.strip()[:160]))
            assign = _DURATION_ASSIGN_PATTERN.search(line)
            if assign:
                hits.append((rel, "duration_frames:assign",
                             _normalise_fallback(assign.group("value")),
                             lineno, line.strip()[:160]))
    return hits


def test_no_creative_get_fallback_anywhere():
    """No creative `.get()` fallback outside the exemption registry.

    Principle-bound where `CREATIVE_SUBSTITUTIONS` is incident-bound:
    the keys are the decisions (effect, sound, level, hold, size,
    weight, face, fade, shadow, position, tier), and any fallback for
    one that is not an explicitly categorised exemption fails - even
    on a file this guard has never named. A new exemption is a
    decision with a reader: it names its category and why, and a
    registry entry matching nothing fails as stale.
    """
    hits = _creative_get_hits()
    unregistered = [
        f"{rel}:{lineno}: .get({key!r}, {fallback}) [{text}]"
        for rel, key, fallback, lineno, text in hits
        if (rel, key, fallback) not in CREATIVE_GET_EXEMPTIONS
    ]
    assert not unregistered, (
        "creative fallbacks outside the exemption registry (add a "
        "categorised entry, or drop the entry with the reason instead "
        "of completing it from a constant):\n" + "\n".join(unregistered)
    )
    matched = {(rel, key, fallback) for rel, key, fallback, _, _ in hits}
    stale = sorted(
        f"{rel} {key} {fallback} ({CREATIVE_GET_EXEMPTIONS[(rel, key, fallback)][0]})"
        for (rel, key, fallback) in CREATIVE_GET_EXEMPTIONS
        if (rel, key, fallback) not in matched
    )
    assert not stale, (
        "stale exemption-registry entries (the line moved or the "
        "constant is gone - delete the entry):\n" + "\n".join(stale)
    )


def test_creative_get_guard_can_fire():
    """Every removed creative fallback trips the matcher; clean lines pass.

    The firing half is the removed lines themselves: F2's emit-path
    completion, all six of F3's look completions, and the seven
    substitution literals. The quiet half is what the guard must never
    flag: technical keys, non-creative keys, and explicit None.
    """
    firing = [
        # F2, second code path for the same hold.
        't.get("duration_frames", int(0.5 * fps))',
        # F3, all six look completions (one shown joined, as it was).
        'm.get("font_size", 42)',
        'm.get("x", 0.5)',
        'm.get("fade_in_frames", 10)',
        'm.get("font_weight", 400)',
        'm.get("text_shadow", "0px 4px 12px rgba(0,0,0,0.6)")',
        'declaration.get("font_family", "Helvetica")',
        # The seven substitution literals, in miniature.
        '.get("effect_type", "slow_zoom_in")',
        '.get("intensity", "moderate")',
        '.get("sfx_type", "whoosh")',
        '.get("volume_db", -14)',
        '.get("duration_feel", "medium")',
        '.get("target_energy", "moderate")',
        # F1's assignment shape.
        "t['duration_frames'] = 15  # default 15 frames (~0.5s at 30fps)",
    ]
    for snippet in firing:
        key_hit = bool(_GET_KEY_PATTERN.search(snippet))
        assign_hit = bool(_DURATION_ASSIGN_PATTERN.search(snippet))
        assert key_hit or assign_hit, (
            f"the shape-1 matcher does not fire on {snippet!r}"
        )
    # F3 once wore a joined shape: fallback on the next line. The
    # continuation arm must see the opening half.
    assert _GET_COMMA_AT_EOL.search('m.get("text_shadow",'), (
        "the shape-1 matcher misses a fallback continued on the next line"
    )
    quiet = [
        'data.get("frame_rate", 30.0)',
        'clip.get("clip_name", clip.get("source_file", "clip_0"))',
        'selection.get("audio_path")',
        'moment.get("font_size")',
        'props.get("type_role", None)',
        't["duration_frames"] = end_f - start_f',
    ]
    for snippet in quiet:
        key_hit = False
        for match in _GET_KEY_PATTERN.finditer(snippet):
            norm = _normalise_fallback(
                _fallback_after(snippet, match.end()))
            if norm not in ("none", ""):
                key_hit = True
        assign_hit = bool(_DURATION_ASSIGN_PATTERN.search(snippet))
        assert not (key_hit or assign_hit), (
            f"the shape-1 matcher fires on legitimate output {snippet!r}"
        )


# ── Shape 2: selection by ordering or truncation, never by judgement ──
#
# F4 settled which picture plays by alphabet
# (`for clip_id in sorted(catalog_lookup)` in select_broll's
# `_pick_alternative_clip`); F5 shapes the creative menu by platform
# order (`keepers[:declaration.fetch_limit]` in music_selection's
# bridge). A broad `sorted(` sweep cannot discriminate - dozens of
# legitimate orderings sort by timeline position, for display, or for
# stable output - so this shape fingerprints the two mechanisms, not
# the builtin:
#
# 2a. `sorted()` over a clip/catalogue mapping (which picture plays
#     settled by alphabet). Timeline sorts (`key=timeline_in`),
#     display sorts and the model's-own-rank ordering (judge_reels)
#     never match: the fingerprint names the collection, not the call.
# 2b. Truncation of a creative-candidate list to a fetch cap
#     (`keepers[:fetch_limit]`). The one live hit is the scout's
#     [music-discovery-shape] captain hold, so it ships as a
#     needs-captain registry entry - fail-closed (a second truncation
#     fails; the hold resolving fails as stale) rather than as a
#     firing rule a resource bound would trip.
# 2c. Behaviour: an A-roll-duplicating cutaway is dropped, never
#     substituted (the F4 fix, pinned where it lives).
#
# Deliberately not swept: the `" background music"` query suffix (one
# literal under the same captain hold - a guard for it would be
# incident-shaped by construction) and top-N reporting caps over
# measured signal (`failed[:5]`, `scored[:2]`), which shape no menu.

_CATALOG_ORDER_PATTERN = re.compile(
    r"sorted\s*\([^)\n]*(catalog_lookup|clip_catalog|catalog)\b"
)
_FETCH_TRUNCATION_PATTERN = re.compile(
    r"(keepers|candidates)\s*\[[^]\n]*:[^]\n]*(fetch_limit|limit)"
)

# (relative path, mechanism) -> (exemption category, why it is not a
# menu shaped by order). Same fail-closed contract as shape 1.
ORDERING_EXEMPTIONS = {
    ("library/steps/step_2_04_music_selection/bridge.py", "fetch-truncation"): (
        "needs-captain",
        "scout hold [music-discovery-shape]: YouTube-order fetch "
        "truncation shaping the candidate menu; the cap is a resource "
        "bound, the ORDER is the hold",
    ),
}


def _ordering_hits():
    """Catalogue-alphabet picks and candidate-list truncations."""
    hits = []
    for path in _swept_source_files():
        rel = str(path.relative_to(REPO))
        for lineno, line in _iter_code_lines(path):
            if _CATALOG_ORDER_PATTERN.search(line):
                hits.append((rel, "catalog-order", lineno,
                             line.strip()[:160]))
            if _FETCH_TRUNCATION_PATTERN.search(line):
                hits.append((rel, "fetch-truncation", lineno,
                             line.strip()[:160]))
    return hits


def test_no_selection_by_catalogue_order_or_fetch_truncation():
    """No menu is shaped by alphabet or by platform order, undeclared."""
    hits = _ordering_hits()
    unregistered = [
        f"{rel}:{lineno}: {mechanism} [{text}]"
        for rel, mechanism, lineno, text in hits
        if (rel, mechanism) not in ORDERING_EXEMPTIONS
    ]
    assert not unregistered, (
        "ordering/truncation shaping a creative menu outside the "
        "exemption registry:\n" + "\n".join(unregistered)
    )
    matched = {(rel, mechanism) for rel, mechanism, _, _ in hits}
    stale = sorted(
        f"{rel} {mechanism} ({ORDERING_EXEMPTIONS[(rel, mechanism)][0]})"
        for (rel, mechanism) in ORDERING_EXEMPTIONS
        if (rel, mechanism) not in matched
    )
    assert not stale, (
        "stale ordering-registry entries (the line moved or the hold "
        "resolved - delete or re-file the entry):\n" + "\n".join(stale)
    )


def test_ordering_guard_can_fire():
    """The removed F4 loop and the live truncation both trip the matcher;
    legitimate orderings do not."""
    firing = [
        # F4, the line PR 1138 deleted.
        "    for clip_id in sorted(catalog_lookup):",
        # F5, the live truncation (fires; the registry exempts it as a
        # captain hold rather than as clean output).
        "    for result in keepers[:declaration.fetch_limit]:",
    ]
    assert _CATALOG_ORDER_PATTERN.search(firing[0]), (
        "the shape-2 matcher does not fire on the removed F4 loop"
    )
    assert _FETCH_TRUNCATION_PATTERN.search(firing[1]), (
        "the shape-2 matcher does not fire on the fetch truncation"
    )
    quiet = [
        # Timeline order, display order, the model's own rank, measured
        # signal with recorded ties: the orderings the scout clears.
        'for clip in sorted(v1_clips, key=lambda c: c["timeline_in"]):',
        "return [catalogue[name] for name in sorted(catalogue)]",
        "for r in sorted(readings,",
        "return max(windows, key=lambda w: w[1] - w[0])",
        "not_read = sorted(",
    ]
    for snippet in quiet:
        assert not _CATALOG_ORDER_PATTERN.search(snippet), (
            f"the shape-2 matcher fires on a legitimate ordering: {snippet!r}"
        )


def test_broll_matching_its_own_aroll_is_dropped_not_substituted():
    """Shape 2c, pinned where it lives: the F4 fix, behaviourally.

    A cutaway naming its own A-roll clip is skipped with the reason
    recorded; no alphabetically-first replacement is picked. One clip
    stays zero clips - which picture replaces it is the model's
    re-plan, never `sorted()` over the catalogue.
    """
    from library.steps.step_3_02_select_broll.post_bridge import (
        resolve_broll,
    )

    spine = {
        "structure": [{
            "position": 1,
            "block_type": "speech",
            "clip_id": "clip_001",
            "timeline_start": 0.0,
            "timeline_end": 4.0,
        }],
    }
    catalog = [
        {"clip_id": "clip_001", "source_file": "/tmp/a.mov",
         "duration_seconds": 30.0, "width": 1080, "height": 1920},
        {"clip_id": "clip_002", "source_file": "/tmp/b.mov",
         "duration_seconds": 30.0, "width": 1080, "height": 1920},
    ]
    creative = [{
        "clip_id": "clip_001",
        "spine_block_position": 1,
        "preferred_moment": "the wide establishing shot",
        "selection_rationale": "illustrates the line",
    }]
    resolved = resolve_broll(
        creative, [], catalog, [], [], spine, (1080, 1920),
    )
    assert resolved["b_roll_assignments"] == [], (
        f"a substitute was picked: {resolved['b_roll_assignments']}"
    )
    assert "clip_002" not in json.dumps(resolved), (
        "the alphabetically-next clip reached the output unplanned"
    )


# ── Shape 3-of-three: every creative bridge accepts a sparse plan ───
#
# Surface C drives four bridges (broll, sfx, vfx, transitions) and the
# scout names six more (mesh_spine, music_selection,
# creative_direction, speech_sequence, render_motion_graphics,
# color_grade). The swept set is DERIVED from
# `undetermined.DECLARING_STEPS` - the same derivation Surface A uses
# for prompts - so a new model-reaching step is covered whether or not
# anybody remembers to add it, and `validate` is excluded the same way
# (it checks a finished render and plans nothing).
#
# "Accepts" is asserted on behaviour, never vocabulary: the real
# function runs over a sparse input and the output is diffed. Where
# the bridge must refuse rather than pad (music_selection with no
# track, like plan_sfx with no playable sound), the test pins the
# loud refusal - never a substitution. creative_direction has no
# bridge files at all, so the test pins that absence: there is nowhere
# for a floor to live.
#
# If a bridge fails its sparse test, that is a finding, not a test to
# adjust: the entry stays and the bridge is fixed.

def _assert_no_quota_language(where: str, text: str):
    """Captured stderr carries no floor, warning-shaped or otherwise."""
    hits = find_floors(text or "")
    assert not hits, (
        f"{where} talks about count on a sparse plan ({hits})"
    )


def test_sparse_mesh_spine_accepts_an_empty_spine(capsys):
    """No block planned is an empty spine, not an error."""
    from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine

    out = enrich_spine({"structure": []}, {"body_sequence": []}, {}, {})
    assert out["audio_spine"]["structure"] == []
    assert out["audio_spine"]["total_estimated_duration_seconds"] == 0
    _assert_no_quota_language("mesh_spine", capsys.readouterr().err)


def test_sparse_music_selection_refuses_without_substituting(tmp_path):
    """No track chosen is refused loudly, never completed from the shelf.

    Deviation from the scout's uniform "empty in, same out", recorded:
    downstream (mesh_spine's bed, the mix, compile_manifest) reads
    `audio_path` off this step's answer, so an empty selection passing
    through would score the piece with something nobody chose - the
    same reason plan_sfx refuses an unplayable sound rather than
    dropping it quietly. What the guard pins is the principle: the
    refusal names the remedy, and no `sorted(...)[0]` fallback picks a
    track (the 001 defect `music_selection_contract` records).
    """
    payload = {
        "music_candidates": {"candidates": [],
                             "target_duration_seconds": 60.0},
        "project_folder": str(tmp_path),
        "music_selection": {},
    }
    proc = _run_bridge(STEPS / "step_2_04_music_selection" / "post_bridge.py",
                       payload)
    assert proc.returncode == 1, (
        "an empty music selection passed - or the refusal broke.\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "must choose a track" in proc.stdout, (
        f"the refusal names no remedy: {proc.stdout[:300]}"
    )
    assert "audio_path" not in proc.stdout, (
        "the refusal carries a track nobody chose"
    )


def test_sparse_creative_direction_has_no_bridge_to_pad():
    """Step 2.01 plans in the model call; no bridge runs after it."""
    step = STEPS / "step_2_01_creative_direction"
    for name in ("bridge.py", "post_bridge.py"):
        assert not (step / name).exists(), (
            f"{name} appeared under creative_direction - the sparse plan "
            f"for this step is the model's own answer, and a new bridge "
            f"needs its own sparse driver in SPARSE_DRIVERS below"
        )
    # The prompt itself is under Surface A's quota guard; downstream
    # bridges already take `"creative_direction": {}` (the sfx, vfx and
    # transitions sparse payloads above), so an undecided direction
    # pads nothing downstream either.


def test_sparse_speech_sequence_accepts_an_empty_body(tmp_path):
    """No passage selected is an empty sequence, not a count violation."""
    from library.steps.step_2_02_speech_sequence.post_bridge import (
        enrich_speech_sequence,
    )

    out = enrich_speech_sequence({"body_sequence": []}, str(tmp_path))
    assert out["body_sequence"] == []


def test_sparse_motion_graphics_accepts_an_empty_plan(capsys):
    """No element planned is no segment, not a padded layer."""
    from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
        generate_motion_props,
    )

    spine = {"structure": [], "total_estimated_duration_seconds": 0.0}
    segments, resolved = generate_motion_props(
        [], spine, 30, width=1080, height=1920, project_folder="")
    assert segments == []
    assert getattr(resolved, "dropped", []) == []
    _assert_no_quota_language("render_motion_graphics",
                              capsys.readouterr().err)


def test_sparse_color_grade_records_no_correction_needed():
    """An empty colourist answer is a judged decision, not an approval."""
    from library.steps.step_5_01_color_grade.post_bridge import (
        resolve_color_grade,
    )

    out = resolve_color_grade(
        {"project_folder": "", "color_correction": [],
         "grade_assessment": {}})
    spec = out["color_grade_spec"]
    assert spec["per_clip_adjustments"] == []
    assert spec["correction_basis"]["basis"] == "judged_no_correction_needed"


def test_sparse_select_reels_accepts_an_empty_transcript():
    """No turns found is no candidates, not a default reel."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    out = build_context({})
    assert out["reel_candidates"] == []
    assert out["turns"] == []


def test_sparse_judge_reels_accepts_empty_readings():
    """No readings is an empty ordering, never an invented rank."""
    from library.steps.step_3_05_judge_reels.post_bridge import resolve

    out = resolve({}, {"timeline_transcript": {},
                       "reel_selection": {"moments": []}})
    assert out["reel_judgement"]["ordering"] == []
    assert out["reel_judgement"]["readings"] == []


def test_sparse_review_accepts_an_empty_cut():
    """Nothing cut yet is an empty script, not a failing review."""
    from library.steps.step_3_03_review_rough_cut.step import (
        build_actual_script,
    )

    out = build_actual_script([], {"structure": []})
    assert out["blocks"] == []
    assert out["full_text"] == ""


# Every declaring step (minus `validate`, which plans nothing) resolves
# to the sparse test that drives it. A step with no entry here fails
# this test - coverage cannot drift when a model-reaching step is
# added. The four Surface-C originals resolve to their existing tests.
SPARSE_DRIVERS = {
    "select_broll": "test_select_broll_accepts_a_single_cutaway",
    "plan_sfx": "test_plan_sfx_accepts_a_sparse_plan",
    "plan_vfx": "test_plan_vfx_accepts_an_empty_plan",
    "plan_transitions": "test_plan_transitions_accepts_an_empty_plan",
    "mesh_spine": "test_sparse_mesh_spine_accepts_an_empty_spine",
    "music_selection": "test_sparse_music_selection_refuses_without_substituting",
    "creative_direction": "test_sparse_creative_direction_has_no_bridge_to_pad",
    "speech_sequence": "test_sparse_speech_sequence_accepts_an_empty_body",
    "render_motion_graphics": "test_sparse_motion_graphics_accepts_an_empty_plan",
    "color_grade": "test_sparse_color_grade_records_no_correction_needed",
    "select_reels": "test_sparse_select_reels_accepts_an_empty_transcript",
    "judge_reels": "test_sparse_judge_reels_accepts_empty_readings",
    "review_rough_cut": "test_sparse_review_accepts_an_empty_cut",
}


def test_every_declaring_step_has_a_sparse_driver():
    """The sparse set is derived, and the drivers are fail-closed."""
    from library.tools.undetermined import DECLARING_STEPS

    expected = set(DECLARING_STEPS) - {"validate"}
    assert set(SPARSE_DRIVERS) == expected, (
        "sparse-driver drift: missing "
        f"{sorted(expected - set(SPARSE_DRIVERS))}, extra "
        f"{sorted(set(SPARSE_DRIVERS) - expected)}. A new "
        f"model-reaching step needs a sparse driver, not an exception."
    )
    missing = [name for name in SPARSE_DRIVERS.values()
               if name not in globals()]
    assert not missing, (
        f"sparse drivers naming no test in this module: {missing}"
    )

