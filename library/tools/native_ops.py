"""Native Resolve operations reachable from the plan (fidelity rung 3b).

PR 1376 (rung 3a, merged) measured which Resolve 21.1 operations really
work, each judged by read-back on a throwaway project: `TimelineItem.SetSpeed`
(constant percent and 0.0 freeze, with ripple), `TimelineItem.AddTransition`
and the audio cross fade. This module is the plan-side enumeration of
exactly that measured set - what the plan vocabulary may name, what the
timeline build may apply, and what refuses by name.

What is granted (measured 2026-09-24, Resolve 21.1):

- `speed_ramp`: a SEQUENCE of constant-speed segments. The 21.1 stub
  carries no speed-curve API, so a ramp is stepped segments, never a
  curve - a `curve` param refuses (see `NativeSpeedRefused`), and each
  segment is one constant `SetSpeed` judged by `GetSpeed`.
- `freeze_frame`: `SetSpeed` 0.0 on a fresh item, judged by `GetSpeed`
  re-reading 0.0. A freeze AFTER a rippled retime re-reads 100.0 while
  the pixels freeze - that case refuses here exactly as the verb does
  (see `FREEZE_AFTER_RIPPLE`).
- Native transitions, each granted only in its measured category:
  Cross Dissolve in simple and fusion, Slide in simple, Smooth Cut in
  simple, Spin in fusion. (Audio Cross Fade +3 dB also read back, but
  it is an audio operation and out of the transition plan's scope.)

What refuses (measured empty answers on the same build):

- Whip Pan (fusion), Dip to Color Dissolve, Push, Blur Dissolve (ofx),
  Slide/Smooth Cut in fusion (granted only in simple), Cross Fade -3 dB.
  A refused name is refused BY NAME through `NativeTransitionRefused`,
  never silently downgraded to `hard_cut` - a whip that ships as a hard
  cut is a plan the picture disobeyed without saying so.

Out of scope (the next rung): the Resolve AI senses SmartReframe,
MagicMask, DetectSceneCuts and TranscribeAudio speakers. They answered
in the probe but need pixel or word judgement this rung does not build.

    python3 -m library.tools.native_ops          # the vocabulary
"""

from __future__ import annotations

import re

from library.tools.ren_refusal import RenRefusal


# ── Native speed effects ──────────────────────────────────────────────
#
# Both are timeline operations, not Fusion comps: they reach the picture
# through `TimelineItem.SetSpeed`, applied by
# `library/tools/native_ops_apply.py` during the timeline build and
# judged by `GetSpeed`. Nothing here draws pixels.
NATIVE_SPEED_EFFECTS = ("speed_ramp", "freeze_frame")

# A ramp is stepped constant segments because the 21.1 stub carries no
# speed-curve API. Each segment is one constant `SetSpeed` with its own
# read-back - so the plan names segments, never a curve.
SPEED_RAMP_SEGMENT_KEYS = frozenset({"percent", "anchor", "anchor_end"})

# What a `speed_ramp` entry's `params` may carry. `segments` is the
# sequence of constant-speed steps; `curve` (or any easing/behaviour
# spelling) refuses - rounding a curve to constants would invent the
# pacing the plan declined to step out.
SPEED_RAMP_PARAM_KEYS = frozenset({"segments", "curve", "easing", "bezier"})

# Spelling out loud what `--freeze` already says on the verb: a freeze
# is never a `--percent 0` typo. In the plan the name `freeze_frame`
# IS the saying-so, so a `speed_ramp` segment at 0% refuses naming it.
FREEZE_SPELLED_OUT = (
    "a freeze is spelled `freeze_frame`, never a 0% speed: "
    "a `speed_ramp` segment at 0% refuses so a freeze is never a typo"
)

# Measured 2026-09-24: `SetSpeed` 0.0 after a rippled 40% answers True
# but `GetSpeed` re-reads 100.0 (the render pixels ARE frozen -
# inter-frame diff 0.03 vs 8.3 moving). The verb refuses this case by
# read-back, and the plan path refuses it the same way: any earlier
# speed op on the same item in this build, or a `GetSpeed` that does
# not read 100.0 before the freeze, refuses the freeze.
FREEZE_AFTER_RIPPLE = (
    "freeze after a retime refuses: measured 2026-09-24, `SetSpeed` 0.0 "
    "after a rippled speed change answers True while `GetSpeed` re-reads "
    "100.0, so the write cannot be judged and is refused rather than "
    "claimed"
)


class NativeSpeedRefused(RenRefusal):
    """A planned speed op names something with no measured write."""


def refuse_hold(effect_type: str, target, detail: str) -> NativeSpeedRefused:
    """Refuse a freeze hold the plan states but nothing can place."""
    return NativeSpeedRefused(
        what=(f"step plan_vfx plan entry for block {target!r} plans "
              f"{effect_type!r} with {detail}"),
        why=("a freeze holds the span the plan addresses - from its "
             "anchor, inside its block, for one stated length - and a "
             "hold naming anything else would freeze a moment the plan "
             "did not address"),
        fix=(f"re-plan block {target!r} with `anchor` and one of "
             f"`hold_seconds` / `hold_frames` stating a span inside "
             f"the block, and no `anchor_end` beside them"),
    )


