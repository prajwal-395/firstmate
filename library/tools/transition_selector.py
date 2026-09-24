import sys

from library.tools.energy_reading import HIGH_ENERGY_WORDS, is_high_energy
from library.tools.transition_vocabulary import (
    CUT_TYPES,
    NATIVE_TYPES,
    PLANNABLE_TYPES,
    canonical_type,
    filter_allowed,
    native_canonical_type,
    withdrawal_reason,
)
from library.tools.native_ops import (
    refused_native_canonical,
    refuse_native_transition,
)

# The energy vocabulary lives in ONE module, because two readers of the
# same field used to disagree about "building" and only one of them acted.
# See library/tools/energy_reading.py.
_HIGH_ENERGY_WORDS = HIGH_ENERGY_WORDS

# There is no scene-change default, and there must not be one again.
#
# This module used to invent a DRAWN transition for any cut the plan had
# not decorated: `fade_to_black` on a breather or a transition_slot,
# `flash` on a music step up, and `defocus` on everything else, capped at
# one per twenty seconds. None of it was asked for by an editor. It is
# the same shape as `inject_default_ken_burns` (removed by the captain's
# ruling of 2026-08-20, still alive in code until #192) and as the
# `min_trans` floor in step 4.02's post-bridge that padded the plan with
# `defocus` entries - taste, chosen by a constant, outvoting the plan.
#
# A cut the plan did not decorate is a HARD CUT. `transition_vocabulary`
# says so in as many words: a cut type draws nothing, and AGENTS.md 10.4
# records that "an edit of nothing but hard cuts is the absence of
# decoration". Absence is a legitimate answer; an invented `defocus` is
# not. Guarded by tests/test_no_creative_floors.py.
WITHDRAWN_SCENE_CHANGE_DEFAULTS = {
    "flash": "invented on a `music_behavior` of 'step_up' - which is not "
             "even a word in library/tools/music_behavior.py, so the "
             "branch could never fire on a real spine",
    "fade_to_black": "invented on a 'breather' or 'transition_slot' block",
    "defocus": "invented on every other cut across two source clips, once "
               "per twenty seconds",
}


def brand_duration_bounds_ms(raw) -> tuple:
    """The (min, max) a brand template PERMITS, or (None, None).

    A project copy writes `transition_duration_ms: {min: 200, max:
    500}`.  A RANGE is a permission, in exactly the way an allow-list of
    transition types is - it says what the brand will accept, not how
    long any one transition should hold.  A SCALAR is a declaration, and
    reads as both bounds.
    """
    if isinstance(raw, dict):
        lo, hi = raw.get("min"), raw.get("max")
        lo = int(lo) if lo is not None else None
        hi = int(hi) if hi is not None else None
        return lo, hi
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return int(raw), int(raw)
    return None, None


def brand_declared_duration_ms(raw):
    """The one length a brand DECLARED, or None if it declared a range.

    Returning the range's `max` here is what silently overruled the
    plan: project 001's two drawn transitions were planned "quick" and
    "medium" and both were held for 500 ms, the top of a range in a
    template the project never selected.  A range names no length, so
    this answers None and the plan's own `duration_feel` decides.

    There is no fallback constant.  This used to end `return default`
    with `default=500`, which handed a transition a length nobody chose
    even when neither the brand nor the plan had said anything.
    """
    lo, hi = brand_duration_bounds_ms(raw)
    return lo if lo is not None and lo == hi else None


def _is_high_energy(creative_direction: dict) -> bool:
    return is_high_energy(creative_direction)


