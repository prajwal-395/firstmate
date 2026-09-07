"""The clip under the playhead, joined to everything the pipeline knows.

The steer, 2026-08-30
---------------------
The captain asked the panel *"what does this clip where my playhead is
at show?"* over IMG_1806.MOV and was told the model could see a filename
and could not watch the video.

**The model was not wrong. The panel handed it a filename and nothing
else.**  It attached Resolve-side facts only - clip name, timeline
frames, source offset, source path, markers - and nothing the pipeline
already knows about that clip, all of which is on disk and already
indexed: the vision document, the transcript, the temporal-index
measurements, the usable ranges and their METHOD, the stability label
and its method, and the decision record that put the clip on the
timeline at that point.

So this module is the JOIN, and it is the panel's whole reason to exist:
the browser dashboard cannot do it, because it does not know where the
playhead is.

Two failure modes it is built against, both from that one screenshot
-------------------------------------------------------------------
1. **Ordering.**  The model latched onto `0_01_validate_sfx_library` -
   the step the editor happened to have open in another tab - because
   that was the most concrete thing in the prompt.  So :func:`prompt_block`
   puts what the captain is LOOKING AT first and what they last CLICKED
   last, under a heading that says it is secondary.
2. **Attaching more of the same.**  The answer to a thin prompt is not a
   bigger prompt.  Every joined fact here is a READING - the vision
   document through `semantic_index.clip_observations`, the transcript
   through the same `view:transcript` projection a step gets, the
   placement through `timeline_decisions` - never a document pasted in.
   `PROMPT_BUDGET_CHARS` bounds the whole thing and what it drops is
   said rather than silently cut.

Every join goes through the repository's own translator
------------------------------------------------------
The documents are keyed by FILE STEM and the catalog by `clip_XXX`
(AGENTS.md 10.1), so the lookup is `semantic_index.build_semantic_lookup`
and not a filename guess.  The placement is
`timeline_decisions.placement_for_clip`, which matches on the source FILE
and the source RANGE and returns None rather than a best fit.

An absence is stated, never filled in
-------------------------------------
A clip the catalog does not know, a step that never ran, a measurement
nobody took: each comes back as a sentence saying so.  That is the same
rule the pipeline holds everywhere, and here it is what stops the model
inventing a description of footage nobody measured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from library.tools import semantic_index

# What the whole assembled block may cost. A prompt is paid per call and
# the point of the join is that it is SPECIFIC, not that it is large.
PROMPT_BUDGET_CHARS = 9000

# How many transcript lines of one clip travel. The transcript of a
# 60-second clip is the useful unit; a whole project's is not.
TRANSCRIPT_LINES = 24

NOT_IN_THE_CATALOG = (
    "This source file is not in the project's clip catalog, so nothing "
    "the pipeline measured can be joined to it.")


@dataclass
class ResolveContext:
    """The live facts the entry point reads off Resolve.

    A plain dataclass with no Resolve types in it, so a test can build
    one and this module never has to import the scripting API.
    """

    page: str = ""
    project: str = ""
    timeline: str = ""
    timecode: str = ""
    timeline_frame: Optional[int] = None
    fps: float = 30.0
    clip: Optional[dict] = None
    """`{name, start, end, duration, left_offset, file}` - Resolve's own
    numbers, in Resolve's own vocabulary."""

    markers: List[dict] = field(default_factory=list)
    overlays: List[dict] = field(default_factory=list)
    """What the pipeline is drawing OVER the picture right now - the
    subtitle card, the motion graphic. Reported, never mistaken for the
    shot: see :func:`picture_at`."""

    topmost: Optional[dict] = None
    """What `GetCurrentVideoItem()` answered, when that is NOT the
    picture. Kept so the panel can say why it is showing a different
    clip from the one Resolve calls current."""

    error: str = ""

    @property
    def source_file(self) -> str:
        return str((self.clip or {}).get("file") or "")

    @property
    def source_basename(self) -> str:
        return os.path.basename(self.source_file)


# Everything the pipeline RENDERS - subtitle cards, motion graphics,
# timed text - lands under `pipeline_output/`, and everything the captain
# SHOT is elsewhere (AGENTS.md section 8). That is what tells a picture
# clip from one of the pipeline's own overlays, and it is a fact about
# the layout rather than a guess about a filename.
_OUTPUT_MARKER = os.sep + "pipeline_output" + os.sep


