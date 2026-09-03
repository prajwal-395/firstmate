"""The vision pass's per-clip analysis as ONE document, reached by path.

Step 3.02 `select_broll` carried **three views of one vision analysis**
in a single prompt.  Measured on 001's frozen snapshot at 75d3e84, over
a context of 88,475 B:

    semantic_analysis_documents   35,813 B   40.5%
    picture                       10,250 B   11.6%
    broll_candidates_toon          7,613 B    8.6%
    ---------------------------------------------
                                  53,676 B   60.7%   of 88,475 B

After: 57,169 B, and the structure is at a path.  With #340's frame
strips also in the context, 94,994 B -> 63,688 B: the collapse is what
pays for putting a picture in front of the step that chooses one.

    picture                       10,250 B   17.9%
    broll_candidates_toon          7,613 B   13.3%
    footage_analysis_reference     4,507 B    7.9%
    ---------------------------------------------
                                  22,370 B   39.1%

and the share had grown, not shrunk: #295 stopped copying the captain's
brief into the prompt, so the denominator fell faster than the
duplication did.  This is the step whose cutaway choices the captain
complained about, and it is the step about to be handed a real frame.

## What each of the three carries, and why only one of them moves

`broll_candidates_toon` is the pre-bridge's table and the handoff calls
it "the primary source of WHAT is in each clip".  Its `description` is
`vision_schema_adapter.scene_prose` - `scene[]` rendered - and its
`framing`/`stability`/`camera_move` are the `camera[]` summaries.  On
001 that rendering is LOSSLESS: no clip's scene prose reaches the 600
character cap, and 16 of 17 clips have exactly one `camera[]` segment.
It also carries two things the analysis has not got at all - the clip's
catalogue `duration_s`, and `used_as_aroll`.  It stays inline.

`view:picture` is `actions[]`: one row per observed action window, with
its own bounds, covering 95.0% of 001's footage where `scene[]` covers
46.4%.  It is also the view the ANSWER is resolved against -
`cutaway_window.choose_window` matches the model's `preferred_moment`
against `blocks[].visual` - so a model that cannot see it is writing a
moment into a matcher it cannot see.  It stays inline.

`semantic_analysis_documents` is the raw structure both of those were
rendered from, and it is 40.5% of the prompt.  62.8% of its cells are
`objects[]` alone.  It is also the only one of the three keyed by the
document's own file stem (`IMG_1806_v3`) rather than the catalogue id
(`clip_001`) every other table in the context uses, so it is the one
view the model cannot join to the others without a mapping.  It moves.

## What moving it must not lose

The handoff tells the model to read it, twice: *"read these when a
candidate row is not enough to choose a sub-range"*, and Step C's *"use
the scene segment bounds ... and the per-range `camera[]` entries"*.
So it is not deleted.  Every byte of it is written to the step's own
directory and reached by a line range, through the mechanism #295 built
for the brief and #299 applied to the SFX catalogue - the same rule,
the same map, the same clause 5.  Nothing is filtered, ranked,
shortlisted or summarised away.

Four things really are only in here, and the map's per-clip lede is
sized to say which clips have them:

  * `objects[]` past the four labels the table's `subjects` column
    keeps - 159 objects on 001, of which 68 reach the table - with the
    role, the category, the time ranges and any `readable_text`;
  * `camera[]`'s per-segment TIME BOUNDS, which the table's deduped
    `wide -> close-up` summary does not carry;
  * `scene[]` as structure - location, type, lighting and
    notable_features as fields rather than as one prose line;
  * the assessment fields the table has no column for:
    `interest_score`, `keywords`, `clip_type`, `primary_subject_visible`
    and `usable_ranges_method`.

## The shape

One `##` section per clip, titled with the CATALOGUE clip id - the
exact string an answer has to name, and the id `broll_candidates_toon`
and `view:picture` are keyed by, so following the map lands in the
same vocabulary the rest of the context speaks.  A document that joins
to no routed clip list keeps its own id and is NAMED as such, the rule
`context_views._picture` already follows.

The section opens with its measured facts on one line, so `##`-splitting
alone gives the map a per-clip lede saying how much is behind the range.

Two shapes inside a section are chosen for the MAP rather than for the
page.  Nothing below a `##` is a heading, because `parse_sections`
collects every deeper heading into the map and four identical
subheadings per clip is 68 lines of the prompt saying nothing.  And the
identity line carries no underscore, because `brief_reference._lede`
strips markdown emphasis out of a lede and would turn `content_type`
into `contenttype` and `IMG_1806_v3` into `IMG1806v3` - a corrupted
identifier in the one line a model reads to decide whether to open the
section.  The vision pass's own id for a clip is inside the section,
where nothing rewrites it.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**A step may carry ONE reading of a measurement, or two on different axes - never the reading and the structure it was read from.**
The summary rule below says it for a rendered pair; this is the same rule when the second copy is the whole structure. [why](docs/RULE_EVIDENCE.md#three-views-of-one-analysis)
- **Establish what each view uniquely carries before deleting one**, and move the STRUCTURE rather than a rendering: the largest view is usually the one holding the per-segment bounds, the object labels and the assessment fields no table has a column for.
- The structure goes BY REFERENCE (`library/tools/footage_reference.footage_document`); nothing is filtered away and every byte is at the path.
- **Shape a referenced document for the MAP.** Nothing below a `##` may be a heading (`parse_sections` lifts deeper headings into the map), and the section's opening line carries no underscore or backtick (`_lede` strips markdown emphasis).
- `tests/test_broll_context_share.py` guards the RATIO of what the prompt spends on readings to what the structure costs inline, because that number does not depend on a fixture.
- **Measure a value where the model READS it** - the whole assembled prompt, pre-bridge included - not on the one route it used to arrive by.
- **Collapsing a structure into a summary makes the summary's blank cells load-bearing.** All three states - measured and usable, measured and unusable, never measured - must be legible in the cell itself.
"""

