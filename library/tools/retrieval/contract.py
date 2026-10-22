"""One logical retrieval contract for every footage entry point (R02).

There are several ways to ask this repo for footage - the text index
(`ren search`), the multi-lane `search_footage`, the planning slot
search, the world-model query surface, the resident worker - and each
one grew its own idea of which passes a question needs.  The measured
cost of that is in `search_footage`: it runs the text embedding pass
first on EVERY query, even one whose plan names no textual requirement
("applause after the speech" pays for a transcript ranking it cannot
use), and it runs that pass with no facet filters, so the deterministic
reduction other callers get for free never happens here.

This module is the one place that decision lives, WITHOUT forcing every
operation through one implementation.  Each entry point keeps its own
physical path; each one routes through here:

* `ENTRY_POINTS` - every logical entry point, its physical function,
  and the production callers that reach it.  A new caller is added
  here, or it is a caller this contract does not cover.
* `MODALITY_ROUTES` - the eight modalities the punchlist names (text,
  OCR, appearance, objects, action, audio, identity, temporal
  relations), each with the requirement keys that claim it, the lane
  that answers it, and whether that lane is a source-record LOOKUP or
  a semantic-similarity pass.
* `route_plan` - which lanes a decomposed plan needs, which it skips
  and why.  `search_footage` executes this decision; the other entry
  points consult it to stay consistent with each other.
* `RetrievalRequest` / `RetrievalEvidence` - the shared envelope.  Every
  route returns timestamped evidence carrying its uncertainty and its
  provenance, so equivalent requests from the CLI, the planner and the
  internal tools can be compared row for row.

The two pass classes are the whole cost model:

* LOOKUP - deterministic record access over what ingest already
  measured.  Facet `filter()` calls, the person roster, `verify_span`
  against the catalog, the OCR token join, the sound label join, the
  timeline itself.  No embedding, no ranking model, no floor.
* SEMANTIC - embedding similarity ranking.  The text index dense pass
  and the frame lane.  A query encode plus a ranked scan, priced by the
  candidates the facet filters let through - which is why the contract
  keeps deterministic filtering BEFORE expensive ranking, never after.

A lane that is skipped says so by name (`skipped_no_requirement`,
`skipped_no_visual_caller`, `refused`), and the text lane distinguishes
`primary` (the plan asks for words) from `backstop` (the plan does not,
but a requirement no other lane answered still could be).  A skipped
pass is observable in the `lanes` dict, so passes-per-query is measured
off the answer, not off a log line.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

# ── Lanes ─────────────────────────────────────────────────────────
#
# The same six lanes `search_footage` reports.  They are defined here so
# every entry point names the same lane the same way; `search` keeps
# aliases for compatibility.

LANE_TEXT = "text"
LANE_VISUAL = "visual"
LANE_OCR = "ocr"
LANE_SOUND = "sound"
LANE_TIME = "time"
LANE_RELATIONAL = "relational"

LANES = (LANE_TEXT, LANE_VISUAL, LANE_OCR, LANE_SOUND, LANE_TIME, LANE_RELATIONAL)
"""Every lane the contract routes to, in report order."""

# ── Pass classes ──────────────────────────────────────────────────

LOOKUP = "lookup"
SEMANTIC = "semantic"
"""Source-record lookup vs semantic-similarity retrieval.