def is_overlay(source_file: str) -> bool:
    """True for a clip the PIPELINE rendered, rather than footage."""
    return _OUTPUT_MARKER in (source_file or "")


def picture_at(items: Sequence[dict], frame: Optional[int]
               ) -> Optional[dict]:
    """The PICTURE under the playhead, which is not the topmost item.

    `Timeline.GetCurrentVideoItem()` answers with the highest video
    track, and on a finished build that is V3 - a subtitle card.  Asked
    "what does this clip show", the panel would then join a rendered
    overlay to the footage catalog, find nothing, and say so about the
    wrong clip entirely.

    So the picture is the highest track carrying FOOTAGE: V2 when a
    cutaway covers the moment, V1 otherwise.  Overlays are still
    reported - they are on screen - but through :func:`overlays_at`,
    where they cannot be mistaken for the shot.
    """
    if frame is None:
        return None
    covering = [item for item in items
                if not is_overlay(item.get("file") or "")
                and _covers(item, frame)]
    if not covering:
        return None
    return max(covering, key=lambda item: int(item.get("track") or 0))


def overlays_at(items: Sequence[dict], frame: Optional[int]) -> List[dict]:
    """What the pipeline is drawing OVER the picture at this moment."""
    if frame is None:
        return []
    return sorted((item for item in items
                   if is_overlay(item.get("file") or "")
                   and _covers(item, frame)),
                  key=lambda item: int(item.get("track") or 0))


def _covers(item: dict, frame: int) -> bool:
    try:
        return int(item["start"]) <= int(frame) < int(item["end"])
    except (KeyError, TypeError, ValueError):
        return False


@dataclass
class ClipFacts:
    """Everything the pipeline recorded about the clip under the playhead."""

    clip_id: str = ""
    filename: str = ""
    catalog: Dict[str, Any] = field(default_factory=dict)
    observations: Dict[str, str] = field(default_factory=dict)
    transcript: List[dict] = field(default_factory=list)
    transcript_total: int = 0
    speech_coverage: str = ""
    placement: Dict[str, Any] = field(default_factory=dict)
    reasoning_routes: Dict[str, str] = field(default_factory=dict)
    window_basis: str = ""
    video_only: Optional[bool] = None
    absences: List[str] = field(default_factory=list)
    """One sentence per thing that could not be joined, and why. Printed,
    never swallowed: a silent absence reads as a measurement of nothing."""

    @property
    def resolved(self) -> bool:
        return bool(self.clip_id)


def clip_facts(state: dict, context: ResolveContext) -> ClipFacts:
    """Join the clip under the playhead to what the pipeline recorded."""
    facts = ClipFacts(filename=context.source_basename)
    outputs = (state or {}).get("step_outputs") or {}

    catalog = ((outputs.get("catalog") or {}).get("clip_catalog")) or []
    if not catalog:
        facts.absences.append(
            "Step 1.02 has recorded no clip catalog in this project, so "
            "nothing can be joined to this file by clip id.")
        return facts

    entry = _catalog_entry(catalog, context.source_file)
    if entry is None:
        facts.absences.append(NOT_IN_THE_CATALOG)
        return facts
    facts.clip_id = str(entry.get("clip_id") or "")
    facts.catalog = {k: entry[k] for k in
                     ("clip_id", "filename", "duration", "resolution", "fps",
                      "codec") if k in entry}

    _join_vision(facts, outputs, catalog)
    _join_transcript(facts, outputs, context)
    _join_placement(facts, outputs, context, state)
    return facts


def _catalog_entry(catalog: Sequence[dict], source_file: str
                   ) -> Optional[dict]:
    """The catalog row for a source path.

    By REAL PATH first and by basename second - the same order
    `semantic_index` joins in - because a project reached through a
    symlink and one reached directly are the same footage.
    """
    if not source_file:
        return None
    target = os.path.realpath(source_file)
    base = os.path.basename(source_file)
    for entry in catalog:
        for key in ("path", "source_file", "file_path"):
            value = entry.get(key)
            if value and os.path.realpath(str(value)) == target:
                return entry
    for entry in catalog:
        if str(entry.get("filename") or "") == base:
            return entry
        for key in ("path", "source_file", "file_path"):
            value = entry.get(key)
            if value and os.path.basename(str(value)) == base:
                return entry
    return None


