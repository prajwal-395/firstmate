"""The series registry: the captain's planning tree IS the brief.

The captain's channel already writes every episode's brief down.  Each
series folder in the planning tree carries a creative-direction document
and an `episode_database.md` with a row per episode (title, hook or
summary, status).  Until this module nothing in the engine knew the tree
existed, so a new episode project started from an empty `brief.md` and an
interview asking what the planning tree already answered.

## Two halves, with different lifetimes

**The registry is a per-machine ADDRESS BOOK.**  `~/.config/ren/series.yaml`
(beside `config.env`, outside every checkout and project; `$REN_SERIES_REGISTRY`
names it outright for a test or a second user) maps a series id to:

  * `creative_direction` - the series' brief document;
  * `episode_database`   - the series' episode table;
  * `brand_template`     - a `brand.json` in the series' shared store
                           (`library/tools/brand_library.py`), optional;
  * `delivery`           - `format`, `platform` and `max_duration_seconds`,
                           each optional and each validated against its
                           own enumeration (`delivery_format`, `delivery_spec`).

**The project carries REFERENCES, not the registry.**  `ren new <slug>
--series <id> --episode <n>` looks the series up once and writes into the
project's `project.yaml`: `creative_brief` (the series document's absolute
path, attached), `series` (the membership name, `brief_reference`'s #258
anchor), `episode` (series id, number and the episode database's absolute
path) and the delivery declarations.  A project therefore runs on a machine
with no registry, and editing the registry never silently changes a project
that already exists.

## Both documents are attached BY REFERENCE, never copied

The series document is the `creative_brief`, so it travels exactly as every
brief does: an absolute path plus a map (`brief_reference.build_reference`).
The episode row is read off the episode database AT RUN TIME, by number, and
travels inside that same reference as a short block naming the file and the
line it came from - a row is a few hundred bytes, under the byte threshold
that keeps a section inline (`INLINE_WHEN_UNDER_BYTES`), so it is quoted
rather than mapped.  Nothing is copied into the project: the run's
provenance records what it read (`record_episode_for_run`, the episode
row's sibling of `brief_snapshot`'s versioned home for a free path).

## The interview asks only what the row lacks

With the series brief attached the starter interview has nothing to ask -
that is the attached reading (`brief_attachment`).  What the row can still
lack is a CELL: the 4th Wall's curated rows carry an empty "Personal Story
Anchor (to be filled)".  `gap_questions` asks one question per empty cell
and none about a filled one, never about the episode column itself, the
status column, or a column the registry lists under `unasked_columns`.
`briefing_chat.conduct_if_needed` conducts those questions in chat under the
agent backend and records the count either way.

## The episode database is REPORTED, never written

Ren reads the captain's production status (Idea -> Scripted -> Filmed ->
Edited -> Posted) and says where the episode stands and what it would move
to after this edit.  It writes nothing into the planning tree; the captain
has not said otherwise.  `status_report` is that line.

`tests/unit/context/test_series_registry.py`.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Names the registry file outright, for a test or a second user.
REGISTRY_ENV = "REN_SERIES_REGISTRY"
#: The registry's filename, beside `config.env`.
REGISTRY_FILENAME = "series.yaml"

#: The project.yaml key (under `pipeline:`) that binds a project to one
#: episode.  A mapping: `series_id`, `number`, `database`, and the three
#: column declarations the row is read with.
EPISODE_KEY = "episode"

#: The column that numbers an episode, unless the series declares another
#: (Punch Card numbers its log by `Day`).  Only a table whose FIRST column
#: has this header is an episode table, so an item table that also starts
#: at 1 is never mistaken for one.
DEFAULT_EPISODE_COLUMN = "#"
#: The column holding the captain's production status.
DEFAULT_STATUS_COLUMN = "Status"

#: Where `ren new --series` reads the episode database's own status
#: order from: the table whose first column is the status column (the
#: planning tree's "Pipeline Status Key").
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_SEPARATOR_CELL = re.compile(r"^:?-{2,}:?$")

#: The provenance record of the row a run read, beside the brief's own
#: snapshot (`brief_snapshot.SNAPSHOT_JSON`).
SNAPSHOT_JSON = "episode_row_snapshot.json"


class SeriesRegistryError(ValueError):
    """A registry, series or episode declaration this module refuses."""


# ── The registry ─────────────────────────────────────────────────────


def registry_path() -> Path:
    """Where the per-machine registry lives: `$REN_SERIES_REGISTRY`, else
    beside the per-user `config.env` (`paths.user_config_path`)."""
    explicit = os.environ.get(REGISTRY_ENV)
    if explicit:
        return Path(explicit).expanduser()
    from library.tools.paths import user_config_path
    return user_config_path().parent / REGISTRY_FILENAME


@dataclass(frozen=True)
class SeriesEntry:
    """One series, as the registry declares it, with paths made absolute."""

    series_id: str
    name: str
    creative_direction: str
    episode_database: str
    brand_template: str = ""
    delivery_format: str = ""
    delivery_platform: str = ""
    max_duration_seconds: float | None = None
    episode_column: str = DEFAULT_EPISODE_COLUMN
    status_column: str = DEFAULT_STATUS_COLUMN
    unasked_columns: tuple = ()


def _expand(value: str, root: str, where: str) -> str:
    """A declared path made absolute: `~`/`$VAR` expanded, a relative one
    anchored at the entry's `root`.  A relative path with no root is
    refused - anchoring it at the current directory would make the same
    registry mean different files from different shells."""
    text = os.path.expanduser(os.path.expandvars(str(value).strip()))
    if os.path.isabs(text):
        return os.path.normpath(text)
    if root:
        return os.path.normpath(os.path.join(root, text))
    raise SeriesRegistryError(
        f"{where}: {value!r} is a relative path and the series declares no "
        f"`root` to anchor it. Give an absolute path, or a `root`.")


def _positive_seconds(value, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SeriesRegistryError(
            f"{where}: max_duration_seconds must be a number of seconds, "
            f"got {value!r}.")
    if value <= 0:
        raise SeriesRegistryError(
            f"{where}: max_duration_seconds must be positive, got {value!r}.")
    return float(value)


def _entry(series_id: str, raw, where: str) -> SeriesEntry:
    if not isinstance(raw, dict):
        raise SeriesRegistryError(
            f"{where}: series {series_id!r} must be a mapping, got "
            f"{type(raw).__name__}.")
    here = f"{where} (series {series_id!r})"
    root = raw.get("root") or ""
    if root:
        root = os.path.expanduser(os.path.expandvars(str(root)))
        if not os.path.isabs(root):
            raise SeriesRegistryError(f"{here}: root {raw['root']!r} must be absolute.")
    for required in ("creative_direction", "episode_database"):
        if not raw.get(required):
            raise SeriesRegistryError(
                f"{here}: `{required}` is required - the series document "
                f"and the episode database are the two things a series "
                f"entry exists to name.")
    delivery = raw.get("delivery") or {}
    if not isinstance(delivery, dict):
        raise SeriesRegistryError(f"{here}: `delivery` must be a mapping.")
    fmt = str(delivery.get("format") or "").strip()
    if fmt:
        from library.tools.delivery_format import resolve_format_name
        resolve_format_name(fmt)  # raises, naming the known formats
    platform = str(delivery.get("platform") or "").strip()
    if platform:
        from library.tools.delivery_spec import resolve_delivery_spec
        resolve_delivery_spec(platform)  # raises, naming the known platforms
    unasked = raw.get("unasked_columns") or []
    if isinstance(unasked, str):
        unasked = [unasked]
    if not isinstance(unasked, list):
        raise SeriesRegistryError(f"{here}: `unasked_columns` must be a list of headers.")
    brand = raw.get("brand_template") or ""
    return SeriesEntry(
        series_id=series_id,
        name=str(raw.get("name") or series_id).strip(),
        creative_direction=_expand(raw["creative_direction"], root, here),
        episode_database=_expand(raw["episode_database"], root, here),
        brand_template=_expand(brand, root, here) if brand else "",
        delivery_format=fmt,
        delivery_platform=platform,
        max_duration_seconds=_positive_seconds(
            delivery.get("max_duration_seconds"), here),
        episode_column=str(raw.get("episode_column")
                           or DEFAULT_EPISODE_COLUMN).strip(),
        status_column=str(raw.get("status_column")
                          or DEFAULT_STATUS_COLUMN).strip(),
        unasked_columns=tuple(str(c).strip() for c in unasked),
    )


def _read_raw(path: Path) -> dict:
    if not path.is_file():
        return {}
    import yaml
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SeriesRegistryError(f"{path}: not readable YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SeriesRegistryError(f"{path}: the registry must be a mapping.")
    series = data.get("series") or {}
    if not isinstance(series, dict):
        raise SeriesRegistryError(f"{path}: `series` must map an id to an entry.")
    return data


def load_registry(path: Path | None = None) -> dict:
    """Every series the registry declares, `{series_id: SeriesEntry}`.

    No file is an empty registry, not an error: a machine that never
    registered a series has none.  A malformed entry RAISES, naming the
    file and the series, rather than dropping the entry.
    """
    path = Path(path) if path else registry_path()
    raw = _read_raw(path)
    return {str(sid): _entry(str(sid), entry, str(path))
            for sid, entry in (raw.get("series") or {}).items()}


def get_series(series_id: str, path: Path | None = None) -> SeriesEntry:
    """One series by id.  An unknown id raises, naming the ones known."""
    path = Path(path) if path else registry_path()
    registry = load_registry(path)
    key = (series_id or "").strip()
    if key not in registry:
        known = ", ".join(sorted(registry)) or "none"
        raise SeriesRegistryError(
            f"series {series_id!r} is not in the series registry {path} "
            f"(known: {known}). Register it with `ren series add {key or '<id>'} "
            f"--dir <its folder>`.")
    return registry[key]


def add_series(series_id: str, entry: dict,
               path: Path | None = None) -> Path:
    """Write one entry into the registry, keeping every other entry.

    The entry is validated through the same reader every consumer uses
    before anything is written, so a registry this function wrote is one
    `load_registry` reads.
    """
    path = Path(path) if path else registry_path()
    raw = _read_raw(path)
    series = dict(raw.get("series") or {})
    _entry(series_id, entry, str(path))
    series[series_id] = entry
    raw["series"] = series
    import yaml
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Ren series registry: one entry per series (library/tools/series_registry.py).\n"
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8")
    return path


# ── The episode database ─────────────────────────────────────────────


@dataclass(frozen=True)
class Table:
    """One markdown table: its headers, its rows and where it sits."""

    section: str
    headers: tuple
    #: `(line_number, cells)` per row; 1-based line numbers.
    rows: tuple


def _cells(line: str) -> list:
    body = line.strip()
    body = body.removeprefix("|")
    body = body.removesuffix("|")
    return [c.strip() for c in body.split("|")]


def _is_separator(cells: list) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL.match(c) for c in cells if c) \
        and any(cells)


def parse_tables(text: str) -> list:
    """Every markdown table in `text`, with the heading it sits under."""
    lines = text.split("\n")
    tables, section, i = [], "", 0
    while i < len(lines):
        line = lines[i]
        heading = _HEADING.match(line)
        if heading:
            section = heading.group(2)
        if (line.lstrip().startswith("|") and i + 1 < len(lines)
                and lines[i + 1].lstrip().startswith("|")
                and _is_separator(_cells(lines[i + 1]))):
            headers = tuple(_cells(line))
            rows, j = [], i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                rows.append((j + 1, tuple(_cells(lines[j]))))
                j += 1
            tables.append(Table(section, headers, tuple(rows)))
            i = j
            continue
        i += 1
    return tables


def _plain(cell: str) -> str:
    """A cell without its markdown emphasis, for matching and asking."""
    return re.sub(r"[*_`]", "", cell or "").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _plain(text)).casefold()


@dataclass(frozen=True)
class EpisodeRow:
    """One episode's row, read off the episode database."""

    database: str
    episode: str
    line: int
    section: str
    #: `(header, cell)` in the table's own column order, cells verbatim.
    columns: tuple
    status_column: str = DEFAULT_STATUS_COLUMN

    @property
    def status(self) -> str:
        for header, cell in self.columns:
            if _norm(header) == _norm(self.status_column):
                return cell.strip()
        return ""

    @property
    def title(self) -> str:
        for header, cell in self.columns:
            if _norm(header) == "title":
                return _plain(cell)
        return ""


