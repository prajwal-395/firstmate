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

And the PROJECT
---------------
A project may declare `pipeline.subtitle_typography` in its project.yaml,
in the same `{font, size, weight}` shape the template uses, and it
outranks the template - the same project-over-template precedence
`delivery_format_name` and `framing_intent` use.

It overrides KEY BY KEY rather than replacing the slot. A project saying
`size: 85` is asking for a size, not asking to give up the family its
template names; replacing the whole slot would silently take the typeface
with it, which is the kind of quiet degradation this repository keeps
having to undo.

It exists because a video may be typeset for itself. Project 001 is one:
the captain typed a Text+ block onto its timeline at an effective 85 px
and said the typography was "for this specific test project only and is
not meant to be the end standard design". A number that governs one video
belongs in that video's project.yaml, not in a preset four other videos
read - so none of `bold_large` (192), `clean_standard` (144), `minimal`
(120) or the legacy 160 moves.

Adding a style means adding it here and naming it from a template;
`tests/test_subtitle_style.py` fails on an orphan in either direction, the
same contract `transition_vocabulary` and `house_look` hold.


Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**A project may typeset its own captions, and that is not a change to anyone else's.**
`pipeline.subtitle_typography` in a project.yaml, the same `{font, size, weight}` shape a template's `style.typography` uses, resolved by `subtitle_style.resolve_subtitle_style`. [why - the captain's measurement, and what the size moves on 001](docs/RULE_EVIDENCE.md#the-caption-size-that-governs-one-video)
- It overrides the template's typography **KEY BY KEY**. That is the one place this precedence differs from `delivery_format_name`'s and `timed_text_overlay`'s, deliberately.
- `TYPOGRAPHY_KEYS` is the whole of what may be declared and a fourth key is refused by name, because `SubtitleStyle.resolve` reads exactly three.
- **A number that governs one video does not go in a preset four other videos read.** `bold_large` (192), `clean_standard` (144), `minimal` (120) and `LEGACY_FONT_SIZE` (160) are untouched.
- `tests/test_subtitle_style.py`.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from library.tools.brand_palette import roles_from_palette
from library.tools.safe_area import SafeAreaInsets, resolve_safe_area

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

# What a `typography` mapping may declare, template-side or project-side.
# `SubtitleStyle.resolve` reads exactly these three; a fourth key would be
# a declaration nothing draws.
TYPOGRAPHY_KEYS = ("font", "size", "weight")

SPEAKER_STYLE_KEYS = (
    "fontFamily", "fontSize", "fontWeight", "fontColor", "accentColor",
    "outlineColor", "outlineWidth", "position",
)
"""What a project may vary PER SPEAKER. Complete, and checked.

A two-speaker conversation where each speaker is captioned differently is
how the viewer reads who is talking without a name tag - the styling IS
the diarization signal, which is why the field-test podcast asked for it.

The values are the captain's taste and live in the project (AGENTS.md 14
- per-series parameters belong with the series). **The engine declares
none.** A project that names no speaker styles gets ONE style for
everybody, which is the absence of a distinction rather than a default
set of colours: picking colours per speaker here would be inventing taste
(AGENTS.md 10.5).

`captionMaxWidth` and `safeArea` are deliberately absent. They are
MEASURED from the delivery frame, not chosen, and a speaker who could
override them could put their own captions outside the safe area."""

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
        safe_area: Optional[SafeAreaInsets] = None,
    ) -> Dict[str, Any]:
        """The concrete props `SubtitleOverlay` reads.

        Every key here has a reader in
        `remotion-subtitles/src/compositions/SubtitleOverlay/`. Do not add
        one without adding the reader in the same commit.

        `safe_area` is what turns `position` into a real distance. The
        overlay used to put a bottom caption at a literal `bottom: 200px`
        - 10.4% of a 1920-row frame, inside the band the platform paints
        its own caption and audio bar over. The inset now comes from
        `library/tools/safe_area.py`, and `captionMaxWidth` comes from
        the same place so the caption is bounded left and right as well
        as below. See that module for where the numbers come from.
        """
        typography = typography or {}
        safe_area = safe_area or resolve_safe_area()
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
            # The four platform insets in pixels, for the overlay to
            # position against whichever edge `position` names.
            "safeArea": safe_area.as_props(),
            # A caption box is CENTRED, so it runs into the nearer edge
            # first and can only be twice that distance wide.
            "captionMaxWidth": safe_area.centered_usable_width,
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


