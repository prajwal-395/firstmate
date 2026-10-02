"""A large document reaches a step as a REFERENCE, not as a copy.

The models that answer this pipeline have a shell, so a model can read a
file; the brief is there for the LLM to reference if it needs it
(captain, 2026-08-28).

## The rule

**The map is always inline; a body is inline only when the map cannot
stand in for it.**  Applied per SECTION, in order, and to any document -
not per step, and not by a hand-tuned list of headings (`is_inline`,
`build_reference`):

1. **The preamble is inline.**  Everything before the first heading is
   the document's own statement of what it is.
2. **A section whose body is shorter than `INLINE_WHEN_UNDER_BYTES` is
   inline.**  This is byte economics, not taste.
3. **A section the PROJECT pins is inline.**  `pipeline.creative_brief_inline`
   in `project.yaml` names headings (`project_pinned_sections`).  There is
   no default list.
4. **Everything else is a heading, a byte size, a LINE RANGE and a lede.**
   Nothing is filtered and nothing is summarised away.

And one clause about the reader:

5. **A harness that cannot read a file gets the document whole.**
   `HARNESS_READS_FILES` is that enumeration and it is complete; an
   unknown harness raises `UnknownHarness`.  `restore_for_harness` is
   the restore.

The line range is the load-bearing affordance: `sed -n '19,44p' <file>`
is one command, exact.

Nothing here decides anything creative.  The brief is prompt-only, so
reshaping it changes what a model READS and never what the pipeline
computes.  `series_membership_line` adds one factual line naming the
series the project declares (`project_series_identity`, `SERIES_KEY`).

## The documents it carries

`REFERENCED_INPUTS` is the enumeration, and it is what
`run_pipeline.present_llm_step` walks when clause 5 has to put one back
inline:

  * `creative_brief` - the captain's channel brief.
  * `footage_analysis_reference` - step 3.02's per-clip vision analysis;
    step 3.02's `bridge.py` writes it; the shape is
    `footage_reference.footage_document`.
  * `sfx_catalog_reference` - step 4.04's SFX catalogue; step 4.04's
    `bridge.py` writes it; the shape is `sfx_library.catalog_document`,
    one `##` section per sound titled with the exact `sfx_id` an answer
    has to name.
  * `reel_diagnostics_reference` - step 3.04's per-candidate
    repeated-take evidence; step 3.04's `bridge.py` writes it; the shape
    is `reel_diagnostics_reference.diagnostics_document`, one `##`
    section per candidate titled with its own `start-end`.

**A second document costs a row here and nothing else.**  Only the two
sentences of header naming the document differ, and they are parameters
(`document_name`, `why_referenced`).


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**The captain's creative brief is one per-project declaration that reaches NINE steps, BY REFERENCE, WHEN THE PROJECT ATTACHES IT.**
`project.yaml`'s `creative_brief` - top level or under `pipeline:` - names a markdown file, and `pipeline.attach_creative_brief` is the three-state choice about sending it (§3, "A step with no creative brief ASKS").
`load_pipeline_state` reads the PATH into state ONLY when the reading is ATTACHED; `gather_step_inputs` reads the FILE and hands the step a REFERENCE to it, and only if the step's own manifest declares the input.
A path that cannot be read or is empty RAISES. **A run that attaches none INTERVIEWS rather than going quiet.**
- Seven of them - `creative_direction`, `speech_sequence`, `music_selection`, `select_broll`, `plan_transitions`, `plan_vfx` and `plan_sfx` - are the ones whose handoffs tell the model to read one. `tests/contracts/test_creative_brief_reaches_prompt.py` fails if a handoff documents a brief its manifest does not declare.
- **The eighth is `mesh_spine`, and it is declared without a handoff line.** A step gets the brief because its manifest asked, not because its prompt mentions one. **The ninth is `color_grade`**, added when 5.01 grew a handoff on 2026-09-03 - its new handoff DOES name the brief, so it is in the first group; 001's brief carries a whole "Color System Philosophy" section no colour step had ever seen. [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)
- It is not a `context_fields` entry. Like `brand_template` it is restored around the projection BY NAME, so a step's allow-list neither has to list it nor can drop it.
- **The cost is per step, not per run.** [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)

**A reference is an ABSOLUTE PATH plus a MAP, and the rule for what still travels inline is in `library/tools/brief_reference.py`.**
[why](docs/RULE_EVIDENCE.md#the-brief-was-copied-seven-times)
- **The map carries a LINE RANGE per heading**, so following it is one `sed -n 'a,bp'` and not a search.
- **The rule is per SECTION, not per step**, so every step sees the same document: the preamble inline, a section under `INLINE_WHEN_UNDER_BYTES` inline, everything else a heading, a size, a range and a lede. Nothing is filtered or summarised away - the whole document is at the path.
- **Which sections are about THIS video is not the engine's judgement.** A project pins sections inline with `pipeline.creative_brief_inline` in its `project.yaml`, and there is no default list.
- **The mechanism carries FOUR documents, and a fifth costs a row.** `brief_reference.REFERENCED_INPUTS` is that enumeration - the brief, step 4.04's SFX catalogue, step 3.02's per-clip vision analysis (`library/tools/footage_reference.py`) and step 3.04's per-candidate repeated-take evidence (`library/tools/reel_diagnostics_reference.py`). Do not build a second by-reference mechanism.
- **`HARNESS_READS_FILES` is a complete enumeration and an unknown harness raises.** `agent` and `mock` reach a file; `api` does not, so under `api` the document is carried whole - a route the model cannot follow is a loss, not a saving. `present_llm_step` does that restore.
- `tests/unit/context/test_brief_reference.py` FOLLOWS the reference rather than asserting its shape: it parses the path and the range out of the string the model reads and requires that what comes back was not in the prompt.

The byte measurements and the ruling behind this rule: docs/evidence/brief_reference.md.
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
# - `agent`: the request is a file on disk answered by an agent with a
#   shell.  Proven on the run of record - the answering agent read
#   repository source and ran ffmpeg (see the degradation report §1).
# - `mock`: replays a recorded answer.  No model runs, so the shape of
#   the brief reaches nobody; it is listed as reaching because a recorded
#   answer is unaffected either way.
# - `api`: the removed `llm_client.LLMClient.generate` posted one string
#   to an HTTP endpoint and returned the reply.  There was no tool loop
#   and no filesystem on the other side, so a path was a dead end.
HARNESS_READS_FILES = {
    "agent": True,
    "mock": True,
    "api": False,
}

WITHDRAWN_HARNESS_ASSUMPTIONS = {
    "any harness with a capable model": (
        "Capability is a property of the HARNESS, not of the model. The same "
        "model reaches a file under `agent` and cannot under `api`, because "
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


# What the brief is, in the two places the header names it.  A DEFAULT
# rather than a constant, because the rule above is about documents and
# the brief is the first one it was applied to - step 4.04's 44,575-byte
# SFX catalogue is the second (#299), and it is the same mechanism with
# its own two sentences rather than a second mechanism.
DEFAULT_DOCUMENT_NAME = "The captain's channel creative brief"
DEFAULT_WHY_REFERENCED = ("most of it is about the channel rather than "
                          "about this video")


def build_reference(path: str, content: str, pinned=(), harness: str = "agent",
                    document_name: str = DEFAULT_DOCUMENT_NAME,
                    why_referenced: str = DEFAULT_WHY_REFERENCED,
                    series_identity: str | None = None) -> str:
    """The text a step receives in place of the document's own bytes.

    `path` is recorded exactly as the step will have to use it, so it is
    the caller's job to hand an ABSOLUTE one.  A harness that cannot read
    a file (clause 5) gets `content` back unchanged, with a line saying
    why - so a prompt never claims a route it does not have.

    `document_name` and `why_referenced` are the only two things the
    header says about WHICH document this is; everything else - the map,
    the line ranges, the inline clauses - is the same for any markdown
    document with `##` sections.

    `series_identity` is the brief's membership anchor (#258) and only
    the brief carries one: the channel brief names several series, and
    without a line saying which one this video is (or that the project
    names none) the roster reads as a choice.  `None` means "not the
    brief" - the SFX catalogue and the footage analysis carry no such
    line.  Any string, including "", states the reading, so only the
    creative-brief call site passes it.
    """
    if not harness_reads_files(harness):
        if series_identity is None:
            return content
        # The whole document travels, so the header does not - but the
        # series anchor (#258) is not the header, it is the one line
        # that keeps the full roster from reading as a choice.
        return series_membership_line(series_identity) + "\n\n" + content

    pinned = {_normalise(p) for p in pinned}
    preamble, sections = parse_sections(content)
    total = len(content.encode("utf-8"))
    lines_total = content.count("\n") + 1

    out = [
        f"{document_name} is NOT copied into this prompt.",
        f"It is {total:,} bytes across {lines_total:,} lines, and "
        f"{why_referenced}.",
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

    if series_identity is not None:
        out.append(series_membership_line(series_identity))
        out.append("")

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


# ── Which series this video belongs to ─────────────────────────────

# Issue #258: the captain's channel brief names eight series, and the
# blind A/B showed the model reaching for one of them by name - "Through
# the 4th Wall", which is precisely the series 001 is not.  The brief
# improves the answer overall and simultaneously pulls toward the wrong
# creative world, because nothing in the run says which series (if any)
# this video is in.  The issue's own prescription is "at minimum a line
# in the project's own configuration stating which series it belongs to
# - or explicitly that it belongs to none yet", so that is what the
# engine carries: a project-declared identity, read here and stated in
# the reference header where the model reads the brief.  The engine
# chooses nothing - a project that declares none gets the absence said
# out loud, which leaves the model no roster entry to adopt.
SERIES_KEY = "series"


def project_series_identity(project_folder: str) -> str:
    """The series this project says its video belongs to, or "".

    Read off `series` at the top level or under `pipeline:` in
    `project.yaml` - the same two places `creative_brief` itself is read
    from, so a project does not memorise one home for one brief key and
    another for the rest.  A declaration that is not a string is
    malformed and raises, the way a malformed `creative_brief_inline`
    does: a list where a name should be is not a name the run can state.
    """
    if not project_folder:
        return ""
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return ""
    try:
        import yaml
        with open(project_yaml, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    declared = data.get(SERIES_KEY)
    if declared is None:
        pipeline = data.get("pipeline")
        if isinstance(pipeline, dict):
            declared = pipeline.get(SERIES_KEY)
    if declared is None:
        return ""
    if not isinstance(declared, str):
        raise ValueError(
            f"{project_yaml}: {SERIES_KEY} must be the name of the series "
            f"this video belongs to, got {type(declared).__name__}."
        )
    return declared.strip()


def series_membership_line(series_identity: str) -> str:
    """One factual line anchoring the video against the brief's roster.

    A membership statement, not a creative judgement: it says which
    named series this video is (or that the project names none), so the
    channel brief's list of series reads as background rather than as a
    choice.  Both readings refuse the failure #258 measured - presenting
    the video as belonging to a series the project never named.
    """
    name = (series_identity or "").strip()
    if name:
        return (
            f"This video belongs to the series {name!r}. The brief may "
            f"name other series as channel background; do not present "
            f"this video as one of them."
        )
    return (
        "The project names no series for this video. The brief may name "
        "several series as channel background; that roster is not a "
        "menu, so do not present this video as belonging to any of them "
        "by name."
    )


# ── Clause 5, applied ───────────────────────────────────────────────

REFERENCED_INPUTS = ("creative_brief", "sfx_catalog_reference",
                     "footage_analysis_reference",
                     "reel_diagnostics_reference")
"""Every step input that travels as a reference built here.

