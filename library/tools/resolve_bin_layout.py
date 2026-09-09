"""One folder structure, expressed the same way in Resolve and on disk.

The captain, on the field-test project (2026-09-09): *"standardize the
folder structure within the project as well, it might be helpful to just
create a unified versioning of the folder structure between resolve and
what exists in the file system"* - and, in the same instruction, explicit
authority to delete the 52 reel timelines, every subtitle asset, the stale
bins and the pipeline-generated markers on the master timeline.

That deletion authority is a ONE-OFF for that reset, recorded here
because this module is where its result lives.  It does not amend the
2026-09-06 ruling `resolve_organization.py` records (*"a refusal is cheap
and a deleted timeline is not"*): organising still never deletes, and the
test that asserts it scans those files, not this one.  What changed is
that the field-test project was reset under newer, explicit authority,
and the bins below are what it was reset TO.

The filesystem side already has an owner -
`library/tools/project_layout.py` calls itself "the one place in the
pipeline that decides where a project's files go" - so this module does
not invent a second scheme.  Every bin that holds files names the `Area`
it mirrors; the bins that hold timelines (which live only in Resolve)
say so and name the disk Areas their renders land in instead.  Numbered
prefixes keep the created bins walking the filesystem order - inputs,
timelines, renders, deliverables - with ONE exception, and it is
deliberate: the source bin keeps the captain's own name.

`Source footage` is not renamed to `01 - Source` because the Folder API
has no rename - a rename would mean moving every clip out and deleting
the bin, which is exactly what the firstmate constraint on the 2026-09-09
reset forbids: the bin HOLDS THE MASTER SOURCE MEDIA (seven camera
originals plus the VFX assets that shipped with them) and emptying it
takes `GEO Podcast - Synced` media-offline.  So the layout adopts the
captain's name, the same way `resolve_organization.BIN_SOURCE` does, and
the bin sorts after the numbered ones in Resolve's alphabetical listing.
A name the captain chose outranks a prefix scheme; the mapping is what
is unified, not the sort key.
then what the pipeline renders, then what the run is for.  Numbered
prefixes keep Resolve's alphabetical sort walking that order.

The versioning half replaces the suffixes the old project accumulated -
`(harvest)`, `(rebuild)`, `(selector redraw)`, `(vox test)`,
`(fragment fix)`, `(free selection)`, `(whole-take rebuild)`,
`(pipeline rebuild)`, `(firstmate rebuild)`, `(clean and complete)` -
competing in timeline names with no agreed meaning.  A rebuilt reel is
`Reel <NN> - <slug> v<NNN>` in Resolve and `<slug>-v<NNN>` under the
owning step's `Area` on disk, so either side answers which cut is
current without opening the other.  Superseded versions move to
`05 - Reels/Archive` (and `run_archives/` on disk) rather than gathering
a new suffix.

`tests/test_resolve_bin_layout.py`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from library.tools.project_layout import Area

MASTER_BIN = "04 - Master"
REELS_BIN = "05 - Reels"
REELS_ARCHIVE_BIN = "Archive"


@dataclass(frozen=True)
class Bin:
    """One bin in the canonical layout, and the filesystem it mirrors."""

    path: tuple[str, ...]
    """Bin path from the root bin, e.g. `("05 - Reels", "Archive")`."""

    area: Area | None
    """The `project_layout.Area` this bin mirrors.  None for the one bin
    that holds no files: the master timeline is the source everything new
    is built from, and it lives only in Resolve."""

    purpose: str

    @property
    def name(self) -> str:
        return "/".join(self.path)


BINS: tuple[Bin, ...] = (
    Bin(("Source footage",), Area.RAW,
        "The captain's own bin, kept under that name (see above). Camera "
        "originals, linked read-only - the pipeline never writes here and "
        "deleting it loses what no re-run reproduces. The two VFX assets, "
        "the still and the compound that shipped with them stay alongside "
        "rather than moving to 03 - Assets, because moving them is what "
        "the reset was forbidden to risk."),
    Bin(("02 - Music",), Area.MUSIC,
        "Music the captain put here by hand. Tracks the pipeline fetches "
        "land under the step that fetched them, not here."),
    Bin(("03 - Assets",), Area.ASSETS,
        "Project-owned artwork: stills, overlays, presets referenced by "
        "name from project.yaml. Brand fonts and logos (Area.BRAND_ASSETS) "
        "and staged compositions (Area.COMPOSITIONS) gain sub-bins here "
        "when a project needs them, rather than new top-level bins."),
    Bin((MASTER_BIN,), None,
        "The master timeline, alone. Timelines live only in Resolve, and "
        "this one is the thing in the project that cannot be rebuilt, so "
        "it holds a bin rather than sharing one: nothing else may land "
        "here."),
    Bin((REELS_BIN,), None,
        "Current-cut reel timelines. Timelines live only in Resolve, so "
        "this bin mirrors no single Area: the rendered versions sit under "
        "the owning step's Areas on disk (subtitle segments, "
        "motion-graphics segments, timeline interchange) and finished "
        "masters in Area.EXPORTS; the bin holds the cuts those came from."),
    Bin((REELS_BIN, REELS_ARCHIVE_BIN), None,
        "Superseded reel versions, moved here when a rebuild lands rather "
        "than renamed with a suffix. Timelines live only in Resolve, so "
        "this is a convention, not an Area: on disk the same role is "
        "run_archives/ - the record of what used to be current."),
    Bin(("06 - Subtitle renders",), Area.SUBTITLE_SEGMENTS,
        "Per-block subtitle overlays the 4.05 step renders. Regenerable "
        "output: empty until a build lands, deleted freely on the next "
        "reset."),
    Bin(("07 - Motion graphics",), Area.MOTION_GRAPHICS_SEGMENTS,
        "Per-block motion-graphics overlays the 4.06 step renders. Same "
        "lifetime as the subtitle bin."),
    Bin(("08 - Exports",), Area.EXPORTS,
        "Finished renders handed to the captain. What the run is for."),
)

BIN_PATHS = tuple(b.path for b in BINS)

AREA_BY_BIN = {b.path: b.area for b in BINS}


def bins_to_create(existing: set[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """Bin paths in `BINS` that are not in `existing`, parents first.

    `AddSubFolder` forks a same-named duplicate instead of refusing
    (`execution/organise_media_pool.py`), so callers create only what
    this returns, never the whole table unconditionally.
    """
    have = set(existing)
    missing: list[tuple[str, ...]] = []
    for path in BIN_PATHS:
        if path not in have:
            for i in range(1, len(path) + 1):
                if path[:i] not in have:
                    have.add(path[:i])
                    missing.append(path[:i])
    return missing


VERSION_RE = re.compile(
    r"^(?P<base>Reel \d+ - .+) v(?P<version>\d{3})$")
"""`Reel 20 - search-didnt-change-the-question-did v003`. Three digits,
so `v010` sorts past `v009` in Resolve's alphabetical listing."""