from __future__ import annotations

from library.tools.semantic_index import build_semantic_lookup
from library.tools.vision_schema_adapter import (
    UNMEASURED_SUMMARY,
    adapt_semantic_document,
    format_ranges,
    usable_ranges_summary,
)

DOCUMENT_NAME = "footage_analysis.md"

# The two sentences `build_reference` says about WHICH document this is.
# Everything else about the reference - the map, the ranges, the inline
# clauses, clause 5 - is the same for any markdown document.
REFERENCE_DOCUMENT_NAME = "The vision pass's own per-clip footage analysis"
REFERENCE_WHY = (
    "the candidate table and the observed-action rows in this prompt are "
    "already renderings of it, and most choices need only the clips you "
    "have shortlisted"
)


def _fmt_range(start, end) -> str:
    """`[0.0-3.5s]`, or `[unbounded]` when the record carries no time."""
    try:
        return f"[{float(start):.1f}-{float(end):.1f}s]"
    except (TypeError, ValueError):
        return "[unbounded]"


def _appearances(obj: dict) -> str:
    spans = obj.get("appearances") or obj.get("time_ranges") or []
    out = []
    for span in spans:
        if isinstance(span, (list, tuple)) and len(span) >= 2:
            out.append(_fmt_range(span[0], span[1]))
        elif isinstance(span, dict):
            out.append(_fmt_range(span.get("start"), span.get("end")))
    return " ".join(out)


