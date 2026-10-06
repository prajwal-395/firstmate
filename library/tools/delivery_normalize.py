"""Master to the delivery spec through Resolve's Normalize Audio.

`delivery_spec` resolves the targets (integrated loudness, its
tolerance, the true-peak ceiling); `render_qa` and `audio_verify`
judge renders against them. Neither applies anything - this module
does: `Timeline.NormalizeAudioLevel` over timeline items, with the
options the registry's numbers fill. New module because no existing
one owns mastering: the registry stays data (it must never import the
scripting API), the QA stays measurement, and `resolve_axi` stays the
RPC surface rather than growing a second mastering path.

The options this Resolve answers are read, never assumed:
`normalizationMode` must be a member of the live
`GetNormalizeAudioModes()` list the caller passes in (read off the
running instance, like the leveler mode), `targetLoudness` is the
spec's own `loudness_target_lufs`, and unknown option keys refuse -
a misspelled `targetLoudness` normalizing to a peak default would be
the silent wrong-mastering this refusal exists to catch. A False
answer refuses the same way True-without-effect would: claiming it
is the fake success rung 1 removed.
"""

from __future__ import annotations

from library.tools.ren_refusal import RenRefusal


class DeliveryNormalizeRefused(RenRefusal):
    """A delivery normalization cannot be planned or applied."""


#: The `NormalizeAudioOptions` keys this Resolve answers, read off the
#: installed scripting stub. Anything else refuses.
NORMALIZE_OPTION_KEYS = (
    "normalizationMode",
    "targetLevel",
    "targetLoudness",
    "setLevelMode",
)


def _refuse(what: str, why: str, fix: str) -> DeliveryNormalizeRefused:
    return DeliveryNormalizeRefused(what=what, why=why, fix=fix)


def normalize_options_for_spec(spec,
                               normalization_mode: str,
                               known_modes) -> dict:
    """The options that master items to `spec`, validated.

    `spec` is a `delivery_spec.DeliverySpec` (its numbers, never
    restated constants); `normalization_mode` the loudness mode read
    off `GetNormalizeAudioModes()`; `known_modes` that live list. A
    mode outside it refuses naming what answered - guessing a mode
    string masters to whatever default it lands on.
    """
    modes = list(known_modes or [])
    if normalization_mode not in modes:
        raise _refuse(
            f"delivery normalization cannot be planned: mode "
            f"{normalization_mode!r} is not among the modes this "
            f"Resolve answers ({modes})",
            "a mode name nothing answers falls back to a default the "
            "plan did not state.",
            "read GetNormalizeAudioModes() off the running Resolve "
            "and plan one of its members.",
        )
    try:
        target = float(spec.loudness_target_lufs)
    except (TypeError, ValueError) as exc:
        raise _refuse(
            "delivery normalization cannot be planned: the spec "
            f"carries no loudness target ({exc})",
            "a target that is not a number masters to nothing.",
            "resolve the delivery spec for a project that names its "
            "platform.",
        ) from exc
    return {"normalizationMode": normalization_mode,
            "targetLoudness": target}


def normalize_items(timeline, items: list, options: dict,
                    known_modes) -> dict:
    """Normalize `items` on `timeline`, judged by the answer.

    Options are checked against `NORMALIZE_OPTION_KEYS` and the mode
    against `known_modes` before the call - a refusal here travels the
    caller's retry path instead of the mix carrying unmastered audio
    reported mastered. `timeline` is duck-typed so fakes judge the
    discipline in tests. Returns the applied record.
    """
    if not isinstance(options, dict):
        raise _refuse(
            "delivery normalization cannot be applied: options are "
            "not an object",
            "the mode and target cannot be read from this plan.",
            "plan the options with normalize_options_for_spec.",
        )
    unknown = sorted(set(options) - set(NORMALIZE_OPTION_KEYS))
    if unknown:
        raise _refuse(
            f"delivery normalization cannot be applied: unknown "
            f"option keys {unknown}",
            "an option Resolve does not answer is silently ignored - "
            "a misspelled target mastering to a default.",
            "drop the unknown keys.",
        )
    mode = options.get("normalizationMode")
    if mode not in list(known_modes or []):
        raise _refuse(
            f"delivery normalization cannot be applied: mode {mode!r} "
            f"is not among this Resolve's modes",
            "the call would master to an unstated default.",
            "read GetNormalizeAudioModes() and re-plan.",
        )
    if not items:
        raise _refuse(
            "delivery normalization cannot be applied: no timeline "
            "items were named",
            "normalizing nothing would report the mix mastered.",
            "name the timeline items the edit really plays.",
        )
    try:
        wrote = bool(timeline.NormalizeAudioLevel(list(items),
                                                   dict(options)))
    except Exception as exc:
        raise _refuse(
            "delivery normalization cannot be applied: "
            f"NormalizeAudioLevel raised ({exc})",
            "the call declined instead of answering True.",
            "verify the timeline items in Resolve and re-run.",
        ) from exc
    if not wrote:
        raise _refuse(
            "delivery normalization cannot be applied: "
            "NormalizeAudioLevel answered False",
            "False is Resolve declining the write - claiming it would "
            "be the fake success rung 1 removed.",
            "verify the timeline items in Resolve and re-run.",
        )
    return {"normalized": True, "items": len(items),
            "options": dict(options)}
