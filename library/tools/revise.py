"""`ren revise`: one verb from an editor's note to a verified change.

Every part of this already existed and nothing called the parts in
order; a person had to type `ren spec prepare`, answer a handshake file,
run `ren spec proposal` and `ren spec resolve`, hand-write execution
requests, and then pick an editing workflow themselves. This module is the
order:

1. **Note.** From chat (`ren revise <p> "<note>"`), which becomes a new
   durable note (`edit_spec._write_request_note`); from a delivered
   file's own clock (`ren revise <p> --at 0:12 [--reel N] "<note>"`),
   which maps the file time onto the timeline through the reel's
   `.deliver.json` and stores it in the same note store with a stable
   note id (`delivered_note_anchor`); or from a pulled
   Resolve marker (`--note-id`), which keeps its own id. The note joins
   the assignment that owns its reel (`assignment_spine.attach_revision`).
2. **Translate.** One `edit_spec` handshake. The host model answers it;
   until it has, the verb says `awaiting_translation` and names the file.
   Where the marker's placement touches recorded edit decisions
   (`decision_implication.candidates_for_note`), the same translation
   judges them, so the proposal names the decision the note is about and
   the version that change belongs on.
3. **Propose.** `edit_spec.proposal_for_spec`, shown in the result. A
   spec that asks anything returns EXACTLY ONE question and changes
   nothing - no ledger row, no outcome, no timeline write. The answer
   (`--answer`) is translated again under the same note id.
4. **Route.** A timeline program becomes an `EditRequest`; AXI prepares,
   routes, executes and verifies it using the qualified backend. A program
   with no safe backend is refused with the router's reason. Typed planning
   clauses still use their owning planners and the scoped rebuild path.

5. **Verify and record.** A direct edit is complete only when the AXI
   receipt proves its requested postconditions and commit. Planning clauses
   still use a scoped rebuild; without request-specific verification the
   revision is recorded as unverified. The reply names the affected region.

A revision is one durable record per note under
`pipeline_output/llm_requests/revise/`, so the verb resumes where it
stopped: `ren revise <p>` alone continues the newest open one. A landed
semantic edit records the generation it landed at and the scope it
changed; a program planned against an older generation is refused naming
the revisions that landed since (and which of them changed the same
targets), and a landing over a retranslated base records the revisions
it supersedes.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import shlex
import subprocess
import sys
import uuid
from dataclasses import asdict
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

FORMAT = "ren_revise/1"
RECORDS_SUBDIR = "revise"

STATUS_AWAITING = "awaiting_translation"
STATUS_QUESTION = "question"
STATUS_PROPOSED = "proposed"
STATUS_PATCHED = "patched"
STATUS_TOUCHED = "touched"
STATUS_REBUILT = "rebuilt"
STATUS_UNVERIFIED = "unverified"
STATUS_REFUSED = "refused"
STATUS_INCOMPLETE = "incomplete"
FINISHED = (STATUS_PATCHED, STATUS_TOUCHED, STATUS_REBUILT,
            STATUS_UNVERIFIED, STATUS_REFUSED)

ROUTE_PATCH = "patch"
ROUTE_REBUILD = "rebuild"

_ENGINE = Path(__file__).resolve().parents[2]


class ReviseRefused(RenRefusal):
    """The note cannot become a change as asked; nothing was changed."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


# ── The durable record ──────────────────────────────────────────────


def record_path(project: str, revise_id: str, *, write: bool = False) -> Path:
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project)
    return (layout.write_path if write else layout.read_path)(
        Area.LLM_REQUESTS, RECORDS_SUBDIR, f"{revise_id}.json")


def load(project: str, revise_id: str) -> dict:
    path = record_path(_root(project), revise_id)
    if not path.is_file():
        raise ReviseRefused(
            f"no revision {revise_id!r} in this project",
            "a revision id names the record `ren revise` wrote for one note",
            f"run `ren revise {shlex.quote(_root(project))}` to continue the "
            f"newest open revision, or give the note again")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(project: str, record: dict) -> dict:
    record["updated_at"] = _now()
    path = record_path(project, record["revise_id"], write=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False)
                         + "\n", encoding="utf-8")
    temporary.replace(path)
    return record