def find_episode_row(database: str, episode, *,
                     episode_column: str = DEFAULT_EPISODE_COLUMN,
                     status_column: str = DEFAULT_STATUS_COLUMN) -> EpisodeRow:
    """The row numbered `episode`, or a refusal saying why there is none.

    Only tables whose FIRST column is `episode_column` are searched.  A
    number in no row raises with the numbers that do exist; a number in
    two rows raises naming both sections, because picking one is choosing
    which episode this video is.
    """
    try:
        text = Path(database).read_text(encoding="utf-8")
    except OSError as exc:
        raise SeriesRegistryError(
            f"the episode database {database!r} cannot be read: {exc}") from exc
    wanted = _norm(str(episode))
    if not wanted:
        raise SeriesRegistryError("an episode number is required.")
    matches, numbers = [], []
    for table in parse_tables(text):
        if not table.headers or _norm(table.headers[0]) != _norm(episode_column):
            continue
        for line_no, cells in table.rows:
            if not cells or not _plain(cells[0]):
                continue
            numbers.append(_plain(cells[0]))
            if _norm(cells[0]) == wanted:
                padded = list(cells) + [""] * (len(table.headers) - len(cells))
                matches.append(EpisodeRow(
                    database=str(database), episode=_plain(cells[0]),
                    line=line_no, section=table.section,
                    columns=tuple(zip(table.headers, padded)),
                    status_column=status_column))
    if not matches:
        shown = ", ".join(numbers[:12]) + (" ..." if len(numbers) > 12 else "")
        raise SeriesRegistryError(
            f"episode {episode!r} has no row in {database!r} (tables whose "
            f"first column is {episode_column!r} number: {shown or 'none'}). "
            f"Add its row to the episode database, or name the episode the "
            f"database uses.")
    if len(matches) > 1:
        where = "; ".join(f"line {m.line} under {m.section!r}" for m in matches)
        raise SeriesRegistryError(
            f"episode {episode!r} has {len(matches)} rows in {database!r} "
            f"({where}). Number them apart in the episode database - which "
            f"row is this video is the captain's to say, not Ren's to pick.")
    return matches[0]


