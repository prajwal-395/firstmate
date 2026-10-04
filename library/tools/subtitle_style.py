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
`tests/unit/captions/test_subtitle_style.py` fails on an orphan in either direction, the
same contract `transition_vocabulary` and `series_look` hold.


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
- `tests/unit/captions/test_subtitle_style.py`.
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

#: Delivery-frame pixels every caption's design row sits ABOVE the
#: platform safe-area bottom inset.
#:
#: Measured, not chosen. On Reel 13 the captain hand-corrected all 20
#: tight captions uniformly from the computed Tilt -1700.0 to -1740.0
#: (the live timeline's own numbers - #979 recorded this correction in
#: the halved spelling the retired draw gain produced, -850 to -870);
#: at the 480-pixel tight-canvas floor one Tilt unit is a QUARTER of a
#: delivery pixel (`resolve_transform`: shift_y = -Tilt * canvas_h /
#: frame_h, and 480/1920 = 0.25), so 40 units is exactly 10.0 delivery
#: pixels. An exported still of that timeline correlation-scans the
#: corrected canvases onto frame row 1155 with their ink on the caption
#: row, while the computed -1700 draws the same cards' ink ~10px high
#: (worst card 12px). The shift-rule terms are pixel-verified on that
#: still, so the error sits in the DESIGN row, not the carriage: the
#: probe props carry the correction here.
#:
#: This supersedes the earlier +11px value taken from Reel 09's
#: uniform -1744.0 to -1700.0 correction, which was read on pre-floor
#: canvases; the lifted row it produced draws 10px high on the current
#: 480-floor carrying, as Reel 13's stills prove. Reels built under the
#: +11 row keep their live timelines - nothing here rebuilds them -
#: but a future rebuild of one moves its captions 10px down onto the
#: corrected row; a reel that must keep its old band pins it per
#: segment in `external/declarations/overlay_intent.json` instead.
#:
#: Applied to the BOTTOM inset only, and only to the props captions
#: render from: `safe_area.py`'s own insets are platform facts (the
#: short-form UI map) and motion graphics were never corrected this
#: way, so neither moves. `captionMaxWidth` still derives from the
#: unlifted left/right insets.
#: AGENTS.md 10.2 carried this row as a NUMBER. #979 corrected the
#: value here and not there, so the index and the code disagreed until
#: 2026-09-11; the index now names the constant and the superseded
#: headline is kept verbatim on the next line, which is where it lives:
#: Captions sit 11px above the safe-area bottom inset (`CAPTION_LIFT_PX`).
#:
#: AGENTS.md 10.2's headline moved here on 2026-09-11, when a project
#: gained the right to DECLARE its own caption row and the index line
#: stopped being true on its own. The engine's rule is unchanged where
#: no project declares one, so it is kept verbatim:
#: Captions sit `CAPTION_LIFT_PX` above the safe-area bottom inset.
CAPTION_LIFT_PX = 1

#: What a project declares to put its caption row somewhere else, as a
#: FRACTION of the delivery frame's height measured from the top. A
#: place on the delivered picture, not a stored transform: a full-frame
#: caption and a 480-tall tight one need different Pan/Tilt numbers for
#: the same row, and every value downstream is COMPUTED from this one.
CAPTION_ROW_KEY = "caption_row"

#: What `pipeline.subtitle_position` may declare. Complete, and checked
#: on read, for the reason `TYPOGRAPHY_KEYS` is.
POSITION_KEYS = (CAPTION_ROW_KEY, "reason")


def caption_row_px(row: float, frame_h: int) -> int:
    """A declared caption row as a pixel row of the delivery frame."""
    return int(round(float(row) * int(frame_h)))


