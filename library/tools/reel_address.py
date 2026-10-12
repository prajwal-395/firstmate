"""Address a reel by number everywhere a timeline name is required (LE-14).

Agents spell full reel timeline names (`Reel 07 - x`) because the
timeline reads take `--project "<Resolve name>" --timeline "<exact
name>"`. The project's own records already map reel numbers to those
exact names: LE-07's `reel_state` carries each reel's recorded `final`
and `staging` names, and `project.yaml` carries the Resolve project
binding (`resolve.project_name`). This module is that mapping,
so `ren timeline` and the `resolve-axi` read commands accept
`--reel N` (plus the Ren project path) instead.

Resolution is never a prefix match (AGENTS.md 5): the returned name is
the recorded exact final (or, with `staging=True`, the recorded exact
staging) name, and the caller looks it up exactly. A number that maps
to nothing recorded is a refusal (exit 4), naming the recorded reels;
a number with both a final and a staging names both, so the caller
picks one with `--staging` rather than guessing.

Read only: never calls a model or Resolve, never writes.
"""

from __future__ import annotations

from library.tools.reel_state import UnknownReel, reel_number_of
from library.tools.ren_refusal import RenRefusal


class ReelAddressRefused(RenRefusal):
    """A `--reel` address that names no exact timeline to read."""

    CODE = "REN_REEL_ADDRESS_REFUSED"

    USER_MESSAGE = (
        "That reel number names no exact timeline to read, so nothing "
        "was shown. List the recorded reels, then ask me to look again."
    )


def parse_reel_number(value) -> int | None:
    """The reel number in a `--reel` value, or None where it has none."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError):
        number = reel_number_of(text)
    if number is None or number <= 0:
        return None
    return number


def _staging_suffix() -> str:
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX

    return STAGING_TIMELINE_SUFFIX


def _project_yaml_path(project_ref: str):
    """The project's `project.yaml`, by path or by slug, or None."""
    from library.tools.project_registry import (
        resolve_project_path,
        scan_projects,
    )

    yaml_path = resolve_project_path(project_ref)
    if yaml_path is not None:
        return yaml_path
    match = next(
        (c for c in scan_projects() if c.slug == project_ref), None)
    if match is None:
        return None
    return match.project_root / "project.yaml"


def resolve_reel_timeline(project_ref: str, reel, *,
                          staging: bool = False) -> dict:
    """`(Resolve project, exact timeline)` for reel number `reel`.

    Reads the project's `resolve.project_name` binding and LE-07's
    recorded exact names (`reel_state`): the final by default, the
    staging sibling with `staging=True`. Raises `UnknownReel` for a
    number nothing recorded, `ReelAddressRefused` for a project with
    no binding or a reel with no name on the requested side - both
    refusals (exit 4), never a prefix guess.
    """
    from library.schemas.project_config import load_project_config
    from library.tools import reel_state

    number = parse_reel_number(reel)
    if number is None:
        raise ReelAddressRefused(
            what=f"no reel number in {reel!r}",
            why="a reel is addressed by its number (e.g. `--reel 7`)",
            fix=f"pass `--reel <number>` - `ren reels {project_ref}` "
            "lists the recorded reels",
        )
    yaml_path = _project_yaml_path(str(project_ref))
    if yaml_path is None:
        raise ReelAddressRefused(
            what=f"no Ren project found for {project_ref!r}",
            why="the reel number is resolved through the project's own "
            "records, so the project must name one on disk",
            fix="pass the Ren project folder (or a known slug) - "
            "`ren projects` lists them",
        )
    config = load_project_config(
        yaml_path, migrate_format=False, strict=False)
    resolve_project = str(
        (config.resolve.project_name if config.resolve else "") or ""
    ).strip()
    if not resolve_project:
        raise ReelAddressRefused(
            what=f"{yaml_path} names no resolve.project_name",
            why="the reel's exact timeline name lives under one Resolve "
            "project, and the binding says which",
            fix=f"set resolve.project_name in {yaml_path} to the exact "
            "Resolve project name, then re-run",
        )
    state = reel_state.reels_state(str(yaml_path.parent), only_reel=number)
    rows = [row for row in (state.get("reels") or ())
            if isinstance(row, dict)]
    if not rows:
        raise UnknownReel(
            what=f"no reel {number} is recorded for this project",
            why="the reel number names nothing the builds wrote",
            fix=f"run `ren reels {project_ref}` to list the recorded reels",
        )
    row = rows[0]
    final = str(row.get("final") or "").strip()
    staging_name = str(row.get("staging") or "").strip()
    timeline_name = str(row.get("timeline_name") or "").strip()
    if staging:
        if not staging_name:
            raise ReelAddressRefused(
                what=f"reel {number} has no recorded staging timeline"
                + (f" (final is {final!r})" if final else ""),
                why="nothing filed a `(rebuild staging)` build for this reel",
                fix=(f"read the final instead - drop `--staging` - or "
                     f"rebuild reel {number} first"),
            )
        return {"reel": number, "resolve_project": resolve_project,
                "timeline": staging_name, "final": final,
                "staging": staging_name}
    if (not final and timeline_name and timeline_name != staging_name
            and not timeline_name.endswith(_staging_suffix())):
        final = timeline_name
    if not final:
        if staging_name:
            raise ReelAddressRefused(
                what=f"reel {number} names only a staging timeline "
                f"({staging_name!r}) and no final",
                why="the final is promoted from staging, and this reel "
                "was never promoted",
                fix=f"pass `--staging` to read {staging_name!r}, or "
                f"promote reel {number} first",
            )
        raise ReelAddressRefused(
            what=f"reel {number} names no recorded timeline",
            why="the builds filed no final (or staging) name for this reel",
            fix=f"run `ren reels {project_ref} --reel {number} --json` "
            "to see what the records hold, then build the reel",
        )
    return {"reel": number, "resolve_project": resolve_project,
            "timeline": final, "final": final, "staging": staging_name}
