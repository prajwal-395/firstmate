"""Localise a difference to a named section, so a delta names its cause.

A context is one long TOON string, and "the two differ by 18,133 bytes" is
not a finding.  A top-level key in TOON starts at column zero, so the
string splits back into the sections the projector selected - and the same
delta reported as `timed_spine: -18,133` names the manifest path that
moved.  Every one of the eleven reconstructions in the first run of this
bench localised to exactly one section.
"""

from __future__ import annotations

import difflib
import re

_TOP_LEVEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:\[{]")

# What `present_llm_step` appends to the context before a QA retry.  A
# request file is last-write-wins per step, so an archived context that
# carries this block is the RETRY, not the first attempt.
QA_RETRY_MARKER = "\n\nQA Feedback from previous attempt:"


def split_sections(context: str) -> dict:
    """The context, keyed by the top-level input key each section came from."""
    out = {}
    current = None
    buf = []
    for line in context.split("\n"):
        if line and not line[0].isspace():
            m = _TOP_LEVEL.match(line)
            if m:
                if current is not None:
                    out[current] = "\n".join(buf)
                current = m.group(1)
                buf = [line]
                continue
        buf.append(line)
    if current is not None:
        out[current] = "\n".join(buf)
    return out


def split_qa_retry(context: str):
    """Separate a first-attempt context from an appended QA-retry block."""
    idx = context.find(QA_RETRY_MARKER)
    if idx < 0:
        return context, ""
    return context[:idx], context[idx:]


def section_deltas(left: str, right: str) -> list:
    """Per-section byte deltas, biggest absolute difference first."""
    a, b = split_sections(left), split_sections(right)
    rows = []
    for key in sorted(set(a) | set(b)):
        x, y = a.get(key), b.get(key)
        if x == y:
            continue
        rows.append({
            "section": key,
            "left_bytes": None if x is None else len(x.encode("utf-8")),
            "right_bytes": None if y is None else len(y.encode("utf-8")),
            "delta_bytes": (len(x.encode("utf-8")) if x else 0)
                           - (len(y.encode("utf-8")) if y else 0),
            "only_in": "left" if y is None else ("right" if x is None else None),
        })
    rows.sort(key=lambda r: -abs(r["delta_bytes"]))
    return rows


def unified(left: str, right: str, left_name: str, right_name: str,
            context_lines: int = 2, max_lines: int = 200) -> str:
    """A bounded unified diff.  Two contexts can be a megabyte each."""
    out = []
    for line in difflib.unified_diff(
            left.splitlines(), right.splitlines(),
            fromfile=left_name, tofile=right_name,
            n=context_lines, lineterm=""):
        out.append(line if len(line) <= 240 else line[:237] + "...")
        if len(out) >= max_lines:
            out.append(f"... diff truncated at {max_lines} lines")
            break
    return "\n".join(out)


def json_answer_diff(left, right) -> list:
    """Diff two ANSWERS key by key, so a changed decision is legible.

    An answer is JSON, and comparing it as text reports every reordering
    as a change.  This walks it instead, and reports the leaf paths that
    differ - which is the question the bench exists to ask: did the edit
    move, and where.
    """
    rows = []

    def walk(a, b, path):
        if type(a) is not type(b):
            rows.append({"path": path or "<root>", "kind": "type",
                         "left": _brief(a), "right": _brief(b)})
            return
        if isinstance(a, dict):
            for k in sorted(set(a) | set(b)):
                p = f"{path}.{k}" if path else k
                if k not in a:
                    rows.append({"path": p, "kind": "added", "left": None,
                                 "right": _brief(b[k])})
                elif k not in b:
                    rows.append({"path": p, "kind": "removed",
                                 "left": _brief(a[k]), "right": None})
                else:
                    walk(a[k], b[k], p)
            return
        if isinstance(a, list):
            if len(a) != len(b):
                rows.append({"path": path or "<root>", "kind": "length",
                             "left": len(a), "right": len(b)})
            for i in range(min(len(a), len(b))):
                walk(a[i], b[i], f"{path}[{i}]")
            return
        if a != b:
            rows.append({"path": path or "<root>", "kind": "changed",
                         "left": _brief(a), "right": _brief(b)})

    walk(left, right, "")
    return rows


def _brief(value, limit: int = 120):
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."
