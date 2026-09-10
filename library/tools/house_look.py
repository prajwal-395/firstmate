"""The look a brand template DECLARES, and the two halves it is delivered in.

**There is no house look.** This module used to carry four of them -
`pmk_default`, `warm_reflection`, `electric_contrast`,
`film_stock_warmth` - each a complete set of numbers: a slope, an offset
and a power triple, a saturation, a pivot contrast, a glow gain,
threshold and size, a grain power and size, and a vignette blend and
falloff. Every one of those numbers was authored here. The *directions*
came from the captain's planning documents ("warm shadows, never blue";
"cream highlights"); the *strengths* did not, and could not - the
documents state a direction, not a magnitude. On project 001, whose
`project.yaml` names no brand template at all, `pmk_default` still
reached all seventeen per-clip Fusion comps and all ten CDLs.

The captain's ruling, 2026-08-28: *"i want no hardcoded values. there are
no house glow looks, there are no settled house grain or anything"*. So
the catalogue is gone, and it is not relocated: no shipped template
carries those numbers either.

What survives is the MECHANISM, which is not taste. Which terms an ASC
CDL has, which parameter names the Fusion comp builder dispatches on,
and which of the two can express a given idea are facts about Resolve
and about this repository. A look is now something a brand template a
project NAMED writes down, and this module reads that declaration:

* **CDL** carries hue and level. An ASC CDL is
  ``out = (in * slope + offset) ** power`` per channel, then a
  saturation term, so:
  - `slope` scales, which moves the bright end most -> highlight tint;
  - `offset` adds, which moves the dark end most -> shadow tint and
    the black floor;
  - `power` warps the middle -> midtone tint;
  - `saturation` is global.
  That is a complete split-tone in four terms, and it is what the
  Resolve API can set on a timeline item without a hand-built node tree
  (`TimelineItem.SetCDL`).

* **Fusion** carries everything a CDL has no term for: pivot contrast,
  highlight bloom, grain, and a shaped - and optionally coloured -
  vignette. Every key it emits is a name
  `library/tools/fusion/comp_builder.build_effect_comp` dispatches on.
  Emitting a name that module does not read produces a comp without that
  effect in it and no warning, which is how three VFX types and four
  grade nodes were silently lost before; `tests/test_house_look.py`
  asserts the nodes get drawn.

Three rules make a declaration incapable of smuggling a value back in:

1. **A project that declares nothing gets nothing.** Not a substitute
   look, not a reduced one - `resolve_look` returns None, step 5.01
   writes an empty `fusion_look`, no clip gets a comp for the look's
   sake, and the CDL stays `NEUTRAL_CDL`. This is the shape #297
   established for every other brand slot.
2. **An element is declared WHOLE or not at all.** A declaration
   carrying a glow gain but no threshold is refused by name, because the
   only way to finish it is for this file to pick the missing number.
   That is exactly what it must never do again.
3. **No element has a default and none has a bound.** How strong a glow
   is, and how far a slope may travel, are the declaring author's
   decisions. An engine-supplied range is a strength nobody chose,
   arriving one level up.

Nothing here depends on a file inside a DaVinci Resolve installation.

**On the names.** `house_look` is kept as the name of the module, of the
brand-template slot and of the manifest key. It is an address - templates
write it, the renderer reads it, project 001's recorded state carries it -
and renaming an address moves no frame while risking every reader. What
changed is what it MEANS: it is no longer a name into a catalogue in this
repository, it is the declaration itself.


Rules relocated from AGENTS.md 12
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 12
keeps the headline and points here.

**There is no house look.** The engine ships no slope, no saturation, no contrast, no glow, no grain and no vignette, and a project gets a grade only where a brand template it NAMED declares one.
`library/tools/house_look.py` holds no values of its own. [why](docs/RULE_EVIDENCE.md#there-is-no-house-look)

A look is delivered in two halves, because that is what the mechanisms can express:
- **CDL** carries hue and level - slope (highlights), offset (shadows and the black floor), power (midtones), saturation - applied by `SetCDL` in `resolve_build_timeline`.
- **Fusion** carries what a CDL has no term for - pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and reaches the picture only through the parameter names `fusion/comp_builder.build_effect_comp` dispatches on (§10.2).
- **`LOOK_ELEMENTS` is the whole vocabulary**, and an element outside it is REFUSED by name. Each row says which half delivers it and why that half and not the other.
- **An element is declared WHOLE or refused.** A glow with a gain and no threshold cannot be finished without the engine choosing the missing number, which is the defect this section exists for. Same shape as `bookends` (§13): raise, never drop and never complete.
- **No element has a default and none has a bound.** How strong a glow is, and how far a slope may travel, are the declaring author's decisions; an engine-supplied range is a strength nobody chose arriving one level up.
- **A project declaring no look gets NOTHING** - not a reduced look and not exposure normalisation. `NEUTRAL_CDL` is identity and `fusion_look` is `{}`, so no clip gets a comp for the look's sake at all. This is the shape #297 established for every other brand slot (§10.1).
- **A vignette is drawn only where one was asked for.** `build_effect_comp` used to default `vignette` to True, drawing one at blend 0.25 on every clip carrying a zoom.
- **Exposure is MEASURED, and normalised only onto a reference the declaration carries.** A clip nothing measured carries `null` and a reason, never `0.0`. `exposure_reference` is the declared target. [why](docs/RULE_EVIDENCE.md#the-exposure-probe-measured-nothing)
- `tests/test_house_look.py`, `tests/test_color_grade_delivery.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

RGB = tuple[float, float, float]


class LookDeclarationError(ValueError):
    """A `style.house_look` declaration that cannot be delivered as written.

    Raised rather than dropped, and rather than completed. A dropped
    element ships a video missing a grade somebody asked for, forty
    minutes into an unattended run; a completed one is this file choosing
    a strength again.
    """


@dataclass(frozen=True)
class LookElement:
    """One thing a look may declare, and what carries it to the picture.

    Attributes:
        key: What a brand template writes under `style.house_look`.
        scalar: True when the value is one number rather than a mapping.
        delivered_by: "cdl" or "fusion" - which of the two halves.
        required: Sub-keys that must ALL be present when the element is.
        optional: Sub-keys that may be absent, each with a documented
            reading of that absence which is not a substitute value.
        emits: The parameter names this element puts into the manifest.
        why_here: Why this mechanism and not the other one.
    """

    key: str
    scalar: bool
    delivered_by: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    emits: tuple[str, ...]
    why_here: str


#: Everything a declaration may carry. A key outside this table is
#: REFUSED: a misspelt element that silently draws nothing is the exact
#: failure this repository keeps hitting, and a look is the one place
#: where "nothing drawn" and "drawn faintly" look alike in a report.
LOOK_ELEMENTS: tuple[LookElement, ...] = (
    LookElement(
        key="cdl",
        scalar=False,
        delivered_by="cdl",
        required=("slope", "offset", "power", "saturation"),
        optional=(),
        emits=("slope_r", "slope_g", "slope_b",
               "offset_r", "offset_g", "offset_b",
               "power_r", "power_g", "power_b", "saturation"),
        why_here=(
            "Hue and level, in the four terms TimelineItem.SetCDL takes. "
            "All four are required together because they are one grade: "
            "a slope without an offset is a chosen black floor."
        ),
    ),
    LookElement(
        key="contrast",
        scalar=True,
        delivered_by="fusion",
        required=(),
        optional=(),
        emits=("grade_contrast",),
        why_here=(
            "A CDL has no contrast term - slope/offset/power cannot pivot "
            "around mid grey. Fusion's BrightnessContrast can."
        ),
    ),
    LookElement(
        key="glow",
        scalar=False,
        delivered_by="fusion",
        required=("gain", "threshold", "size"),
        optional=(),
        emits=("glow_gain", "glow_threshold", "glow_size"),
        why_here=(
            "Highlight bloom is a filter, not a transfer function. All "
            "three are required together: a gain says how much bloom, a "
            "threshold says what is hot enough to bloom, and a size says "
            "how far it spreads. Two of the three describe no picture."
        ),
    ),
    LookElement(
        key="grain",
        scalar=False,
        delivered_by="fusion",
        required=("power", "size"),
        optional=(),
        emits=("film_grain", "film_grain_power", "film_grain_size"),
        why_here=(
            "Grain is added texture. Both terms are required: how much, "
            "and how coarse."
        ),
    ),
    LookElement(
        key="vignette",
        scalar=False,
        delivered_by="fusion",
        required=("blend", "soft"),
        optional=("color",),
        emits=("vignette", "vignette_blend", "vignette_soft",
               "vignette_color"),
        why_here=(
            "A vignette shapes falloff, which is the clearest thing a CDL "
            "cannot express at all. `color` is optional because its "
            "absence is the absence of a TINT, not a chosen one: a "
            "vignette with no colour is the frame going dark, which is "
            "what a vignette is."
        ),
    ),
    LookElement(
        key="exposure_reference",
        scalar=True,
        delivered_by="cdl",
        required=(),
        optional=(),
        emits=(),
        why_here=(
            "The average luma (0-255) this series wants its clips to sit "
            "at, so step 5.01 can normalise them onto it. It is delivered "
            "as a plain gain on the CDL slope and is NOT part of the "
            "look's hue. It lives in the declaration because the target "
            "brightness of a finished video is a creative decision: the "
            "engine used to hold it as the constant 122.0, which is the "
            "same defect one level down."
        ),
    ),
)

ELEMENTS_BY_KEY: dict[str, LookElement] = {e.key: e for e in LOOK_ELEMENTS}

#: The one key that is not an element: the label a report, a dashboard
#: and the manifest use to say WHICH look shipped. Required, because a
#: look nobody can name is a grade nobody can review.
NAME_KEY = "name"

#: Prose the declaring author may attach. Never read by the picture.
INTENT_KEY = "intent"


#: A CDL that changes nothing. What a project declaring no look gets, so
#: "no look" is an explicit identity rather than an accidental one. It is
#: the absence of decoration and not a choice of it (AGENTS.md 10.5).
NEUTRAL_CDL = {
    "slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0,
    "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
    "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
    "saturation": 1.0,
}


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LookDeclarationError(
            f"{where} must be a number, got {value!r}. A look is values; "
            f"a name or a path is not a value. A finished node tree "
            f"travels as `color.power_grade_drx` in the project's own "
            f"project.yaml (library/tools/color_page_grade.py), never "
            f"inline in a look."
        )
    return float(value)


def _triple(value: Any, where: str) -> RGB:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise LookDeclarationError(
            f"{where} must be three numbers (r, g, b), got {value!r}."
        )
    values = list(value)
    if len(values) != 3:
        raise LookDeclarationError(
            f"{where} must be three numbers (r, g, b), got {len(values)}. "
            f"A per-channel term with a channel missing cannot be "
            f"completed without this file choosing the missing one."
        )
    return (
        _number(values[0], f"{where}[r]"),
        _number(values[1], f"{where}[g]"),
        _number(values[2], f"{where}[b]"),
    )


@dataclass(frozen=True)
class DeclaredLook:
    """One look, exactly as a brand template declared it.

    Every element is Optional and None means the template did not declare
    it, which means it is not drawn. There is no field with a default
    strength, because a default strength is the thing this module was
    emptied of.
    """

    name: str
    intent: str = ""
    slope: Optional[RGB] = None
    offset: Optional[RGB] = None
    power: Optional[RGB] = None
    saturation: Optional[float] = None
    contrast: Optional[float] = None
    glow_gain: Optional[float] = None
    glow_threshold: Optional[float] = None
    glow_size: Optional[float] = None
    grain_power: Optional[float] = None
    grain_size: Optional[float] = None
    vignette_blend: Optional[float] = None
    vignette_soft: Optional[float] = None
    vignette_color: Optional[RGB] = None
    exposure_reference: Optional[float] = None
    #: The element keys the template really wrote, in declaration order.
    declared: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_cdl(self) -> bool:
        return self.slope is not None

    def cdl(self, *, exposure_gain: float = 1.0) -> dict[str, float]:
        """The CDL half, in the key names the renderer reads.

        `exposure_gain` is the per-clip exposure normalisation - a plain
        multiplier on slope, computed by step 5.01 against the
        `exposure_reference` this look declares. It is not part of the
        look: it makes an under- or over-exposed clip sit where the look
        expects it, and it leaves the hue balance alone because it
        multiplies all three channels equally.

        A look with no `cdl` element returns the neutral CDL, scaled by
        that gain. Neutral is identity, not a grade.
        """
        if not self.has_cdl:
            values = dict(NEUTRAL_CDL)
            for channel in "rgb":
                values[f"slope_{channel}"] = round(exposure_gain, 4)
            return values
        assert self.slope and self.offset and self.power
        assert self.saturation is not None
        return {
            "slope_r": round(self.slope[0] * exposure_gain, 4),
            "slope_g": round(self.slope[1] * exposure_gain, 4),
            "slope_b": round(self.slope[2] * exposure_gain, 4),
            "offset_r": round(self.offset[0], 4),
            "offset_g": round(self.offset[1], 4),
            "offset_b": round(self.offset[2], 4),
            "power_r": round(self.power[0], 4),
            "power_g": round(self.power[1], 4),
            "power_b": round(self.power[2], 4),
            "saturation": round(self.saturation, 4),
        }

    def fusion(self) -> dict[str, object]:
        """The Fusion half, in the parameter names `build_effect_comp` reads.

        ONLY what the template declared. An undeclared element emits no
        key at all rather than a key set to a quiet value: the comp
        builder dispatches on presence, so an absent key is a node that
        is never added, which is what "not declared" has to mean.

        Saturation is deliberately absent even when the CDL declares it:
        the CDL already carries it, and `fx.grade` would multiply it a
        second time in the picture.
        """
        values: dict[str, object] = {}
        if self.contrast is not None:
            values["grade_contrast"] = round(self.contrast, 4)
        if self.glow_gain is not None:
            values["glow_gain"] = round(self.glow_gain, 4)
            values["glow_threshold"] = round(float(self.glow_threshold), 4)
            values["glow_size"] = round(float(self.glow_size), 4)
        if self.grain_power is not None:
            values["film_grain"] = True
            values["film_grain_power"] = round(self.grain_power, 4)
            values["film_grain_size"] = round(float(self.grain_size), 4)
        if self.vignette_blend is not None:
            values["vignette"] = True
            values["vignette_blend"] = round(self.vignette_blend, 4)
            values["vignette_soft"] = round(float(self.vignette_soft), 4)
            if self.vignette_color is not None:
                values["vignette_color"] = list(self.vignette_color)
        return values


def _require_whole(element: LookElement, value: Mapping[str, Any]
                   ) -> dict[str, Any]:
    """Every required sub-key, or a refusal naming the missing ones."""
    if not isinstance(value, Mapping):
        raise LookDeclarationError(
            f"style.house_look.{element.key} must be a mapping carrying "
            f"{', '.join(element.required)}; got {value!r}."
        )
    unknown = sorted(set(value) - set(element.required) - set(element.optional))
    if unknown:
        raise LookDeclarationError(
            f"style.house_look.{element.key} carries {unknown}, which "
            f"nothing reads. Known: "
            f"{', '.join(element.required + element.optional)}."
        )
    missing = [k for k in element.required if k not in value]
    if missing:
        raise LookDeclarationError(
            f"style.house_look.{element.key} declares "
            f"{sorted(set(value)) or 'nothing'} but not {missing}. An "
            f"element is declared whole or not at all - finishing it "
            f"means this engine choosing {'a strength' if len(missing) == 1 else 'strengths'} "
            f"nobody chose, which is what the look catalogue was removed "
            f"for. {element.why_here}"
        )
    return dict(value)


def resolve_look(declaration: Any) -> Optional[DeclaredLook]:
    """Read a `style.house_look` declaration, or None when there is none.

    Args:
        declaration: What a brand template wrote under `style.house_look`.
            An absent, empty or None declaration means the template
            declares no look, and the answer is None - no grade, no glow,
            no grain, no vignette.

    Raises:
        LookDeclarationError: the declaration cannot be delivered as
            written. Refused, never completed and never dropped.
    """
    if declaration is None or declaration == "" or declaration == {}:
        return None

    if isinstance(declaration, str):
        raise LookDeclarationError(
            f"style.house_look is a DECLARATION, not a name: got "
            f"{declaration!r}. The four looks this engine used to ship "
            f"were removed - their strengths were numbers nobody chose "
            f"(captain, 2026-08-28: 'there are no house glow looks, there "
            f"are no settled house grain or anything'). Declare the values "
            f"instead:\n\n" + describe_declaration_shape()
        )

    if not isinstance(declaration, Mapping):
        raise LookDeclarationError(
            f"style.house_look must be a mapping, got "
            f"{type(declaration).__name__}."
        )

    unknown = sorted(
        set(declaration) - set(ELEMENTS_BY_KEY) - {NAME_KEY, INTENT_KEY}
    )
    if unknown:
        raise LookDeclarationError(
            f"style.house_look carries {unknown}, which nothing reads. A "
            f"key the renderer never dispatches on produces a comp "
            f"without that effect and no warning. Known elements: "
            f"{', '.join(ELEMENTS_BY_KEY)}."
        )

    name = str(declaration.get(NAME_KEY, "") or "").strip()
    if not name:
        raise LookDeclarationError(
            "style.house_look declares no `name`. The name is the label "
            "the manifest, the renderer's log and the step summary use to "
            "say which look shipped; a grade nobody can name is a grade "
            "nobody can review."
        )

    declared = tuple(k for k in declaration if k in ELEMENTS_BY_KEY)
    if not declared:
        raise LookDeclarationError(
            f"style.house_look {name!r} declares no element, so it draws "
            f"nothing. Omit the key to declare no look; a named look that "
            f"changes no pixel reads as a grade in every report that names "
            f"it. Elements: {', '.join(ELEMENTS_BY_KEY)}."
        )

    fields: dict[str, Any] = {
        "name": name,
        "intent": str(declaration.get(INTENT_KEY, "") or ""),
        "declared": declared,
    }

    if "cdl" in declaration:
        cdl = _require_whole(ELEMENTS_BY_KEY["cdl"], declaration["cdl"])
        fields["slope"] = _triple(cdl["slope"], "style.house_look.cdl.slope")
        fields["offset"] = _triple(cdl["offset"], "style.house_look.cdl.offset")
        fields["power"] = _triple(cdl["power"], "style.house_look.cdl.power")
        fields["saturation"] = _number(
            cdl["saturation"], "style.house_look.cdl.saturation")

    if "contrast" in declaration:
        fields["contrast"] = _number(
            declaration["contrast"], "style.house_look.contrast")

    if "glow" in declaration:
        glow = _require_whole(ELEMENTS_BY_KEY["glow"], declaration["glow"])
        fields["glow_gain"] = _number(glow["gain"], "style.house_look.glow.gain")
        fields["glow_threshold"] = _number(
            glow["threshold"], "style.house_look.glow.threshold")
        fields["glow_size"] = _number(glow["size"], "style.house_look.glow.size")

    if "grain" in declaration:
        grain = _require_whole(ELEMENTS_BY_KEY["grain"], declaration["grain"])
        fields["grain_power"] = _number(
            grain["power"], "style.house_look.grain.power")
        fields["grain_size"] = _number(
            grain["size"], "style.house_look.grain.size")

    if "vignette" in declaration:
        vig = _require_whole(
            ELEMENTS_BY_KEY["vignette"], declaration["vignette"])
        fields["vignette_blend"] = _number(
            vig["blend"], "style.house_look.vignette.blend")
        fields["vignette_soft"] = _number(
            vig["soft"], "style.house_look.vignette.soft")
        if "color" in vig:
            fields["vignette_color"] = _triple(
                vig["color"], "style.house_look.vignette.color")

    if "exposure_reference" in declaration:
        fields["exposure_reference"] = _number(
            declaration["exposure_reference"], "style.house_look.exposure_reference")

    return DeclaredLook(**fields)


def project_house_look(project_folder: Any) -> Any:
    """The `style.house_look` a project declares in its own project.yaml.

    Top-level `style:` block, the same shape `effect.timed_text_overlay`
    takes (library/tools/timed_text_overlay.py): a project may differ
    from its series without forking the series' template, so the project
    wins over the template. Returns the declaration mapping, or None
    when the project declares none.

    A malformed block raises `LookDeclarationError` here rather than at
    render time, because a declaration that renders nothing is
    indistinguishable from no declaration at all.
    """
    import os

    if not project_folder or not isinstance(project_folder, str):
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None
    try:
        import yaml
    except ImportError:
        raise LookDeclarationError(
            "PyYAML is required to read project.yaml for "
            "`style.house_look`.")
    with open(project_yaml, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise LookDeclarationError(
            f"{project_yaml} does not parse as a mapping.")
    style = config.get("style")
    if style is None:
        return None
    if not isinstance(style, dict):
        raise LookDeclarationError(
            f"{project_yaml} has a `style:` block that is not a mapping, "
            f"got {type(style).__name__}.")
    declaration = style.get("house_look")
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise LookDeclarationError(
            f"{project_yaml} `style.house_look` must be a declaration "
            f"mapping, got {declaration!r}.")
    return declaration


def effective_house_look(template_style: Any,
                         project_folder: Any) -> Any:
    """The house_look declaration step 5.01 resolves: project wins.

    The whole slot is replaced rather than merged key by key: half a
    look from each of two sources is a look nobody designed. A project
    that declares nothing leaves the template's slot exactly as it was,
    so this is invisible to every existing project.
    """
    project_declaration = project_house_look(project_folder)
    if project_declaration is not None:
        return project_declaration
    return (template_style or {}).get("house_look")


def describe_declaration_shape() -> str:
    """The declaration shape, as the text an error message shows.

    Generated from `LOOK_ELEMENTS`, so it cannot drift from what
    `resolve_look` accepts.
    """
    lines = [
        "style:",
        "  house_look:",
        "    name: <a label for reports; required>",
        "    intent: <one line on what it is for; optional>",
    ]
    for element in LOOK_ELEMENTS:
        if element.scalar:
            lines.append(f"    {element.key}: <number>"
                         f"    # {element.delivered_by}")
        else:
            keys = ", ".join(f"{k}: <number|[r,g,b]>" for k in element.required)
            extra = "".join(
                f", {k}: <optional>" for k in element.optional)
            lines.append(f"    {element.key}: {{{keys}{extra}}}"
                         f"    # {element.delivered_by}")
    lines.append("")
    lines.append(
        "Every element is optional; an undeclared one is not drawn. A "
        "declared one must carry all of its required keys. Omit "
        "`house_look` entirely to declare no look.")
    return "\n".join(lines)


def describe_look(look: Optional[DeclaredLook]) -> str:
    """One line for the step summary and the renderer's log."""
    if look is None:
        return (
            "No look: no brand template declares `style.house_look`, so "
            "the clips carry no CDL, no contrast, no glow, no grain and "
            "no vignette. There is no house look to fall back to - see "
            "library/tools/house_look.py."
        )
    parts = ", ".join(look.declared)
    intent = f" {look.intent}" if look.intent else ""
    return (
        f"{look.name}: declared by the brand template, carrying {parts}."
        f"{intent}"
    )
