"""What decided each clip on the built timeline, and how to find why.

A note the captain types onto the timeline has to reach the step that
made the decision it is about.  `marker_routing.py` answers that from the
NOTE - a declared `step:` line, or the words the captain used - and when
the words name nothing, it can only say so.

This module is the other side of that question, and it answers a
different one.  It reads the assembly manifest and says, for every
placement on the timeline, WHICH STEP'S DECISION PUT IT THERE.  The
answer is a fact about the build, not a reading of anybody's prose, and
it is recorded twice:

* as a LEDGER, one JSON file in step 6.01's own directory, written on
  every build from the manifest alone - no Resolve, no cost, nothing
  visible;
* as a `marker_payload` DECISION RECORD stamped into a marker's
  `customData`, where the UI cannot show it, so a marker carries its own
  answer even when the ledger is not to hand.

`marker_routing` then reads the stamp instead of inferring - but only
where inference has nothing, and only for a note attached to ONE clip.
See "Where the stamp may decide" below, which is the whole of the
judgement in this module.

── Which step decided a clip ───────────────────────────────────────────

`TRACK_DECISIONS` is the enumeration, one row per track of the manifest,
and a track that is not a row is reported rather than guessed at.  Two
rows are worth stating outright because the obvious answer is wrong:

V1 A-ROLL IS `speech_sequence` (2.02), NOT `assign_aroll` (3.01).  3.01
is deterministic and its `assign_a_roll` reads `block["clip_id"]` and
`block["source_start"]` straight off the spine - it chooses no clip and
no range.  The choice of which passage plays, cut from which clip and
from which seconds, is 2.02's, which is also the step that anchors it by
searching the WhisperX words (AGENTS.md section 6).  Stamping the
transform would send a note about the wrong take to a step that could
not act on it.

A BOOKEND CARD IS NOT STAMPED AT ALL.  An intro, outro or end card is a
brand template's DECLARATION (section 13), turned into a spine block and
played like any other clip; no step weighed it against an alternative.
`UNSTAMPED_PLACEMENTS` records that, with the reason, rather than
attributing the card to whichever step happened to place it.  A stamp
that names a step which decided nothing is worse than no stamp: it reads
as an answer.

`DECISION_BASES` separates the two things a stamp can mean.  `chosen` is
a step that weighed alternatives; `declared` is a value that arrived from
a brand template or the project and that the step delivered without
choosing.  Nothing in this pipeline may read `declared` as taste
(section 10.5), and a reader of a stamp should not either.

── Where the stamp may decide ──────────────────────────────────────────

`marker_routing.WITHDRAWN_ROUTERS` already refuses "the clip under the
playhead decides", and it is right: every frame of this pipeline's output
has a V1 clip, a caption card, a music bed and often a cutaway under it,
so the structure at a frame narrows nothing.  The stamp does not reopen
that.  It is allowed to route only when BOTH hold:

1. the note is attached to exactly ONE clip - a clip marker whose
   placement `marker_routing.resolve_target` resolved, not a timeline
   marker about a moment.  That is the captain's own selection, not the
   stack at a frame; and
2. the note's own words name NO step's decision.  The words are about
   what the note MEANS; the stamp is about what produced the picture, and
   the two are not the same fact.

`STAMP_RANKS_BELOW_THE_WORDS` records why the second rule is not the
other way round, and it is measured on the captain's own notes: the note
reading *"why is this fully blurry, is it the zoom blur applied wrong?"*
sits on `speech_9_seg0`, a V1 A-roll clip.  Its stamp is
`speech_sequence`.  Its words are ambiguous between `plan_transitions`
and `plan_vfx`, and one of those two is where a blur that held for the
whole clip is actually decided.  A stamp that outranked the words would
have routed that note to the step that chose the passage.

── Collisions ──────────────────────────────────────────────────────────

Two placements can sit under one frame - a cutaway over the A-roll it
covers, a caption over both - and a marker there gets ONE RECORD PER
PLACEMENT, each with its own id, never one record that picks a winner.
Routing then sees more than one step and reports an ambiguity, which is
the honest outcome and the same one the vocabulary router reaches.

TIMELINE MARKERS ONLY, AND THAT IS SAID RATHER THAN LEFT IMPLICIT.
`stamp_timeline` reads `Timeline.GetMarkers()`.  A CLIP marker - one
typed onto a `TimelineItem`, which is what two of the captain's three
notes are - is not stamped, because `TimelineItem.UpdateMarkerCustomData`
has not been measured against a running Resolve here and `hasattr` proves
nothing about it (AGENTS.md section 5).  Those notes are answered from
the LEDGER instead, which needs no Resolve call at all, and extending the
stamp to clip markers is a probe away rather than a guess away.

A MARKER THAT IS ALREADY THERE IS UPDATED, NEVER REPLACED.  `stamp_timeline`
only ever calls `UpdateMarkerCustomData`, which leaves `name`, `note`,
`colour` and `duration` untouched (measured, see `marker_capture`), and
`marker_payload.parse` carries anything it does not understand into
`foreign`.  **It never calls `AddMarker`**: a marker is drawn on the
timeline ruler, and thirty of them the captain did not ask for would be a
visible change to their timeline for a payload they cannot see.  The
ledger is where an unmarked placement is recorded; the stamp is for the
markers that exist.

    python3 -m library.tools.timeline_decisions ledger --project <dir>
    python3 -m library.tools.timeline_decisions show   --project <dir>
    python3 -m library.tools.timeline_decisions stamp  --project <dir>

`tests/test_timeline_decisions.py`.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

**Every clip on the built timeline carries the decision that produced it, and the routing
reads that instead of inferring - but only where inference has nothing.**
One enumeration, `library/tools/timeline_decisions.py`, and it is the producer half of the
loop above.
- **`TRACK_DECISIONS` is one row per track of the manifest, and a track with no row is
  REPORTED, never attributed to the nearest step.** **V1 A-roll is `speech_sequence` (2.02), not `assign_aroll` (3.01)**; **a bookend card is not stamped at all** (§13). `UNSTAMPED_PLACEMENTS` records both.
- `DECISION_BASES` keeps `chosen` and `declared` apart, the same line §10.5 draws.
- **A1 is not a placement.** `LINKED_AUDIO_OF` says so: `compile_manifest` builds A1 from the
  same V1 clip dicts, and surveying it would stamp two records where the timeline has one clip.
- **The build writes a LEDGER and creates NO marker.** Step 6.01 writes `timeline_decisions.json` from the manifest and merges decisions into existing markers via `UpdateMarkerCustomData`.
  `pipeline_output/steps/6_01_render/timeline_decisions.json` from the manifest alone, and
- **The stamp ranks BELOW the captain's own words.** Order: declared > vocabulary > stamped. The stamp closes the UNROUTED case without touching the routed ones.
  ones. `STAMP_RANKS_BELOW_THE_WORDS` is the record.
- **A MOMENT note is never routed by the stamp**, only a note attached to ONE clip. That is
  the captain's own selection, not the stack at a frame, so it is not
  `marker_routing.WITHDRAWN_ROUTERS["the_clip_under_the_playhead_decides"]` coming back. What
  was playing under a moment is recorded as `decision_context` and routes nothing.
- A marker's own stamp outranks the ledger, because it was written when the marker was made
  and the ledger describes the LAST build. Neither is guessed at: a placement that matches no
  row comes back as a stated reason.
- `tests/test_timeline_decisions.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools import marker_payload  # noqa: E402
from library.tools.project_layout import (  # noqa: E402
    STEP_BY_ID,
    Area,
    ProjectLayout,
)

LEDGER_FORMAT = "timeline_decisions/1"
LEDGER_FILENAME = "timeline_decisions.json"
LEDGER_STEP = "render"
"""Step 6.01 owns the file: it is a statement about the timeline the
build produced, and it is written where that build's other artifacts are."""