def select_transition(
    from_clip: dict,
    to_clip: dict,
    brand_effect: dict,
    creative_direction: dict,
    requested_type: str = "",
    fallback_type: str = "",
) -> dict:
    """
    Choose the transition to draw at one cut.

    - The type the creative plan explicitly requested wins, when it is
      drawable and the brand allows it - the editor's call outranks the
      heuristic. Drawable covers the per-clip Fusion route AND the
      native Resolve route (`cross_dissolve`, `slide`, `smooth_cut`,
      `spin` - the names PR 1376 measured as granted, drawn by Resolve
      itself at the V1 cut).
    - A requested type Resolve measured as refusing (Whip Pan, Dip,
      Push, Blur Dissolve and the others `native_ops` lists) REFUSES by
      name through `NativeTransitionRefused` - it is never downgraded to
      a hard cut, and never swapped for the nearest granted native
      transition unless the plan states one in `fallback_type`.
    - A requested type that is neither drawable nor a measured refusal
      (a typo, a withdrawn type) is downgraded to a hard cut and the
      reason is recorded. It is never quietly swapped for a different
      creative transition.
    - Otherwise nothing is drawn. A cut inside one take is labelled a
      jump cut; every other undecorated cut is a hard cut. This function
      never invents a DRAWN transition - see
      `WITHDRAWN_SCENE_CHANGE_DEFAULTS`.

    Returns {"type", "duration_ms", "duration_bounds_ms",
    "requested_type", "downgrade_reason"}.

    `duration_ms` is None for a drawn transition whose brand declared no
    single length - which is the usual case, because a template writes a
    RANGE.  How long the transition holds is then the plan's to say; see
    `brand_declared_duration_ms`.
    """
    allowed, rejected = filter_allowed(
        brand_effect.get("transition_types"), source="brand template"
    )
    for name, reason in rejected:
        print(
            f"  Brand template lists transition type {name!r}, which the "
            f"renderer cannot draw: {reason}",
            file=sys.stderr,
        )
    preferred_types = allowed or list(PLANNABLE_TYPES + NATIVE_TYPES)
    raw_duration = brand_effect.get("transition_duration_ms")
    duration_ms = brand_declared_duration_ms(raw_duration)
    bound_min, bound_max = brand_duration_bounds_ms(raw_duration)

    requested_raw = (requested_type or "").strip()
    # No request at all means nothing is drawn here.
    # The Fusion route reads first: long-standing spellings keep their
    # meaning (`blur_dissolve` is the Fusion `defocus`, not the refused
    # ofx dissolve - see `native_ops`).
    requested = canonical_type(requested_raw) if requested_raw else None
    native_requested = (
        native_canonical_type(requested_raw) if requested_raw else None
    )
    result = {
        "type": "hard_cut",
        "duration_ms": 0,
        "duration_bounds_ms": (bound_min, bound_max),
        "requested_type": requested_raw,
        "downgrade_reason": "",
    }

    def settle(ttype: str, reason: str = ""):
        result["type"] = ttype
        result["duration_ms"] = 0 if ttype in CUT_TYPES else duration_ms
        result["downgrade_reason"] = reason
        return result

    # 0. Honour an explicit creative choice.
    if requested_raw and requested is None and native_requested is None:
        refused = refused_native_canonical(requested_raw)
        if refused is not None:
            # A measured refusal: Resolve answered this name empty, so
            # shipping a hard cut would be a plan the picture disobeyed
            # without saying so. Refuse by name - unless the plan states
            # the granted native transition to ship instead.
            fallback = (native_canonical_type(fallback_type)
                        if (fallback_type or "").strip() else None)
            if fallback is not None and fallback in preferred_types:
                reason = (
                    f"{requested_raw!r} refused by Resolve "
                    f"(measured 2026-09-24); shipping the plan's stated "
                    f"fallback {fallback!r}"
                )
                print(f"  Transition type {reason}", file=sys.stderr)
                return settle(fallback, reason)
            raise refuse_native_transition(requested_raw)
        reason = withdrawal_reason(requested_raw)
        print(
            f"  Transition type {requested_raw!r} downgraded to hard_cut: "
            f"{reason}",
            file=sys.stderr,
        )
        return settle("hard_cut", reason)

    if requested or native_requested:
        chosen = requested or native_requested
        if chosen in preferred_types:
            return settle(chosen)
        reason = (
            f"{chosen!r} is drawable but the brand template allows only "
            f"{preferred_types}"
        )
        print(f"  Transition type {reason}; using hard_cut", file=sys.stderr)
        return settle("hard_cut", reason)

    # Spine blocks expose clip_id (see spine_contract), so "did we change
    # source clip?" is a real signal, not a None == None guess.
    from_clip_id = from_clip.get("clip_id")
    to_clip_id = to_clip.get("clip_id")
    known = from_clip_id is not None and to_clip_id is not None

    # 1. Same source clip = jump cut (there is no second angle to move to)
    #
    # This is the one label chosen without a request, and it draws
    # nothing: `jump_cut` is in CUT_TYPES. It DESCRIBES the cut that is
    # already there - a cut inside one take IS a jump cut - rather than
    # deciding anything about how the piece should feel.
    if known and from_clip_id == to_clip_id:
        return settle("jump_cut" if "jump_cut" in preferred_types else "hard_cut")

    # 2. Anything else the plan did not decorate is a hard cut.
    #    See WITHDRAWN_SCENE_CHANGE_DEFAULTS above.
    return settle("hard_cut")