A LOOKUP lane reads stored measurements deterministically: it either
matches or it does not, and it never invents a score.  A SEMANTIC lane
ranks by embedding similarity under a floor, and its answer carries the
floor and the band that decided each row.  OCR and sound are LOOKUPs:
the on-screen-text token join and the sound-event label join read what
ingest stored, with no encoder and no ranking model.
"""

LANE_PASS_CLASS = {
    LANE_TEXT: SEMANTIC,
    LANE_VISUAL: SEMANTIC,
    LANE_OCR: LOOKUP,
    LANE_SOUND: LOOKUP,
    LANE_TIME: LOOKUP,
    LANE_RELATIONAL: LOOKUP,
}
"""Which cost class each lane pays in."""

# ── Entry points ──────────────────────────────────────────────────

ENTRY_POINTS = {
    "text_index": {
        "function": "library.tools.analysis.footage_query.FootageIndex.search_report",
        "also": ("search", "ocr_search", "sound_search", "filter", "search_and_filter"),
        "production_callers": (
            "ren search (search_json CLI)",
            "library/tools/search_in_planning.py::slot_search",
            "library/tools/resident_query.py (resident worker)",
            "library/tools/qualification_workflow.py",
            "library/tools/retrieval_eval.py (ren eval-search harness)",
        ),
    },
    "multilane": {
        "function": "library.tools.retrieval.search.search_footage",
        "also": (),
        "production_callers": (
            "internal tools only; no pipeline step calls it yet - "
            "it executes this contract so the first caller inherits "
            "the routing instead of inventing it",
        ),
    },
    "planning_slot": {
        "function": "library.tools.search_in_planning.slot_search",
        "also": ("search_hints_for_blocks",),
        "production_callers": (
            "library/steps/step_3_02_select_broll/bridge.py",
            "library/steps/step_3_02_select_broll/post_bridge.py",
            "library/tools/picture_plan.py (search_hints)",
        ),
    },
    "world": {
        "function": "library.tools.world_model_query.World.search",
        "also": ("answer", "who_at", "what_at", "where_at", "entity", "entities"),
        "production_callers": (
            "its own CLI (world-model search --mode ...); "
            "no pipeline step calls it yet",
        ),
    },
    "frame": {
        "function": "library.tools.analysis.footage_frames.FrameIndex.search_ranges",
        "also": (),
        "production_callers": (
            "ren search --visual",
            "the multilane visual lane",
        ),
    },
}
"""Every logical retrieval entry point and who reaches it in production.

A caller that is not named here is not covered by this contract; name
it here when it starts routing through `route_plan`, not in a comment
beside its own call.
"""

# ── Modality routes ───────────────────────────────────────────────

MODALITY_ROUTES = {
    "text": {
        "requirement_keys": ("textual:speech_content",),
        "lane": LANE_TEXT,
        "pass_class": SEMANTIC,
        "answered_by": "the text index speech/chunk rows (words said)",
    },
    "ocr": {
        "requirement_keys": ("textual:on_screen_text",),
        "lane": LANE_OCR,
        "pass_class": LOOKUP,
        "answered_by": "the on-screen-text token join over step 1.07 "
        "strings (words written)",
    },
    "appearance": {
        "requirement_keys": ("visual:person", "visual:composition", "visual:scene"),
        "lane": LANE_VISUAL,
        "pass_class": SEMANTIC,
        "answered_by": "the frame lane (who is in the picture, how it "
        "is framed), backed by the text index "
        "scene/camera rows",
    },
    "objects": {
        "requirement_keys": ("visual:object", "visual:state"),
        "lane": LANE_VISUAL,
        "pass_class": SEMANTIC,
        "answered_by": "the frame lane for the thing seen, backed by "
        "the text index object rows",
    },
    "action": {
        "requirement_keys": ("visual:action",),
        "lane": LANE_VISUAL,
        "pass_class": SEMANTIC,
        "answered_by": "the text index action rows first (a still frame "
        "cannot establish a gesture, so the frame lane "
        "may refuse), backed by the frame lane",
    },
    "audio": {
        "requirement_keys": ("acoustic:sound_event", "acoustic:voice_character"),
        "lane": LANE_SOUND,
        "pass_class": LOOKUP,
        "answered_by": "the sound-event label join over measured M5 "
        "events (what is heard that is not speech)",
    },
    "identity": {
        "requirement_keys": ("relational:same_subject",),
        "lane": LANE_RELATIONAL,
        "pass_class": LOOKUP,
        "answered_by": "a NAMED project-graph query (reaction_shots, "
        "broll_matches, coverage), or the person facet "
        "filter for a roster name - never free text",
    },
    "temporal": {
        "requirement_keys": (
            "temporal:ordering",
            "temporal:duration",
            "temporal:repetition",
            "temporal:interval",
        ),
        "lane": LANE_TIME,
        "pass_class": LOOKUP,
        "answered_by": "the timeline itself (order, span, count) - no "
        "retrieval pass runs for WHEN",
    },
}
"""The eight modalities the punchlist names, and where each one goes.