def records(project: str) -> list[dict]:
    """Every revision recorded in this project, by id."""
    path = record_path(_root(project), "x").parent
    found = []
    for candidate in sorted(path.glob("revise_*.json")) if path.is_dir() else []:
        try:
            found.append(json.loads(candidate.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def open_records(project: str) -> list[dict]:
    """Every revision that has not finished."""
    return [record for record in records(project)
            if record.get("status") not in FINISHED]


def newest_open(project: str) -> dict | None:
    """The most recently touched revision that has not finished."""
    records = open_records(project)
    return (max(records, key=lambda r: r.get("updated_at") or "")
            if records else None)


def _status(record: dict, status: str, **fields) -> dict:
    record["status"] = status
    record.update(fields)
    record.setdefault("history", []).append({"status": status, "at": _now()})
    return record


# ── 1. The note ─────────────────────────────────────────────────────


def _routed_notes(project: str) -> list:
    from library.tools import marker_routing

    return marker_routing.route_project(project)


def _find_note(project: str, note_id: str):
    """The collected note an id names: its routing id or its edit link."""
    for routed in _routed_notes(project):
        if note_id in (routed.note_id, routed.edit_link_id):
            return routed
    return None


def _decision_candidates(project: str, routed) -> tuple[list, str]:
    """The recorded decisions the note's own placement touches.

    Only a placed note has an anchor; a chat note has none yet, and no
    anchor means no candidates - never the whole graph.
    """
    from library.tools import decision_implication, marker_routing

    if routed is None:
        return [], "the note has no placement, so it anchors no decision"
    span = marker_routing.note_span_seconds(routed)
    if span is None:
        return [], "the note has no placement, so it anchors no decision"
    try:
        from library.tools import project_graph

        graph = project_graph.build_graph(project)
    except Exception as exc:  # noqa: BLE001 - the fold is supplementary
        return [], f"the project's decision graph could not be read: {exc}"
    candidates = decision_implication.candidates_for_note(graph, (), [span])
    return candidates, ("" if candidates else
                        "no recorded decision sits under the note")


def start(project, note: str = "", *, note_id: str = "", reel: str = "",
          at: str = "", **options) -> dict:
    """Take a note in and carry it as far as it can go now."""
    from library.tools import assignment_spine as spine
    from library.tools import edit_spec

    project = _root(project)
    routed = None
    anchor = None
    if at:
        if note_id:
            raise ReviseRefused(
                "a pulled marker note is already placed on the timeline",
                "a file time anchors a chat note watched in the delivered "
                "file, not a marker Resolve already placed",
                "revise the marker with `--note-id` alone, or the chat "
                "note with `--at` alone")
        from library.tools import delivered_note_anchor as anchor_mod

        file_seconds = anchor_mod.parse_at(at)
        anchor = anchor_mod.resolve(project, reel, file_seconds)
        note_id, _pull_path = anchor_mod.write_anchored_note(
            project, note, anchor)
        reel = anchor["timeline_name"]
    if note_id:
        routed = _find_note(project, note_id)
        if routed is None:
            raise ReviseRefused(
                f"no collected note {note_id!r} in this project",
                "a marker note is revised by the id its pull recorded",
                f"run `ren notes {shlex.quote(project)}` to list the "
                f"collected notes and their ids")
        note = note or routed.text
        reel = reel or routed.timeline
    if not note or not note.strip():
        raise ReviseRefused(
            "no note was given",
            "a revision starts from what the editor wants changed",
            f'run `ren revise {shlex.quote(project)} "<what to change>"`')
    note = note.strip()
    owner = spine.revision_owner(project, reel)
    candidates, why_none = _decision_candidates(project, routed)
    spec_id, request_path, linked = edit_spec.prepare_request(
        project, note, reel=reel,
        note_id=routed.edit_link_id if routed is not None else "",
        decision_candidates=candidates)
    record = {
        "format": FORMAT,
        "revise_id": "revise_" + uuid.uuid4().hex[:12],
        "project": project,
        "note": note,
        "note_id": linked,
        "source": ("chat-at" if anchor is not None
                   else "marker" if routed is not None else "chat"),
        "reel": reel,
        "anchor": anchor,
        "assignment_id": owner["assignment_id"] if owner else None,
        "spec_id": spec_id,
        "request_path": request_path,
        "decision_candidates": candidates,
        "decision_candidates_reason": why_none,
        "preview_requested": bool(options.get("preview", False)),
        "clarifications": [],
        "created_at": _now(),
    }
    _save(project, _status(record, STATUS_AWAITING))
    return advance(project, record["revise_id"], **options)


# ── 2-3. Translate and propose ──────────────────────────────────────


def _reprepare(project: str, record: dict, answer: str = "") -> None:
    """Translate the note again, same note id: with the editor's answer
    to the open question, or (no answer) against the project as it is."""
    from library.tools import edit_spec

    if answer:
        record["clarifications"].append(
            {"question": record["question"]["question"], "answer": answer})
    else:
        # A retranslation replaces a program the reel has moved past. Keep
        # the base it was planned on, so the landing can say which
        # revisions it retranslated over (F08 conflicting revisions).
        try:
            old = edit_spec.load_response_spec(project, record["spec_id"])
            base = _program_base(old.get("program") or {})
        except edit_spec.EditSpecError:
            base = None
        if base is not None and base not in record.get("prior_bases", []):
            record.setdefault("prior_bases", []).append(base)
    request = record["note"] + "".join(
        f"\n\nAsked: {entry['question']}\nAnswered: {entry['answer']}"
        for entry in record["clarifications"])
    spec_id, request_path, _ = edit_spec.prepare_request(
        project, request, reel=record["reel"], note_id=record["note_id"],
        decision_candidates=record["decision_candidates"])
    record.update(spec_id=spec_id, request_path=request_path, question=None)
    record.pop("execution_receipt", None)
    _status(record, STATUS_AWAITING)


def _fold_decisions(project: str, record: dict, spec: dict) -> dict | None:
    """The decisions the note implicates, judged by the translation."""
    from library.tools import decision_implication, feedback_ledger

    candidates = record["decision_candidates"]
    if not candidates:
        return None
    judge = None
    if "implicated" in spec:
        def judge(_text, _candidates):
            return {"implicated": spec["implicated"]}
    verdict = decision_implication.judge_candidates(
        record["note"], candidates, judge)
    reel = record["reel"]
    return decision_implication.propose(
        feedback_ledger.durable_identity(reel, record["note"]),
        record["note"], verdict, candidates,
        decision_implication.parent_version(project, reel) if reel else None)


# ── 4. Route ────────────────────────────────────────────────────────


def classify(spec: dict, frame=None) -> dict:
    """`feedback_patch.decide` over the spec's own operations, plus route."""
    from library.tools import edit_spec, feedback_patch, marker_routing

    operations = marker_routing.edit_spec_operations(
        edit_spec.ledger_rows(spec))
    program = any(op["ledger_op"] == "edit_program" for op in operations)
    owners = list(dict.fromkeys(op["owner"] for op in operations
                                if op["owner"]))
    verdict = feedback_patch.decide({
        "basis": marker_routing.BASIS_EDIT_SPEC,
        "outcome": (marker_routing.OUTCOME_UNROUTED if program
                    else marker_routing.OUTCOME_ROUTED),
        "steps": owners, "frame": frame,
        "evidence": {"edit_spec_ops": operations}})
    if verdict["verdict"] == feedback_patch.VERDICT_REFUSED:
        route = None
    elif program:
        route = ROUTE_PATCH
    else:
        route = ROUTE_REBUILD
    return {"verdict": verdict, "route": route, "operations": operations}


def _program_ops(spec: dict) -> list[dict]:
    """The program's algebra ops as the patch path takes them: flat."""
    return [{"op": op["op"], **copy.deepcopy(op.get("params") or {})}
            for op in spec["program"]["ops"]]


def scope_for(operations: list[dict]) -> dict:
    """The declared dirty region: exactly the targets the ops name.

    `guarded_apply` refuses an op outside it before Resolve is written
    and restores anything else that moved after, so the scope is the
    region the change may touch and nothing more.
    """
    clips, frames, clip_markers = [], [], []
    for op in operations:
        if op.get("unique_id"):
            clips.append(str(op["unique_id"]))
        if op["op"] in ("marker.add", "marker.delete"):
            frames.append(int(op["frame"]))
        elif op["op"] in ("clip_marker.add", "clip_marker.delete"):
            clip_markers.append([str(op["unique_id"]), int(op["frame"])])
    return {"clips": list(dict.fromkeys(clips)),
            "marker_frames": list(dict.fromkeys(frames)),
            "clip_markers": clip_markers}


def _program_base(program: dict) -> int | None:
    """The program's base generation as an int, or None when it says none."""
    try:
        return int(str((program or {}).get("base_generation")))
    except (TypeError, ValueError):
        return None


def _verified_completion(receipt: dict) -> bool:
    """Accept only the common receipt's committed, verified outcome.

    A persisted read-back at stage VERIFIED is useful recovery evidence, but
    it is not a completed edit.  The no-op path is the sole exception: AXI
    proves the requested state was already present and no commit is needed.
    """
    contract = receipt.get("execution_contract") or {}
    verification = contract.get("verification") or {}
    checks = verification.get("checks") or []
    checks_passed = bool(checks) and all(
        isinstance(check, dict) and check.get("passed") is True
        for check in checks)
    if receipt.get("status") == "already_satisfied":
        return (verification.get("status") == "verified" and checks_passed
                and (receipt.get("lifecycle") or {}).get("stage")
                in {"VERIFIED", "COMMITTED"})
    committed = receipt.get("committed") or {}
    applied = receipt.get("applied") or {}
    return (
        receipt.get("status") == "committed"
        and (receipt.get("lifecycle") or {}).get("stage") == "COMMITTED"
        and verification.get("status") == "verified"
        and checks_passed
        and committed.get("status") == "committed"
        and committed.get("generation") is not None
        and receipt.get("generation") is not None
        and applied.get("complete") is True
    )


def _landed_revisions(project: str, reel: str) -> list[dict]:
    """Finished patch/touch revisions on this reel, oldest landing first.

    A patch records the generation it created; a touch-up records the
    head it applied onto (it stages beside the reel without recording a
    generation of its own), so a touch orders against later stale
    programs but not against landings on that same head.
    """
    out = [record for record in records(project)
           if record.get("reel") == reel
           and record.get("status") in (STATUS_PATCHED, STATUS_TOUCHED)
           and type(record.get("landed_generation")) is int]
    return sorted(out, key=lambda record: record["landed_generation"])


def _scope_overlap(mine: dict, theirs: dict) -> list[str]:
    """The targets two scopes both name, in words an editor recognises."""
    theirs = theirs or {}
    hits = [f"clip {clip}" for clip in mine.get("clips", ())
            if clip in set(theirs.get("clips", ()))]
    hits += [f"the timeline marker at frame {frame}"
             for frame in mine.get("marker_frames", ())
             if frame in set(theirs.get("marker_frames", ()))]
    their_markers = {(uid, frame) for uid, frame in
                     (theirs.get("clip_markers") or [])}
    hits += [f"the clip marker at source frame {frame} on clip {uid}"
             for uid, frame in (mine.get("clip_markers") or [])
             if (uid, frame) in their_markers]
    return hits


def _intervening(project: str, reel: str, revise_id: str, base: int,
                 head_generation: int, scope: dict) -> list[dict]:
    """Landed revisions this program was not planned over, with overlap.

    Each entry carries the other revision's id, its landed generation, its
    change summary and the overlapping targets (empty when it landed
    elsewhere on the reel). Ordered oldest landing first.
    """
    out = []
    for other in _landed_revisions(project, reel):
        if other.get("revise_id") == revise_id:
            continue
        landed = other["landed_generation"]
        if not base < landed <= head_generation:
            continue
        summary = ((other.get("change") or {}).get("summary")
                   or "no summary recorded")
        out.append({"revise_id": other["revise_id"],
                    "landed_generation": landed, "summary": summary,
                    "overlap": _scope_overlap(scope, other.get("scope"))})
    return out


def _recorded_reel(project: str, reel: str):
    """`(head generation, snapshot)` of the reel, or a refusal saying why."""
    from library.tools import edit_spec, timeline_shadow

    try:
        project_name = edit_spec._resolve_project_name(project)
        store = timeline_shadow.ShadowStore()
        head = store.head(project_name,
                          store.resolve_timeline(project_name, reel))
        if head is None:
            raise timeline_shadow.ShadowError(f"no generation of {reel!r}")
        return head, store.snapshot(head)
    except (edit_spec.EditSpecError, timeline_shadow.ShadowError) as exc:
        raise ReviseRefused(
            f"the reel {reel!r} has no recorded generation to patch",
            f"a patch names items of the reel as it was last read ({exc})",
            "open the reel in Resolve and run `ren timeline observe`, then "
            "`ren revise` the note again") from exc


def _items(snapshot: dict) -> dict:
    """`unique_id -> (row, position, track name, clip)` for picture rows."""
    found = {}
    for track in snapshot.get("tracks") or []:
        row = f"{str(track.get('type') or 'v')[0].upper()}{int(track['index'])}"
        for position, clip in enumerate(track.get("clips") or []):
            found[str(clip.get("unique_id"))] = (
                row, position, track.get("name") or "", clip)
    return found


def _clock(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes)}:{rest:04.1f}"


def region_summary(reel: str, snapshot: dict, operations: list[dict]) -> dict:
    """The change in creator terms, naming where it lands.

    Speaks the categories `timeline_diff.creative_summary` speaks, read
    off the rows the ops touch; the region is the reel, those rows and
    the seconds from the reel's start the touched items span.
    """
    from library.tools import timeline_diff

    items = _items(snapshot)
    fps = float(snapshot.get("fps") or 0.0)
    origin = int(snapshot.get("start_frame") or 0)
    changes, categories, rows, starts, ends = [], set(), [], [], []
    for op in operations:
        hit = items.get(str(op.get("unique_id")))
        if hit is None:
            frame = op.get("frame")
            target = str(op.get("unique_id") or "")
            if target:
                rows.append(f"target {target}")
            changes.append({"op": op["op"],
                            "row": (f"target {target}" if target
                                    else "timeline"),
                            "item": str(op.get("name") or ""),
                            "frame": frame})
            continue
        row, _position, name, clip = hit
        label = f"{row} {name}".strip()
        rows.append(label)
        categories |= timeline_diff.row_categories(label)
        starts.append(int(clip["record_in"]))
        ends.append(int(clip["record_out"]))
        before = (clip.get("transform") or {}).get(op.get("key"))
        change = {"op": op["op"], "row": label, "item": clip.get("name") or "",
                  "start_frame": int(clip["record_in"]),
                  "end_frame": int(clip["record_out"])}
        if op["op"] == "clip.set_property":
            change.update(key=op["key"], before=before, after=op["value"])
        elif op["op"] == "clip.set_enabled":
            change.update(enabled=bool(op["enabled"]))
        changes.append(change)
    rows = list(dict.fromkeys(rows))
    region = {"reel": reel, "rows": rows}
    if starts and fps:
        region.update(start_seconds=round((min(starts) - origin) / fps, 3),
                      end_seconds=round((max(ends) - origin) / fps, 3))
    count = len({c["item"] + str(c.get("start_frame")) for c in changes
                 if c.get("start_frame") is not None})
    what = _what_changed(changes)
    where = (f"on {', '.join(rows)}" if rows else "on the timeline")
    when = (f" from {_clock(region['start_seconds'])} to "
            f"{_clock(region['end_seconds'])}"
            if "start_seconds" in region else "")
    sentence = (f"{what} on {count} item{'s' if count != 1 else ''} "
                f"{where}{when} of {reel}." if count else
                f"{what} {where} of {reel}.")
    return {"region": region, "changes": changes,
            "changed_categories": [c for c in timeline_diff.CATEGORIES
                                   if c in categories],
            "summary": sentence}


def _what_changed(changes: list[dict]) -> str:
    keys = {}
    for change in changes:
        if change.get("key"):
            keys.setdefault(change["key"], set()).add(json.dumps(
                change["after"]))
    parts = [f"Set {key} to {', '.join(sorted(values))}"
             for key, values in keys.items()]
    switched = [c for c in changes if "enabled" in c]
    if switched:
        parts.append("Switched " + ("on" if switched[0]["enabled"]
                                    else "off"))
    markers = [c for c in changes if c["op"].startswith("marker.")]
    if markers:
        parts.append(f"Changed {len(markers)} timeline marker(s)")
    if not parts:
        operations = list(dict.fromkeys(c["op"] for c in changes))
        parts.extend(operations)
    return "; ".join(parts) or "Changed"




# ── 5. Execute, verify, record ──────────────────────────────────────


def _run_engine(argv: list[str]) -> int:
    """Run one `manage_project.py` subcommand; its output is the user's."""
    completed = subprocess.run(
        [sys.executable, str(_ENGINE / "manage_project.py"), *argv],
        cwd=str(_ENGINE), stdout=sys.stderr, stderr=sys.stderr,
        encoding="utf-8", check=False)
    return completed.returncode



def _note_for(project: str, record: dict) -> dict:
    routed = _find_note(project, record["note_id"])
    if routed is None:
        raise ReviseRefused(
            f"the note {record['note_id']!r} is no longer collected",
            "its outcome is recorded against the note it answers",
            "pull the notes again (`ren notes pull`) or give the note again")
    return asdict(routed)


def _patch(project: str, record: dict, spec: dict, verdict: dict,
           options: dict) -> dict:
    from library.tools import assignment_spine as spine
    from library.tools import edit_request, edit_spec, resolve_axi_core

    program = spec["program"]
    reel = program.get("reel") or record["reel"]
    if not reel:
        raise ReviseRefused(
            "the timeline program names no reel",
            "a semantic edit request needs one exact timeline",
            "answer the translation again naming the reel")
    head, snapshot = _recorded_reel(project, reel)
    operations = _program_ops(spec)
    scope = scope_for(operations)
    if str(head.generation) != str(program["base_generation"]):
        base = _program_base(program)
        intervening = (_intervening(
            project, reel, record["revise_id"], base, head.generation, scope)
            if base is not None else [])
        what = (f"{reel} changed since the note was translated "
                f"(generation {program['base_generation']} then, "
                f"{head.generation} now)")
        for entry in intervening:
            where = (f", also changing {', '.join(entry['overlap'])}"
                     if entry["overlap"] else " elsewhere on the reel")
            what += (f"; {entry['revise_id']} landed at generation "
                     f"{entry['landed_generation']}{where}: "
                     f"{entry['summary']}")
        why = ("the program names items as they were; applying it to what "
               "the reel holds now could change the wrong ones")
        if any(entry["overlap"] for entry in intervening):
            why += ("; another revision already changed the same targets, "
                    "so this program would undo that work")
        elif not intervening:
            why += ("; with no landed revision explaining the move, the "
                    "change came from a hand edit or a rebuild")
        raise ReviseRefused(
            what, why,
            f"run `ren revise {shlex.quote(project)} --id "
            f"{record['revise_id']} --retranslate`")
    missing = [op["unique_id"] for op in operations if op.get("unique_id")
               and str(op["unique_id"]) not in _items(snapshot)]
    if missing:
        raise ReviseRefused(
            f"the program names items {reel} does not carry: {missing}",
            "the recorded timeline no longer contains the requested targets",
            "answer the translation again from the listed timeline items")

    change = region_summary(reel, snapshot, operations)
    request = edit_request.from_edit_program(
        program, project=project, timeline=reel,
        originator={"kind": "human", "name": "ren revise",
                    "detail": record.get("note", "")})
    record["assignment_id"] = spine.attach_revision(
        project, reel, request=record["note"])["assignment_id"]
    act = spine.begin_revision(project, record["assignment_id"], reel)
    edit_spec.record_spec(project, spec)
    note = _note_for(project, record)
    try:
        prepared = resolve_axi_core.prepare_edit_request(request)
        record["edit_request"] = request.to_dict()
        record["preparation"] = prepared
        _save(project, record)
        receipt = resolve_axi_core.submit_edit_request(
            request, authorized=True, owner="ren revise")
        if isinstance(receipt.get("result"), dict):
            receipt = receipt["result"]
        record["execution_receipt"] = receipt
        _save(project, record)
        verified = _verified_completion(receipt)
        if not verified:
            _status(record, STATUS_INCOMPLETE,
                    execution_receipt=receipt)
            _save(project, record)
            raise ReviseRefused(
                f"AXI returned without verified completion on {reel}: "
                f"status={receipt.get('status')!r}, "
                f"stage={(receipt.get('lifecycle') or {}).get('stage')!r}, "
                f"detail={receipt.get('error') or receipt.get('message') or ''}",
                "ren revise completes only when AXI verifies the requested "
                "postconditions and records a committed generation; partial "
                "or uncommitted receipts remain unresolved",
                f"inspect the AXI receipt, then run `ren revise "
                f"{shlex.quote(project)} --id {record['revise_id']} "
                "--retranslate` if the request needs replanning")
        route = ROUTE_PATCH
        verification = {"state": "axi_verified", "receipt": receipt}
        landed_generation = receipt.get("generation", head.generation)
        status = STATUS_PATCHED
        _addressed(project, note, "revised through AXI",
                   verdict["reason"], change["summary"])
    except Exception as exc:
        spine.end_act(act, ok=False, detail=str(exc))
        if isinstance(exc, RenRefusal):
            raise
        raise ReviseRefused(
            f"AXI could not complete the requested edit on {reel}: {exc}",
            "the semantic request was prepared and routed before execution; "
            "an unsupported or unqualified backend is refused before write",
            f"inspect the routing or preparation detail, then run `ren revise "
            f"{shlex.quote(project)} --id {record['revise_id']} "
            "--retranslate` after the capability is available") from exc
    spine.end_act(act, ok=True, detail=change["summary"])
    base = _program_base(program)
    priors = [entry for entry in record.get("prior_bases", [])
              if type(entry) is int]
    floor = min(priors) if priors else base
    supersedes = (
        [entry["revise_id"] for entry in _intervening(
            project, reel, record["revise_id"], floor, base, scope)
         if entry["overlap"]]
        if floor is not None and base is not None else [])
    return {"status": status, "route": route, "change": change,
            "verification": verification,
            "landed_generation": landed_generation,
            "scope": scope, "supersedes": supersedes}


def _addressed(project: str, note: dict, action: str, rationale: str,
               summary: str) -> dict:
    """Record a change no check in `marker_resolution.CHECKS` proves."""
    from library.tools import marker_resolution

    out = marker_resolution.resolve_note(
        project, note, action=action, rationale=rationale,
        verifier="revise", outcome_details={"change_summary": summary})
    return out["record"]


def rebuild_commands(project: str, record: dict, spec: dict, *,
                     preview: bool = False) -> list[list]:
    """The scoped rebuild: the owners the spec names, then the one reel."""
    from library.tools import assignment_spine as spine
    from library.tools import edit_spec

    steps = edit_spec.rerun_steps(edit_spec.routes_for(spec))
    reel = (spec.get("program") or {}).get("reel") or next(
        (clause["reel"] for clause in spec.get("clauses") or ()
         if clause.get("reel")), "") or record["reel"]
    moment = spine._moment_for_timeline(project, reel) if reel else None
    commands = []
    if steps:
        commands.append(["run", project,
                         *[arg for step in steps for arg in ("--rerun", step)]])
    if spine._moments(project):
        command = ["build-reels", project]
        if moment:
            command.extend(["--only-reel", str(int(moment.number))])
        if preview:
            command.append("--preview")
        commands.append(command)
    elif not steps:
        commands.append(["run", project, "--rerun", "render"])
    return commands


def _shown(argv: list) -> str:
    verb = {"run": "edit", "build-reels": "build"}[argv[0]]
    return "ren " + " ".join([verb, *(shlex.quote(a) for a in argv[1:])])


def _rebuild(project: str, record: dict, spec: dict, verdict: dict,
             options: dict) -> dict:
    from library.tools import assignment_spine as spine
    from library.tools import edit_spec, feedback_outcomes

    commands = rebuild_commands(project, record, spec,
                                preview=bool(options.get("preview")))
    anchors = [(clause.get("anchor") or {}).get("stated")
               or (clause.get("anchor") or {}).get("phrase") or ""
               for clause in spec.get("clauses") or ()]
    reel = next((a[-1] for a in commands if a[0] == "build-reels"
                 and "--only-reel" in a), "")
    owners = edit_spec.rerun_steps(edit_spec.routes_for(spec))
    where = record["reel"] or (f"reel {reel}" if reel else "the edit")
    summary = (f"Rebuilding {where} from "
               f"{', '.join(owners) or 'its recorded edits'}"
               + (f" at \"{anchors[0]}\"" if anchors and anchors[0] else "")
               + ": " + "; ".join(clause["text"] for clause in
                                  spec.get("clauses") or ()))
    change = {"region": {"reel": record["reel"], "owners": owners,
                         "anchors": [a for a in anchors if a]},
              "changes": [{"op": clause["op"], "text": clause["text"]}
                          for clause in spec.get("clauses") or ()],
              "changed_categories": [],
              "summary": summary}
    note = _note_for(project, record)
    record["assignment_id"] = spine.attach_revision(
        project, record["reel"], request=record["note"])["assignment_id"]
    if record.get("status") != STATUS_INCOMPLETE:
        # A resumed rebuild already recorded its spec and its plan.
        edit_spec.record_spec(project, spec)
        feedback_outcomes.record_plan(project, note, action_kind="rebuild",
                                      summary=summary)
    run = options.get("run") or _run_engine
    for argv in commands:
        code = run(argv)
        if code != 0:
            return {"status": STATUS_INCOMPLETE, "route": ROUTE_REBUILD,
                    "change": change, "commands": [_shown(a) for a in commands],
                    "failed_command": _shown(argv), "exit_code": code}
    return {"status": STATUS_UNVERIFIED, "route": ROUTE_REBUILD,
            "change": change, "commands": [_shown(a) for a in commands],
            "verification": {"state": "not_proven",
                             "record": _addressed(
                                 project, note,
                                 f"rebuilt from {', '.join(owners) or 'the ledger'}",
                                 verdict["reason"], summary)}}


# ── The whole verb ──────────────────────────────────────────────────


def _result(project: str, record: dict) -> dict:
    """What the host reads: the state, and the one line to tell the person."""
    status = record["status"]
    reply = {
        STATUS_AWAITING: "Translating the note.",
        STATUS_QUESTION: (record.get("question") or {}).get("question"),
        STATUS_PROPOSED: "Here is what would change; nothing is changed yet.",
        STATUS_UNVERIFIED: ("The scoped rebuild ran, but the requested "
                            "outcome has no request-specific verification."),
        STATUS_REFUSED: record.get("refusal"),
        STATUS_INCOMPLETE: (
            f"The rebuild stopped at `{record.get('failed_command')}`."
            if record.get("failed_command") else
            "AXI did not return a committed, verified receipt; the note "
            "remains unresolved."),
    }.get(status) or (record.get("change") or {}).get("summary")
    out = {key: record.get(key) for key in (
        "revise_id", "status", "note", "note_id", "source", "reel", "anchor",
        "assignment_id",
        "spec_id", "proposal", "classification", "route", "change",
        "commands", "decisions", "verification", "question", "failed_command",
        "landed_generation", "scope", "supersedes", "prior_bases",
        "edit_request", "preparation", "execution_receipt")}
    out["reply"] = reply
    if status == STATUS_AWAITING:
        out["llm_request"] = record["request_path"]
    if status in (STATUS_AWAITING, STATUS_INCOMPLETE):
        suffix = (" --retranslate" if status == STATUS_INCOMPLETE
                  and record.get("execution_receipt") else "")
        out["resume"] = (f"ren revise {shlex.quote(project)} --id "
                         f"{record['revise_id']}{suffix}")
    if status == STATUS_QUESTION:
        out["answer_with"] = (f"ren revise {shlex.quote(project)} --id "
                              f"{record['revise_id']} --answer \"...\"")
    return {key: value for key, value in out.items() if value is not None}


def advance(project, revise_id: str, *, answer: str = "",
            plan_only: bool = False, retranslate: bool = False,
            **options) -> dict:
    """Carry one revision as far as it can go now, and say where it is."""
    from library.tools import edit_spec, llm_handshake

    project = _root(project)
    record = load(project, revise_id)
    preview = bool(options.get("preview") or record.get("preview_requested"))
    if preview:
        record["preview_requested"] = True
    if record["status"] in FINISHED:
        return _result(project, record)
    if (record["status"] == STATUS_INCOMPLETE
            and record.get("execution_receipt") and not retranslate
            and not answer):
        return _result(project, record)
    if answer:
        if record["status"] != STATUS_QUESTION:
            raise ReviseRefused(
                "there is no open question on this revision",
                "an answer is translated with the question it answers",
                f"run `ren revise {shlex.quote(project)} --id {revise_id}`")
        _reprepare(project, record, answer)
        _save(project, record)
    elif retranslate:
        _reprepare(project, record)
        _save(project, record)
    if not Path(llm_handshake.response_path(
            project, record["spec_id"])).is_file():
        return _result(project, record)
    spec = edit_spec.load_response_spec(project, record["spec_id"])
    proposal = edit_spec.proposal_for_spec(project, record["spec_id"])
    record["proposal"] = proposal
    if proposal["questions"]:
        _status(record, STATUS_QUESTION, question=proposal["questions"][0])
        return _result(project, _save(project, record))
    record["decisions"] = _fold_decisions(project, record, spec)
    note = _find_note(project, record["note_id"])
    classification = classify(spec, frame=note.frame if note else None)
    record["classification"] = classification["verdict"]
    if classification["route"] is None:
        from library.tools import feedback_outcomes

        if note is not None:
            feedback_outcomes.record_blocked(
                project, asdict(note),
                reason=classification["verdict"]["reason"])
        _status(record, STATUS_REFUSED,
                refusal=classification["verdict"]["reason"])
        return _result(project, _save(project, record))
    record["route"] = classification["route"]
    if plan_only:
        if classification["route"] == ROUTE_REBUILD:
            record["commands"] = [_shown(argv) for argv in
                                  rebuild_commands(
                                      project, record, spec,
                                      preview=preview)]
        elif classification["route"] == ROUTE_PATCH:
            from library.tools import edit_request, resolve_axi_core

            _reel = ((spec.get("program") or {}).get("reel")
                     or record["reel"])
            _request = edit_request.from_edit_program(
                spec["program"], project=project, timeline=_reel,
                originator={"kind": "human", "name": "ren revise",
                            "detail": record.get("note", "")})
            record["edit_request"] = _request.to_dict()
            record["preparation"] = resolve_axi_core.prepare_edit_request(
                _request)
        _status(record, STATUS_PROPOSED)
        return _result(project, _save(project, record))
    act = (_patch if classification["route"] == ROUTE_PATCH else _rebuild)
    try:
        options["preview"] = preview
        outcome = act(project, record, spec, classification["verdict"],
                      options)
    except RenRefusal as refused:
        record["refusal_detail"] = refused.to_dict()
        _save(project, record)
        raise
    record.update({key: value for key, value in outcome.items()
                   if key != "status"})
    _status(record, outcome["status"])
    return _result(project, _save(project, record))


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren revise",
        description="Turn an editor's note into a verified change: "
                    "translate it, show what would change, then let AXI "
                    "route a semantic edit or run a scoped rebuild.")
    parser.add_argument("project", help="project folder path")
    parser.add_argument("note", nargs="?", default="",
                        help="what to change, in the editor's own words")
    parser.add_argument("--reel", default="",
                        help="the reel's exact timeline name, if known")
    parser.add_argument("--note-id", default="",
                        help="revise a pulled Resolve marker note by its id")
    parser.add_argument("--at", default="", dest="at",
                        help="anchor a chat note to this delivered-file time "
                             "(seconds, M:SS or H:M:SS), e.g. `--at 0:12`; "
                             "maps to the timeline through the reel's "
                             "`.deliver.json` without Resolve")
    parser.add_argument("--id", default="", dest="revise_id",
                        help="continue this revision (default: the newest "
                             "open one)")
    parser.add_argument("--answer", default="",
                        help="answer the revision's open question")
    parser.add_argument("--retranslate", action="store_true",
                        help="translate the note again against the reel as "
                             "it is now")
    parser.add_argument("--plan", action="store_true", dest="plan_only",
                        help="stop after showing what would change")
    parser.add_argument(
        "--preview", action="store_true",
        help="render a watchable preview after a successful rebuild")
    args = parser.parse_args(argv)
    try:
        if args.note or args.note_id or args.at:
            result = start(args.project, args.note, note_id=args.note_id,
                           reel=args.reel, at=args.at,
                           plan_only=args.plan_only, preview=args.preview)
        else:
            revise_id = args.revise_id
            if not revise_id:
                newest = newest_open(args.project)
                if newest is None:
                    raise ReviseRefused(
                        "there is no open revision to continue",
                        "`ren revise` without a note continues one",
                        f'run `ren revise {shlex.quote(_root(args.project))} '
                        f'"<what to change>"`')
                revise_id = newest["revise_id"]
            result = advance(args.project, revise_id, answer=args.answer,
                             plan_only=args.plan_only,
                             retranslate=args.retranslate,
                             preview=args.preview)
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    except ValueError as exc:
        print(f"ren revise: {exc}", file=sys.stderr)
        return 2
    if result.get("status") == STATUS_AWAITING:
        print(f"LLM_REQUEST_READY: {result['llm_request']}")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") == STATUS_INCOMPLETE:
        return 1
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
