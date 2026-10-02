"""The captain's context folder, reaching a step as a MAP rather than a copy.

The captain: *"having some kind of robust context folder that the LLM
can reference as it is going through the editing process."* The folder
holds anything - documents, references, notes, links, images - and a
folder of documents inlined would blow every prompt in the pipeline.
So this generalises the rule `library/tools/brief_reference.py` solved
for one document ("the map is always inline; a body is inline only when
the map cannot stand in for it") from one document to a folder of them.
There is no second scheme: per markdown file the SAME section parser,
the SAME inline bar (`INLINE_WHEN_UNDER_BYTES`), the SAME line ranges
and the SAME `FILE:` affordance apply, and `project_context_for_step`
is the one entry point the runner calls.

The rule, per file, in order:

1. **A file small enough to quote is quoted.** Under
   `INLINE_WHEN_UNDER_BYTES`, quoting costs what describing costs.
2. **A markdown file over the bar travels as its section map** -
   heading, byte size, `lines a-b`, lede - with bodies fetched by
   `sed -n 'a,bp' <path>`, exactly the brief's affordance.
3. **A non-markdown text file over the bar travels as a lede** - its
   first real line plus its line count - and the same `sed` fetches it.
4. **An image has no map.** It travels as a listing - name, format,
   byte size, path - and the step opens it with an image-capable tool.
   Bytes of an image in a text prompt are not a map entry; they are
   corruption, so they never travel.
5. **An empty or missing folder says so out loud.** No context is a
   stated reading (`brief_attachment`'s contract, generalised), never
   silence.

How it reaches a step: the step declares `project_context` in its own
manifest's `interface.inputs` (the same declaration route the brief
takes - `PROCESS_LEVEL_INPUTS` in `run_pipeline.py`, restored BY NAME
around projection so an allow-list neither has to list it nor can drop
it). `project_context_for_step` appends the learnings routed to that
step (`learned_context.render_for_prompt`), so one key carries both
halves of what the project knows and each half says who said it.

Deliberately NOT in `brief_reference.REFERENCED_INPUTS`: that tuple is
the harness-restore enumeration (a file-blind harness gets the document
whole), and inlining whole folder bodies under `api` would rebuild per
file the problem the brief rule solved once. Small files are already
inline under every harness; large ones stay a map with an honest
header.

`tests/unit/captions/test_project_context.py`.
"""

from __future__ import annotations

import os

#: The manifest input name a step declares to receive the map.
CONTEXT_INPUT = "project_context"

# The brief's bar, reused rather than retuned: a body under it costs
# about what describing it costs, whatever document it came from.
try:
    from library.tools.brief_reference import INLINE_WHEN_UNDER_BYTES
except Exception:  # noqa: BLE001 - standalone use; the value is the rule
    INLINE_WHEN_UNDER_BYTES = 400

MARKDOWN_EXTS = frozenset({".md", ".markdown", ".mdown"})
TEXT_EXTS = frozenset({".txt", ".md", ".markdown", ".mdown", ".yaml",
                       ".yml", ".json", ".csv", ".url", ".link"})

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"\xff\xd8\xff", "JPEG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"RIFF", "WebP"),
    (b"\x00\x00\x01\x00", "ICO"),
    (b"BM", "BMP"),
)


def context_dir(project_folder: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout
    return str(ProjectLayout(project_folder).read_dir(Area.CONTEXT))


def _image_format(head: bytes, ext: str) -> str | None:
    for magic, name in _IMAGE_MAGIC:
        if head.startswith(magic):
            return name
    if ext.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
                       ".heic", ".tiff", ".bmp", ".ico"):
        return ext.lower().lstrip(".").upper()
    return None


def scan_context(project_folder: str) -> list:
    """Every file in the captain's context folder, sorted by name.

    Returns `[{name, abspath, bytes, kind}]` where kind is one of
    `markdown`, `text`, `image`, `other`. Dotfiles and directories are
    skipped - the folder is the captain's desk, not an archive, and a
    `.DS_Store` is not context. Missing folder reads as no files, the
    way a project with no `music/` simply has no music. An EMPTY
    project folder reads the same way: `gather_step_inputs` promises
    the context is a stated absence with no missing-file case to raise
    on, and `ProjectLayout` deliberately refuses an empty folder - so
    the refusal is checked HERE, before the layout is ever asked, and
    the map states the absence instead of raising it.
    """
    if not project_folder or not str(project_folder).strip():
        return []
    base = context_dir(project_folder)
    found = []
    if not os.path.isdir(base):
        return found
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            ext = os.path.splitext(name)[1]
            with open(path, "rb") as handle:
                head = handle.read(16)
            image = _image_format(head, ext)
            if image:
                kind = "image"
            elif ext.lower() in MARKDOWN_EXTS:
                kind = "markdown"
            elif ext.lower() in TEXT_EXTS:
                kind = "text"
            else:
                kind = "other"
            found.append({
                "name": os.path.relpath(path, base),
                "abspath": os.path.abspath(path),
                "bytes": size,
                "kind": kind,
                "image_format": image or "",
            })
    return found


def folder_bytes(project_folder: str) -> int:
    """The folder's full weight - the number the map is measured against."""
    return sum(f["bytes"] for f in scan_context(project_folder))


def _lede(lines: list) -> str:
    from library.tools.brief_reference import _lede as _brief_lede
    return _brief_lede(lines)