def _lifted_props(safe_area: SafeAreaInsets,
                  row_px: Optional[int] = None,
                  frame_h: Optional[int] = None) -> Dict[str, int]:
    """The safe-area props captions render from, with the row lifted.

    Only the bottom inset moves, and only here: the profile in
    `safe_area.py` stays the platform's own numbers, `captionMaxWidth`
    is derived from the unlifted left/right, and motion graphics read
    the profile directly, so no other layer follows the captions up.

    `row_px` is a project's DECLARED caption row
    (`project_caption_row`), and where one is declared it replaces the
    lift rather than adding to it: the project is naming the row, so
    the engine's own lift is not a second opinion to stack on top.
    The inset is the distance from the frame's bottom edge to that row,
    which is what the overlay positions against - so one declared place
    moves the render, and the tight-box placement follows it because
    that placement is read off the render's own ink rather than
    computed beside it.
    """
    props = safe_area.as_props()
    if row_px is not None and frame_h:
        props["bottom"] = max(int(frame_h) - int(row_px), 0)
        return props
    props["bottom"] = props["bottom"] + CAPTION_LIFT_PX
    return props

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
    because that style exists to reproduce today's output, and a
    placeholder palette of three pure RGB primaries is visibly
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
        caption_row_px: Optional[int] = None,
        frame_h: Optional[int] = None,
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
            # position against whichever edge `position` names - with
            # the caption row lifted by CAPTION_LIFT_PX above the
            # bottom inset. See that constant for the measurement.
            "safeArea": _lifted_props(safe_area, caption_row_px, frame_h),
            # A caption box is CENTRED, so it runs into the nearer edge
            # first and can only be twice that distance wide.
            "captionMaxWidth": safe_area.centered_usable_width,
        }


#: The caption shape a project that declares none renders in. The
#: pre-P3.2 look, preserved exactly so it is a no-op rather than a
#: surprise. Single owner - `video_prefs` reads this rather than
#: restating it, so there is nothing left to drift.
DEFAULT_SUBTITLE_STYLE = "default_subtitles"


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
            "the shortform brand copy: energy_profile high, vfx_intensity "
            "0.8, dense SFX. Larger and heavier than the legacy look, with a "
            "thicker outline because a pushed-contrast grade swallows a "
            "thin outline."
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
            "the cinematic brand copy: framing_intent 0.0, moderate SFX. "
            "Smaller, lighter and barely outlined, because a heavy caption "
            "fights a letterboxed cinematic frame."
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
            "the interview brand copy: sparse SFX. Between the other two "
            "on every axis: legible over talking heads without reading "
            "as shortform."
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


