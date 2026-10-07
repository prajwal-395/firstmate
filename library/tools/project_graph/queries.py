"""Project-graph queries: the read API over a built graph."""
from __future__ import annotations
from .core import (
    APPEARS_IN,
    AUDIO_EVENT,
    COMMIT,
    COVERS,
    EDIT,
    EDITORIAL_FINDING,
    FINDING_REACTION_CUTAWAY,
    MUSIC_CHANGES_AT,
    OPERATION,
    PARTICIPANT,
    PROPOSAL,
    SHOT,
    TAG,
    BRANCH,
    SPEAKS_IN,
    TRANSCRIPT_SEGMENT,
    VARIANT,
    VERSION,
    VFX_DETECTION,
    _overlap,
    edges_from,
    edges_of_type,
    node,
)

# ── The query API ─────────────────────────────────────────────────


def find_reaction_shots(graph: dict, person_id: str,
                        around_t: float) -> list:
    """Shots where another person is visible while person_id speaks
    around time t (in person_id's source timeline).

    A reaction shot is a shot where a DIFFERENT person appears during
    the time person_id is speaking. The reaction is temporal: the other
    person's appearance overlaps the speaking span.
    """
    speaking_shots = set()
    for edge in edges_of_type(graph, SPEAKS_IN):
        if edge["from"] == person_id:
            speaking_shots.add(edge["to"])

    reaction_shots = []
    for edge in edges_of_type(graph, APPEARS_IN):
        other_person = edge["from"]
        if other_person == person_id:
            continue
        shot_id = edge["to"]
        if shot_id in speaking_shots:
            continue
        for speak_edge in edges_of_type(graph, SPEAKS_IN):
            if speak_edge["from"] != person_id:
                continue
            speak_start = speak_edge["payload"].get("start", 0)
            speak_end = speak_edge["payload"].get("end", 0)
            appear_start = edge["payload"].get("start", 0)
            appear_end = edge["payload"].get("end", 0)
            if _overlap(speak_start, speak_end, appear_start, appear_end):
                reaction_shots.append({
                    "shot_id": shot_id,
                    "person_id": other_person,
                    "reaction_start": appear_start,
                    "reaction_end": appear_end,
                })
                break
    return reaction_shots


def find_alternate_takes(graph: dict, clip_id: str) -> list:
    """Other shots covering the same CONTENT.

    An alternate take is a different clip where the same line is spoken
    - the spec's "shot 431 covers the same content as shot 183". The
    match is by transcript overlap: two shots are alternates when they
    share spoken words (the same sentence or phrase was recorded in
    both). A shot sharing a digest or file with the target is a
    DUPLICATE, not an alternate - it is reported separately.
    """
    target = node(graph, f"shot:{clip_id}")
    if not target:
        return []
    target_words = _transcript_words(graph, clip_id)
    target_digest = target["payload"].get("content_digest")
    target_file = target["payload"].get("source_file")

    alternates = []
    duplicates = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != SHOT:
            continue
        if candidate["node_id"] == f"shot:{clip_id}":
            continue
        candidate_id = candidate["payload"].get("clip_id")
        candidate_digest = candidate["payload"].get("content_digest")
        candidate_file = candidate["payload"].get("source_file")

        if target_digest and candidate_digest == target_digest:
            duplicates.append({
                "clip_id": candidate_id,
                "reason": "same-digest",
            })
            continue
        if target_file and candidate_file == target_file:
            duplicates.append({
                "clip_id": candidate_id,
                "reason": "same-file",
            })
            continue

        candidate_words = _transcript_words(graph, candidate_id)
        shared = target_words & candidate_words
        if shared:
            alternates.append({
                "clip_id": candidate_id,
                "reason": "transcript-overlap",
                "shared_words": sorted(shared)[:5],
            })

    return alternates + duplicates


def _transcript_words(graph: dict, clip_id: str) -> set:
    """The set of words spoken in a clip, from the transcript segments."""
    words = set()
    for candidate in graph["nodes"]:
        if candidate["node_type"] != TRANSCRIPT_SEGMENT:
            continue
        if candidate["payload"].get("content_digest") is None:
            continue
        shot = node(graph, f"shot:{clip_id}")
        if shot and candidate["payload"]["content_digest"] == shot["payload"].get("content_digest"):
            text = candidate["payload"].get("text", "")
            words.update(text.lower().split())
    return words


