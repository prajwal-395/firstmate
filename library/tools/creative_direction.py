"""The ONE reading of the `creative_direction` state key.

Step 2.01 is `llm_only`: its `handoff.md` names eight fields and its
`manifest.json` declares the same eight as `expected_schema`, which is
what `present_llm_step` injects into the prompt.  Eight is therefore the
whole of what the model is ASKED for, and the whole of what any reader
can find in the answer.

Ten code sites read the direction.  **Seven of them named a key the
schema does not define**, so each silently returned its own default on
every run the pipeline has ever made, and the default read as a
decision:

* `visual_style`, `accent_color`, `title`, `subtitle`, `series_name` and
  `episode_label` in step 4.06's `generate_motion_props`;
* `content_type` in step 4.04's post-bridge, which picked the Fairlight
  preset the renderer applies.

Two more were the same defect and are already gone: `energy_level`, which
`audio_reactive_sfx.scale_sfx_density` deleted half the SFX plan by, and
`energy`/`mood`, which `transition_selector` chose transitions by.

**Which side was wrong is established from the handoff, not from the
code.**  2.01's handoff lists exactly the eight, and says in as many
words that the direction "is NOT a script or shot list - it's a
compass".  A title, a series name and an accent colour are not on the
list and were never asked for, so the schema is right and the read sites
were wrong.  That matters because repairing the other side - adding
`title` to the schema - would have looked identical and would have asked
a creative director for artwork the captain's §14 puts with the project.

`direction_value` is how a key is read from here on.  It RAISES on a key
outside `DIRECTION_KEYS`, so a read of a field nobody is asked for is a
failure at the read rather than a default three steps downstream.  Same
shape as AGENTS.md §10.1's rule for contract keys: a missing key is a
contract violation and must raise.

`DIRECTION_KEYS` is loaded from step 2.01's own manifest, so the reader
and the prompt cannot drift.
"""

import json
import os

_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "library", "steps", "step_2_01_creative_direction", "manifest.json",
)


class UndeclaredDirectionKey(KeyError):
    """A read named a key step 2.01 is not asked to produce."""


def _load_direction_keys() -> tuple:
    with open(_MANIFEST, encoding="utf-8") as fh:
        manifest = json.load(fh)
    for output in manifest["interface"]["outputs"]:
        if output["name"] == "creative_direction":
            return tuple(output["expected_schema"].keys())
    raise RuntimeError(
        "step_2_01_creative_direction/manifest.json declares no "
        "`creative_direction` output; library/tools/creative_direction.py "
        "reads its expected_schema and has nothing to read."
    )


DIRECTION_KEYS = _load_direction_keys()

# What each withdrawn read wanted, and where that value really lives.
# A key here is REFUSED by `direction_value`; the entry is the record of
# why, in the shape `energy_reading.WITHDRAWN_HIGH_WORDS` uses.
WITHDRAWN_DIRECTION_KEYS = {
    "visual_style": (
        "read by step 4.06's generate_motion_props as the wrapper around "
        "an accent colour. A brand's visuals are the template's "
        "`style` slot, which that step is already routed as "
        "`brand_style`; the direction is prose about the footage and "
        "carries no style object."
    ),
    "accent_color": (
        "read by step 4.06 for the corner accents and the upper third. "
        "The colour comes from the template's `style.color_palette` "
        "through library/tools/brand_palette.py, which is the only "
        "declared source of one; there is no per-video accent."
    ),
    "title": (
        "read by step 4.06 for the upper third. On-screen copy is "
        "ARTWORK and belongs to the project, not to the engine and not "
        "to a brand template - AGENTS.md section 14. No project-side "
        "declaration feeds the upper third today, so it draws nothing."
    ),
    "subtitle": (
        "same as `title`: the second line of the same upper third, and "
        "the same section 14 ruling."
    ),
    "series_name": (
        "read by step 4.06 as a fallback title. A series name is the "
        "brand template's `content.series_title`; drawing it would make "
        "it on-screen copy, which section 14 keeps out of a template."
    ),
    "episode_label": (
        "read by step 4.06 as a fallback subtitle. Which episode this "
        "is, is the project's, and nothing declares it."
    ),
    "content_type": (
        "read by step 4.04's post-bridge to pick the Fairlight preset "
        "the renderer applies. A creative direction describes the "
        "footage's story, not the product's format; the declared route "
        "into that choice is the brand template's audio slot, which "
        "`select_preset_for_content` already prefers as "
        "`preferred_preset`."
    ),
    "energy_level": (
        "read by the deleted `audio_reactive_sfx.scale_sfx_density`, "
        "which cut half the SFX plan by it. The real key is "
        "`target_energy`; see library/tools/energy_reading.py."
    ),
    "energy": (
        "read by `transition_selector` before "
        "library/tools/energy_reading.py existed. The real key is "
        "`target_energy`."
    ),
    "mood": (
        "same vintage as `energy`. The real key is `target_mood`."
    ),
}

# Of the eight, only `target_energy` has a mechanical reader.  The other
# seven reach the model's eye and nothing else, and that is a legitimate
# thing for a compass to do - `mesh_spine`'s `context_fields` names six
# of them by path, and nine handoffs read the direction whole.  The
# distinction is recorded rather than left implicit so that a field which
# is neither read nor sent shows up as the defect it would be.
PROMPT_ONLY_KEYS = (
    "narrative_theme",
    "target_mood",
    "energy_arc",
    "emotional_landscape",
    "audience_emotion",
    "key_moments",
    "rationale",
)

MECHANICALLY_READ_KEYS = ("target_energy",)


def direction_value(creative_direction, key, default=None):
    """One value out of the creative direction, or `default`.

    Raises `UndeclaredDirectionKey` when `key` is not one step 2.01 is
    asked to produce, because a default returned for a key that cannot
    exist is a decision nobody took.
    """
    if key not in DIRECTION_KEYS:
        reason = WITHDRAWN_DIRECTION_KEYS.get(key)
        detail = f" {reason}" if reason else ""
        raise UndeclaredDirectionKey(
            f"{key!r} is not a creative_direction field. Step 2.01 is "
            f"asked for {', '.join(DIRECTION_KEYS)} and nothing else, so "
            f"this read could only ever return its default.{detail}"
        )
    return (creative_direction or {}).get(key, default)
