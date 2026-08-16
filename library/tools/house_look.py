"""The house look, as values this repository owns.

ONE enumeration of every look a brand template may name, in the same
spirit as `transition_vocabulary.py`: a look named anywhere else fails
CI, and a look nothing names does not ship.

Each look is delivered in two halves, and the split is not arbitrary -
it is what the two mechanisms can express:

* **CDL** (`HouseLook.cdl`) carries hue and level. An ASC CDL is
  ``out = (in * slope + offset) ** power`` per channel, then a
  saturation term, so:
  - `slope` scales, which moves the bright end most -> highlight tint;
  - `offset` adds, which moves the dark end most -> shadow tint and
    the black floor;
  - `power` warps the middle -> midtone tint;
  - `saturation` is global.
  That is a complete split-tone in four numbers, and it is what the
  Resolve API can set on a timeline item without a hand-built node
  tree (`TimelineItem.SetCDL`).

* **Fusion** (`HouseLook.fusion`) carries everything a CDL has no term
  for: pivot contrast, highlight bloom, grain, and a shaped - and
  optionally coloured - vignette. Every key it emits is a name
  `library/tools/fusion/comp_builder.build_effect_comp` dispatches on.
  Emitting a name that module does not read produces a comp without
  that effect in it and no warning, which is how three VFX types and
  four grade nodes were silently lost before; `tests/test_house_look.py`
  asserts the nodes get drawn.

The numbers below are authored from the captain's planning docs
(``PLAN/series portfolio '26 planning/``), which are read-only and live
outside this repo. `derived_from` on each look records the trail, and
`withdrawn` records design the docs asked for that neither mechanism can
deliver - a stated gap, not a silent one.

Nothing here depends on a file inside a DaVinci Resolve installation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

RGB = tuple[float, float, float]


@dataclass(frozen=True)
class HouseLook:
    """One named look: a CDL half and a Fusion half.

    Attributes:
        name: The slug a brand template names in `style.house_look`.
        title: Human label for dashboards and PR bodies.
        intent: One line on what the look is for.
        derived_from: Which planning document(s) the values come from.
        slope/offset/power: Per-channel ASC CDL terms, (r, g, b).
        saturation: ASC CDL saturation term.
        contrast: Fusion BrightnessContrast pivot contrast.
        glow_gain/glow_threshold/glow_size: Fusion SoftGlow bloom.
        grain_power/grain_size: Fusion FilmGrain.
        vignette_blend/vignette_soft: Fusion vignette strength and falloff.
        vignette_color: Vignette colour, 0-1 RGB. Black unless the look
            wants the frame to fall off into a tint - a CDL cannot shape
            a falloff at all, so this is Fusion-only by construction.
        withdrawn: {design note: why it cannot be delivered}.
    """

    name: str
    title: str
    intent: str
    derived_from: str
    slope: RGB
    offset: RGB
    power: RGB
    saturation: float
    contrast: float
    glow_gain: float
    glow_threshold: float
    glow_size: float
    grain_power: float
    grain_size: float
    vignette_blend: float
    vignette_soft: float
    vignette_color: RGB = (0.0, 0.0, 0.0)
    withdrawn: dict[str, str] = field(default_factory=dict)

    def cdl(self, *, exposure_gain: float = 1.0) -> dict[str, float]:
        """The CDL half, in the key names the renderer reads.

        `exposure_gain` is the per-clip exposure normalisation (a plain
        multiplier on slope). It is not part of the look: it makes an
        under- or over-exposed clip sit where the look expects it.
        """
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

        Saturation is deliberately absent: the CDL already carries it,
        and `fx.grade` would multiply it a second time. The grade node
        here exists for `contrast` alone, which a CDL cannot express.
        """
        return {
            "grade_contrast": round(self.contrast, 4),
            "glow_gain": round(self.glow_gain, 4),
            "glow_threshold": round(self.glow_threshold, 4),
            "glow_size": round(self.glow_size, 4),
            "film_grain": True,
            "film_grain_power": round(self.grain_power, 4),
            "film_grain_size": round(self.grain_size, 4),
            "vignette": True,
            "vignette_blend": round(self.vignette_blend, 4),
            "vignette_soft": round(self.vignette_soft, 4),
            "vignette_color": list(self.vignette_color),
        }


#: A CDL that changes nothing. What a template naming no look gets, so
#: "no look" is an explicit identity rather than an accidental one.
NEUTRAL_CDL = {
    "slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0,
    "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
    "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
    "saturation": 1.0,
}


