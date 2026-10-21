"""A large context section reaches a step as a REFERENCE, not as a copy.

The engine already owns this shape for the captain's brief
(`library/tools/brief_reference.py`): an absolute path, a map of what is
inside, and the line range that holds each part.  This module applies the
same shape to the CONTEXT TABLES - the ones the planning band sends to
several steps at once.

## Why

Measured on project 001 at `c2419c4cb` (scout
`scout-opt-planning-cost`, `data/scout-opt-planning-cost/report.md`):
**138,380 bytes of the planning band's 455,353 context bytes were
byte-identical repeats across steps** - about 48,700 tokens, 21% of the
band's 231,346 input tokens.  The `picture` table (10,044 B) went to
five steps, `timed_spine` (13,698 B) to three, the brief's reference map
to seven.  The wire layer already de-duplicates the bytes ON DISK
(`llm_handshake.share_request`), which saves disk and IO and **not one
token**: the host materialises the request and reads the whole context
every time.

The fix is the one the brief already uses: deliver the table once, by
path, and let the model fetch the part it needs.

## What travels, and what does not

**The table travels by path; the MAP travels inline.**  The map is one
line per group of rows - the clip, the spine block, the bar - with the
line range that holds it, so following it is one `sed -n 'a,bp'`.  The
map is a map and not a summary: every row is in the file, at the range
the map names.

**Nothing is withheld.**  The file holds the section exactly as it would
have appeared in the prompt, byte for byte, and the map says so.  A
section that is NOT worth referencing stays inline.

## The rules

1. **A section is referenced when it is large AND the reference is
   materially smaller.**  `REFERENCE_MIN_BYTES` is the floor; a table
   whose map costs more than the table stays inline, because a map that
   costs as much as the thing it maps is a worse prompt.
2. **The file is content-addressed.**  The same bytes produce the same
   path, so the five steps that read the same `picture` table share ONE
   file, and the file is written once.
3. **A harness that cannot read a file gets the bytes inline.**  The
   same clause the brief reference has
   (`brief_reference.HARNESS_READS_FILES`): a route the model cannot
   follow is a loss, not a saving.
4. **A section that is ALREADY a reference is left alone.**  The brief
   arrives as a reference already; wrapping it in a second one would
   make the model read a file to find out which file to read.
5. **Only a TABLE travels by reference.**  A table is indexed: the
   model picks rows out of it, and a map of the rows lets it pick the
   ones it needs.  PROSE is read - a direction, a note, a legend - and
   a step cannot decide from an index of prose, so prose stays inline
   however large it is.

## Where the files live

`<project>/pipeline_output/llm_requests/refs/<sha256>.md` - the area that
holds "what the model was asked", because a referenced table IS what the
model was asked, and an archived request has to be able to rebuild it
(`library/tools/versions/runs.py` copies the directory).

`tests/unit/context/test_context_reference.py` follows the reference
rather than asserting its shape: it opens the path the model reads and
requires that what comes back is what the prompt no longer carries.
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
from pathlib import Path

#: A section smaller than this stays inline.  Below it, quoting the
#: table costs less than describing where it is.
REFERENCE_MIN_BYTES = 1500

#: The reference must be this much smaller than the section it replaces,
#: or the section stays inline.  A map that costs as much as the table
#: is a worse prompt, not a cheaper one.
MAX_INLINE_FRACTION = 0.6

#: The marker that says "this section is a reference", read back by
#: `is_reference` so a second pass never wraps a reference in a
#: reference.
REFERENCE_MARKER = "NOT copied into this prompt"

#: The line the model reads, exactly as `brief_reference` writes it.
PATH_MARKER = "FILE:"

_TABLE_HEADER = re.compile(r"^(\s*)\[(\d+)\]\{(.*)\}\s*$")
_KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*):\s*$")

#: Which column identifies a row, most specific first.  The first one a
#: table actually has is used; a table with none of them is keyed by its
#: first column, because a map needs SOME way to name a row.
IDENTITY_COLUMNS = (
    "spine_block_position", "block_position", "target_block_position",
    "position", "clip_id", "source_clip_id", "segment_id", "asset_id",
    "sfx_id", "track_id", "id", "key", "name", "bar", "label", "index",
)


def reference_dir(project_folder: str) -> Path:
    """Where this project's context references live.

    Under the requests area, because a referenced table is part of what
    the model was asked: an archived request names it by path, and the
    archive copies the directory (`library/tools/versions/runs.py`).
    """
    return (Path(project_folder) / "pipeline_output" / "llm_requests"
            / "refs")


def is_reference(value) -> bool:
    """True when `value` is already a reference block."""
    return isinstance(value, str) and REFERENCE_MARKER in value


def _shell_quote(path: str) -> str:
    """POSIX single-quoting, so a path with an apostrophe is pasteable."""
    return "'" + path.replace("'", "'\\''") + "'"


def _identity_index(columns: list[str]) -> int:
    for name in IDENTITY_COLUMNS:
        if name in columns:
            return columns.index(name)
    return 0


def _row_fields(line: str) -> list[str]:
    """The cells of one TOON table row.

    TOON quotes a cell with a BACKTICK and doubles an embedded one, so
    the reader uses the same dialect the writer does.
    """
    try:
        return next(csv.reader([line], quotechar="`", doublequote=True))
    except (csv.Error, StopIteration):
        return []


def _table_map(text: str) -> list[str]:
    """The map of a rendered section: what is inside, and where.

    One line per group of rows, with the line range that holds it.  The
    ranges are the file's OWN line numbers, so `sed -n 'a,bp'` is exact.
    """
    lines = text.split("\n")
    groups: list[str] = []
    keys: dict[int, str] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        header = _TABLE_HEADER.match(line)
        if not header:
            key = _KEY_LINE.match(line)
            if key:
                depth = len(key.group(1))
                for deeper in [d for d in keys if d >= depth]:
                    del keys[deeper]
                keys[depth] = key.group(2)
            index += 1
            continue
        indent = len(header.group(1))
        count = int(header.group(2))
        columns = [c.strip() for c in header.group(3).split(",")]
        rows = lines[index + 1:index + 1 + count]
        path = [keys[depth] for depth in sorted(keys) if depth < indent]
        column = _identity_index(columns)
        buckets: dict[str, list[int]] = {}
        order: list[str] = []
        for offset, row in enumerate(rows):
            fields = _row_fields(row)
            label = fields[column].strip() if column < len(fields) else ""
            label = label.strip("`")
            if not label:
                label = f"row {offset + 1}"
            if label not in buckets:
                buckets[label] = []
                order.append(label)
            buckets[label].append(index + 2 + offset)
        name = path[-1] if path else "table"
        groups.append(f"  {name}: {count} rows in {len(order)} group(s)")
        for label in order:
            numbers = buckets[label]
            groups.append(
                f"    {label}: {len(numbers)} row(s), "
                f"lines {_ranges(numbers)}")
        index += 1 + count
    return groups


def _ranges(numbers: list[int]) -> str:
    """`4-9`, or `4,6,9` when the rows are not contiguous."""
    spans = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append((start, previous))
        start = previous = number
    spans.append((start, previous))
    return ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in spans)


def reference_block(section: str, path: str, groups: list[str]) -> str:
    """The text a step receives in place of the section's own bytes.

    `section` is the rendered section, `path` the file it was written
    to, and `groups` the map.  The wording follows the brief reference's
    wording on purpose: the model has already been taught to read one
    of these, and a second shape would be a second thing to learn.
    """
    total = len(section.encode("utf-8"))
    lines = section.count("\n") + 1
    out = [
        "This table is NOT copied into this prompt.",
        f"It is {total:,} bytes across {lines:,} lines, and it is at:",
        "",
        f"  {PATH_MARKER} {path}",
        "",
        "Read the rows you need, in full, before you decide anything that "
        "turns on them.",
        f"You have a shell: `sed -n '1,{lines}p' {_shell_quote(path)}`, or "
        "grep the file",
        "for a phrase. The map below is a map, not a summary - it is there "
        "so you can tell",
        "whether you need a row, not so you can answer instead of reading "
        "one. Nothing has",
        "been withheld or rewritten: every byte of the table is at that path.",
        "",
    ]
    if groups:
        out.append("What is inside:")
        out.extend(groups)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _reference_path(directory: Path, section: str) -> Path:
    """Where this section's file lives - named by its own bytes.

    The same bytes produce the same path, so every step that reads them
    shares ONE file.
    """
    digest = hashlib.sha256(section.encode("utf-8")).hexdigest()
    return directory / f"{digest}.md"


def _write_reference(path: Path, section: str) -> str:
    """Write the section once, by content, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temp = path.with_suffix(".md.tmp")
        temp.write_text(section, encoding="utf-8")
        os.replace(temp, path)
    return str(path)