def _join_vision(facts: ClipFacts, outputs: dict,
                 catalog: Sequence[dict]) -> None:
    """What the vision pass observed, through the repo's own reading.

    `clip_observations` is what gives the description, the framing, the
    stability WITH its method and the usable ranges WITH theirs - the
    fields a bare document dump makes the reader dig for.
    """
    documents = (outputs.get("semantic_analysis") or {})
    lookup = semantic_index.build_semantic_lookup(documents, list(catalog))
    document = lookup.get(facts.clip_id)
    if not document:
        facts.absences.append(
            f"Step 1.03 recorded no vision document for {facts.clip_id}, so "
            f"nothing here describes what the clip LOOKS like.")
        return
    observed = semantic_index.clip_observations(document)
    facts.observations = {k: v for k, v in observed.items() if v}
    empty = [k for k, v in observed.items() if not v]
    if empty:
        facts.absences.append(
            "The vision document measured nothing for: "
            + ", ".join(sorted(empty))
            + ". Absent, not zero.")


def _join_transcript(facts: ClipFacts, outputs: dict,
                     context: ResolveContext) -> None:
    """What was said in this clip, in the seconds the timeline plays.

    Read off step 1.04's `temporal_event_indices`, which is a LIST of per-clip
    dicts and not a mapping (AGENTS.md 10.3) - the shape a `.get` on it
    silently misses.
    """
    index = outputs.get("temporal_index") or {}
    per_clip = index.get("temporal_event_indices")
    if not isinstance(per_clip, list) or not per_clip:
        facts.absences.append(
            "Step 1.04 has recorded no temporal index in this project, so "
            "there is no transcript and no speech measurement for this clip.")
        return
    entry = next((e for e in per_clip
                  if isinstance(e, dict) and e.get("clip_id") == facts.clip_id),
                 None)
    if entry is None:
        facts.absences.append(
            f"The temporal index has no entry for {facts.clip_id}.")
        return

    method = entry.get("speech_coverage_method")
    coverage = entry.get("speech_coverage")
    if method and coverage is not None:
        facts.speech_coverage = "%s (method: %s)" % (coverage, method)
    elif method:
        facts.speech_coverage = "not measured (method: %s)" % method

    regions = [r for r in (entry.get("speech_regions") or [])
               if isinstance(r, dict) and (r.get("text") or "").strip()]
    facts.transcript_total = len(regions)
    if not regions:
        # AGENTS.md 10.3: an empty `speech_regions` list is not a
        # measurement of silence - WhisperX returns [] both when it ran
        # and heard nothing and when it raised. Saying "no speech" here
        # would be the engine answering a question nobody measured.
        facts.absences.append(
            f"The temporal index recorded no speech regions for "
            f"{facts.clip_id}. An empty list is not a measurement of "
            f"silence - it reads the same whether nothing was said or "
            f"the transcription failed."
            + ("" if method else " No speech_coverage_method was recorded "
                                "either, so nobody has established which."))
        return

    # Only the seconds this placement PLAYS, when we know them. Handing
    # over a whole clip's transcript for a 2.5 s cutaway is the "attach
    # more of the same" failure the steer names.
    played = _played_seconds(context)
    if played is not None:
        start, end = played
        in_window = [r for r in regions
                     if float(r.get("end", 0)) >= start
                     and float(r.get("start", 0)) <= end]
        if in_window:
            regions = in_window
    facts.transcript = [
        {"start": round(float(r.get("start", 0.0)), 2),
         "end": round(float(r.get("end", 0.0)), 2),
         "text": (r.get("text") or "").strip()}
        for r in regions[:TRANSCRIPT_LINES]]