def find_coverage(graph: dict, timeline_start: float,
                  timeline_end: float) -> list:
    """Shots that cover a timeline span.

    Coverage comes from the edit graph's spine blocks: a block that
    plays a clip over a timeline range that overlaps the query span.
    """
    covering = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDIT:
            continue
        payload = candidate["payload"].get("edit_payload") or {}
        block_start = payload.get("timeline_start")
        block_end = payload.get("timeline_end")
        clip_id = payload.get("clip_id")
        if block_start is None or block_end is None or not clip_id:
            continue
        if _overlap(block_start, block_end, timeline_start, timeline_end):
            covering.append({
                "clip_id": clip_id,
                "timeline_start": block_start,
                "timeline_end": block_end,
                "edit_node_id": candidate["payload"].get("edit_node_id"),
            })
    return covering


def find_broll_matches(graph: dict, transcript_segment_id: str) -> list:
    """Shots whose content matches a speech segment.

    B-roll matching comes from the edit graph's broll_cover nodes: a
    B-roll assignment covers a spine block, and the block plays a
    passage cut from a transcript segment.
    """
    segment = node(graph, transcript_segment_id)
    if not segment:
        return []

    matches = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDIT:
            continue
        if candidate["payload"].get("node_type") != "broll_cover":
            continue
        payload = candidate["payload"].get("edit_payload") or {}
        clip_id = payload.get("clip_id")
        if not clip_id:
            continue
        block_position = payload.get("spine_block_position")
        for block in graph["nodes"]:
            if block["node_type"] != EDIT:
                continue
            if block["payload"].get("node_type") != "spine_block":
                continue
            block_payload = block["payload"].get("edit_payload") or {}
            if str(block_payload.get("position")) != str(block_position):
                continue
            block_clip = block_payload.get("clip_id")
            if block_clip:
                matches.append({
                    "clip_id": clip_id,
                    "covers_block": block_position,
                    "broll_clip": clip_id,
                })
    return matches


def find_shot_colour(graph: dict, clip_id: str) -> dict | None:
    """The colour track's registration for one shot, or None.

    The shot node's `colour` payload: `measurement` (the measured row -
    exposure, white balance, contrast, saturation, skin vector),
    `grade` (the delivered per-clip CDL) and `match` (this shot's
    camera against the scene's reference). A shot the colour track
    never measured returns None - an absent measurement is absent,
    never a default.
    """
    shot = node(graph, f"shot:{clip_id}")
    if not shot:
        return None
    return shot["payload"].get("colour")


def find_reel_versions(graph: dict, final: str | None = None) -> list:
    """The reel versions the graph registered, oldest first.

    Each row names the final it versions, the version number, the
    kind the versions store recorded (build, touch, undo, rollback,
    redo) and when it landed. A final the ledger never versioned
    reads as no rows - an absent version is absent, never version 0.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VERSION:
            continue
        payload = candidate["payload"] or {}
        if final is not None and payload.get("final") != final:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "final": payload.get("final"),
            "version": payload.get("version"),
            "kind": payload.get("kind"),
            "at": payload.get("at"),
            "rows_digest": payload.get("rows_digest"),
        })
    rows.sort(key=lambda r: (str(r["final"]), r["version"] or 0))
    return rows


def find_commits(graph: dict, timeline: str | None = None) -> list:
    """The creative commits the graph registered, oldest first.

    Each row names the commit, the timeline it stands on, the agent
    that recorded it (touch, merge, revert, replay), its intent, its
    parent - and a merge's second parent - so lineage reads off the
    graph without re-reading the commit store. A timeline with no
    commits reads as no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != COMMIT:
            continue
        payload = candidate["payload"] or {}
        if timeline is not None and payload.get("timeline") != timeline:
            continue
        row = {
            "node_id": candidate["node_id"],
            "id": payload.get("id"),
            "timeline": payload.get("timeline"),
            "agent": payload.get("agent"),
            "intent": payload.get("intent"),
            "parent": payload.get("parent"),
            "at": payload.get("at"),
            "sequence": payload.get("sequence") or 0,
            "change_counts": payload.get("change_counts") or {},
        }
        if payload.get("second_parent"):
            row["second_parent"] = payload.get("second_parent")
        rows.append(row)
    rows.sort(key=lambda r: (str(r["timeline"]), r["sequence"]))
    return rows