WRITER = "timeline_decisions"
WRITER_VERSION = 1

KIND_DECISION = "decision"
"""The `marker_payload` record kind this module writes.  Deliberately not
`marker_routing.KIND_ROUTE`: a `route` record is a WRITER DECLARING where
a note should go and is authoritative, and this is a statement about what
produced a picture, which is weaker.  Collapsing them would have made the
stamp outrank the captain's own words."""


# ── What a stamp can mean ───────────────────────────────────────────

BASIS_CHOSEN = "chosen"
BASIS_DECLARED = "declared"

DECISION_BASES = {
    BASIS_CHOSEN:
        "a step weighed this against alternatives and picked. A note "
        "about it reaches a step that can decide differently.",
    BASIS_DECLARED:
        "a brand template or the project declared it and the step "
        "delivered it without choosing (AGENTS.md 10.5, 13). A note "
        "about it is about the declaration, and the step named is where "
        "the declaration is read.",
}


# ── Which step decided what plays on each track ─────────────────────

@dataclass(frozen=True)
class TrackDecision:
    """One track of the manifest, and the step whose decision fills it."""

    track: str
    """`V1`, `V2`, `A2`, `A3`, `V3`, `V4`, `V5`, `V6` - this pipeline's
    own name for the track, as `resolve_build_timeline` places it."""

    what: str
    """One sentence: what plays on this track."""

    decided_by: str
    """The DAG node id of the step whose decision put it there."""

    basis: str
    """A key of `DECISION_BASES`."""

    locator: str
    """Where inside that step's own output the decision is written down.
    A human-followable path, not a machine pointer: the step's output is
    JSON and its shape is the step's business, not this module's."""

    why_this_step: str = ""
    """Why this step and not the one that looks obvious.  Empty when
    nothing looks obvious."""