def status_sequence(database: str,
                    status_column: str = DEFAULT_STATUS_COLUMN) -> list:
    """The database's own status order, off its status-key table, or []."""
    try:
        text = Path(database).read_text(encoding="utf-8")
    except OSError:
        return []
    for table in parse_tables(text):
        if table.headers and _norm(table.headers[0]) == _norm(status_column):
            return [cells[0].strip() for _, cells in table.rows
                    if cells and cells[0].strip()]
    return []


def status_report(row: EpisodeRow) -> str:
    """Where the episode stands in the captain's database, REPORTED.

    Ren never writes the episode database (the captain has not said
    otherwise), so the line says what the row would move to after this
    edit, read off the database's own status key, for the captain to
    write.  A database with no status key, or a status the key does not
    list, is said so rather than guessed past.
    """
    current = row.status or "(empty)"
    head = (f"Episode {row.episode} reads {current} in {row.database} "
            f"(line {row.line}).")
    sequence = status_sequence(row.database, row.status_column)
    edited = next((s for s in sequence if "edited" in _norm(s)), "")
    if not edited:
        return (head + " Ren never writes the episode database; update "
                "the status yourself when this edit is done.")
    if _norm(current) == _norm(edited):
        return head + " Ren never writes the episode database."
    return (head + f" Ren never writes the episode database: when this "
            f"edit is done, it would read {edited} - update it yourself.")