BANNED_SUFFIXES = (
    "harvest", "rebuild", "selector redraw", "vox test", "fragment fix",
    "free selection", "whole-take rebuild", "pipeline rebuild",
    "firstmate rebuild", "clean and complete",
)
"""Every parenthesised suffix found competing in the field-test timeline
names at reset time. A name carrying one is an unversioned cut: it says
something happened without saying which cut is current."""


def parse_reel_name(name: str) -> tuple[str, int | None]:
    """`(base, version)` for a reel timeline name; version None when the
    name carries no `vNNN` suffix."""
    m = VERSION_RE.match(name)
    if m:
        return m.group("base"), int(m.group("version"))
    return name, None


def banned_suffix(name: str) -> str | None:
    """The banned parenthesised suffix in `name`, or None when clean."""
    lowered = name.lower()
    for suffix in BANNED_SUFFIXES:
        if f"({suffix})" in lowered:
            return suffix
    return None


def format_reel_name(number: int, slug: str, version: int) -> str:
    """`Reel 20 - search-didnt-change-the-question-did v003`."""
    if version < 1:
        raise ValueError(f"reel version starts at 1, not {version}")
    return f"Reel {number:02d} - {slug} v{version:03d}"


def next_version(base: str, names: list[str]) -> int:
    """One past the highest `vNNN` already taken for `base`, else 1."""
    versions = [v for n, v in (parse_reel_name(x) for x in names)
                if n == base and v is not None]
    return max(versions, default=0) + 1


def ensure_bins(media_pool) -> list[str]:
    """Create the canonical bins under the root bin; return what was made.

    Lookup-first (see `bins_to_create`): creating unconditionally forks
    duplicates.  Restores the current folder afterwards, because
    `AddSubFolder` moves it to the bin it made and the next import or
    timeline creation would otherwise land somewhere nobody recorded.
    The one Resolve-touching function in this module; everything above
    it is pure and tested without the application running.
    """
    root = media_pool.GetRootFolder()
    existing: set[tuple[str, ...]] = set()

    def walk(folder, path: tuple[str, ...]) -> None:
        existing.add(path)
        for sub in folder.GetSubFolderList() or []:
            walk(sub, path + (sub.GetName(),))

    for sub in root.GetSubFolderList() or []:
        walk(sub, (sub.GetName(),))
    before = media_pool.GetCurrentFolder()
    made: list[str] = []
    for path in bins_to_create(existing):
        parent = root
        for part in path[:-1]:
            nxt = next((s for s in parent.GetSubFolderList() or []
                        if s.GetName() == part), None)
            if nxt is None:  # pragma: no cover - bins_to_create orders parents first
                raise RuntimeError(f"bin parent missing: {part}")
            parent = nxt
        if next((s for s in parent.GetSubFolderList() or []
                 if s.GetName() == path[-1]), None) is None:
            created = media_pool.AddSubFolder(parent, path[-1])
            if created is None:
                raise RuntimeError(f"AddSubFolder made nothing for {path[-1]!r}")
            made.append("/".join(path))
    if before is not None:
        media_pool.SetCurrentFolder(before)
    return made
