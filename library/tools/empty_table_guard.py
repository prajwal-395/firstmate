#!/usr/bin/env python3
"""A table the prompt names, arriving with zero rows, says so on the run.

Twice now a step's handoff has spent a paragraph describing a table the
context delivered empty, and both times an audit found it weeks later:

  * `cuts_toon` in `plan_transitions` - thirteen rows of
    `unknown-to-unknown` from two key-name failures (#218);
  * `sfx_candidates_toon` in `plan_sfx` - `[0]{...}`, built from an input
    no DAG edge routes (#223).

The defect is cheap to see at the moment it happens and expensive to
find afterwards, so this reads the context the run is ABOUT TO SEND and
reports the empty tables in it. It is a REPORT, not a gate: a table can
be legitimately empty - an edit with no cutaways really has no B-roll
interjections - and failing the run on one would be a gate that fires on
correct output (AGENTS.md 10.4).

**What it reads.** The serialised context string and the prompt text -
exactly what `present_llm_step` holds, and exactly the two fields the
`llm_requests/<step>.json` archive keeps. One reader therefore serves
the live run and an after-the-fact scan of a run that already happened,
with no second implementation to drift.

**What it flags.** A TOP-LEVEL context key whose value is a table with
no rows: the `[0]{cols}` header a pre-bridge writes, or the bare `[]`
`json_to_toon` writes for an empty list. `named_in_prompt` records
whether the prompt text mentions the key by name, which is what makes
the difference between "the model was told to read this" and "an empty
collection went past".

**What it misses.**

  * A table described in prose without its key name ("the list of cuts")
    reads as not named in the prompt. It is still reported, just without
    that mark.
  * A table nested inside a routed document rather than sitting at the
    top level of the context. Only top-level keys are scanned, because
    below that a `[0]{...}` is usually a legitimately empty field of one
    record rather than a section the prompt describes.
  * **Rows that are present but hollow.** `cuts_toon`, the first
    occurrence, had thirteen rows and every one read
    `unknown-to-unknown`. This guard would not have caught it. It
    catches the zero-row half of the family only.
  * A table projected out of the context entirely, which arrives as no
    key at all rather than as an empty one.

**What it falsely flags.** A collection that is legitimately empty and
whose key the prompt happens to name - the honest answer is then "no
rows, and that is correct", which is why the report never fails a run
and always prints the count rather than a verdict.

CLI, over a project's archived requests:

    python3 -m library.tools.empty_table_guard <project_folder>
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# A top-level key introducing a structured value: `key:` for a dict or
# list, `key: |` for a multi-line string (which is how a pre-bridge's
# already-serialised TOON table arrives).
_KEY_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*):[ \t]*(\|?)[ \t]*$")

# `[N]{col,col}` - the TOON tabular header.
_TABLE_HEADER = re.compile(r"^\[(\d+)\]\{(.*)\}$")


@dataclass(frozen=True)
class EmptyTable:
    """One top-level context key that arrived carrying no rows."""

    key: str
    form: str            # the literal line, e.g. "[0]{segment_id,text}"
    columns: tuple       # declared column names; empty for a bare `[]`
    named_in_prompt: bool

    def describe(self) -> str:
        where = ("the prompt names it" if self.named_in_prompt
                 else "the prompt does not name it")
        if self.columns:
            what = (f"declares {len(self.columns)} column(s) "
                    f"({', '.join(self.columns)}) and no rows")
        else:
            what = "is an empty list"
        return f"{self.key}: {what} - {where}"


def _first_value_line(lines: list, start: int) -> str:
    """The first non-blank indented line under a key, or ''."""
    for line in lines[start:]:
        if not line.strip():
            continue
        if not line[:1].isspace():
            return ""       # the next top-level key: this one had no body
        return line.strip()
    return ""


def find_empty_tables(context_toon: str, prompt_text: str = "") -> list:
    """Top-level context keys whose value is a table with zero rows.

    `context_toon` is the serialised context (`json_to_toon` output);
    `prompt_text` is the handoff, used only to record whether it names
    the key.
    """
    lines = context_toon.split("\n")
    found = []
    for i, line in enumerate(lines):
        match = _KEY_LINE.match(line)
        if not match:
            continue
        key = match.group(1)
        value = _first_value_line(lines, i + 1)
        if not value:
            continue
        if value == "[]":
            found.append(EmptyTable(key, "[]", (), key in prompt_text))
            continue
        header = _TABLE_HEADER.match(value)
        if header and int(header.group(1)) == 0:
            columns = tuple(c for c in header.group(2).split(",") if c)
            found.append(EmptyTable(key, value, columns, key in prompt_text))
    return found


def format_report(step_id: str, empties: list) -> str:
    """The lines a run prints for one step, or '' when there is nothing.

    Named tables come first: those are the ones a paragraph of prompt
    told the model to read.
    """
    if not empties:
        return ""
    ordered = sorted(empties, key=lambda e: (not e.named_in_prompt, e.key))
    named = sum(1 for e in ordered if e.named_in_prompt)
    head = (f"  [empty-table] {step_id}: {len(ordered)} context table(s) "
            f"arrived with zero rows, {named} named in the prompt")
    return "\n".join([head] + [f"    - {e.describe()}" for e in ordered])


def report_step_context(step_id: str, context_toon: str, prompt_text: str,
                        stream=None) -> list:
    """Find and print. Returns what it found so a caller can record it."""
    empties = find_empty_tables(context_toon, prompt_text)
    report = format_report(step_id, empties)
    if report:
        print(report, file=stream or sys.stderr)
    return empties


# ── After the fact: the same reader over an archived run ──────────────


def scan_archived_requests(project_folder) -> list:
    """Every archived LLM request in a project, scanned.

    Returns `(step_id, path, empties)` per request file, in name order.
    The archive keeps `prompt` and `context` as the run sent them, so
    this answers the same question the live report answers, for a run
    that already happened.
    """
    from library.tools.project_layout import Area, ProjectLayout

    requests = ProjectLayout(project_folder).read_dir(Area.LLM_REQUESTS)
    results = []
    for path in sorted(Path(requests).glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            results.append((path.stem, path, [], str(exc)))
            continue
        empties = find_empty_tables(
            payload.get("context", "") or "",
            payload.get("prompt", "") or "",
        )
        results.append((payload.get("step_id") or path.stem, path,
                        empties, None))
    return results


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        print("usage: python3 -m library.tools.empty_table_guard "
              "<project_folder>", file=sys.stderr)
        return 2

    results = scan_archived_requests(argv[0])
    if not results:
        print("no archived LLM requests in this project")
        return 0

    flagged = 0
    for step_id, path, empties, error in results:
        if error:
            print(f"{step_id}: could not be read - {error}")
            continue
        if not empties:
            print(f"{step_id}: no empty tables")
            continue
        flagged += 1
        print(format_report(step_id, empties))
    print(f"\n{flagged} of {len(results)} archived request(s) carry a "
          f"table with zero rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