def _played_seconds(context: ResolveContext):
    """The SOURCE seconds this placement plays, from Resolve's frames."""
    clip = context.clip or {}
    offset = clip.get("left_offset")
    duration = clip.get("duration")
    fps = context.fps or 30.0
    if offset is None or duration is None or not fps:
        return None
    try:
        start = float(offset) / fps
        return start, start + float(duration) / fps
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _join_placement(facts: ClipFacts, outputs: dict,
                    context: ResolveContext, state: dict) -> None:
    """WHICH STEP put this clip here, and what it was reasoning from.

    `timeline_decisions` already owns this - `TRACK_DECISIONS` is one row
    per track of the manifest and a track with no row is REPORTED rather
    than attributed to the nearest step. This asks it; it does not
    re-derive it.
    """
    from library.tools import timeline_decisions

    manifest = ((outputs.get("compile_manifest") or {})
                .get("assembly_manifest") or {})
    if not manifest:
        facts.absences.append(
            "Step 5.04 has recorded no assembly manifest, so nothing here "
            "says which decision put this clip on the timeline.")
        return

    project_folder = (state or {}).get("project_folder") or ""
    try:
        ledger = timeline_decisions.build_ledger(manifest, project_folder)
    except Exception as exc:                       # noqa: BLE001 - reported
        facts.absences.append(
            "The timeline decision ledger could not be built: %s: %s"
            % (type(exc).__name__, exc))
        return

    clip = context.clip or {}
    placement = timeline_decisions.placement_for_clip(ledger, {
        "source_file": context.source_file,
        "source_start": clip.get("left_offset"),
        "source_end": (None if clip.get("left_offset") is None
                       or clip.get("duration") is None
                       else int(clip["left_offset"]) + int(clip["duration"])),
        "timeline_start": clip.get("start"),
    })
    if placement is None:
        facts.absences.append(
            "No placement in the last build's manifest matches this clip's "
            "source file and source range, so which step chose it is "
            "unresolved. It is not guessed at.")
        return

    facts.placement = {
        "track": placement.get("track"),
        "label": placement.get("label"),
        "decided_by": placement.get("step") or "(nothing decided this)",
        "basis": placement.get("basis"),
        "decision_id": placement.get("decision_id"),
        "why_this_step": placement.get("why_this_step"),
        "timeline_in_frame": placement.get("timeline_in_frame"),
        "timeline_out_frame": placement.get("timeline_out_frame"),
    }
    if not placement.get("stamped"):
        facts.absences.append(
            "This placement is not stamped: %s"
            % placement.get("unstamped_reason", "no reason recorded"))
    facts.reasoning_routes = dict(placement.get("routes") or {})

    _join_broll(facts, manifest, placement)


def _join_broll(facts: ClipFacts, manifest: dict, placement: dict) -> None:
    """For a cutaway: which window was chosen, why, and whether it is heard.

    A cutaway is placed `video_only`, so its own audio never plays - the
    thing a reviewer most often assumes wrongly (AGENTS.md 10.5).
    """
    if placement.get("track") != "V2":
        return
    frame = placement.get("timeline_in_frame")
    for entry in ((manifest.get("tracks") or {}).get("V2") or {}).get(
            "clips") or []:
        entry_frame = entry.get("timeline_in_frame")
        if entry_frame is None:
            continue
        if int(entry_frame) != int(frame or -1):
            continue
        facts.window_basis = str(entry.get("window_basis") or "")
        facts.video_only = bool(entry.get("video_only"))
        return


# ── What goes to the model ───────────────────────────────────────────

LOOKING_AT = "WHAT THE EDITOR IS LOOKING AT"
LAST_CLICKED = "SECONDARY - what the editor last opened in the panel"


def prompt_block(context: ResolveContext, facts: ClipFacts,
                 open_step: str = "", project_folder: str = "",
                 budget: int = PROMPT_BUDGET_CHARS) -> str:
    """The context the panel attaches, ORDERED.

    What the captain is looking at comes first and what they last
    clicked comes last under a heading that says it is secondary - the
    ordering failure the steer names, fixed where it happened.
    """
    lines: List[str] = [LOOKING_AT, ""]
    lines.append("Resolve page: %s" % (context.page or "unknown"))
    lines.append("Project: %s    Timeline: %s"
                 % (context.project or "-", context.timeline or "-"))
    lines.append("Playhead: %s" % (context.timecode or "-"))

    clip = context.clip or {}
    if clip:
        lines.append("")
        lines.append("Clip under the playhead: %s" % clip.get("name"))
        lines.append("  on the timeline: frames %s-%s"
                     % (clip.get("start"), clip.get("end")))
        lines.append("  playing source frames %s-%s of %s"
                     % (clip.get("left_offset"),
                        (None if clip.get("left_offset") is None
                         or clip.get("duration") is None
                         else clip["left_offset"] + clip["duration"]),
                        context.source_basename or "an unknown file"))
    else:
        lines.append("")
        lines.append("There is no video clip under the playhead.")

    if context.topmost is not None:
        lines.append("")
        lines.append("(Resolve's own 'current item' here is %s on V%s, which "
                     "is an overlay the pipeline rendered. The clip above is "
                     "the PICTURE underneath it.)"
                     % (context.topmost.get("name"),
                        context.topmost.get("track")))
    if context.overlays:
        lines.append("")
        lines.append("Drawn over the picture at this moment: "
                     + ", ".join("%s (V%s)" % (o.get("name"), o.get("track"))
                                 for o in context.overlays))

    lines += _facts_lines(facts)
    lines += _marker_lines(context)

    if open_step:
        lines += ["", LAST_CLICKED, "",
                  "The panel has step %s open in its trace view. This is "
                  "where the editor last clicked, NOT what they are asking "
                  "about, unless they say so." % open_step]
    if project_folder:
        lines += ["", "Pipeline project on disk: %s" % project_folder]

    text = "\n".join(lines)
    if len(text) <= budget:
        return text
    kept = text[:budget]
    return (kept + "\n\n[%d characters of this context were not sent - the "
            "budget is %d. Nothing above was summarised; the tail was cut.]"
            % (len(text) - budget, budget))