`present_llm_step` walks this to put a document back inline for a
harness that cannot follow a path.  An input NOT in this tuple is never
restored, so a step that happens to carry a `FILE:` line in some other
string is left alone.
"""


def restore_for_harness(inputs: dict, harness: str) -> tuple:
    """Carry every referenced document inline when the harness cannot read.

    Returns `(inputs, restored_keys)`.  `inputs` is copied only if
    something changed.  The path comes back out of the reference by
    reading the SAME line the model reads, so a harness that cannot
    follow it and a test that can are following exactly the same string.
    """
    if not harness or harness_reads_files(harness):
        return inputs, []

    restored = []
    for key in REFERENCED_INPUTS:
        value = inputs.get(key)
        if not isinstance(value, str) or not value:
            continue
        path = reference_path(value)
        if not path:
            continue
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if key == "creative_brief":
            # The whole document travels, so the header - and the series
            # anchor in it (#258) - does not.  State it ahead of the
            # document rather than lose it: under this harness the model
            # reads the full roster, which is where the pull toward a
            # named series was measured.
            content = (series_membership_line(project_series_identity(
                inputs.get("project_folder") or "")) + "\n\n" + content)
        if not restored:
            inputs = dict(inputs)
        inputs[key] = content
        restored.append(key)
    return inputs, restored