TRACK_DECISIONS = (
    TrackDecision(
        "V1", "the A-roll - the speaking picture and the hook",
        "speech_sequence", BASIS_CHOSEN,
        "speech_sequence.passages[] - the passage whose block this clip "
        "covers, with the clip and the source range it is cut from",
        why_this_step=(
            "step 3.01 assign_aroll is deterministic and copies the "
            "spine block's own clip_id and source_start; the choice of "
            "passage, clip and range is 2.02's, and 2.02 is where a note "
            "about the wrong take or the wrong seconds can be acted on"),
    ),
    TrackDecision(
        "V2", "the b-roll - covering assignments and standalone cutaways",
        "select_broll", BASIS_CHOSEN,
        "broll_selections.b_roll_assignments[] / .b_roll_interjections[] "
        "- the entry whose spine block position this label names",
    ),
    TrackDecision(
        "V3", "the caption cards",
        "plan_subtitles", BASIS_CHOSEN,
        "subtitle_plan - the grouping this segment's block was cut into",
        why_this_step=(
            "4.01 groups the words into cards and decides when each is "
            "on screen; 4.05 render_subtitles DRAWS them, and a note "
            "about the typeface or the box routes there by its own words"),
    ),
    TrackDecision(
        "V4", "the motion-graphic overlays",
        "render_motion_graphics", BASIS_DECLARED,
        "motion_graphics_overlay.segments[] - the segment covering this "
        "range, rendered from the brand template's effect slot",
        why_this_step=(
            "nothing PLANS a motion graphic (AGENTS.md 10.2): what can "
            "be asked for is a brand template's effect.motion_* slot, "
            "and 4.06 is where that declaration is read and drawn"),
    ),
    TrackDecision(
        "V5", "generator overlays - the full-frame effects the VFX plan asked for",
        "plan_vfx", BASIS_CHOSEN,
        "enhancement_spec.generator_overlays[] - the entry covering this range",
    ),
    TrackDecision(
        "V6", "the timed text cards",
        "render_motion_graphics", BASIS_DECLARED,
        "timed_text_overlay.segments[] - the moment covering this range, "
        "declared by the project or the brand template (AGENTS.md 14)",
    ),
    TrackDecision(
        "A2", "the music bed",
        "music_selection", BASIS_CHOSEN,
        "music_selection - the chosen track, and the section of it that plays",
    ),
    TrackDecision(
        "A3", "the sound effects",
        "plan_sfx", BASIS_CHOSEN,
        "sfx_plan.sound_effects[] - the entry whose label this clip carries, "
        "naming the sfx_id it chose out of the library",
    ),
)