def refuse_speed_curve(effect_type: str, target) -> NativeSpeedRefused:
    """Refuse a ramp planned as a curve rather than stepped segments."""
    return NativeSpeedRefused(
        what=(f"step plan_vfx plan entry for block {target!r} plans "
              f"{effect_type!r} as a curve"),
        why=("Resolve 21.1 carries no speed-curve API - the stub offers "
             "`TimelineItem.SetSpeed` with a constant Percentage only, "
             "measured 2026-09-24 (PR 1376) - so a ramp reaches the "
             "timeline as stepped constant-speed segments, never as a "
             "curve that nothing would draw"),
        fix=(f"re-plan block {target!r} with `params.segments` - a list of "
             "`{{percent}}` steps (each above 0; a freeze is "
             "`freeze_frame`, never a 0% step) - and drop the curve key"),
    )


# ── Native transitions ────────────────────────────────────────────────
#
# Canonical snake_case name -> the Resolve display name `AddTransition`
# takes plus the categories 1376 measured as granted. A name granted in
# one category and refused in another (Slide/Smooth Cut: simple yes,
# fusion empty) carries both, so the refusal can name the category.
NATIVE_TRANSITIONS = {
    "cross_dissolve": {
        "resolve_name": "Cross Dissolve",
        "granted_categories": ("simple", "fusion"),
        "reading": (
            "mixes the outgoing and incoming clips; drawn by Resolve "
            "itself at the V1 cut, which a per-clip Fusion comp can never "
            "do (measured granted in simple and fusion, 2026-09-24)"
        ),
    },
    "slide": {
        "resolve_name": "Slide",
        "granted_categories": ("simple",),
        "reading": (
            "slides the incoming clip in; granted in simple, refused in "
            "fusion (measured 2026-09-24)"
        ),
    },
    "smooth_cut": {
        "resolve_name": "Smooth Cut",
        "granted_categories": ("simple",),
        "reading": (
            "morphs across the cut; granted in simple, refused in fusion "
            "(measured 2026-09-24)"
        ),
    },
    "spin": {
        "resolve_name": "Spin",
        "granted_categories": ("fusion",),
        "reading": (
            "spins across the cut; granted in fusion "
            "(measured 2026-09-24)"
        ),
    },
}

# Spellings that mean a granted native transition, not a new one.
NATIVE_ALIASES = {
    "dissolve": "cross_dissolve",
    "crossdissolve": "cross_dissolve",
    "cross_dissolve_simple": "cross_dissolve",
    "smoothcut": "smooth_cut",
}

# Names 1376 measured as refused (empty `AddTransition` answer), each
# with the measured reason. A plan naming one is refused BY NAME - never
# downgraded to `hard_cut`, and never swapped for the nearest granted
# native transition unless the plan states one (`fallback_type`).
REFUSED_NATIVE_TRANSITIONS = {
    "whip_pan": {
        "resolve_name": "Whip Pan",
        "reading": (
            "`AddTransition` Whip Pan in fusion answered empty "
            "(measured 2026-09-24); `zoom_blur` is a crash zoom and looks "
            "nothing like a whip pan, so it is not a substitute either"
        ),
    },
    "dip": {
        "resolve_name": "Dip to Color Dissolve",
        "reading": (
            "`AddTransition` Dip answered empty (measured 2026-09-24); "
            "`fade_to_black` is a dip to black, not a dip to color, and "
            "is not a substitute"
        ),
    },
    "dip_to_color": {
        "resolve_name": "Dip to Color Dissolve",
        "reading": (
            "`AddTransition` Dip answered empty (measured 2026-09-24)"
        ),
    },
    "push": {
        "resolve_name": "Push",
        "reading": (
            "`AddTransition` Push in fusion answered empty "
            "(measured 2026-09-24)"
        ),
    },
    "blur_dissolve": {
        "resolve_name": "Blur Dissolve",
        "reading": (
            "`AddTransition` Blur Dissolve in ofx answered empty "
            "(measured 2026-09-24). Note the spelling: a bare "
            "`blur_dissolve` on the plan keeps its long-standing reading "
            "as the Fusion `defocus` (see `transition_vocabulary.ALIASES`) "
            "and never reaches here - this refusal is for the ofx "
            "dissolve named as `Blur Dissolve`"
        ),
    },
}

NATIVE_ALIAS_OF_REFUSED = {
    "whip": "whip_pan",
    "whip_pan_fusion": "whip_pan",
    "dip_to_color_dissolve": "dip_to_color",
    # The ofx dissolve, named as Resolve names it - a bare
    # `blur_dissolve` stays the Fusion `defocus` alias (checked first),
    # so these spellings are the ones that refuse.
    "blur dissolve": "blur_dissolve",
    "ofx_blur_dissolve": "blur_dissolve",
}