def find_tags(graph: dict) -> list:
    """The version names the graph registered, by name.

    Each row names the tag, the commit it points at and why it was
    kept. A project with no tags reads as no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != TAG:
            continue
        payload = candidate["payload"] or {}
        rows.append({
            "node_id": candidate["node_id"],
            "name": payload.get("name"),
            "commit": payload.get("commit"),
            "note": payload.get("note") or "",
            "at": payload.get("at"),
        })
    rows.sort(key=lambda r: str(r["name"]))
    return rows


def find_branches(graph: dict, timeline: str | None = None) -> list:
    """The branches the graph registered, by name.

    Each row names the branch, the base commit it stands on, the
    timeline it lives on and why it was cut. Filtered to one
    timeline's lines when asked; a timeline with no branches reads as
    no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != BRANCH:
            continue
        payload = candidate["payload"] or {}
        if timeline is not None and payload.get("timeline") != timeline:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "name": payload.get("name"),
            "base": payload.get("base"),
            "timeline": payload.get("timeline"),
            "note": payload.get("note") or "",
            "at": payload.get("at"),
        })
    rows.sort(key=lambda r: str(r["name"]))
    return rows


def find_editorial_findings(graph: dict, kind: str | None = None,
                            clip_id: str | None = None) -> list:
    """The compile's report-only findings the graph registered.

    Each row carries the finding verbatim - a dead section's timeline
    span and reasons, a trim proposal's edge and seconds, a J/L
    placement's measured bound, a J/L decision's chosen offset and
    status, a reaction cutaway's speech and reaction clips. Filter by
    finding kind, by clip, or both; a kind nothing recorded reads as
    no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDITORIAL_FINDING:
            continue
        payload = candidate["payload"] or {}
        if kind is not None and payload.get("kind") != kind:
            continue
        record = payload.get("record") or {}
        if clip_id is not None:
            clips = _finding_clip_ids(record)
            for edge in edges_from(graph, candidate["node_id"]):
                if edge["type"] == COVERS and edge["to"].startswith("shot:"):
                    clips.add(edge["to"][len("shot:"):])
            if clip_id not in clips:
                continue
        rows.append({
            "node_id": candidate["node_id"],
            "kind": payload.get("kind"),
            "record": record,
        })
    return rows


def _finding_clip_ids(record: dict) -> set:
    """Every clip id a finding record names, whatever the key."""
    ids = set()
    for key in ("clip_id", "speech_clip_id", "reaction_clip_id"):
        value = record.get(key)
        if value:
            ids.add(value)
    return ids


def find_reaction_decisions(graph: dict, clip_id: str | None = None,
                            at_time: float | None = None) -> list:
    """The reaction-cutaway EDIT DECISIONs the run recorded.

    Each row is one planner's decision: what it did (action), why
    (reason), what it rests on (evidence refs to measured graph nodes),
    what it must satisfy (constraints), whose taste it serves
    (style source, absent unless stated) and how strongly the measured
    overlap supports it (confidence). Filter by clip - the speech clip
    or the reaction clip the decision names - or by timeline time,
    which matches the speech span the decision reacts to. A finding the
    compile recorded before decisions existed carries no decision and
    reads as no row: an absent decision is absent, never synthesised.
    A time filter a decision cannot be placed in (no speech span on
    record) matches nothing.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != EDITORIAL_FINDING:
            continue
        payload = candidate["payload"] or {}
        if payload.get("kind") != FINDING_REACTION_CUTAWAY:
            continue
        record = payload.get("record") or {}
        decision = record.get("decision")
        if not isinstance(decision, dict):
            continue
        if clip_id is not None and clip_id not in _finding_clip_ids(record):
            continue
        if at_time is not None:
            start = record.get("speech_timeline_start")
            end = record.get("speech_timeline_end")
            if (not isinstance(start, (int, float))
                    or not isinstance(end, (int, float))):
                continue
            if not (start <= at_time <= end):
                continue
        rows.append({
            "node_id": candidate["node_id"],
            "speech_block_position": record.get("speech_block_position"),
            "speech_clip_id": record.get("speech_clip_id"),
            "reaction_clip_id": record.get("reaction_clip_id"),
            "speech_timeline_start": record.get("speech_timeline_start"),
            "speech_timeline_end": record.get("speech_timeline_end"),
            "reaction_land": (record.get("timing") or {}).get(
                "reaction_land"),
            "decision": decision,
        })
    return rows