def project_subtitle_typography(
        project_folder: Optional[str]) -> Optional[Dict[str, Any]]:
    """`pipeline.subtitle_typography` off a project.yaml, or None.

    None means the project declared none, which is different from an
    empty mapping. A malformed declaration RAISES: a typography
    declaration that is silently dropped is a caption the editor believes
    shipped, the same reasoning `framing_intent` states.
    """
    from library.tools.brand_registry import project_pipeline_block

    block = project_pipeline_block(project_folder)
    if "subtitle_typography" not in block:
        return None
    declared = block.get("subtitle_typography")
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise TypeError(
            f"pipeline.subtitle_typography in {project_folder}/project.yaml "
            f"must be a mapping of {{font, size, weight}}, got "
            f"{type(declared).__name__}: {declared!r}"
        )
    unknown = sorted(set(declared) - set(TYPOGRAPHY_KEYS))
    if unknown:
        raise ValueError(
            f"pipeline.subtitle_typography in {project_folder}/project.yaml "
            f"declares {unknown}, which nothing reads. It takes "
            f"{list(TYPOGRAPHY_KEYS)}."
        )
    return declared


def project_speaker_styles(
        project_folder: Optional[str]) -> Optional[Dict[str, Dict[str, Any]]]:
    """`pipeline.speaker_subtitle_styles` off a project.yaml, or None.

    None means the project declared none - one style for everybody - and
    that is different from an empty mapping. A malformed declaration
    RAISES rather than being dropped, the same reasoning
    `project_subtitle_typography` states: a per-speaker look that is
    silently ignored is a caption the editor believes shipped.
    """
    from library.tools.brand_registry import project_pipeline_block

    block = project_pipeline_block(project_folder)
    if "speaker_subtitle_styles" not in block:
        return None
    declared = block.get("speaker_subtitle_styles")
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise TypeError(
            f"pipeline.speaker_subtitle_styles in {project_folder}/"
            f"project.yaml must be a mapping of speaker name to style "
            f"overrides, got {type(declared).__name__}: {declared!r}"
        )
    for speaker, overrides in declared.items():
        if not isinstance(overrides, dict):
            raise TypeError(
                f"pipeline.speaker_subtitle_styles[{speaker!r}] in "
                f"{project_folder}/project.yaml must be a mapping of "
                f"style overrides, got {type(overrides).__name__}: "
                f"{overrides!r}"
            )
        unknown = sorted(set(overrides) - set(SPEAKER_STYLE_KEYS))
        if unknown:
            raise ValueError(
                f"pipeline.speaker_subtitle_styles[{speaker!r}] in "
                f"{project_folder}/project.yaml declares {unknown}, which "
                f"nothing reads. It takes {list(SPEAKER_STYLE_KEYS)}."
            )
    return declared


def speaker_style_overrides(project_folder: Optional[str],
                            speaker: Optional[str]) -> Dict[str, Any]:
    """One speaker's declared overrides, or `{}`.

    An unnamed speaker, or one the project does not mention, gets `{}` -
    the shared style. Nothing is guessed from the name.
    """
    if not speaker:
        return {}
    declared = project_speaker_styles(project_folder) or {}
    return dict(declared.get(speaker) or {})


def resolve_subtitle_style(
    brand_effect: Optional[Dict[str, Any]] = None,
    brand_style: Optional[Dict[str, Any]] = None,
    project_folder: Optional[str] = None,
    speaker: Optional[str] = None,
) -> Dict[str, Any]:
    """Template slots in, Remotion props out.

    `brand_effect.subtitle_style` picks the shape; `brand_style.typography`
    and `brand_style.color_palette` supply the brand specifics. A template
    that names no style gets the legacy look, because a project with no
    brand template at all must keep rendering.

    `project_folder` resolves the delivery format, and through it the safe
    area the captions must sit inside - and it carries the project's own
    `pipeline.subtitle_typography`, which overrides the template's key by
    key. See "And the PROJECT" in the module docstring.

    `speaker` applies that speaker's `pipeline.speaker_subtitle_styles`
    entry LAST, over everything else, so a two-speaker conversation can
    be captioned in two looks. A speaker the project does not name
    changes nothing - see `SPEAKER_STYLE_KEYS`.
    """
    brand_effect = brand_effect or {}
    brand_style = brand_style or {}

    name = brand_effect.get("subtitle_style") or "default_subtitles"
    style = get_subtitle_style(name)
    typography = dict(brand_style.get("typography") or {})
    typography.update(project_subtitle_typography(project_folder) or {})
    resolved = style.resolve(
        typography=typography or None,
        color_palette=brand_style.get("color_palette"),
        safe_area=resolve_safe_area(project_folder),
    )
    # Last, over everything: the project's per-speaker look. Applied
    # after the brand so a speaker's declared accent wins, and checked
    # against SPEAKER_STYLE_KEYS on read so a typo raises rather than
    # silently doing nothing.
    overrides = speaker_style_overrides(project_folder, speaker)
    if overrides:
        resolved = {**resolved, **overrides, "speaker": speaker}
    elif speaker:
        resolved = {**resolved, "speaker": speaker}
    return resolved


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