# Audio Cross Fade -3 dB answered False while +3 dB read back (measured
# 2026-09-24). It is an audio operation, out of the transition plan's
# scope, and recorded here so a plan reaching for it is told where it
# belongs rather than refused as unknown.
AUDIO_CROSS_FADE_NOTE = (
    "audio cross fades are an audio operation, not a transition-plan "
    "type: Cross Fade +3 dB read back while -3 dB answered False "
    "(measured 2026-09-24); the transition plan names picture "
    "transitions only"
)


class NativeTransitionRefused(RenRefusal):
    """A planned transition names a native type Resolve refused."""


def _norm(raw) -> str:
    """Lowercase with separators collapsed: `Smooth Cut`, `smooth-cut`
    and `smooth_cut` are one name, as on the plan."""
    return re.sub(r"[\s_\-]+", "_", str(raw or "").strip().lower())


def native_canonical(raw):
    """The granted native transition `raw` names, or None."""
    key = NATIVE_ALIASES.get(_norm(raw), _norm(raw))
    return key if key in NATIVE_TRANSITIONS else None


def refused_native_canonical(raw):
    """The measured-refused native transition `raw` names, or None."""
    key = NATIVE_ALIAS_OF_REFUSED.get(_norm(raw), _norm(raw))
    if key in REFUSED_NATIVE_TRANSITIONS:
        return key
    # `dip` aliases are handled above; anything else stays as-is.
    return None


def granted_categories(canonical: str) -> tuple:
    """The categories `AddTransition` granted for this transition."""
    return NATIVE_TRANSITIONS[canonical]["granted_categories"]


def resolve_transition_name(canonical: str) -> str:
    """The display name `AddTransition` takes for this transition."""
    return NATIVE_TRANSITIONS[canonical]["resolve_name"]


def refuse_native_transition(raw, category: str | None = None) -> NativeTransitionRefused:
    """Refuse a measured-refused native transition by name."""
    canonical = refused_native_canonical(raw) or _norm(raw)
    entry = REFUSED_NATIVE_TRANSITIONS.get(canonical, {})
    reading = entry.get("reading") or (
        f"{raw!r} is not a native transition this pipeline measured; "
        f"granted: {', '.join(sorted(NATIVE_TRANSITIONS))}"
    )
    resolve_name = entry.get("resolve_name", str(raw))
    cat = category or "fusion"
    return NativeTransitionRefused(
        what=(f"transition {raw!r} ({resolve_name} in {cat}) is not "
              f"deliverable: Resolve answered empty (measured 2026-09-24)"),
        why=reading,
        fix=("re-plan the cut with a granted native transition "
             f"({', '.join(sorted(NATIVE_TRANSITIONS))}), a drawn Fusion "
             "transition (`fade_to_black`, `zoom_blur`, `defocus`, "
             "`flash`), or a `hard_cut` - stated in the plan, never "
             "substituted by the engine; a `fallback_type` on the entry "
             "states the granted native transition to ship instead"),
    )


def assert_vocabulary_is_well_formed() -> None:
    """Granted and refused sets are disjoint and every row says what it is."""
    overlap = set(NATIVE_TRANSITIONS) & set(REFUSED_NATIVE_TRANSITIONS)
    if overlap:
        raise ValueError(
            f"native transitions both granted and refused: {sorted(overlap)}"
        )
    for table, label in ((NATIVE_TRANSITIONS, "NATIVE_TRANSITIONS"),
                         (REFUSED_NATIVE_TRANSITIONS,
                          "REFUSED_NATIVE_TRANSITIONS")):
        for key, row in table.items():
            reading = row.get("reading", "")
            if not isinstance(reading, str) or len(reading.split()) < 4:
                raise ValueError(
                    f"{label}[{key!r}] must state what it is in a sentence"
                )
            if label == "NATIVE_TRANSITIONS" and not row.get(
                    "granted_categories"):
                raise ValueError(
                    f"{label}[{key!r}] must name its granted categories"
                )


def _main() -> None:
    assert_vocabulary_is_well_formed()
    print("Native speed effects (TimelineItem.SetSpeed, judged by GetSpeed)")
    for name in NATIVE_SPEED_EFFECTS:
        print(f"\n  {name}")
    print("\nNative transitions (TimelineItem.AddTransition, judged by "
          "the returned item)")
    for name, row in NATIVE_TRANSITIONS.items():
        print(f"\n  {name} ({row['resolve_name']}: "
              f"{', '.join(row['granted_categories'])})\n      "
              f"{row['reading']}")
    print("\nMeasured refusals (refused by name, never downgraded)")
    for name, row in REFUSED_NATIVE_TRANSITIONS.items():
        print(f"\n  {name} ({row['resolve_name']})\n      {row['reading']}")
    print(f"\nFreeze after a retime\n      {FREEZE_AFTER_RIPPLE}")


if __name__ == "__main__":
    _main()