def _facts_lines(facts: ClipFacts) -> List[str]:
    lines: List[str] = ["", "WHAT THE PIPELINE RECORDED ABOUT THIS CLIP", ""]
    if not facts.resolved:
        lines.append(NOT_IN_THE_CATALOG if not facts.absences
                     else facts.absences[0])
        return lines

    lines.append("Catalog id: %s  (%s)" % (facts.clip_id, facts.filename))
    for key in ("duration", "resolution", "fps", "codec"):
        if key in facts.catalog:
            lines.append("  %s: %s" % (key, facts.catalog[key]))

    if facts.observations:
        lines.append("")
        lines.append("What the vision pass observed (step 1.03):")
        for key in ("description", "activity", "framing", "stability",
                    "movement", "content_type", "usable_ranges", "subjects"):
            if facts.observations.get(key):
                lines.append("  %s: %s" % (key, facts.observations[key]))

    if facts.speech_coverage:
        lines.append("")
        lines.append("Speech coverage (step 1.04): %s" % facts.speech_coverage)
    if facts.transcript:
        lines.append("")
        lines.append("What is said in the seconds this placement plays "
                     "(%d of %d regions in the clip):"
                     % (len(facts.transcript), facts.transcript_total))
        for region in facts.transcript:
            lines.append("  [%.2f-%.2f] %s"
                         % (region["start"], region["end"], region["text"]))

    if facts.placement:
        lines.append("")
        lines.append("What put this clip here (from the last build):")
        lines.append("  track %s, %s" % (facts.placement.get("track"),
                                         facts.placement.get("label")))
        lines.append("  decided by: %s (%s)"
                     % (facts.placement.get("decided_by"),
                        facts.placement.get("basis")))
        if facts.placement.get("why_this_step"):
            lines.append("  why that step: %s"
                         % facts.placement["why_this_step"])
        if facts.window_basis:
            lines.append("  the cutaway window was chosen by: %s"
                         % facts.window_basis)
        if facts.video_only:
            lines.append("  this cutaway is placed VIDEO ONLY - its own "
                         "audio is never heard.")
        for name, path in sorted(facts.reasoning_routes.items()):
            lines.append("  reasoning on disk - %s: %s" % (name, path))

    if facts.absences:
        lines.append("")
        lines.append("What could NOT be joined, and why (these are absences, "
                     "not measurements of nothing):")
        for absence in facts.absences:
            lines.append("  - %s" % absence)
    return lines


def _marker_lines(context: ResolveContext) -> List[str]:
    markers = context.markers or []
    if not markers:
        return ["", "The editor has typed no markers on this timeline."]
    lines = ["", "Markers the editor typed on this timeline (%d). BOTH the "
             "Name and the Notes field are read:" % len(markers), ""]
    for marker in markers:
        lines.append("  frame %s [%s] %s | %s"
                     % (marker.get("frame"), marker.get("color", ""),
                        marker.get("name", ""), marker.get("note", "")))
        for record in marker_records(marker.get("custom")):
            lines.append("      attached %s by %s: %s"
                         % (record.get("kind"), record.get("writer"),
                            record.get("path", "")))
    return lines


def marker_records(custom_data) -> List[dict]:
    """The capture button's records off a marker, through the owner.

    `library/tools/marker_payload.py` owns that envelope; this reads it
    and writes nothing, which is the same contract the scout's prototype
    kept and the reason it does not need a second schema.
    """
    if not custom_data:
        return []
    from library.tools import marker_payload

    try:
        return list(marker_payload.records_of(
            marker_payload.parse(str(custom_data))))
    except Exception:                              # noqa: BLE001 - a marker
        return []                                  # the panel did not write