Each entry names the decomposed requirement keys that claim the
modality, the lane that answers it, and whether answering costs a
semantic pass or a deterministic lookup.  Identity and temporal
relations are LOOKUPs on purpose: a name resolves against the roster
and a count resolves against the timeline, and neither is a question
an embedding answers.
"""

# The text index kinds that carry each lane's evidence.  The text lane
# returns rows for several lanes at once (the semantic-visual rows live
# in the same index as the transcript), so a requirement counts the
# rows of the kinds its lane owns.
LANE_TEXT_KINDS = {
    LANE_TEXT: ("speech", "chunk"),
    LANE_VISUAL: ("action", "scene", "camera", "object"),
    LANE_OCR: ("ocr",),
    LANE_SOUND: ("sound",),
}
"""Which text-index row kinds answer which lane."""

SKIP_NO_REQUIREMENT = "skipped_no_requirement"
SKIP_NO_VISUAL_CALLER = "skipped_no_visual_caller"
TEXT_PRIMARY = "primary"
TEXT_BACKSTOP = "backstop"
TEXT_SKIP = "skip"
"""Why a lane did not run, and in what capacity the text lane ran.

`primary` - the plan asks for words.  `backstop` - the plan does not,
but a mandatory requirement no other lane answered still could be, so
the text lane ran rather than report an unsearched requirement.
`skip` is recorded with the reason beside it, never silently.
"""


@dataclass(frozen=True)
class RetrievalRequest:
    """One question, in the form every entry point can share.

    `filters` are deterministic facets (clip_id, person, kinds, framing,
    ...) applied BEFORE any semantic ranking - the same mapping
    `FootageIndex.search_report` takes, so an equivalent request from
    the CLI, the planner and the internal tools names the same facets
    and gets the same candidates.  `lanes` optionally pins the route
    (a caller that already knows it only wants the sound lane says so);
    empty means `route_plan` decides from the query.
    """

    query: str
    top_k: int = 5
    mode: str = "hybrid"
    filters: Mapping | None = None
    lanes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    """What `route_plan` decided, and why each lane is in its state."""

    lanes_to_run: tuple[str, ...]
    skipped: dict = field(default_factory=dict)
    text_policy: str = TEXT_PRIMARY
    text_kinds: tuple[str, ...] | None = None

    def to_dict(self) -> dict:
        return {
            "lanes_to_run": list(self.lanes_to_run),
            "skipped": dict(self.skipped),
            "text_policy": self.text_policy,
            "text_kinds": list(self.text_kinds)
            if self.text_kinds is not None
            else None,
        }


def route_plan(plan, *, use_visual: bool = True) -> RouteDecision:
    """Which lanes a decomposed plan needs, and in what capacity.

    OCR, sound and visual run exactly when the plan names a mandatory
    requirement for them; time always runs (it is the timeline, not a
    pass); relational never runs as free text (it needs a named graph
    query).  The text policy is `primary` when the plan asks for words
    and `backstop` otherwise: the caller runs the decided lanes first,
    then asks `text_backstop_needed` whether the text lane still owes
    the plan anything.  A visual requirement with `use_visual=False`
    keeps its lane wanted but unrun, so the backstop sees it uncovered.
    """
    wanted = {
        requirement.lane for requirement in plan.requirements if requirement.mandatory
    }
    lanes_to_run: list[str] = []
    skipped: dict[str, str] = {}
    if LANE_OCR in wanted:
        lanes_to_run.append(LANE_OCR)
    else:
        skipped[LANE_OCR] = SKIP_NO_REQUIREMENT
    if LANE_SOUND in wanted:
        lanes_to_run.append(LANE_SOUND)
    else:
        skipped[LANE_SOUND] = SKIP_NO_REQUIREMENT
    if LANE_VISUAL in wanted:
        if use_visual:
            lanes_to_run.append(LANE_VISUAL)
        else:
            skipped[LANE_VISUAL] = SKIP_NO_VISUAL_CALLER
    else:
        skipped[LANE_VISUAL] = SKIP_NO_REQUIREMENT
    if LANE_TIME in wanted:
        lanes_to_run.append(LANE_TIME)
    if LANE_RELATIONAL in wanted:
        skipped[LANE_RELATIONAL] = (
            "the project graph answers named questions "
            "(reaction_shots, broll_matches, coverage), not free text; "
            "issue the named query for this relation"
        )
    text_policy = TEXT_PRIMARY if LANE_TEXT in wanted else TEXT_BACKSTOP
    return RouteDecision(
        lanes_to_run=tuple(lanes_to_run), skipped=skipped, text_policy=text_policy
    )


# Lanes whose requirements the text lane can cover when their own lane
# did not run: the semantic-visual rows live in the text index, and so
# do the fused OCR/sound signals.  Time and relational are not covered:
# the timeline answers WHEN without a retrieval pass, and a relation
# needs its named graph query however many transcript rows match.
BACKSTOP_COVERED_LANES = (LANE_TEXT, LANE_VISUAL, LANE_OCR, LANE_SOUND)
"""Lanes whose mandatory requirements the text backstop can still answer."""


def text_backstop_needed(plan, lanes_ran) -> tuple[bool, list[str]]:
    """Whether the text lane still owes the plan anything.

    True when a mandatory requirement names a lane the text index can
    cover (itself, or the visual/OCR/sound rows it carries) and that
    lane did not run.  Returns the requirement keys the backstop would
    cover, so the caller records WHAT the backstop ran for.
    """
    ran = set(lanes_ran)
    uncovered = [
        requirement.key
        for requirement in plan.requirements
        if requirement.mandatory
        and requirement.lane in BACKSTOP_COVERED_LANES
        and requirement.lane not in ran
    ]
    return bool(uncovered), uncovered


# ── The evidence envelope ─────────────────────────────────────────

EVIDENCE_FIELDS = (
    "clip_id",
    "start",
    "end",
    "kind",
    "text",
    "score",
    "uncertainty",
    "provenance",
)
"""Every route returns rows carrying these fields.