def project_caption_row(project_folder: Optional[str],
                        reel_name: Optional[str] = None) -> Optional[float]:
    """`pipeline.subtitle_position.caption_row` off a project.yaml, or None.

    The captain's ruling of 2026-09-11, after looking at Reel 28:
    *"all of the subtitles were positioned at y=-870 but the subtitles
    were not in the right place, and in actuality positioning them
    closer to y=-420 is around where the subtitles should actually
    be."*

    Measured, that is not a stored-number complaint.  The engine's own
    row (`safe_area` bottom inset lifted by `CAPTION_LIFT_PX`, 1599 on
    a 1080x1920 frame) is where exactly ONE reel in the field test
    draws - the only one rebuilt since that row took its current value
    - and the other four sit together 220px above it.  The row is a
    per-series look, so it belongs to the PROJECT (AGENTS.md 14), and
    the engine keeps stating none.

    Declared as a FRACTION of the delivery frame from the top, so it
    survives a change of delivery format, and every stored transform
    downstream is computed from it.  None means the project declares
    none and the engine's own row applies exactly as before.

    `reel_name` prefers that reel's declared row
    (`external/declarations/reel_caption_row.json`, `reel_caption_row.declared_row`)
    over the project value: one reel's band is a standing per-reel
    decision, not a special case in code.  None everywhere means
    today's behaviour exactly.

    A malformed declaration RAISES, the same reasoning
    `project_subtitle_typography` states: a caption position silently
    dropped is a caption the editor believes shipped.
    """
    from library.tools.brand_registry import project_pipeline_block
    from library.tools.reel_caption_row import declared_row

    override = declared_row(project_folder, reel_name or "")
    if override is not None:
        return override
    block = project_pipeline_block(project_folder)
    if "subtitle_position" not in block:
        return None
    declared = block.get("subtitle_position")
    if declared is None:
        return None
    if not isinstance(declared, dict):
        raise TypeError(
            f"pipeline.subtitle_position in {project_folder}/project.yaml "
            f"must be a mapping carrying {CAPTION_ROW_KEY!r}, got "
            f"{type(declared).__name__}: {declared!r}")
    unknown = sorted(set(declared) - set(POSITION_KEYS))
    if unknown:
        raise ValueError(
            f"pipeline.subtitle_position in {project_folder}/project.yaml "
            f"declares {unknown}, which nothing reads. It takes "
            f"{list(POSITION_KEYS)}.")
    if CAPTION_ROW_KEY not in declared:
        raise ValueError(
            f"pipeline.subtitle_position in {project_folder}/project.yaml "
            f"names no {CAPTION_ROW_KEY!r}. A position block that states "
            f"no row moves nothing, which reads as a declaration that "
            f"was honoured.")
    try:
        row = float(declared[CAPTION_ROW_KEY])
    except (TypeError, ValueError) as bad:
        raise TypeError(
            f"pipeline.subtitle_position.{CAPTION_ROW_KEY} in "
            f"{project_folder}/project.yaml must be a number, got "
            f"{declared[CAPTION_ROW_KEY]!r}") from bad
    if not 0.0 < row < 1.0:
        raise ValueError(
            f"pipeline.subtitle_position.{CAPTION_ROW_KEY} is {row}: it "
            f"is a FRACTION of the delivery frame measured from the top, "
            f"so it lies strictly between 0 and 1. A pixel row would put "
            f"the captions off the frame.")
    return row


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
    reel_name: Optional[str] = None,
    video_preferences: Optional[Dict[str, Any]] = None,
    reel=None,
) -> Dict[str, Any]:
    """Template slots in, Remotion props out.

    Precedence for the style NAME: the merged video preferences'
    `subtitle_style` (``style.yaml`` locked value, then ``video.yaml``
    shared and per-reel layers)  >  `brand_effect.subtitle_style`  >
    :data:`DEFAULT_SUBTITLE_STYLE`. A template that names no style -
    and a project with no brand template at all - gets the legacy
    look, because it must keep rendering. `reel` is the addressed reel
    where the caller has one; `video_preferences` is an already-merged
    mapping that wins over a disk read.

    `brand_style.typography` and `brand_style.color_palette` supply the
    brand specifics.

    `project_folder` resolves the delivery format, and through it the safe
    area the captions must sit inside - and it carries the project's own
    `pipeline.subtitle_typography`, which overrides the template's key by
    key. See "And the PROJECT" in the module docstring.

    `speaker` applies that speaker's `pipeline.speaker_subtitle_styles`
    entry LAST, over everything else, so a two-speaker conversation can
    be captioned in two looks. A speaker the project does not name
    changes nothing - see `SPEAKER_STYLE_KEYS`.

    `reel_name` prefers that reel's declared caption row
    (`external/declarations/reel_caption_row.json`) over the project value, falling
    back to it where the reel declares none. The reel path passes the
    timeline name it builds; every other caller passes nothing and
    reads today's answer exactly.
    """
    brand_effect = brand_effect or {}
    brand_style = brand_style or {}

    from library.tools import video_prefs as _video_prefs
    preferred = _video_prefs.effective_subtitle_style(
        project_folder or "", reel=reel,
        video_preferences=video_preferences)
    if preferred:
        name = preferred
    else:
        name = (brand_effect.get("subtitle_style")
                or DEFAULT_SUBTITLE_STYLE)
    style = get_subtitle_style(name)
    typography = dict(brand_style.get("typography") or {})
    typography.update(project_subtitle_typography(project_folder) or {})
    # The project's DECLARED caption row, if it names one. Resolved
    # here, where the delivery format already is, so one place on the
    # frame reaches the render and every stored transform downstream
    # is computed from it rather than typed beside it.
    from library.tools.delivery_format import (
        delivery_format_name, resolve_format_name)

    _frame_w, frame_h = resolve_format_name(
        delivery_format_name(project_folder))
    declared_row = project_caption_row(project_folder, reel_name=reel_name)
    resolved = style.resolve(
        typography=typography or None,
        color_palette=brand_style.get("color_palette"),
        safe_area=resolve_safe_area(project_folder),
        caption_row_px=(None if declared_row is None
                        else caption_row_px(declared_row, frame_h)),
        frame_h=frame_h,
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