def find_vfx_detections(graph: dict, clip_id: str | None = None) -> list:
    """The VFX detections the graph registered, optionally for one clip.

    Each row names the clip, the detected effect and the timeline
    position the edit graph recorded, with the edit node it came from.
    A clip with no detection reads as no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VFX_DETECTION:
            continue
        payload = candidate["payload"] or {}
        if clip_id is not None and payload.get("clip_id") != clip_id:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "clip_id": payload.get("clip_id"),
            "effect": payload.get("effect"),
            "position": payload.get("position"),
            "edit_node_id": payload.get("edit_node_id"),
        })
    return rows


def find_variant_branches(graph: dict, exploration: str | None = None,
                          timeline: str | None = None) -> list:
    """The variant branches the graph registered, oldest first.

    Each row names the exploration, the variant, the branch it owns,
    the base it forked, the direction it tries and who planned it -
    the comparison surface the merge queue evaluates. Filter by
    exploration id or by timeline; an exploration nothing recorded
    reads as no rows.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != VARIANT:
            continue
        payload = candidate["payload"] or {}
        if exploration is not None and payload.get("exploration") != exploration:
            continue
        if timeline is not None and payload.get("timeline") != timeline:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "exploration": payload.get("exploration"),
            "name": payload.get("name"),
            "direction": payload.get("direction"),
            "branch": payload.get("branch"),
            "timeline": payload.get("timeline"),
            "base": payload.get("base"),
            "agent": payload.get("agent"),
            "at": payload.get("at"),
        })
    rows.sort(key=lambda r: (str(r["at"] or ""), str(r["name"] or "")))
    return rows


def find_project_events(graph: dict, task: str | None = None,
                        event_type: str | None = None) -> list:
    """The operations the graph registered, oldest first.

    Each row carries the operation verbatim - its sequence number,
    when and where it ran, which worker's task it belonged to, what
    happened and the event's own fields. Filter by worker task, by
    event type, or both; a project whose workers did nothing reads as
    no rows. This is how a resumed task and the scheduler read the
    operation history without re-reading the log file.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != OPERATION:
            continue
        payload = candidate["payload"] or {}
        if task is not None and payload.get("task") != task:
            continue
        if event_type is not None and payload.get("type") != event_type:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "seq": payload.get("seq"),
            "at": payload.get("at"),
            "task": payload.get("task"),
            "type": payload.get("type"),
            "fields": payload.get("fields") or {},
        })
    rows.sort(key=lambda r: (r["seq"] is None, r["seq"] or 0))
    return rows


def find_audio_events(graph: dict, clip_id: str | None = None,
                      label: str | None = None) -> list:
    """The audio events the graph registered, optionally for one clip.

    Each row names the clip whose source audio carries the event, the
    measured label (music, laughter, applause - whatever the audio
    analysis recorded), the source span and the confidence. A label
    filter matches case-insensitively on substring: the analysis, not
    the caller, owns the exact wording. A clip with no measured audio
    reads as no rows - unmeasured is absent, never silent.
    """
    rows = []
    for candidate in graph["nodes"]:
        if candidate["node_type"] != AUDIO_EVENT:
            continue
        payload = candidate["payload"] or {}
        if label is not None and label.lower() not in str(
                payload.get("label") or "").lower():
            continue
        event_clip = None
        for edge in edges_from(graph, candidate["node_id"]):
            if edge["type"] == MUSIC_CHANGES_AT and edge["to"].startswith(
                    "shot:"):
                event_clip = edge["to"][len("shot:"):]
                break
        if clip_id is not None and event_clip != clip_id:
            continue
        rows.append({
            "node_id": candidate["node_id"],
            "clip_id": event_clip,
            "label": payload.get("label"),
            "start": payload.get("start"),
            "end": payload.get("end"),
            "confidence": payload.get("confidence"),
        })
    rows.sort(key=lambda r: (str(r["clip_id"]),
                             r["start"] if r["start"] is not None else -1.0))
    return rows


def participants_with(graph: dict, level: str) -> list:
    """The rostered participants whose level grants `level`.

    The ladder is the spec's own table order: asking who may approve
    returns the approvers, the deliverers and the admins - everyone at
    or above approve - and never the client's observe+suggest Ren.
    """
    from library.tools import fabric_permissions
    if level not in fabric_permissions.LEVELS:
        raise ValueError(
            f"{level!r} is not an authority level - the levels are "
            f"{', '.join(fabric_permissions.LEVELS)}.")
    found = []
    for candidate in graph.get("nodes") or []:
        if candidate.get("node_type") != PARTICIPANT:
            continue
        payload = candidate.get("payload") or {}
        held = payload.get("level")
        if held in fabric_permissions.LEVELS and fabric_permissions.grants(
                held, level):
            found.append(payload)
    return found


def open_proposals(graph: dict) -> list:
    """The undecided proposals awaiting approval, oldest first."""
    return [candidate.get("payload") or {}
            for candidate in graph.get("nodes") or []
            if candidate.get("node_type") == PROPOSAL]