BY_TRACK = {d.track: d for d in TRACK_DECISIONS}

LINKED_AUDIO_OF = {
    "A1": "V1",
}
"""A track whose clips ARE another track's clips, not placements of their
own.  `compile_manifest` builds A1 from the same V1 dicts - it is the
audio Resolve links to the picture - so surveying it as well would count
every A-roll clip twice and stamp two records where there is one clip.
`marker_routing._same_placement` reaches the same conclusion from the
other end: the linked audio item and the video item of one placement are
the one clip they are."""

UNSTAMPED_PLACEMENTS = {
    "bookend_card":
        "An intro, outro or end card is a brand template DECLARATION "
        "(AGENTS.md 13), played on V1 like any other clip. No step chose "
        "it against an alternative, so naming one would read as an "
        "answer where there is none. A note about a card is about the "
        "template that declared it and the artwork the project owns.",
    "unknown_track":
        "The manifest carried a placement on a track TRACK_DECISIONS has "
        "no row for. Adding a track means adding a row saying which "
        "step's decision fills it - not defaulting to the nearest one.",
}

STAMP_RANKS_BELOW_THE_WORDS = (
    "A stamp says what PRODUCED the picture; the captain's words say "
    "what the note is ABOUT, and they are different facts. Measured on "
    "the captain's own three notes off 001's timeline: the note reading "
    "'why is this fully blurry, is it the zoom blur applied wrong?' is "
    "attached to speech_9_seg0, a V1 A-roll clip whose stamp is "
    "speech_sequence, while its words are ambiguous between "
    "plan_transitions and plan_vfx - and a blur holding for a whole clip "
    "is decided in one of those two. Ranking the stamp above the words "
    "would have routed that note to the step that chose the passage. So "
    "the stamp answers only where the words answer nothing, which on "
    "those three notes changes none of them and is the point: it closes "
    "the unrouted case without touching the routed ones."
)


# ── One placement on the built timeline ─────────────────────────────

@dataclass
class Placement:
    """One clip on the timeline, and the decision that put it there."""

    track: str
    label: str
    source_file: str
    source_basename: str
    timeline_in_frame: int
    timeline_out_frame: int
    source_in_frame: Optional[int] = None
    source_out_frame: Optional[int] = None

    step: str = ""
    """The DAG node id, or `""` when nothing decided this placement."""

    basis: str = ""
    decision_id: str = ""
    """`select_broll/broll_4`. Stable across builds that make the same
    decision, and unique inside one ledger."""

    locator: str = ""
    why_this_step: str = ""
    routes: dict = field(default_factory=dict)
    """Project-relative paths to the reasoning behind the decision."""

    stamped: bool = True
    unstamped_reason: str = ""

    @property
    def record_id(self) -> str:
        """Deterministic, so re-stamping REPLACES rather than accumulates."""
        return f"{KIND_DECISION}_{self.track}_{self.timeline_in_frame}"


def _frames(entry: dict, fps: float) -> tuple:
    """(in, out) timeline frames, from whichever pair the entry carries."""
    tl_in = entry.get("timeline_in_frame")
    tl_out = entry.get("timeline_out_frame")
    if tl_in is None:
        seconds = entry.get("timeline_in", entry.get("timeline_start"))
        tl_in = round(float(seconds or 0.0) * fps)
    if tl_out is None:
        seconds = entry.get("timeline_out", entry.get("timeline_end"))
        tl_out = round(float(seconds or 0.0) * fps)
    return int(tl_in), int(tl_out)


