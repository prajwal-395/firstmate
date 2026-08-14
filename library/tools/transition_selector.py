import sys

from library.tools.transition_vocabulary import (
    CUT_TYPES,
    PLANNABLE_TYPES,
    canonical_type,
    filter_allowed,
    withdrawal_reason,
)

# Words a free-text `target_energy` may use. creative_direction writes
# prose ("building", "start observational -> build to a peak"), never a
# tidy enum, so a high-energy read is a substring test rather than `==`.
_HIGH_ENERGY_WORDS = ("high", "frantic", "intense", "peak", "explosive", "energetic")

# Scene-change defaults, most to least energetic. Every entry is drawable.
_SCENE_CHANGE_HIGH = "flash"
_SCENE_CHANGE_DEFAULT = "defocus"


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
    # `energy`/`mood` never existed on creative_direction; the real keys
    # are `target_energy`/`target_mood`, which is why the energy-driven
    # branch here was unreachable for the whole life of the step.
    energy = str(creative_direction.get("target_energy", "")).lower()
    return any(word in energy for word in _HIGH_ENERGY_WORDS)


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
    - Otherwise: a cut inside one take is a jump cut, a cut across takes
      takes the scene-change transition for the creative direction's
      energy.

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
    # No request at all is not a request for a hard cut - it means fall
    # through to the heuristic below.
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
    if known and from_clip_id == to_clip_id:
        return settle("jump_cut" if "jump_cut" in preferred_types else "hard_cut")

    # 2. Scene change = the drawable transition matching the energy
    if known and from_clip_id != to_clip_id:
        wanted = (
            _SCENE_CHANGE_HIGH
            if _is_high_energy(creative_direction)
            else _SCENE_CHANGE_DEFAULT
        )
        if wanted in preferred_types:
            return settle(wanted)
        for fallback in preferred_types:
            if fallback not in CUT_TYPES:
                return settle(fallback)

    return settle(preferred_types[0] if preferred_types else "hard_cut")
