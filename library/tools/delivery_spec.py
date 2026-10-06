"""The delivery spec: what each platform requires, AS DATA. One enumeration.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation - masters, stems, captions, social crops, platform variants,
codec and loudness targets, QC. This module is the registry half of that
track's foundation: the per-platform delivery spec, held as data with its
provenance, so the rest of the track reads a target instead of a constant.

What was wrong before
--------------------
The delivery loudness target was a bare constant, ``-14.0``, in
``render_qa.py`` (``DEFAULT_LUFS_TARGET``), with no provenance and no
per-platform reading. The codec was a ``project.yaml`` declaration
(``deliver_preset``) that nothing cross-checked against what the platform
accepts. The caption format did not exist as a concept at all - captions
were always burned in, and a project that wanted a sidecar had no way to
say so. And the resolution lived in ``delivery_format.py`` as four named
formats with no platform attached to any of them.

So a master could be rendered at a loudness no platform normalises to, in a
codec the platform re-encodes on arrival, with no record of which platform
it was FOR. The registry makes each of those a declared, sourced fact.

One enumeration
---------------
Like ``delivery_format``, ``series_look`` and ``platform_safe_zones``,
this is a closed enumeration and an unknown name RAISES. A silent fallback
is exactly how a master ships to the wrong platform's spec. Adding a
platform means adding a row.

The six fields the track spec names are :data:`DeliverySpec`'s first six:
resolution (``width``/``height``), aspect (``width``:``height``),
``fps``, ``codec``, ``loudness_target_lufs`` and ``caption_format``. The
rest are the values a deliverable needs in order to be checked:
``container``, the loudness ``tolerance`` and ``true_peak_ceiling_dbtp``
the gate already enforces, and ``source`` - where the numbers come from,
the same provenance ``platform_safe_zones`` carries.

The loudness target is DATA now, not a constant
-----------------------------------------------
Every spec carries ``-14.0`` LUFS because that is the normalisation target
the short-form platforms apply and the value the pipeline already masters
to - the registry does not invent a target, it records one with a source.
``render_qa``'s ``DEFAULT_LUFS_TARGET``/``DEFAULT_LUFS_TOLERANCE``/
``DEFAULT_TRUE_PEAK_CEILING_DBTP`` now read from
:data:`DEFAULT_DELIVERY_SPEC`, so the gate's default IS the registry's
default and the two cannot drift. A project that names a platform gets
that platform's spec; a project that names none gets the default, exactly
as ``delivery_format`` resolves.

What this module does not do
----------------------------
It does not render, reframe, master or QC anything. It is the data those
actions read. The reframing path reads the target aspect, the mastering
path reads the loudness target, the QC path reads the whole spec - and
each of those is a later slice that builds on this one.

Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline and
points here.

**The delivery format is a property of the PRODUCT, not of the footage.**
One enumeration, `library/tools/delivery_format.py`: a brand template declares `delivery_format`, a project may override with `pipeline.delivery_format`, the default is vertical 1080x1920, and an unknown name raises.
The catalog's `source_resolution` DESCRIBES the footage and is never a render target. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DeliverySpec:
    """One delivery target's full requirement, as data.

    ``width``/``height`` are the delivered frame in pixels and ``fps`` the
    delivered frame rate; together they are the resolution and aspect the
    track spec names. ``codec``/``container`` are the encoded file. The
    three loudness fields are the integrated target, its tolerance and the
    true-peak ceiling the gate enforces. ``caption_format`` is how the
    captions reach the viewer: ``burned_in`` (composited onto the picture,
    what the pipeline renders today) or ``sidecar`` (a separate .srt/.vtt
    the platform reads). ``source`` is where the numbers come from.
    """

    platform: str
    width: int
    height: int
    fps: float
    codec: str
    container: str
    loudness_target_lufs: float
    loudness_tolerance_lufs: float
    true_peak_ceiling_dbtp: float
    caption_format: str
    source: str

    @property
    def aspect(self) -> str:
        """``"9:16"``/``"1:1"``/``"16:9"`` - the reduced aspect ratio.

        Reduced by the greatest common divisor, so 1080x1920 is ``"9:16"``
        and not ``"1080:1920"``. A frame whose dimensions do not reduce to
        a small integer ratio (a 1080x1081 crop) is reported as the raw
        ``"W:H"`` rather than rounded into a named ratio it is not.
        """
        from math import gcd

        divisor = gcd(int(self.width), int(self.height))
        if divisor <= 0:
            return f"{self.width}:{self.height}"
        w, h = int(self.width) // divisor, int(self.height) // divisor
        # Only the ratios a platform actually ships get a name; anything
        # else is reported raw rather than mislabelled.
        named = {(9, 16): "9:16", (1, 1): "1:1", (16, 9): "16:9",
                 (4, 5): "4:5", (3, 4): "3:4"}
        return named.get((w, h), f"{w}:{h}")

    @property
    def resolution(self) -> List[int]:
        """``[width, height]`` - the frame the deliverable is rendered at."""
        return [int(self.width), int(self.height)]


# The loudness every spec below carries. This is the normalisation target
# the short-form platforms apply and the value the pipeline already masters
# to; the registry records it with a source rather than leaving it a bare
# constant in render_qa. See the module docstring.
_LUFS_TARGET = -14.0
_LUFS_TOLERANCE = 1.0
_TRUE_PEAK_CEILING_DBTP = -1.0

# Where the loudness numbers come from. The platforms do not publish a
# single LUFS figure; they normalise to a target the community measures at
# about -14 LUFS (YouTube's is the documented one). The pipeline's own
# pre-registry default was -14.0, so the registry preserves it as data.
_LOUDNESS_SOURCE = (
    "platform normalisation target (YouTube documents -14 LUFS; TikTok, "
    "Reels and LinkedIn normalise to the same neighbourhood). Matches the "
    "pipeline's pre-registry default, so naming no platform changes nothing."
)

# Where the resolution/codec numbers come from: each platform's published
# upload spec. Short-form vertical is 1080x1920 H.264/mp4 everywhere; the
# master is 1920x1080.
_SHORT_FORM_SOURCE = (
    "platform published upload spec: 1080x1920 (9:16) H.264/mp4, 30 fps. "
    "TikTok, Reels, Shorts and LinkedIn all publish this frame for "
    "vertical short-form."
)
_MASTER_SOURCE = (
    "long-form master: 1920x1080 (16:9) H.264/mp4. The frame the edit is "
    "authored in before any social crop; fps is the project's measured "
    "frame rate, so the registry carries the platform's 30 fps "
    "recommendation and the delivery path substitutes the measured one."
)

#: The registry. One row per delivery target. A project that names a
#: platform gets its spec; the default is :data:`DEFAULT_DELIVERY_SPEC`.
DELIVERY_SPECS: Dict[str, DeliverySpec] = {
    "tiktok": DeliverySpec(
        platform="tiktok", width=1080, height=1920, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=_LUFS_TARGET,
        loudness_tolerance_lufs=_LUFS_TOLERANCE,
        true_peak_ceiling_dbtp=_TRUE_PEAK_CEILING_DBTP,
        caption_format="burned_in",
        source=_SHORT_FORM_SOURCE + " " + _LOUDNESS_SOURCE,
    ),
    "instagram_reels": DeliverySpec(
        platform="instagram_reels", width=1080, height=1920, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=_LUFS_TARGET,
        loudness_tolerance_lufs=_LUFS_TOLERANCE,
        true_peak_ceiling_dbtp=_TRUE_PEAK_CEILING_DBTP,
        caption_format="burned_in",
        source=_SHORT_FORM_SOURCE + " " + _LOUDNESS_SOURCE,
    ),
    "youtube_shorts": DeliverySpec(
        platform="youtube_shorts", width=1080, height=1920, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=_LUFS_TARGET,
        loudness_tolerance_lufs=_LUFS_TOLERANCE,
        true_peak_ceiling_dbtp=_TRUE_PEAK_CEILING_DBTP,
        caption_format="burned_in",
        source=_SHORT_FORM_SOURCE + " " + _LOUDNESS_SOURCE,
    ),
    "linkedin": DeliverySpec(
        platform="linkedin", width=1080, height=1920, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=_LUFS_TARGET,
        loudness_tolerance_lufs=_LUFS_TOLERANCE,
        true_peak_ceiling_dbtp=_TRUE_PEAK_CEILING_DBTP,
        caption_format="burned_in",
        source=_SHORT_FORM_SOURCE + " " + _LOUDNESS_SOURCE,
    ),
    "master": DeliverySpec(
        platform="master", width=1920, height=1080, fps=30.0,
        codec="H264", container="mp4",
        loudness_target_lufs=_LUFS_TARGET,
        loudness_tolerance_lufs=_LUFS_TOLERANCE,
        true_peak_ceiling_dbtp=_TRUE_PEAK_CEILING_DBTP,
        caption_format="burned_in",
        source=_MASTER_SOURCE + " " + _LOUDNESS_SOURCE,
    ),
}

#: The spec a project gets when it names no platform. Vertical 1080x1920 -
#: the same default ``delivery_format`` resolves - carrying the loudness
#: the gate already enforces, so naming no platform changes nothing.
DEFAULT_DELIVERY_SPEC = DELIVERY_SPECS["tiktok"]

#: The platform ``DEFAULT_DELIVERY_SPEC`` is the spec of, named so a
#: caller can say "the default" without hardcoding a platform name.
DEFAULT_DELIVERY_PLATFORM = "tiktok"


def spec_names() -> List[str]:
    """Every platform a project may name, sorted."""
    return sorted(DELIVERY_SPECS)


def resolve_delivery_spec(platform: str = "") -> DeliverySpec:
    """The :class:`DeliverySpec` for a declared platform.

    An empty name is not a declaration and yields
    :data:`DEFAULT_DELIVERY_SPEC` - that is how "declares nothing" is
    spelled, the same rule ``delivery_format.resolve_format_name`` applies.
    An unknown non-empty name RAISES: a silent fallback is how a master
    ships to the wrong platform's spec.
    """
    if not platform:
        return DEFAULT_DELIVERY_SPEC
    if not isinstance(platform, str):
        raise TypeError(
            f"delivery platform must be one of {spec_names()}, "
            f"got {type(platform).__name__}: {platform!r}"
        )
    key = platform.strip()
    if key not in DELIVERY_SPECS:
        raise ValueError(
            f"Unknown delivery platform {platform!r}. "
            f"Known platforms: {spec_names()}. "
            "Add a row to library/tools/delivery_spec.py to introduce one."
        )
    return DELIVERY_SPECS[key]


def _project_pipeline_block(project_folder: Optional[str]) -> dict:
    """The `pipeline:` mapping of a project's project.yaml, or {}.

    One parse, in library/tools/brand_registry.py - this module, that one
    and delivery_format all read the same block.
    """
    from library.tools.brand_registry import project_pipeline_block
    return project_pipeline_block(project_folder)


def delivery_platform_name(project_folder: Optional[str] = None,
                           templates_dir: Optional[str] = None) -> str:
    """The declared delivery platform, by precedence.

    project.yaml ``pipeline.delivery_platform``  >  the brand template's
    ``delivery_platform``  >  :data:`DEFAULT_DELIVERY_PLATFORM`.

    The project override wins because a series shipping one video to a
    different platform says so per video; the template is the series'
    default. An unknown name RAISES at the declaration that named it -
    a silent fallback is exactly how a master ships to the wrong
    platform's spec. An empty name is not a declaration and yields the
    default, the same rule ``delivery_format.delivery_format_name``
    applies.
    """
    pipeline_block = _project_pipeline_block(project_folder)

    override = (pipeline_block.get("delivery_platform") or "").strip()
    if override:
        resolve_delivery_spec(override)  # raise here, naming the project
        return override

    template_name = (pipeline_block.get("brand_template") or "").strip()
    from library.tools.brand_registry import resolve_project_template
    template = resolve_project_template(
        template_name, templates_dir=templates_dir,
        project_folder=project_folder)
    declared = (getattr(template, "delivery_platform", "") or "").strip()
    if declared:
        resolve_delivery_spec(declared)  # raise here, naming the template
        return declared

    return DEFAULT_DELIVERY_PLATFORM


def resolve_delivery_spec_for_project(
        project_folder: Optional[str] = None,
        templates_dir: Optional[str] = None) -> DeliverySpec:
    """The :class:`DeliverySpec` the project's named platform selects.

    This is the ONE call the mastering and QC paths make. It is
    deliberately a function of the project rather than a value threaded
    through the DAG: a value in flight can be renamed, defaulted and
    lost, which is the dominant bug class in this pipeline, and the
    loudness target was lost that way already - the gate's default and
    the mastering default were two constants that could drift (closed
    on 2026-10-06 by the registry; this closes the other half, a
    project that names a platform getting THAT platform's spec rather
    than the default's).
    """
    return resolve_delivery_spec(
        delivery_platform_name(project_folder, templates_dir=templates_dir))