# ── What the row lacks ───────────────────────────────────────────────


def gap_columns(row: EpisodeRow, unasked=()) -> list:
    """The row's EMPTY cells a person could fill, by header.

    Never the episode column, the status column (production state is not
    a brief) or a column the series lists under `unasked_columns`.
    """
    skip = {_norm(row.columns[0][0]) if row.columns else "",
            _norm(row.status_column)} | {_norm(c) for c in unasked}
    return [_plain(header) for header, cell in row.columns
            if _norm(header) not in skip and not _plain(cell)]


def gap_questions(row: EpisodeRow, unasked=()) -> list:
    """One interview question per empty cell, and none for a filled one.

    The shape `briefing_chat` asks in (`step_id`, `question`,
    `why_it_matters`, `what_assumed_instead`).  Each names the column so
    the answer can go back into the row by the captain's hand.
    """
    label = f"episode {row.episode}" + (f" ({row.title})" if row.title else "")
    return [{
        "step_id": "creative_direction",
        "question": (f"The episode database has no {column!r} for "
                     f"{label} yet. What is it for this video?"),
        "why_it_matters": ("the episode's row is this video's brief, and "
                           "this column of it is empty"),
        "what_assumed_instead": ("the series brief and the rest of the row "
                                 "decide it"),
        "episode_column": column,
    } for column in gap_columns(row, unasked)]