def _identity_line(doc: dict) -> str:
    """The measured facts, on the line the map lifts as this clip's lede.

    Counts rather than contents: the map's job is to say whether a
    section is worth opening, and "18 objects" answers that where three
    of the eighteen labels would not.  No underscore and no backtick -
    see the module docstring.
    """
    assessment = doc.get("assessment") or {}
    scene = doc.get("scene") or []
    camera = doc.get("camera") or []
    objects = doc.get("objects") or []
    content = str(assessment.get("content_type") or "").replace("_", " ")
    parts = [
        f"content type {content or 'unrecorded'}",
        f"{len(scene)} scene segment{'' if len(scene) == 1 else 's'}",
        f"{len(camera)} camera segment{'' if len(camera) == 1 else 's'}",
        f"{len(objects)} object{'' if len(objects) == 1 else 's'}",
        ("usable range measured" if _has_measured_usable_range(assessment)
         else "usable range unmeasured"),
    ]
    return " | ".join(parts)


def _has_measured_usable_range(assessment: dict) -> bool:
    """Whether a usable range was MEASURED, read through its METHOD.

    AGENTS.md 10.3: `[[0, duration]]` beside `usable_ranges_method:
    "unmeasured"` is a default wearing a measurement's clothes, and
    `usable_ranges_summary` is the read that already knows it.
    """
    summary = usable_ranges_summary(assessment)
    return bool(summary) and not summary.startswith(UNMEASURED_SUMMARY)


def _scene_lines(doc: dict) -> list:
    out = []
    for seg in doc.get("scene") or []:
        if not isinstance(seg, dict):
            continue
        fields = [f"{k}: {seg[k]}" for k in ("location", "type", "lighting")
                  if seg.get(k)]
        features = [str(f) for f in (seg.get("notable_features") or []) if f]
        if features:
            fields.append("notable_features: " + "; ".join(features))
        out.append(f"- {_fmt_range(seg.get('start'), seg.get('end'))} "
                   + " | ".join(fields))
    return out


def _camera_lines(doc: dict) -> list:
    out = []
    for seg in doc.get("camera") or []:
        if not isinstance(seg, dict):
            continue
        fields = [f"{k}: {seg[k]}"
                  for k in ("framing", "mode", "stability", "movement")
                  if seg.get(k)]
        out.append(f"- {_fmt_range(seg.get('start'), seg.get('end'))} "
                   + " | ".join(fields))
    return out


def _object_lines(doc: dict) -> list:
    out = []
    for obj in doc.get("objects") or []:
        if not isinstance(obj, dict) or not obj.get("label"):
            continue
        fields = []
        for key in ("role", "category"):
            if obj.get(key):
                fields.append(f"{key}: {obj[key]}")
        spans = _appearances(obj)
        if spans:
            fields.append(f"seen {spans}")
        if obj.get("readable_text"):
            fields.append(f'reads "{obj["readable_text"]}"')
        line = f"- {obj['label']}"
        if fields:
            line += " - " + " | ".join(fields)
        out.append(line)
    return out


# The order the assessment is rendered in - the fields a cutaway choice
# turns on, first. It is an ORDER, not a filter: every other key the
# assessment carries follows, so a field the vision pass starts recording
# reaches the document without anyone editing this list. A whitelist here
# would be the thing the preamble says this document is not.
_ASSESSMENT_ORDER = (
    "content_type", "clip_type", "keywords", "interest_score",
    "usable_ranges", "usable_ranges_method", "usable_ranges_signals",
    "unusable_ranges", "camera_stability", "primary_subject_visible",
)


def _assessment_lines(doc: dict) -> list:
    """The assessment, read the way AGENTS.md 10.3 says to read it.

    Three distinctions this must not flatten. An ABSENT key simply has
    no line. A `None` reads "not answered" - the model omitted it. An
    empty list stays `[]`, which is the model answering that there are
    none; wording it as prose here would state that meaning for every
    list, including the ones where it is not what empty means.

    And `usable_ranges` is rendered through `usable_ranges_summary`,
    never through the raw pairs: a stored `[[0, duration]]` beside
    `usable_ranges_method: "unmeasured"` is the contradiction 10.3 says
    the DISPLAY resolves by reading the method, and this is a display.
    """
    assessment = doc.get("assessment") or {}
    ordered = [k for k in _ASSESSMENT_ORDER if k in assessment]
    ordered += [k for k in assessment if k not in _ASSESSMENT_ORDER]
    out = []
    for key in ordered:
        value = assessment[key]
        if key == "usable_ranges":
            value = usable_ranges_summary(assessment) or "none"
        elif key == "unusable_ranges":
            # A pair of numbers flattened by `join` reads as one range
            # whatever the list held; these are bounds a cutaway is cut
            # against, so they are rendered as ranges.
            value = format_ranges(value) or "[]"
        elif value is None:
            value = "not answered"
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value) if value else "[]"
        out.append(f"- {key}: {value}")
    return out