def _source_frames(entry: dict, fps: float) -> tuple:
    """(in, out) SOURCE frames - the space a clip marker's key is in."""
    src_in = entry.get("source_in_frame")
    src_out = entry.get("source_out_frame")
    if src_in is None and entry.get("source_in") is not None:
        src_in = round(float(entry["source_in"]) * fps)
    if src_out is None and entry.get("source_out") is not None:
        src_out = round(float(entry["source_out"]) * fps)
    return (None if src_in is None else int(src_in),
            None if src_out is None else int(src_out))


def _track_entries(manifest: dict) -> list:
    """(track, entry) for every placement the manifest describes.

    One reader for all eight tracks, so a track that grows a placement
    kind cannot quietly stop being surveyed.
    """
    tracks = manifest.get("tracks") or {}
    out = []
    # Every track the manifest carries, not a list of the ones known
    # today: a track nothing here has a row for has to come out as an
    # unstamped placement naming itself, or adding one to
    # `compile_manifest` would silently produce clips nothing decided.
    for key in tracks:
        if key in LINKED_AUDIO_OF:
            continue
        for entry in (tracks.get(key) or {}).get("clips") or []:
            out.append((key, entry))
    for key, container in (
        ("V3", manifest.get("subtitle_overlay")),
        ("V4", manifest.get("motion_graphics_overlay")),
        ("V6", manifest.get("timed_text_overlay")),
    ):
        for entry in (container or {}).get("segments") or []:
            out.append((key, entry))
    for entry in manifest.get("generator_overlays") or []:
        out.append(("V5", entry))
    return out


def _label(track: str, entry: dict, index: int) -> str:
    for key in ("label", "overlay_path", "effect", "name"):
        value = entry.get(key)
        if value:
            return Path(str(value)).stem if key == "overlay_path" else str(value)
    return f"{track.lower()}_{index}"


def reasoning_routes(node_id: str, project_folder=None) -> dict:
    """Project-relative paths to why the step decided what it decided.

    `output` and `summary` are DECLARED by the layout - that is where the
    step writes, whether or not this run produced them.  `prompt` and
    `answer` are MEASURED: they appear only when the file is really on
    disk, because a step with no `handoff.md` reaches no prompt at all
    and claiming one would be a route that leads nowhere.
    """
    step = STEP_BY_ID.get(node_id)
    if step is None:
        return {}
    base = f"pipeline_output/steps/{step.dirname}"
    routes = {"output": f"{base}/output.json", "summary": f"{base}/summary.md"}
    if not project_folder:
        return routes
    root = Path(project_folder)
    for name, relative in (
        ("prompt", f"pipeline_output/llm_requests/{node_id}.json"),
        ("answer", f"pipeline_output/llm_responses/{node_id}.json"),
        ("reasoning", f"pipeline_output/reasoning/{node_id}.md"),
    ):
        if (root / relative).exists():
            routes[name] = relative
    return routes


def placements(manifest: dict, project_folder=None) -> list:
    """Every placement on the built timeline, with what decided it.

    Reads the manifest and nothing else, so it costs a build nothing and
    can be run again later against the same manifest.
    """
    fps = float((manifest.get("project") or {}).get("frame_rate") or 30.0)
    out: list = []
    seen_ids: dict = {}
    for index, (track, entry) in enumerate(_track_entries(manifest)):
        tl_in, tl_out = _frames(entry, fps)
        src_in, src_out = _source_frames(entry, fps)
        source_file = str(entry.get("source_file") or
                          entry.get("overlay_path") or "")
        placement = Placement(
            track=track,
            label=_label(track, entry, index),
            source_file=source_file,
            source_basename=Path(source_file).name if source_file else "",
            timeline_in_frame=tl_in,
            timeline_out_frame=tl_out,
            source_in_frame=src_in,
            source_out_frame=src_out,
        )
        decision = BY_TRACK.get(track)
        if decision is None:
            placement.stamped = False
            placement.unstamped_reason = UNSTAMPED_PLACEMENTS["unknown_track"]
        elif track == "V1" and entry.get("bookend"):
            placement.stamped = False
            placement.unstamped_reason = UNSTAMPED_PLACEMENTS["bookend_card"]
        else:
            placement.step = decision.decided_by
            placement.basis = decision.basis
            placement.locator = decision.locator
            placement.why_this_step = decision.why_this_step
            placement.routes = reasoning_routes(
                decision.decided_by, project_folder)
            base = f"{decision.decided_by}/{placement.label}"
            seen_ids[base] = seen_ids.get(base, 0) + 1
            # Two b-roll interjections over one spine block share a label,
            # so the label alone is not an identity. The ordinal is added
            # only where it is needed, and says the manifest could not
            # tell the two apart either.
            placement.decision_id = (
                base if seen_ids[base] == 1 else f"{base}#{seen_ids[base]}")
        out.append(placement)
    return out


