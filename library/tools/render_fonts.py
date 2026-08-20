"""Which typefaces a render may name, and how one is delivered.

One enumeration, because there were two and one of them was a test.
`tests/test_bundled_fonts.py` owned `ACCEPTED_SYSTEM_FONTS` privately, so
the engine could not consult the list it is judged against: a declaration
naming an unbundled family was caught at CI time for brand templates and
not caught at all anywhere else. This module is the list, and the test
now asserts against it rather than defining it.

Three ways a family can be legitimate, and nothing else is:

**Bundled.** :data:`BUNDLED_FONT_FAMILY` ships in
`remotion-subtitles/public/fonts/` with its licence beside it and is
loaded, blocking, by `src/fonts.ts`. See AGENTS.md section 11.

**Accepted as a system font.** :data:`ACCEPTED_SYSTEM_FONTS` - a
deliberate decision to accept a family whose exact shape depends on the
render machine, with the reason recorded. Adding one is a decision, not
a default.

**Carried by the project.** A per-series typeface lives with the project
that owns the series and never in the engine (captain's ruling,
2026-08-20). The project puts the file in `<project>/brand_assets/`,
`library/tools/remotion_brand_linker.prep_remotion` copies it into
Remotion's `public/brand/`, and the declaration names it with
``font_file`` so the composition can load that exact file and throw if it
is not there.

A family that is none of the three renders in Chromium's fallback sans at
a different width, on a machine that is not the one that added it, and
nothing downstream can tell - the frames are still valid pictures of the
right size. That is the webfont race of P3.3 in a second costume, so it
raises rather than substitutes.
"""
from __future__ import annotations

# The one family this repository ships. Kept equal to `fonts.ts`'s
# BUNDLED_FONT_FAMILY and to subtitle_style.LEGACY_FONT_FAMILY;
# tests/test_bundled_fonts.py fails if they drift.
BUNDLED_FONT_FAMILY = "Montserrat"

# Where prep_remotion stages a project's own brand files, relative to
# Remotion's `public/`. A `font_file` with no directory part resolves
# here, which is the only place a project font can arrive.
PROJECT_FONT_DIR = "brand"

# Families a declaration may name that this repository does NOT bundle.
# Each is a system font, so its exact shape depends on the render machine.
ACCEPTED_SYSTEM_FONTS: dict[str, str] = {
    "Helvetica": (
        "default_brand.yaml names it. Present on macOS, absent on most "
        "Linux render hosts, where Chromium will substitute. Accepted "
        "because default_brand is the fallback template rather than a "
        "shipped look, but it is not deterministic."
    ),
}


def primary_family(declared: str) -> str:
    """The family anyone actually chose, out of a CSS font stack.

    Everything after the first comma is a fallback the declaration is
    admitting it may not get; only the first entry has to be deliverable.
    """
    return declared.split(",")[0].strip().strip("'\"")


def font_is_deliverable(declared: str, font_file: str | None) -> bool:
    """Whether this family will really be the one that draws the glyphs."""
    if font_file:
        return True
    family = primary_family(declared)
    return family == BUNDLED_FONT_FAMILY or family in ACCEPTED_SYSTEM_FONTS


def static_font_path(font_file: str) -> str:
    """The `staticFile()` path for a project-carried font file.

    A bare filename is the normal case and resolves under
    :data:`PROJECT_FONT_DIR`, because that is where `prep_remotion` puts
    a project's `brand_assets/`. A path that already names a directory is
    passed through, so a caller that stages somewhere else can say so.
    """
    font_file = font_file.strip().lstrip("/")
    if "/" in font_file:
        return font_file
    return f"{PROJECT_FONT_DIR}/{font_file}"