# ── The project's binding ────────────────────────────────────────────


@dataclass(frozen=True)
class EpisodeBinding:
    """What a project's `pipeline.episode` declares."""

    series_id: str
    number: str
    database: str
    episode_column: str = DEFAULT_EPISODE_COLUMN
    status_column: str = DEFAULT_STATUS_COLUMN
    unasked_columns: tuple = field(default_factory=tuple)

    def row(self) -> EpisodeRow:
        return find_episode_row(self.database, self.number,
                                episode_column=self.episode_column,
                                status_column=self.status_column)


def binding_declaration(entry: SeriesEntry, episode) -> dict:
    """The `pipeline.episode` mapping `ren new` writes for one episode."""
    return {
        "series_id": entry.series_id,
        "number": str(episode),
        "database": entry.episode_database,
        "episode_column": entry.episode_column,
        "status_column": entry.status_column,
        "unasked_columns": list(entry.unasked_columns),
    }


def project_binding(project_folder: str) -> EpisodeBinding | None:
    """The episode this project is bound to, or None when it names none.

    A declaration that is not a mapping, or that lacks its number or its
    database, RAISES: a project that says it is an episode and cannot be
    read as one would otherwise plan as no episode at all, silently.
    """
    if not project_folder:
        return None
    from library.tools.brand_registry import project_pipeline_block
    declared = project_pipeline_block(project_folder).get(EPISODE_KEY)
    if declared is None:
        return None
    where = os.path.join(project_folder, "project.yaml")
    if not isinstance(declared, dict):
        raise SeriesRegistryError(
            f"{where}: pipeline.{EPISODE_KEY} must be a mapping "
            f"(series_id, number, database), got {type(declared).__name__}.")
    number = str(declared.get("number") or "").strip()
    database = str(declared.get("database") or "").strip()
    if not number or not database:
        raise SeriesRegistryError(
            f"{where}: pipeline.{EPISODE_KEY} needs both `number` and "
            f"`database` - the episode and the table its row is in.")
    from library.tools.portable_paths import expand
    database = expand(database, project_folder)
    if not os.path.isabs(database):
        database = os.path.join(project_folder, database)
    unasked = declared.get("unasked_columns") or []
    return EpisodeBinding(
        series_id=str(declared.get("series_id") or "").strip(),
        number=number,
        database=database,
        episode_column=str(declared.get("episode_column")
                           or DEFAULT_EPISODE_COLUMN).strip(),
        status_column=str(declared.get("status_column")
                          or DEFAULT_STATUS_COLUMN).strip(),
        unasked_columns=tuple(str(c) for c in unasked),
    )


def episode_block(row: EpisodeRow, series_name: str = "",
                  max_duration_seconds: float | None = None,
                  max_duration_source: str = "the project's "
                                             "pipeline.max_duration_seconds"
                  ) -> str:
    """The row as it travels inside the brief reference: where it is and
    what it says, each cell as `header: value`, nothing summarised."""
    of = f" of the series {series_name!r}" if series_name else ""
    lines = [
        f"--- this video: episode {row.episode}{of} ---",
        (f"Its row in the series' episode database is the brief for THIS "
         f"video; the document mapped here is the series' brief. Row read "
         f"from {row.database}, line {row.line}"
         + (f", under {row.section!r}" if row.section else "") + ":"),
    ]
    for header, cell in row.columns:
        lines.append(f"    {_plain(header)}: {cell.strip() or '(empty)'}")
    if max_duration_seconds:
        lines.append(f"Delivery: at most {max_duration_seconds:g} seconds "
                     f"({max_duration_source}).")
    return "\n".join(lines)


def episode_block_for_project(project_folder: str) -> str:
    """The episode block for this project's prompt, or "" when it is no
    episode.  A bound episode whose row cannot be read RAISES, the way a
    declared brief that cannot be read does (`gather_step_inputs`)."""
    binding = project_binding(project_folder)
    if binding is None:
        return ""
    from library.tools.brief_reference import project_series_identity
    from library.tools.delivery_spec import project_max_duration_seconds
    return episode_block(binding.row(),
                         series_name=project_series_identity(project_folder),
                         max_duration_seconds=project_max_duration_seconds(
                             project_folder))


def interview_gaps(project_folder: str) -> list:
    """The interview questions this project's episode row leaves open."""
    binding = project_binding(project_folder)
    if binding is None:
        return []
    return gap_questions(binding.row(), binding.unasked_columns)