def footage_document(documents, catalog_entries=()) -> str:
    """Every per-clip vision document, in full, as one markdown document.

    `catalog_entries` is what the CATALOGUE ids are read off - the same
    join `semantic_index` and `context_views` use, because a section
    titled with an id the rest of the context does not use is a section
    the model cannot look up.
    """
    lookup = build_semantic_lookup(documents, list(catalog_entries or []))

    docs = documents
    if isinstance(docs, dict):
        docs = docs.get("semantic_analysis_documents", list(docs.values()))
    if not isinstance(docs, list):
        docs = []

    joined_by_own_id = {
        str(d.get("clip_id")): cid
        for cid, d in lookup.items()
        if isinstance(d, dict) and d.get("clip_id")
    }

    sections, unjoined = [], []
    for raw in docs:
        if not isinstance(raw, dict):
            continue
        own_id = str(raw.get("clip_id") or "")
        clip_id = joined_by_own_id.get(own_id, own_id)
        if joined_by_own_id and own_id and own_id not in joined_by_own_id:
            unjoined.append(own_id)
        if not clip_id:
            continue
        doc = adapt_semantic_document(raw)
        body = [f"## {clip_id}", _identity_line(doc), ""]
        if own_id and own_id != clip_id:
            body.append(f"The vision pass's own id for this clip is {own_id}, "
                        f"and its usable range and content type are the "
                        f"`usable_range` and `content_type` of this clip's "
                        f"row in `broll_candidates_toon`.")
            body.append("")
        for title, lines in (
            ("Where it is, per scene segment:", _scene_lines(doc)),
            ("How it is shot, per camera segment:", _camera_lines(doc)),
            ("What is in shot, every object the pass recorded:",
             _object_lines(doc)),
            ("What the assessment recorded:", _assessment_lines(doc)),
        ):
            body.append(title)
            # An absence is STATED. A missing label reads as "the map had
            # no room for it", which is a different thing from "the
            # vision pass measured none".
            body.extend(lines or ["- the vision pass recorded none"])
            body.append("")
        sections.append("\n".join(body))

    out = [
        "# The vision pass's per-clip analysis",
        "",
        f"{len(sections)} clip{'' if len(sections) == 1 else 's'}, one "
        f"section each, in the order the analysis was written. Every scene "
        f"segment, every camera segment, every object and every field of "
        f"the assessment is here in full - nothing is filtered, ranked, "
        f"shortlisted or truncated.",
        "",
        "Each heading is the clip id the rest of your prompt uses, so a "
        "row of `broll_candidates_toon` or of the observed-action rows "
        "leads straight to the section that expands it.",
        "",
        "`broll_candidates_toon` is a rendering of the scene and camera "
        "segments below, flattened to one row per clip; the observed-action "
        "rows are a rendering of the action windows. Come here for what "
        "those flatten away: the per-segment time bounds, every object "
        "rather than the first four, and the assessment fields the table "
        "has no column for.",
        "",
    ]
    if unjoined:
        out.append(
            f"{len(unjoined)} clip(s) are titled with the vision pass's own "
            f"id because no routed clip list names them: "
            + ", ".join(sorted(unjoined)))
        out.append("")
    out.extend(sections)
    return "\n".join(out).rstrip() + "\n"