# ── The ledger ──────────────────────────────────────────────────────

def build_ledger(manifest: dict, project_folder=None) -> dict:
    """The whole statement, ready to write."""
    found = placements(manifest, project_folder)
    project = manifest.get("project") or {}
    return {
        "format": LEDGER_FORMAT,
        "timeline": project.get("name") or "",
        "frame_rate": float(project.get("frame_rate") or 30.0),
        "built_at": marker_payload.utc_now(),
        "placements": [asdict(p) for p in found if p.stamped],
        "unstamped": [asdict(p) for p in found if not p.stamped],
    }


def ledger_path(project_folder) -> Path:
    return ProjectLayout(project_folder).step_dir(
        LEDGER_STEP, create=True) / LEDGER_FILENAME


def write_ledger(project_folder, manifest: dict) -> Path:
    path = ledger_path(project_folder)
    path.write_text(
        json.dumps(build_ledger(manifest, project_folder), indent=2),
        encoding="utf-8")
    return path


def read_ledger(project_folder) -> dict:
    """The ledger this project last built, or `{}`.  Never raises."""
    try:
        path = ProjectLayout(project_folder).read_path(
            Area.STEPS_ROOT, STEP_BY_ID[LEDGER_STEP].dirname, LEDGER_FILENAME)
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def ledger_placements(ledger: dict) -> list:
    return [p for p in (ledger.get("placements") or []) if isinstance(p, dict)]


# ── Matching a marker's clip back to a placement ────────────────────

_CLIP_TO_PLACEMENT = {
    "source_start": "source_in_frame",
    "source_end": "source_out_frame",
}
"""A pull file's clip entry names the source range `source_start` /
`source_end`; a placement names it `source_in_frame` / `source_out_frame`.
Both are SOURCE frames.  The two vocabularies are written down here
rather than derived, because deriving one from the other is exactly the
key-name guess AGENTS.md section 10.1 is about."""

SOURCE_OUT_TOLERANCE_FRAMES = 1
"""How far the two ends may disagree, and no further.

MECHANICAL, not a fudge factor: the manifest holds a source range in
SECONDS and this module rounds it to frames, while Resolve reports the
frame it actually played to.  Measured on 001's own timeline, all four
clips under the captain's three notes agree exactly on the IN point and
every OUT point but one - the music bed, clamped to a 59.437 s edit,
comes back 1782 against the manifest's 1783.  The IN point is therefore
compared exactly and only the OUT point is given the one frame.  A
placement whose OUT is unknown (an SFX carries no source_out) is
compared on the IN point alone rather than being failed for a value
nobody recorded."""


def _same_source_range(placement: dict, clip: dict) -> bool:
    """The same seconds of the same file, within the timebase's rounding."""
    p_in, c_in = placement.get("source_in_frame"), clip.get("source_start")
    if p_in is not None and c_in is not None and int(p_in) != int(c_in):
        return False
    p_out, c_out = placement.get("source_out_frame"), clip.get("source_end")
    if p_out is not None and c_out is not None:
        if abs(int(p_out) - int(c_out)) > SOURCE_OUT_TOLERANCE_FRAMES:
            return False
    return not (p_in is None and c_in is None and p_out is None)


