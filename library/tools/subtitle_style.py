"""Every subtitle look a brand template may name, in one enumeration.

P3.2. Captions are on screen for most of the runtime, and until now every
project rendered them identically: Montserrat 800 at 58px, white with a
`#FBF0B8` accent, bottom, 4px black outline. The cause was a one-line
default. `generate_remotion_props.py` did

    "style": subtitle_data.get("style", {...the hardcoded look...})

where `subtitle_data` is the `subtitle_plan` from step 4.01, which never
wrote a `style` key. So `effect.subtitle_style` ("bold_large", "minimal",
"clean_standard"), `style.typography` and `style.color_palette` reached
nothing in all four shipped templates.

The Remotion side was never the problem: `SubtitleOverlay` really does
consume `fontFamily`, `fontSize`, `fontWeight`, `fontColor`,
`accentColor`, `outlineColor`, `outlineWidth` and `position`. Nothing
produced them.

Shape versus brand
------------------
A named style owns the SHAPE of the look - how large, how heavy, how
thick the outline, where on screen. That is what "bold_large" and
"minimal" actually mean, and it does not vary by client.

The BRAND specifics come from the template: `style.typography` supplies
the family, size and weight when it declares them, and
`style.color_palette` supplies the colours for styles that opt into it.
Inventing a palette here would be authoring brand identity in a tool
module, which is not this file's job.

Adding a style means adding it here and naming it from a template;
`tests/test_subtitle_style.py` fails on an orphan in either direction, the
same contract `transition_vocabulary` and `house_look` hold.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from library.tools.brand_palette import roles_from_palette

# The look every project rendered before this module existed. Preserved
# exactly so `default_subtitles` is a no-op rather than a surprise.
LEGACY_FONT_FAMILY = "Montserrat"
LEGACY_FONT_SIZE = 160
LEGACY_FONT_WEIGHT = 800
LEGACY_FONT_COLOR = "#FFFFFF"
LEGACY_ACCENT_COLOR = "#FBF0B8"
LEGACY_OUTLINE_COLOR = "#000000"
LEGACY_OUTLINE_WIDTH = 12

VALID_POSITIONS = ("bottom", "center", "top")

@dataclass(frozen=True)
class SubtitleStyle:
    """One named caption look.

    `palette_driven` decides whether the template's `color_palette`
    replaces the colours below. It is False for `default_subtitles`
    because that style exists to reproduce today's output, and
    `default_brand.yaml`'s palette is three pure RGB primaries - visibly
    a placeholder, and not something to paint captions with.
    """

    name: str
    font_size: int
    font_weight: int
    outline_width: int
    position: str = "bottom"
    font_family: str = LEGACY_FONT_FAMILY
    font_color: str = LEGACY_FONT_COLOR
    accent_color: str = LEGACY_ACCENT_COLOR
    outline_color: str = LEGACY_OUTLINE_COLOR
    palette_driven: bool = True
    derived_from: str = ""

    def resolve(
        self,
        typography: Optional[Dict[str, Any]] = None,
        color_palette: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """The concrete props `SubtitleOverlay` reads.

        Every key here has a reader in
        `remotion-subtitles/src/compositions/SubtitleOverlay/`. Do not add
        one without adding the reader in the same commit.
        """
        typography = typography or {}
        family = typography.get("font") or self.font_family
        size = _coerce_int(typography.get("size"), self.font_size)
        weight = _coerce_weight(typography.get("weight"), self.font_weight)

        font_color = self.font_color
        accent_color = self.accent_color
        outline_color = self.outline_color
        if self.palette_driven and color_palette:
            picked = _colors_from_palette(color_palette)
            font_color = picked.get("font", font_color)
            accent_color = picked.get("accent", accent_color)
            outline_color = picked.get("outline", outline_color)

        return {
            "fontFamily": family,
            "fontSize": size,
            "fontWeight": weight,
            "fontColor": font_color,
            "accentColor": accent_color,
            "outlineColor": outline_color,
            "outlineWidth": self.outline_width,
            "position": self.position,
        }


SUBTITLE_STYLES: Dict[str, SubtitleStyle] = {
    # The pre-P3.2 look, unchanged, so a template naming it renders
    # byte-identically to before.
    "default_subtitles": SubtitleStyle(
        name="default_subtitles",
        font_size=LEGACY_FONT_SIZE,
        font_weight=LEGACY_FONT_WEIGHT,
        outline_width=LEGACY_OUTLINE_WIDTH,
        palette_driven=False,
        derived_from=(
            "The hardcoded default in generate_remotion_props.py that every "
            "project rendered before P3.2. Kept as a named style so 'the old "
            "look' is a choice a template makes rather than a fallback "
            "nobody chose."
        ),
    ),
    # shortform_energetic: high energy, dense SFX, always fill. Captions
    # are part of the punch, so they are large and heavily outlined to
    # survive a busy frame.
    "bold_large": SubtitleStyle(
        name="bold_large",
        font_size=192,
        font_weight=900,
        outline_width=18,
        derived_from=(
            "shortform_energetic.yaml: energy_profile high, vfx_intensity "
            "0.8, dense SFX. Larger and heavier than the legacy look, with a "
            "thicker outline because the electric_contrast grade pushes "
            "contrast and a thin outline disappears into it."
        ),
    ),
    # cinematic_narrative: letterboxed by design, moderate SFX, a warm
    # filmic grade. Captions stay out of the way of the picture.
    "minimal": SubtitleStyle(
        name="minimal",
        font_size=120,
        font_weight=600,
        outline_width=6,
        derived_from=(
            "cinematic_narrative.yaml: framing_intent 0.0, moderate SFX, the "
            "warm_filmic look. Smaller, lighter and barely outlined, because "
            "a heavy caption fights a letterboxed cinematic frame."
        ),
    ),
    # interview_professional: sparse SFX, muted grade. Legible and
    # unobtrusive - the words matter, the styling should not.
    "clean_standard": SubtitleStyle(
        name="clean_standard",
        font_size=144,
        font_weight=700,
        outline_width=9,
        derived_from=(
            "interview_professional.yaml: sparse SFX, the muted_editorial "
            "look. Between the other two on every axis: legible over talking "
            "heads without reading as shortform."
        ),
    ),
}


class UnknownSubtitleStyle(KeyError):
    """A template named a caption look that does not exist.

    Raised rather than defaulted on purpose. Falling back to the legacy
    look is exactly how `effect.subtitle_style` came to mean nothing.
    """


def get_subtitle_style(name: str) -> SubtitleStyle:
    """The named style, or a raise naming what is available."""
    if not name:
        raise UnknownSubtitleStyle(
            "No subtitle style named. A brand template must set "
            "effect.subtitle_style to one of: "
            + ", ".join(sorted(SUBTITLE_STYLES))
        )
    try:
        return SUBTITLE_STYLES[name]
    except KeyError:
        raise UnknownSubtitleStyle(
            f"Unknown subtitle style {name!r}. Available: "
            + ", ".join(sorted(SUBTITLE_STYLES))
        ) from None


def resolve_subtitle_style(
    brand_effect: Optional[Dict[str, Any]] = None,
    brand_style: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Template slots in, Remotion props out.

    `brand_effect.subtitle_style` picks the shape; `brand_style.typography`
    and `brand_style.color_palette` supply the brand specifics. A template
    that names no style gets the legacy look, because a project with no
    brand template at all must keep rendering.
    """
    brand_effect = brand_effect or {}
    brand_style = brand_style or {}

    name = brand_effect.get("subtitle_style") or "default_subtitles"
    style = get_subtitle_style(name)
    return style.resolve(
        typography=brand_style.get("typography"),
        color_palette=brand_style.get("color_palette"),
    )