def _file_block(entry: dict) -> list:
    """One file's map block. Small files inline whole (clause 1);
    markdown over the bar maps its sections (clause 2); long text maps
    a lede (clause 3); images list (clause 4)."""
    from library.tools.brief_reference import (
        _shell_quote, is_inline, parse_sections)
    lines = [
        f"--- context/{entry['name']} "
        f"[{entry['bytes']:,} B, {entry['kind']}] ---",
        f"  FILE: {entry['abspath']}",
    ]
    if entry["kind"] == "image":
        lines.append(f"  An image ({entry['image_format'] or 'image'}, "
                     f"{entry['bytes']:,} B). Open it with an image-capable "
                     f"tool; its bytes are not in this prompt.")
        return lines
    try:
        with open(entry["abspath"], "r", encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, UnicodeDecodeError):
        lines.append("  Could not be read as text; open it by hand if you "
                     "need it.")
        return lines
    if entry["bytes"] < INLINE_WHEN_UNDER_BYTES:
        lines.append(f"  (inline in full below - under "
                     f"{INLINE_WHEN_UNDER_BYTES} B)")
        lines.append("")
        lines.append(content.strip())
        return lines
    if entry["kind"] == "markdown":
        _, sections = parse_sections(content)
        if not sections:
            lines.append("  (no sections; read the file whole - "
                         f"{_shell_quote(entry['abspath'])})")
            return lines
        for s in sections:
            lines.append(f"  ## {s.title}  [{s.body_bytes:,} B, lines "
                         f"{s.start_line}-{s.end_line}]")
            if s.lede:
                lines.append(f"      {s.lede}")
            for sub in s.subheadings:
                lines.append(f"      - {sub}")
        lines.append("")
        lines.append("  Read a section whole before deciding anything that "
                     "turns on it:")
        lines.append(f"    sed -n '{sections[0].start_line},"
                     f"{sections[0].end_line}p' "
                     f"{_shell_quote(entry['abspath'])}")
        inlined = [s for s in sections if is_inline(s, set())[0]]
        if inlined:
            lines.append("  Carried in full (short enough to quote):")
            for s in inlined:
                lines.append(s.text.strip())
        return lines
    body_lines = content.split("\n")
    lines.append(f"  {len(body_lines):,} lines. "
                 f"First line: {_lede(body_lines) or '(empty)'}")
    lines.append(f"  Read it whole with sed -n '1,{len(body_lines)}p' "
                 f"{_shell_quote(entry['abspath'])}")
    return lines


def build_context_map(project_folder: str) -> str:
    """The folder as the model reads it: a map, not a copy. Bodies under
    the inline bar travel whole; everything else travels as a heading, a
    size and a line range the model's shell can follow."""
    from library.tools.brief_reference import _shell_quote  # noqa: F401
    entries = scan_context(project_folder)
    total = sum(e["bytes"] for e in entries)
    out = [
        "The project's context folder is NOT copied into this prompt.",
    ]
    if not entries:
        out += [
            "This project has no context files: no `context/` folder, or "
            "nothing in it. That absence is stated here so it reads as a "
            "fact rather than as a folder you were not told about.",
            "",
        ]
    else:
        out += [
            f"It holds {len(entries)} file(s), {total:,} bytes, and what "
            f"follows is a MAP of it, not its contents: read a body with "
            f"your shell when the map says you need it, and decide from "
            f"the map alone when it does not.",
            "",
            f"  FOLDER: {os.path.abspath(context_dir(project_folder))}",
            "",
            "The map below is a map, not a summary: nothing has been "
            "withheld or rewritten - every byte is at its FILE path.",
            "",
        ]
        for entry in entries:
            out.extend(_file_block(entry))
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def project_context_for_step(project_folder: str, node_id: str) -> str:
    """The one key a declaring step receives: the folder map plus the
    learnings routed to that step. Each half says who said it - the
    captain's folder, the pipeline's write-back - so the model (and the
    captain reading the prompt archive) never confuses the two."""
    from library.tools import learned_context
    context_map = build_context_map(project_folder)
    learnings = learned_context.render_for_prompt(project_folder, node_id)
    if learnings:
        return (context_map + "\n--- what the project already learned ---\n"
                + learnings)
    return (context_map + "\nNo learnings are routed to this step: the "
            "project has learned nothing you are scoped to read, or "
            "nothing at all. Stated, not hidden.")


def map_path_for(built: str, filename: str) -> str:
    """The FILE path a map block carries for `filename`, or "".

    Reads the same line the model reads, the way
    `brief_reference.reference_path` does - so a test that follows it
    follows exactly what the model is given."""
    want = f"--- context/{filename} "
    in_block = False
    for line in built.split("\n"):
        if line.startswith("--- context/"):
            in_block = line.startswith(want)
            continue
        if in_block and line.strip().startswith("FILE:"):
            return line.strip()[len("FILE:"):].strip()
    return ""


def map_range_for(built: str, filename: str, section: str) -> str:
    """The `a,b` line range a map block carries for `section`, or ""."""
    import re
    want = f"--- context/{filename} "
    in_block = False
    for line in built.split("\n"):
        if line.startswith("--- context/"):
            in_block = line.startswith(want)
            continue
        if not in_block:
            continue
        if line.strip().startswith("## "):
            title = line.strip()[3:].split("  [")[0].strip()
            if title == section:
                m = re.search(r"lines (\d+)-(\d+)", line)
                if m:
                    return f"{m.group(1)},{m.group(2)}"
    return ""