def placement_for_clip(ledger: dict, clip: dict) -> Optional[dict]:
    """The placement a pull file's clip entry is, or None.

    Matched on the SOURCE file and the SOURCE range, which is what
    `marker_routing._same_placement` already treats as one placement -
    so Resolve's linked audio item of a V1 clip resolves to the same row
    as its video item, with no separate A1 bookkeeping.

    Returns None rather than a best fit.  Two rows fitting is an honest
    None: the caller reports what it could not resolve.
    """
    if not isinstance(clip, dict):
        return None
    basename = Path(str(clip.get("source_file") or "")).name
    if not basename:
        return None
    hits = [p for p in ledger_placements(ledger)
            if p.get("source_basename") == basename
            and _same_source_range(p, clip)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    # Same file and same source range placed twice - the timeline
    # position is what tells them apart.
    exact = [p for p in hits
             if p.get("timeline_in_frame") == clip.get("timeline_start")]
    return exact[0] if len(exact) == 1 else None


def placements_at_frame(ledger: dict, frame) -> list:
    """Every placement playing at a timeline frame.  CONTEXT, not an answer."""
    if frame is None:
        return []
    return [p for p in ledger_placements(ledger)
            if p.get("timeline_in_frame") is not None
            and p["timeline_in_frame"] <= frame < (
                p.get("timeline_out_frame") or p["timeline_in_frame"])]


# ── The record, and stamping it onto a marker ───────────────────────

def decision_record(placement, timeline: str = "") -> dict:
    """One `marker_payload` record: what decided this clip, and where why is.

    `path` is the producing step's own `output.json`, so a reader gets
    "this is openable" through `marker_payload.attachments_of` without
    this module inventing a second channel for it.
    """
    data = placement if isinstance(placement, dict) else asdict(placement)
    record_id = (f"{KIND_DECISION}_{data['track']}_"
                 f"{data['timeline_in_frame']}")
    record = {
        "kind": KIND_DECISION,
        "writer": WRITER,
        "writer_version": WRITER_VERSION,
        "id": record_id,
        "at": marker_payload.utc_now(),
        "step": data.get("step", ""),
        "decision_id": data.get("decision_id", ""),
        "basis": data.get("basis", ""),
        "locator": data.get("locator", ""),
        "track": data.get("track", ""),
        "label": data.get("label", ""),
        "timeline_in_frame": data.get("timeline_in_frame"),
        "timeline_out_frame": data.get("timeline_out_frame"),
        "source": data.get("source_basename", ""),
        "routes": data.get("routes", {}),
    }
    if timeline:
        record["timeline"] = timeline
    routes = record["routes"] or {}
    if routes.get("output"):
        record["path"] = routes["output"]
    return record


def stamp_envelope(envelope: dict, found: list, timeline: str = "") -> dict:
    """Merge one decision record per placement.  Destroys nothing.

    `marker_payload.merge_record` replaces a record carrying the same id
    and appends otherwise, and the record id is derived from the
    placement, so stamping the same marker twice does not grow the
    string.  Every other writer's records, and any `foreign` payload,
    are untouched.
    """
    for placement in found:
        marker_payload.merge_record(
            envelope, decision_record(placement, timeline))
    return envelope


@dataclass
class StampReport:
    """What stamping a running timeline did, and to what."""

    timeline: str = ""
    markers_seen: int = 0
    markers_stamped: int = 0
    records_written: int = 0
    skipped: list = field(default_factory=list)
    refused: list = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{self.timeline or '(unnamed timeline)'}: "
            f"{self.markers_stamped}/{self.markers_seen} markers stamped, "
            f"{self.records_written} decision records"
        ]
        for entry in self.skipped:
            lines.append(f"  · frame {entry['frame']}: {entry['reason']}")
        for entry in self.refused:
            lines.append(f"  ✗ frame {entry['frame']}: {entry['reason']}")
        return "\n".join(lines)


