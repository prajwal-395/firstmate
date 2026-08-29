"""The channel creative brief reaches a step as a REFERENCE, not a copy.

`#256` wired the captain's channel brief into seven prompts on
2026-08-28 and it immediately became the largest single item in the
pipeline: 37.0%-84.3% of those seven contexts, 337,099 bytes per run,
46.9% of every byte the nine replayable steps send.  The step that picks
the bed under the whole video saw **84.3% brief, 11.7% creative
direction, 3.9% candidate data** - twenty-two times more brief than
material to choose from.  41.9% of the document (19,931 of 47,513 bytes)
is in sections no LLM planning step can act on: thumbnails, posting
cadence, the portfolio table, per-series typography, naming, growth.

The captain's ruling, 2026-08-28: *"we should just have the brief for
the LLM to be able to reference if it needs it... we can just tell it if
you need to reference something again you can look at this file and it
can grep and search."*

The models that answer this pipeline have a shell.  The degradation
investigation proved it on the run of record: the answering agent read
repository source, ran the aligner and measured audio files with ffmpeg.
A model that can run ffmpeg can read a file.

## The rule

**The map is always inline; a body is inline only when the map cannot
stand in for it.**  Applied per SECTION, in order, and to any document -
not per step, and not by a hand-tuned list of headings:

1. **The preamble is inline.**  Everything before the first heading is
   the document's own statement of what it is, and it is what tells the
   model whether to read on.
2. **A section whose body is shorter than `INLINE_WHEN_UNDER_BYTES` is
   inline.**  Quoting it costs about what describing it costs, so
   describing it is the worse trade.  This is byte economics, not taste.
3. **A section the PROJECT pins is inline.**  `pipeline.creative_brief_inline`
   in `project.yaml` names headings.  The judgement of which sections are
   about *this video* belongs to whoever owns the video; the engine does
   not guess it, and there is no default list.
4. **Everything else is a heading, a byte size, a LINE RANGE and a lede.**
   Nothing is filtered and nothing is summarised away - the whole
   document stays reachable, and the map says exactly where.

And one clause that is about the reader rather than the document:

5. **A harness that cannot read a file gets the document whole.**  A
   route the model cannot follow is a loss, not a saving.  `HARNESS_READS_FILES`
   is that enumeration and it is complete; an unknown harness raises,
   because a harness whose reach nobody has established is not a harness
   known to reach.

The line range is the load-bearing affordance.  `sed -n '19,44p' <file>`
is one command, exact, and costs the model nothing to get right - which
is the difference between a path a model *could* follow and one it does.

Nothing here decides anything creative.  The brief is prompt-only: no
`bridge.py`, no `step.py` and no post-bridge reads `creative_brief`, so
reshaping it changes what a model READS and never what the pipeline
computes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


# A section this small costs about as much to quote as to describe, so
# it is quoted.  A MECHANICAL threshold - the cost of a map entry in
# bytes - never a judgement about what the section says.
INLINE_WHEN_UNDER_BYTES = 400

# How much of a section's first real line travels as its lede.  Long
# enough to say what the section is about, short enough that fifteen of
# them are a map rather than a second document.
LEDE_CHARS = 160

# Which harnesses can follow a path.  A COMPLETE enumeration: an unknown
# name raises rather than being assumed either way.
#
# - `agy`: the request is a file on disk answered by an agent with a
#   shell.  Proven on the run of record - the answering agent read
#   repository source and ran ffmpeg (see the degradation report §1).
# - `mock`: replays a recorded answer.  No model runs, so the shape of
#   the brief reaches nobody; it is listed as reaching because a recorded
#   answer is unaffected either way.
# - `api`: `llm_client.LLMClient.generate` posts one string to an HTTP
#   endpoint and returns the reply.  There is no tool loop and no
#   filesystem on the other side, so a path is a dead end.
HARNESS_READS_FILES = {
    "agy": True,
    "mock": True,
    "api": False,
}

WITHDRAWN_HARNESS_ASSUMPTIONS = {
    "any harness with a capable model": (
        "Capability is a property of the HARNESS, not of the model. The same "
        "model reaches a file under `agy` and cannot under `api`, because "
        "`api` gives it no tool with which to try."
    ),
}


class UnknownHarness(ValueError):
    """A harness whose file reach has not been established."""


def harness_reads_files(harness: str) -> bool:
    """True when `harness` can follow a path out of its own prompt."""
    if harness not in HARNESS_READS_FILES:
        raise UnknownHarness(
            f"harness {harness!r} is not in HARNESS_READS_FILES "
            f"({sorted(HARNESS_READS_FILES)}). Establish whether it can read "
            f"a file from where a step runs and record the answer there - a "
            f"harness whose reach nobody has established is not a harness "
            f"known to reach."
        )
    return HARNESS_READS_FILES[harness]


@dataclass
class Section:
    """One heading of the document, with everything needed to fetch it."""
    level: int
    title: str
    start_line: int          # 1-based, inclusive - the heading itself
    end_line: int            # 1-based, inclusive
    body_bytes: int
    lede: str
    subheadings: list = field(default_factory=list)
    text: str = ""


PATH_MARKER = "FILE:"
_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def parse_sections(content: str, top_level: int = 2) -> tuple:
    """Split `content` into a preamble and its `top_level` sections.

    Returns `(preamble_text, [Section, ...])`.  Headings deeper than
    `top_level` become `subheadings` of the section they fall in, so the
    map names them without spending a size and a lede on each.
    """
    lines = content.split("\n")
    starts = [i for i, line in enumerate(lines)
              if (m := _HEADING.match(line)) and len(m.group(1)) == top_level]

    preamble = "\n".join(lines[:starts[0]]) if starts else content

    sections = []
    bounds = starts + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        body = "\n".join(lines[a:b])
        title = _HEADING.match(lines[a]).group(2)
        subs = [_HEADING.match(l).group(2) for l in lines[a + 1:b]
                if _HEADING.match(l)]
        sections.append(Section(
            level=top_level,
            title=title,
            start_line=a + 1,
            end_line=b,
            body_bytes=len(body.encode("utf-8")),
            lede=_lede(lines[a + 1:b]),
            subheadings=subs,
            text=body,
        ))
    return preamble, sections


def _lede(body_lines: list) -> str:
    """The section's first line of real prose, trimmed to LEDE_CHARS."""
    for line in body_lines:
        stripped = line.strip()
        if not stripped or _HEADING.match(line):
            continue
        stripped = re.sub(r"^[-*>]\s*", "", stripped)
        stripped = re.sub(r"[*_`]", "", stripped)
        if not stripped:
            continue
        if len(stripped) > LEDE_CHARS:
            stripped = stripped[:LEDE_CHARS - 1].rstrip() + "…"
        return stripped
    return ""