`start`/`end` are media seconds (the timestamps the row is placed by);
`uncertainty` says how sure the lane is (score band, floor, abstention
or refusal); `provenance` says which lane, which backend and which pass
class produced the row, so a reader can check it without hunting.
"""


@dataclass(frozen=True)
class RetrievalEvidence:
    """One timestamped piece of evidence, whatever lane produced it."""

    clip_id: str
    start: float | None
    end: float | None
    kind: str
    text: str = ""
    score: float | None = None
    uncertainty: Mapping | None = None
    provenance: Mapping | None = None

    def to_dict(self) -> dict:
        return {
            "clip_id": self.clip_id,
            "start": self.start,
            "end": self.end,
            "kind": self.kind,
            "text": self.text,
            "score": self.score,
            "uncertainty": dict(self.uncertainty or {}),
            "provenance": dict(self.provenance or {}),
        }


def evidence_from_row(
    row: Mapping,
    *,
    lane: str,
    backend: str | None = None,
    abstention: Mapping | None = None,
) -> dict:
    """A lane's hit row in the shared envelope.

    The row keeps every field its lane attached (segment_id, word_hits,
    support); the envelope ADDS the interval, the uncertainty and the
    provenance rather than replacing anything, so a caller that reads
    the lane's own shape keeps working and a caller that reads the
    contract sees the same fields whatever the lane.
    """
    out = dict(row)
    uncertainty = dict(row.get("uncertainty") or {})
    uncertainty.setdefault("score", row.get("score"))
    if abstention is not None:
        uncertainty.setdefault("lane_abstention", abstention)
    out["uncertainty"] = uncertainty
    provenance = dict(row.get("provenance") or {})
    provenance.setdefault("lane", lane)
    provenance.setdefault("pass_class", LANE_PASS_CLASS.get(lane))
    if backend is not None:
        provenance.setdefault("backend", backend)
    out["provenance"] = provenance
    return out


def envelope_complete(row: Mapping) -> tuple[bool, list[str]]:
    """Whether a row carries the whole envelope, and what is missing."""
    missing = [
        key
        for key in EVIDENCE_FIELDS
        if row.get(key) is None and key not in ("text", "score")
    ]
    if isinstance(row.get("uncertainty"), Mapping) and isinstance(
        row.get("provenance"), Mapping
    ):
        return not missing, missing
    if "uncertainty" not in row or "provenance" not in row:
        missing = missing + [
            key for key in ("uncertainty", "provenance") if key not in row
        ]
    return not missing, missing