def describe_for_run(project_folder: str) -> list:
    """The run header's lines about the episode, or [] for no episode."""
    binding = project_binding(project_folder)
    if binding is None:
        return []
    row = binding.row()
    gaps = gap_columns(row, binding.unasked_columns)
    lines = [
        (f"Episode brief: episode {row.episode} of series "
         f"{binding.series_id or '(unnamed)'}, row at {row.database} line "
         f"{row.line}, travels with the series brief."),
        status_report(row),
    ]
    if gaps:
        lines.append("The row has no " + ", ".join(repr(g) for g in gaps)
                     + " yet - the briefing interview asks for exactly that.")
    else:
        lines.append("The row answers every column, so the briefing "
                     "interview has nothing to ask about it.")
    return lines


# ── `ren new --series` ───────────────────────────────────────────────


def new_project_declarations(series_id: str, episode=None,
                             path: Path | None = None) -> dict:
    """What `create_project` declares for a series episode, validated.

    Resolved and checked BEFORE the project directory exists, so an
    unknown series, a missing document or an episode with no row refuses
    without leaving a half-made project behind.
    """
    entry = get_series(series_id, path)
    for label, doc in (("creative direction document", entry.creative_direction),
                       ("episode database", entry.episode_database)):
        if not os.path.isfile(doc):
            raise SeriesRegistryError(
                f"series {series_id!r} names {doc!r} as its {label}, and "
                f"there is no such file. Fix the entry in {registry_path() if path is None else path}.")
    if entry.brand_template and not os.path.isfile(entry.brand_template):
        raise SeriesRegistryError(
            f"series {series_id!r} names brand template "
            f"{entry.brand_template!r}, and there is no such file.")
    row = None
    if episode is not None and str(episode).strip():
        row = find_episode_row(entry.episode_database, episode,
                               episode_column=entry.episode_column,
                               status_column=entry.status_column)
    return {
        "entry": entry,
        "row": row,
        "episode": (binding_declaration(entry, row.episode)
                    if row is not None else None),
    }


# ── Provenance ───────────────────────────────────────────────────────


def record_episode_for_run(project_folder: str) -> dict:
    """Record the row this run read, beside the brief's own snapshot.

    The episode database is a free path in the captain's planning tree,
    the same case `brief_snapshot` answers for the brief: the project's
    versioned `pipeline_output/provenance/` gets what was READ (row text,
    line, the database's sha256), so a build can be explained after the
    row changes.  A run bound to no episode removes a stale record.
    Never raises: a record that breaks the build is worse than none.
    """
    try:
        from library.tools.project_layout import Area, ProjectLayout
        target = Path(ProjectLayout(project_folder).write_path(
            Area.PROVENANCE, SNAPSHOT_JSON))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"recorded": False, "reason": f"no provenance folder: {exc}"}
    try:
        binding = project_binding(project_folder)
        if binding is None:
            if target.exists():
                target.unlink()
                return {"recorded": False, "cleared": True,
                        "reason": "the project names no episode"}
            return {"recorded": False, "reason": "the project names no episode"}
        row = binding.row()
        raw = Path(row.database).read_bytes()
        record = {
            "series_id": binding.series_id,
            "episode": row.episode,
            "database": row.database,
            "database_sha256": hashlib.sha256(raw).hexdigest(),
            "line": row.line,
            "section": row.section,
            "columns": [[h, c] for h, c in row.columns],
            "recorded_at": datetime.datetime.now(
                datetime.UTC).isoformat(),
        }
        previous = {}
        if target.exists():
            try:
                previous = json.loads(target.read_text(encoding="utf-8"))
            except ValueError:
                previous = {}
        unchanged = all(previous.get(k) == record[k] for k in
                        ("database", "database_sha256", "line", "columns"))
        if not unchanged:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(record, indent=2, ensure_ascii=False)
                              + "\n", encoding="utf-8")
        return {"recorded": True, "unchanged": unchanged, **record}
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return {"recorded": False, "reason": str(exc)}


# ── `ren series` ─────────────────────────────────────────────────────


#: The file names a planning-tree series folder carries, in the order
#: `ren series add --dir` looks for them.  A folder that carries other
#: names passes them explicitly.
_CREATIVE_DIRECTION_NAMES = ("branding_creative_direction.md",
                             "creative_direction.md", "overview.md")
