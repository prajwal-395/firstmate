"""Which typefaces a render may name, and how one is delivered.

One enumeration, because there were two and one of them was a test.
`tests/contracts/test_asset_policy.py` owned `ACCEPTED_SYSTEM_FONTS` privately, so
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

import os

from library.tools.project_asset import resolve_project_asset, ProjectAssetNotFoundError

# The one family this repository ships. Kept equal to `fonts.ts`'s
# BUNDLED_FONT_FAMILY and to subtitle_style.LEGACY_FONT_FAMILY;
# tests/contracts/test_asset_policy.py fails if they drift.
BUNDLED_FONT_FAMILY = "Montserrat"

# Where prep_remotion stages a project's own brand files, relative to
# Remotion's `public/`. A `font_file` with no directory part resolves
# here, which is the only place a project font can arrive.
PROJECT_FONT_DIR = "brand"

# Families a declaration may name that this repository does NOT bundle.
# Each is a system font, so its exact shape depends on the render machine.
ACCEPTED_SYSTEM_FONTS: dict[str, str] = {
    "Helvetica": (
        "A project copy names it. Present on macOS, absent on most "
        "Linux render hosts, where Chromium will substitute. Accepted "
        "because no project ships it as a look, but it is not "
        "deterministic."
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


# Where the bundled family's file lives, relative to the engine root.
# Public builds stage the licensed file beside the HyperFrames templates;
# personal checkouts keep the historical Remotion source copy as fallback.
BUNDLED_FONT_FILES = (
    "hyperframes/compositions/Montserrat-Variable.ttf",
    "remotion-subtitles/public/fonts/Montserrat-Variable.ttf",
)
BUNDLED_FONT_FILE = BUNDLED_FONT_FILES[0]

# Where a project keeps its own typefaces before `prep_remotion` stages
# them into Remotion's `public/brand/`. Step 4.01 measures captions long
# before 4.05 stages anything, so this is the copy it can open.
PROJECT_FONT_SOURCE_DIR = "brand_assets"


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def measurable_font_path(declared: str,
                         font_file: str | None = None,
                         project_folder: str | None = None) -> str | None:
    """An openable font FILE for the family a render will draw in, or None.

    This is the measurement half of :func:`font_is_deliverable`. A caller
    that needs to know how wide a string will be on screen - the caption
    fitter in step 4.01 - has to open the same face the render loads, and
    only two of the three legitimate routes have a file this side of the
    render:

    **Bundled** resolves to :data:`BUNDLED_FONT_FILE`.

    **Carried by the project** resolves under the project's
    ``brand_assets/``, which is where the declaration's ``font_file``
    comes from and where it still is at planning time.

    **Accepted as a system font** resolves to None. A system face has no
    path this module can promise - that is the whole reason accepting one
    is recorded as a deliberate decision in
    :data:`ACCEPTED_SYSTEM_FONTS` rather than being a default. The caller
    must say what it does without a measurement; it must not quietly
    measure some other face.
    """
    if font_file:
        name = str(font_file).strip().lstrip("/")
        if os.path.isabs(font_file) and os.path.exists(font_file):
            return font_file
        if project_folder:
            candidate = os.path.join(
                PROJECT_FONT_SOURCE_DIR, os.path.basename(name))
            try:
                return resolve_project_asset(candidate, project_folder)
            except ProjectAssetNotFoundError:
                pass
        root = _repo_root()
        relative = static_font_path(name)
        candidates = [
            os.path.join(root, "remotion-subtitles", "public", relative),
            os.path.join(root, "hyperframes", "compositions", name),
        ]
        for staged in candidates:
            if os.path.exists(staged):
                return staged
        return None

    if primary_family(declared) == BUNDLED_FONT_FAMILY:
        root = _repo_root()
        for relative in BUNDLED_FONT_FILES:
            path = os.path.join(root, relative)
            if os.path.exists(path):
                return path
    return None
