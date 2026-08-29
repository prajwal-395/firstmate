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

A floor is a floor whether it pads, rejects, warns, or cuts. The code
guard below is deliberately narrow - it drives the real bridges and
asserts on their real output rather than grepping for the word "default",
which would fail on every legitimate frame rate in the tree.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

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
CREATIVE_PLANNING_STEPS = (BROLL, SFX, VFX, SPEECH, TRANSITIONS)


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

QUOTA_PHRASES = [
    "must plan exactly 5",
    "must plan exactly 5-15",
    "must plan exactly 5-10",
    "must select 5",
    "must plan between",
    "exactly 5-10 sfx",
    "5-15 b-roll",
    "default 5-10 sfx",
    # The two that survived the ruling until 2026-08-25.
    "must plan at least",
    "strictly select exactly",
    "an empty list is a failure",
]

# A quota does not have to be phrased as one. "at least N", "N-M items"
# and "every clip MUST have" all set a floor, so the guard also refuses a
# bare numeric range next to a plural noun and a per-clip MUST.
QUOTA_PATTERNS = [
    # "at least 3", "at least three"
    r"at least\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    # "3-7 VFX items", "10-15 passages"
    r"\b\d+\s*-\s*\d+\s+(?:vfx|sfx|b-roll|passages|items|effects|cutaways|sounds)\b",
    # "every talking head clip >3s MUST have at least a slow zoom".
    # Deliberately narrower than a bare "every ... must have": select_broll
    # says every non-speech block must have B-roll, and that is a COVERAGE
    # requirement, not a floor - an uncovered block is a black hole that
    # compile_manifest._assert_timeline_fully_covered fails on.
    r"every\b[^.\n]{0,80}\bmust\s+have\s+at\s+least\b",
]


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
                "volume_level": "subtle",
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

    with pytest.raises(ValueError, match="distinct timeline position"):
        _assert_sfx_distributed(
            [
                {"timeline_in": 0.0},
                {"timeline_in": 0.0},
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
                "intensity": "subtle",
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


# ── No creative value is substituted for one the plan omitted ─────────
#
# The other half of the captain's 2026-08-26 ruling: the pipeline never
# invents a creative judgement on the model's behalf. A plan entry that
# names no effect, no intensity, no sound or no level is DROPPED with the
# reason, never completed from a constant.

CREATIVE_SUBSTITUTIONS = [
    # (file, the literal that must not be a fallback, what it decided)
    (VFX / "post_bridge.py", '"effect_type", "slow_zoom_in"',
     "which effect a block gets"),
    (VFX / "post_bridge.py", '"intensity", "moderate"',
     "how strong that effect is"),
    (SFX / "post_bridge.py", '"sfx_type", "whoosh"',
     "which sound plays"),
    (SFX / "post_bridge.py", '"volume_level", "subtle"',
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
        {"target_block_position": 1, "intensity": "subtle",
         "rationale": "the shot is held"}
    ]
    proc = _run_bridge(VFX / "post_bridge.py", payload)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout)["enhancement_spec"]["visual_effects"] == [], (
        "an entry naming no effect was completed from a constant"
    )
    assert "names no effect_type" in proc.stderr


def test_plan_vfx_drops_an_entry_that_names_no_intensity():
    """No intensity, and an unrecognised one, both used to mean
    "moderate"."""
    for entry in (
        {"target_block_position": 1, "effect_type": "slow_zoom_in"},
        {"target_block_position": 1, "effect_type": "slow_zoom_in",
         "intensity": "quite strong actually"},
    ):
        payload = _vfx_payload([])
        payload["vfx_creative"] = [entry]
        proc = _run_bridge(VFX / "post_bridge.py", payload)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        effects = json.loads(proc.stdout)["enhancement_spec"]["visual_effects"]
        assert effects == [], f"intensity was substituted for {entry}"


def test_plan_sfx_refuses_an_entry_that_names_no_playable_sound(sfx_library):
    """WHICH sound plays is refused loudly, never substituted.

    Naming no `sfx_id`, or one the library has no file for, fails the
    step where the plan is written - not three steps later inside
    `compile_manifest`, and not by quietly dropping the entry, which
    ships an edit missing a sound nobody decided to cut.
    """
    for entry in (
        {"spine_block_position": 1, "volume_level": "subtle",
         "rationale": "marks the cut"},
        {"spine_block_position": 1, "sfx_id": "not_in_the_library.wav",
         "volume_level": "subtle", "rationale": "marks the cut"},
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
        "volume_level": "deafening", "rationale": "marks the cut",
    }]
    proc = _run_bridge(SFX / "post_bridge.py", payload, sfx_library)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    placed = json.loads(proc.stdout)["sfx_spec"]["sfx_list"]
    assert placed == [], f"a level was substituted: {placed}"