_EPISODE_DATABASE_NAMES = ("episode_database.md",)


def _first_existing(folder: str, names) -> str:
    for name in names:
        if os.path.isfile(os.path.join(folder, name)):
            return name
    return ""


def _cmd_list(args) -> int:
    registry = load_registry()
    if not registry:
        print(f"No series registered in {registry_path()}. "
              f"Add one with `ren series add <id> --dir <series folder>`.")
        return 0
    for sid, entry in sorted(registry.items()):
        limit = (f", at most {entry.max_duration_seconds:g}s"
                 if entry.max_duration_seconds else "")
        print(f"  {sid:<16} {entry.name}{limit}")
        print(f"  {'':<16} brief:    {entry.creative_direction}")
        print(f"  {'':<16} episodes: {entry.episode_database}")
    return 0


def _cmd_show(args) -> int:
    entry = get_series(args.series_id)
    print(json.dumps({k: (list(v) if isinstance(v, tuple) else v)
                      for k, v in entry.__dict__.items()}, indent=2))
    if args.episode:
        row = find_episode_row(entry.episode_database, args.episode,
                               episode_column=entry.episode_column,
                               status_column=entry.status_column)
        print(episode_block(row, entry.name, entry.max_duration_seconds,
                            max_duration_source="the series registry"))
        print(status_report(row))
        gaps = gap_columns(row, entry.unasked_columns)
        print(f"Interview would ask {len(gaps)} question(s)"
              + (f": {', '.join(gaps)}" if gaps else "."))
    return 0


def _cmd_add(args) -> int:
    folder = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else ""
    brief = args.creative_direction or (
        _first_existing(folder, _CREATIVE_DIRECTION_NAMES) if folder else "")
    database = args.episode_database or (
        _first_existing(folder, _EPISODE_DATABASE_NAMES) if folder else "")
    entry = {"name": args.name or args.series_id}
    if folder:
        entry["root"] = folder
    entry["creative_direction"] = brief
    entry["episode_database"] = database
    if args.brand_template:
        entry["brand_template"] = os.path.abspath(
            os.path.expanduser(args.brand_template))
    delivery = {}
    if args.format:
        delivery["format"] = args.format
    if args.platform:
        delivery["platform"] = args.platform
    if args.max_duration is not None:
        delivery["max_duration_seconds"] = args.max_duration
    if delivery:
        entry["delivery"] = delivery
    if args.episode_column:
        entry["episode_column"] = args.episode_column
    if args.unasked_column:
        entry["unasked_columns"] = list(args.unasked_column)
    written = add_series(args.series_id, entry)
    resolved = get_series(args.series_id, written)
    print(f"Registered series {args.series_id!r} in {written}:")
    print(f"  brief:    {resolved.creative_direction}")
    print(f"  episodes: {resolved.episode_database}")
    print(f"Start an episode with: ren new <slug> --series {args.series_id} "
          f"--episode <n>")
    return 0


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    import argparse
    parser = argparse.ArgumentParser(
        prog="ren series",
        description="The per-machine series registry: which planning-tree "
                    "documents are each series' brief and episode table.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List registered series").set_defaults(func=_cmd_list)
    show = sub.add_parser("show", help="Show one series, and an episode's row")
    show.add_argument("series_id")
    show.add_argument("--episode", help="Also show this episode's row, status and gaps")
    show.set_defaults(func=_cmd_show)
    add = sub.add_parser("add", help="Register (or replace) one series")
    add.add_argument("series_id")
    add.add_argument("--dir", help="The series' folder in the planning tree; "
                                   "the documents are found inside it")
    add.add_argument("--name", help="The series' display name")
    add.add_argument("--creative-direction", help="The series brief, if not found in --dir")
    add.add_argument("--episode-database", help="The episode table, if not found in --dir")
    add.add_argument("--brand-template", help="A brand.json in the series' shared store")
    add.add_argument("--format", help="Delivery format, e.g. vertical_1080x1920")
    add.add_argument("--platform", help="Delivery platform, e.g. youtube_shorts")
    add.add_argument("--max-duration", type=float, help="Longest delivery, in seconds")
    add.add_argument("--episode-column", help="The column numbering episodes (default '#')")
    add.add_argument("--unasked-column", action="append", default=[],
                     help="An empty column the interview never asks about; repeatable")
    add.set_defaults(func=_cmd_add)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SeriesRegistryError as exc:
        print(f"ren series: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