def is_inline(section: Section, pinned: set) -> tuple:
    """Clauses 2 and 3 of the rule, per section.

    Returns `(inline, reason)`.  The reason is carried into the map so a
    reader can see why a body is there and never has to guess.
    """
    if _normalise(section.title) in pinned:
        return True, "pinned by the project"
    if section.body_bytes < INLINE_WHEN_UNDER_BYTES:
        return True, f"under {INLINE_WHEN_UNDER_BYTES} B"
    return False, ""


def _normalise(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def build_reference(path: str, content: str, pinned=(), harness: str = "agy") -> str:
    """The text a step receives in place of the brief's 47,903 bytes.

    `path` is recorded exactly as the step will have to use it, so it is
    the caller's job to hand an ABSOLUTE one.  A harness that cannot read
    a file (clause 5) gets `content` back unchanged, with a line saying
    why - so a prompt never claims a route it does not have.
    """
    if not harness_reads_files(harness):
        return content

    pinned = {_normalise(p) for p in pinned}
    preamble, sections = parse_sections(content)
    total = len(content.encode("utf-8"))
    lines_total = content.count("\n") + 1

    out = [
        f"The captain's channel creative brief is NOT copied into this prompt.",
        f"It is {total:,} bytes across {lines_total:,} lines, and most of it is "
        f"about the channel rather than about this video.",
        "",
        f"  {PATH_MARKER} {path}",
        "",
        "Read any section you need, in full, before you decide anything that "
        "turns on it.",
        "You have a shell. Each heading below carries the line range that "
        "holds it:",
        "",
        f"    sed -n '{_example_range(sections)}p' {_shell_quote(path)}",
        "",
        "or grep the file for a phrase. The map below is a map, not a "
        "summary: it is",
        "there so you can tell whether you need a section, not so you can "
        "answer instead",
        "of reading one. Nothing has been withheld or rewritten - every byte "
        "of the",
        "document is at that path.",
        "",
    ]

    if preamble.strip():
        out.append("--- the document's own opening, in full ---")
        out.append(preamble.strip())
        out.append("")

    out.append("--- sections ---")
    for s in sections:
        inline, reason = is_inline(s, pinned)
        head = (f"{'#' * s.level} {s.title}  "
                f"[{s.body_bytes:,} B, lines {s.start_line}-{s.end_line}]")
        out.append(head)
        if s.lede:
            out.append(f"    {s.lede}")
        for sub in s.subheadings:
            out.append(f"    - {sub}")
        if inline:
            out.append(f"    (inline in full below - {reason})")
        out.append("")

    inlined = [s for s in sections if is_inline(s, pinned)[0]]
    if inlined:
        out.append("--- sections carried in full ---")
        for s in inlined:
            out.append(s.text.strip())
            out.append("")

    return "\n".join(out).rstrip() + "\n"


def _example_range(sections: list) -> str:
    """A REAL range out of this document, so the example command works."""
    if not sections:
        return "1,40"
    s = sections[0]
    return f"{s.start_line},{s.end_line}"


def _shell_quote(path: str) -> str:
    """POSIX single-quoting, because the captain's planning tree has an
    apostrophe in it (`series portfolio '26 planning`) and a command the
    model cannot paste is not an affordance."""
    return "'" + path.replace("'", "'\\''") + "'"


def reference_path(text: str) -> str:
    """The path out of a reference built by `build_reference`, or "".

    The same line the model reads, read the same way, so a test that
    follows it is following exactly what the model is given.  Returns ""
    for a whole document carried inline (clause 5), which has no marker.
    """
    for line in text.split("\n", 40)[:40]:
        stripped = line.strip()
        if stripped.startswith(PATH_MARKER):
            return stripped[len(PATH_MARKER):].strip()
    return ""


def project_pinned_sections(project_folder: str) -> list:
    """Headings the project pins inline, off `pipeline.creative_brief_inline`.

    Clause 3.  A project that declares none pins none; there is no
    default list, because a default list would be the engine deciding
    which parts of the captain's document are about their video.
    """
    if not project_folder:
        return []
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return []
    try:
        import yaml
        with open(project_yaml, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return []
    declared = (data.get("creative_brief_inline")
                or (data.get("pipeline") or {}).get("creative_brief_inline")
                or [])
    if isinstance(declared, str):
        declared = [declared]
    if not isinstance(declared, list):
        raise ValueError(
            f"{project_yaml}: pipeline.creative_brief_inline must be a list "
            f"of headings, got {type(declared).__name__}."
        )
    return [str(d) for d in declared]