# ── Typography coercion ──────────────────────────────

def _coerce_int(value: Any, fallback: int) -> int:
    try:
        out = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return out if out > 0 else fallback


# CSS keyword weights a template is likely to write.
_WEIGHT_WORDS = {
    "thin": 100, "extralight": 200, "light": 300, "normal": 400,
    "regular": 400, "medium": 500, "semibold": 600, "demibold": 600,
    "bold": 700, "extrabold": 800, "ultrabold": 800, "black": 900,
    "heavy": 900,
}


def _coerce_weight(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, str):
        key = value.strip().lower().replace(" ", "").replace("-", "")
        if key in _WEIGHT_WORDS:
            return _WEIGHT_WORDS[key]
    return _coerce_int(value, fallback)


def _colors_from_palette(palette: List[str]) -> Dict[str, str]:
    """Caption colours from a brand palette.

    The rule lives in `library/tools/brand_palette.py` because motion
    graphics ask the same question (P3.1). Two copies of it would let the
    same video carry two different "brand" accents.
    """
    roles = roles_from_palette(palette)
    out = {}
    if "text" in roles:
        out["font"] = roles["text"]
    if "outline" in roles:
        out["outline"] = roles["outline"]
    if "accent" in roles:
        out["accent"] = roles["accent"]
    return out
