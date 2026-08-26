import sys

from library.tools.energy_reading import HIGH_ENERGY_WORDS, is_high_energy
from library.tools.transition_vocabulary import (
    CUT_TYPES,
    PLANNABLE_TYPES,
    canonical_type,
    filter_allowed,
    withdrawal_reason,
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


def _resolve_duration_ms(raw, default: int = 500) -> int:
    """Accept both the scalar and the brand template's {min,max} shape.

    `default_brand.yaml` writes `transition_duration_ms: {min: 200, max:
    500}` while every reader treated it as a number, so wiring the brand
    template in would have raised a TypeError on the first dissolve.
    """
    if isinstance(raw, dict):
        upper = raw.get("max", raw.get("min"))
        return int(upper) if upper is not None else default
    if isinstance(raw, (int, float)):
        return int(raw)
    return default


def _is_high_energy(creative_direction: dict) -> bool:
    return is_high_energy(creative_direction)


def select_transition(
    from_clip: dict,
    to_clip: dict,
    brand_effect: dict,
    creative_direction: dict,
    requested_type: str = "",
) -> dict:
    """
    Choose the transition to draw at one cut.

    - The type the creative plan explicitly requested wins, when it is
      plannable and the brand allows it - the editor's call outranks the
      heuristic.
    - A requested type that is not plannable is downgraded to a hard cut
      and the reason is recorded. It is never quietly swapped for a
      different creative transition.
    - Otherwise nothing is drawn. A cut inside one take is labelled a
      jump cut; every other undecorated cut is a hard cut. This function
      never invents a DRAWN transition - see
      `WITHDRAWN_SCENE_CHANGE_DEFAULTS`.

    Returns {"type", "duration_ms", "requested_type", "downgrade_reason"}.
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
    preferred_types = allowed or list(PLANNABLE_TYPES)
    duration_ms = _resolve_duration_ms(brand_effect.get("transition_duration_ms"))

    requested_raw = (requested_type or "").strip()
    # No request at all means nothing is drawn here.
    requested = canonical_type(requested_raw) if requested_raw else None
    result = {
        "type": "hard_cut",
        "duration_ms": 0,
        "requested_type": requested_raw,
        "downgrade_reason": "",
    }

    def settle(ttype: str, reason: str = ""):
        result["type"] = ttype
        result["duration_ms"] = 0 if ttype in CUT_TYPES else duration_ms
        result["downgrade_reason"] = reason
        return result

    # 0. Honour an explicit creative choice.
    if requested_raw and requested is None:
        reason = withdrawal_reason(requested_raw)
        print(
            f"  Transition type {requested_raw!r} downgraded to hard_cut: "
            f"{reason}",
            file=sys.stderr,
        )
        return settle("hard_cut", reason)

    if requested:
        if requested in preferred_types:
            return settle(requested)
        reason = (
            f"{requested!r} is drawable but the brand template allows only "
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