_LOOKS = (
    HouseLook(
        name="pmk_default",
        title="PMK Default",
        intent=(
            "The channel's neutral state - what a video looks like when no "
            "series identity is active."
        ),
        derived_from=(
            "overall_branding_creative_direction.md - 'One-Off Video Colour "
            "Treatment' (the one-off look sits at the intersection of every "
            "series' direction), 'Colour System Philosophy' (saturated, bold, "
            "intentional; no pastels, no washed-out neutrals) and 'The Bullet "
            "Train DNA' (vibrant, not muted)."
        ),
        # A warm-leaning neutral. Five of the eight series grade warm and
        # every one of them forbids cold shadows; two grade neutral-to-cool.
        # The intersection is a third of the committed warm looks' split,
        # which is present in the frame without belonging to any series.
        slope=(1.020, 1.005, 0.985),
        offset=(0.006, 0.004, 0.002),
        power=(0.995, 1.000, 1.005),
        # "No pastels. No washed-out neutrals." - richer than source, but
        # without a series accent to push toward.
        saturation=1.10,
        contrast=0.10,
        glow_gain=0.12,
        glow_threshold=0.78,
        glow_size=3.5,
        grain_power=0.18,
        grain_size=1.5,
        vignette_blend=0.16,
        vignette_soft=0.35,
        withdrawn={
            "per-series accent colour": (
                "The one-off look is defined by the ABSENCE of a series "
                "accent, so there is nothing to deliver. A project that "
                "wants one names the series' look instead."
            ),
        },
    ),
    HouseLook(
        name="warm_reflection",
        title="Warm Reflection",
        intent=(
            "A room at night that glows: warm, rich and calm, for the "
            "reflective long-form register."
        ),
        derived_from=(
            "1_through_the_4th_wall/branding_creative_direction.md - 'Colour "
            "Grading' (push toward warm amber; rich and alive, NOT "
            "desaturated; medium-high contrast; warm shadows toward Deep "
            "Espresso #120B07; golden highlights toward Lamp Amber #FFB23D) "
            "and 3_prestige/branding_creative_direction.md - 'Colour Grading' "
            "(slightly warm, not golden; medium-high contrast; warm shadows, "
            "never cold blue; soft, slightly glowing highlights)."
        ),
        # Lamp Amber #FFB23D as a raw highlight ratio is (1.000, 0.698,
        # 0.239) - a sepia cast, not a grade. The direction is taken from
        # the hex and the magnitude from the in-repo, first-party
        # 4thWall_Base_Memory.dctl gain stage (1.05 / 1.02 / 0.88), pulled
        # back on blue: that DCTL is one series at full strength and this
        # is a template look several series share.
        slope=(1.045, 1.010, 0.935),
        # Deep Espresso #120B07 is (0.071, 0.043, 0.027) - R > G > B. The
        # blue offset is negative so the black floor stays deep and can
        # never go blue, which every warm series doc rules out by name.
        offset=(0.016, 0.008, -0.004),
        power=(0.985, 0.995, 1.025),
        # 4th Wall says rich, Prestige says noticeably desaturated. The
        # channel-level DNA ("even in the quietest series, the visual
        # richness never drops") outranks one series, so the look stays
        # above 1.0 and carries the calm in contrast and falloff instead.
        saturation=1.08,
        contrast=0.10,
        glow_gain=0.15,
        glow_threshold=0.72,
        glow_size=4.0,
        grain_power=0.22,
        grain_size=1.5,
        vignette_blend=0.20,
        vignette_soft=0.40,
        # The frame falls off into the room's warm dark, not into neutral
        # black - Deep Espresso #120B07. A CDL cannot shape a falloff at
        # all, so this is the Fusion half earning its place.
        vignette_color=(0.071, 0.043, 0.027),
        withdrawn={
            "the red clock as the one non-amber element": (
                "4th Wall protects a specific red practical in frame. That "
                "is a qualified secondary, and neither a CDL nor a per-clip "
                "Fusion comp can qualify a hue range. Shoot it, do not "
                "grade it."
            ),
        },
    ),
    HouseLook(
        name="electric_contrast",
        title="Electric Contrast",
        intent=(
            "Inky blacks and full saturation for the high-energy shortform "
            "register."
        ),
        derived_from=(
            "2_pr_or_er/branding_creative_direction.md - 'Colour Grading' "
            "(contrast pushed hard; deep, inky blacks; full Bullet Train "
            "saturation; slight bloom on highlights; NOT desaturated, NOT a "
            "cool/blue overall temperature, NOT flat, NOT orange-and-teal) "
            "and 5_sidequests/branding_creative_direction.md - 'Colour "
            "Grading' (saturation higher than other series; medium-high "
            "contrast, punchy not crunchy; no moody grades)."
        ),
        # Deliberately achromatic. PR or ER names four things the grade
        # must not be and two of them are temperatures; Sidequests says
        # temperature varies per sidequest. The pipeline has no per-episode
        # mood signal, so this look spends nothing on hue and everything on
        # level and saturation.
        slope=(1.025, 1.025, 1.025),
        offset=(-0.014, -0.014, -0.014),
        power=(1.050, 1.050, 1.050),
        saturation=1.22,
        contrast=0.18,
        # "What's lit is LIT" - a high threshold so only the genuinely hot
        # parts of the frame bloom.
        glow_gain=0.16,
        glow_threshold=0.85,
        glow_size=3.0,
        # Not a film look. Grain is present only to keep gradients from
        # banding once contrast is pushed this far.
        grain_power=0.10,
        grain_size=1.2,
        vignette_blend=0.22,
        vignette_soft=0.30,
        withdrawn={
            "per-episode neon cast": (
                "PR or ER shifts violet / green / red / cyan with the "
                "episode's mood. Nothing upstream emits an episode mood, so "
                "a shipped cast would be a guess applied to every clip. The "
                "neon lives in the overlays, which the subtitle and motion "
                "graphics steps already colour."
            ),
            "skin-tone protection": (
                "'The neon lives in the environment, not on your face' needs "
                "a hue/saturation qualifier feeding a secondary. A CDL has "
                "no qualifier and a per-clip Fusion comp has no colour "
                "keyer here. The look stays achromatic partly so that "
                "nothing needs protecting from it."
            ),
        },
    ),
    HouseLook(
        name="film_stock_warmth",
        title="Film Stock Warmth",
        intent=(
            "Cream highlights and lifted warm shadows: a naturalistic "
            "documentary grade that normalises any real location."
        ),
        derived_from=(
            "8_moneyball/branding_creative_direction.md - 'Colour Grading - "
            "The Film Grade' (warm-neutral temperature; highlights pushed to "
            "Cream #FFF5E1 - 'the most distinctive grading decision'; warm, "
            "slightly lifted shadows with visible detail; muted but not "
            "desaturated; medium contrast; grain) and its 'Film Stock "
            "Warmth' palette (Deep Warm Black #1A1A17, never blue-black)."
        ),
        # Cream #FFF5E1 is (1.000, 0.961, 0.882); normalised on green that
        # is (1.041, 1.000, 0.918). Applied at 60% strength, because a CDL
        # slope tints every value in the frame and the doc's cream is the
        # target for bright surfaces specifically.
        slope=(1.025, 1.000, 0.951),
        # Deep Warm Black #1A1A17 is (0.102, 0.102, 0.090) - blue lowest.
        # All three positive: "you should see detail in the shadows."
        offset=(0.012, 0.010, 0.004),
        power=(0.985, 0.995, 1.012),
        # The only shipped look below 1.0, and the doc says why: "nothing
        # screams... earned through tonal control, not saturation boost."
        saturation=0.95,
        contrast=0.06,
        glow_gain=0.10,
        glow_threshold=0.80,
        glow_size=4.0,
        # 35mm Kodak Vision3 is the stated reference and grain is named
        # twice in that document, so this look carries the most of it.
        grain_power=0.30,
        grain_size=1.6,
        vignette_blend=0.14,
        vignette_soft=0.45,
        vignette_color=(0.102, 0.102, 0.090),
        withdrawn={
            "halation": (
                "The 35mm reference implies red bleed around hot highlights. "
                "There is no Fusion builder for it in "
                "library/tools/fusion/effects.py, and a name with no builder "
                "produces a comp without the effect and no warning. Adding "
                "one means adding fx.halation first."
            ),
        },
    ),
)


#: Every look, keyed by the slug a template names.
HOUSE_LOOKS: dict[str, HouseLook] = {look.name: look for look in _LOOKS}


def normalize_name(name: str) -> str:
    """Slugify a look name so 'Film Stock Warmth' finds `film_stock_warmth`."""
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def resolve_look(name: str):
    """Return the named look, or None when no look is named.

    Raises:
        ValueError: the name is not in the vocabulary. A template naming
            a look that does not exist used to resolve to a `.drx` path
            nothing had written, which the renderer discovered one step
            from the end of the run.
    """
    if not name:
        return None
    slug = normalize_name(name)
    if slug not in HOUSE_LOOKS:
        raise ValueError(
            f"Unknown house look {name!r}. Known looks: "
            f"{', '.join(sorted(HOUSE_LOOKS))}. Looks are defined in "
            f"library/tools/house_look.py and nowhere else."
        )
    return HOUSE_LOOKS[slug]