def _is_table_shaped(rendered: str) -> bool:
    """True when the rendered section holds at least one TOON table.

    A TABLE is indexed: the model picks rows out of it, and a map of the
    rows lets it pick the ones it needs.  PROSE is read: a direction, a
    note, a legend - a step cannot decide from an index of it, so prose
    stays inline however large it is.
    """
    for line in rendered.split("\n"):
        if _TABLE_HEADER.match(line):
            return True
    return False


def apply_references(context: dict, project_folder: str, harness: str | None,
                     directory: str | Path | None = None) -> tuple[dict, list[str]]:
    """Deliver the large sections of `context` by reference.

    Returns `(context, referenced_keys)`.  `directory` overrides where
    the files are written - the replay bench writes them beside its own
    scratch so it never writes into the project it is measuring.

    A harness that cannot read a file keeps every byte inline: a route
    the model cannot follow is a loss, not a saving.
    """
    from library.tools.brief_reference import harness_reads_files

    if not isinstance(context, dict) or not context:
        return context, []
    if not project_folder and directory is None:
        # No project to write the reference into.  A relative path would
        # put it in whatever directory the runner happens to be in.
        return context, []
    if not harness:
        return context, []
    try:
        if not harness_reads_files(harness):
            return context, []
    except Exception:  # noqa: BLE001 - an unknown harness reads nothing
        return context, []

    from library.tools.toon_serializer import json_to_toon

    target = (Path(directory) if directory is not None
              else reference_dir(project_folder))
    referenced: list[str] = []
    out = dict(context)
    for key, value in context.items():
        if key.startswith("__") or is_reference(value):
            continue
        try:
            rendered = json_to_toon({key: value})
        except Exception:  # noqa: BLE001 - an unrenderable section stays
            continue
        size = len(rendered.encode("utf-8"))
        if size < REFERENCE_MIN_BYTES:
            continue
        if not _is_table_shaped(rendered):
            continue
        groups = _table_map(rendered)
        path = _reference_path(target, rendered)
        block = reference_block(rendered, str(path), groups)
        if len(block.encode("utf-8")) > size * MAX_INLINE_FRACTION:
            continue
        _write_reference(path, rendered)
        out[key] = block
        referenced.append(key)
    return out, referenced


def reference_path(text: str) -> str:
    """The path out of a reference block, or "".

    The same line the model reads, read the same way - so a test that
    follows it follows exactly what the model is given.
    """
    for line in text.split("\n", 40)[:40]:
        stripped = line.strip()
        if stripped.startswith(PATH_MARKER):
            return stripped[len(PATH_MARKER):].strip()
    return ""