def stamp_timeline(timeline, ledger: dict) -> StampReport:
    """Stamp every marker ALREADY on `timeline`.  Creates none.

    A marker's visible fields - name, note, colour, duration - are never
    passed and never touched: the only call made is
    `UpdateMarkerCustomData`, which measured as replacing the string and
    leaving the other four alone.  A marker at a frame no placement
    covers is left exactly as it is, and said so.
    """
    report = StampReport(timeline=timeline.GetName() or "")
    markers = timeline.GetMarkers() or {}
    for key in sorted(markers):
        marker = markers[key] or {}
        report.markers_seen += 1
        at_frame = placements_at_frame(ledger, key)
        if not at_frame:
            report.skipped.append({
                "frame": key,
                "reason": "no placement in the ledger covers this frame",
            })
            continue
        envelope = marker_payload.parse(marker.get("customData") or "")
        stamp_envelope(envelope, at_frame, report.timeline)
        if not timeline.UpdateMarkerCustomData(
                int(key), marker_payload.dumps(envelope)):
            report.refused.append({
                "frame": key,
                "reason": "Resolve returned False for UpdateMarkerCustomData; "
                          "the marker is unchanged",
            })
            continue
        report.markers_stamped += 1
        report.records_written += len(at_frame)
    return report


# ── CLI ─────────────────────────────────────────────────────────────

def _read_manifest(project_folder) -> dict:
    layout = ProjectLayout(project_folder)
    path = layout.read_path(Area.ASSEMBLY_MANIFEST, "assembly_manifest.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    output = layout.step_dir("compile_manifest") / "output.json"
    if output.exists():
        data = json.loads(output.read_text(encoding="utf-8"))
        return data.get("assembly_manifest") or data
    raise SystemExit(
        f"No assembly manifest under {project_folder}: neither "
        f"{path} nor {output} is there. Nothing to build a ledger from.")


def _describe(ledger: dict) -> str:
    lines = [f"{ledger.get('timeline') or '(unnamed)'}  "
             f"{len(ledger.get('placements') or [])} placements, "
             f"{len(ledger.get('unstamped') or [])} unstamped"]
    for p in ledger.get("placements") or []:
        lines.append(
            f"  {p['track']:<2} {p['timeline_in_frame']:>6}-"
            f"{p['timeline_out_frame']:<6} {p['label']:<24} "
            f"{p['step']} ({p['basis']})  {p['decision_id']}")
    for p in ledger.get("unstamped") or []:
        lines.append(
            f"  {p['track']:<2} {p['timeline_in_frame']:>6}-"
            f"{p['timeline_out_frame']:<6} {p['label']:<24} "
            f"NOT STAMPED: {p['unstamped_reason'][:70]}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.timeline_decisions",
        description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("ledger", "build the ledger from the manifest and write it"),
        ("show", "print the ledger without writing anything"),
        ("stamp", "stamp the markers already on the running timeline"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--project", required=True)
        if name == "stamp":
            p.add_argument("--timeline", default="",
                           help="the timeline to stamp; the current one by default")
    args = parser.parse_args(argv)

    if args.command == "show":
        ledger = read_ledger(args.project) or build_ledger(
            _read_manifest(args.project), args.project)
        print(_describe(ledger))
        return 0
    if args.command == "ledger":
        path = write_ledger(args.project, _read_manifest(args.project))
        print(_describe(read_ledger(args.project)))
        print(f"\nwritten to {path}")
        return 0

    from library.tools.marker_feedback import ResolveUnavailable, connect_resolve
    ledger = read_ledger(args.project)
    if not ledger:
        print("This project has no decision ledger. Build one first:\n"
              "  python3 -m library.tools.timeline_decisions ledger "
              f"--project {args.project}", file=sys.stderr)
        return 2
    try:
        resolve = connect_resolve()
    except ResolveUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = None
    if args.timeline:
        for i in range(project.GetTimelineCount(), 0, -1):
            candidate = project.GetTimelineByIndex(i)
            if candidate and candidate.GetName() == args.timeline:
                timeline = candidate
                break
        if timeline is None:
            print(f"No timeline named {args.timeline!r} in this project.",
                  file=sys.stderr)
            return 2
    else:
        timeline = project.GetCurrentTimeline()
    if timeline is None:
        print("No timeline is open.", file=sys.stderr)
        return 2
    print(stamp_timeline(timeline, ledger).summary())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
