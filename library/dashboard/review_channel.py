"""
The review return channel: anchored notes, one batched send, agent replies
that land back next to the anchor they answer.

P2 (Ren consolidate, D3): the dashboard browser surface is retired. What
stays is this store and its agent side - the hook layer (`steer`) and
`reel_hearing.announce` queue notes here, and an agent polls and replies
from a shell (see below). Notes are still anchored: a note whose anchor
has no `selector` is REJECTED, because a note with no anchor is a page
comment, not a review note.

1. A note is attached to a SPECIFIC THING. The anchor is a CSS path plus
   the element's tag and visible text, measured where the note was
   written; the store keeps what it was given and never computes one.

2. A QUEUE-AND-SEND BATCH wakes an agent, and the agent REPLIES ONTO THE
   SAME PLACE. Several notes are queued, sent once, and the answer is
   threaded under the note that prompted it. `wait_for_batch` is the
   agent's wake-up: it blocks until a batch is sent. `add_reply` is the
   way back.

Storage is one JSON document per project at
`pipeline_output/review/channel.json`. Notes are stored per project and each
one records the view it was written on, so partitioning them per run later
is a filter, not a migration.

Agent side, from a shell:

    python3 -m library.dashboard.review_channel poll  --project <dir>
    python3 -m library.dashboard.review_channel reply --project <dir> \
        --batch <batch_id> --note <note_id> --text "what you did"
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from library.tools.project_layout import Area, ProjectLayout

# The captain's note text and the anchor's text excerpt are both bounded so a
# runaway selection cannot turn the channel document into a megabyte of DOM.
MAX_NOTE_CHARS = 4000
MAX_ANCHOR_TEXT_CHARS = 240

# Every state a note passes through. A note is queued in the browser, sent as
# part of exactly one batch, and answered when an agent replies to it.
NOTE_STATUSES = ("queued", "sent", "answered")

# WHO wrote a note. The channel could already say who wrote a REPLY -
# `add_reply` takes an `author` - and could not say who wrote a NOTE, so a
# machine-written note was indistinguishable from the captain's own. That
# asymmetry is what made the hook layer's `steer` action unbuildable
# without either a second feed or a lie, and both were worse than a field.
#
# `library/tools/hooks.py` writes ORIGIN_HOOK. Nothing else in this
# repository sets it: a note queued straight into the store is the
# captain's by construction rather than by trust.
ORIGIN_CAPTAIN = "captain"
ORIGIN_HOOK = "hook"
NOTE_ORIGINS = (ORIGIN_CAPTAIN, ORIGIN_HOOK)


def channel_path(project_dir: str | os.PathLike) -> Path:
    return ProjectLayout(project_dir).read_path(Area.REVIEW, "channel.json")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def load_channel(project_dir: str | os.PathLike) -> dict[str, Any]:
    """Read the channel document, or an empty one if nothing is stored yet."""
    path = channel_path(project_dir)
    if not path.exists():
        return {"notes": [], "batches": []}
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    doc.setdefault("notes", [])
    doc.setdefault("batches", [])
    return doc


def save_channel(project_dir: str | os.PathLike, doc: dict[str, Any]) -> None:
    path = channel_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    tmp.replace(path)


def normalise_anchor(anchor: dict[str, Any] | None) -> dict[str, Any]:
    """Keep exactly the fields the browser measured, and reject a bare page.

    `selector` is the CSS path the browser computed for the element the note
    is attached to. Without it the note is a page-level comment, which is the
    thing this channel exists NOT to be, so it raises rather than degrading.
    """
    anchor = anchor or {}
    selector = str(anchor.get("selector") or "").strip()
    if not selector:
        raise ValueError(
            "a note needs a browser-computed anchor.selector; "
            "a note with no anchor is a page comment, not a review note"
        )
    return {
        "selector": selector,
        "tag": str(anchor.get("tag") or "").strip().lower(),
        "text": str(anchor.get("text") or "")[:MAX_ANCHOR_TEXT_CHARS],
        "label": str(anchor.get("label") or "")[:MAX_ANCHOR_TEXT_CHARS],
        "view": str(anchor.get("view") or "").strip(),
        "step_id": str(anchor.get("step_id") or "").strip(),
    }


def queue_note(
    project_dir: str | os.PathLike,
    text: str,
    anchor: dict[str, Any] | None,
    origin: str = ORIGIN_CAPTAIN,
) -> dict[str, Any]:
    """Queue one anchored note. It is not visible to an agent until sent.

    `origin` says who wrote it, and defaults to the captain because that
    is who every caller before the hook layer was. An unknown origin
    raises rather than being stored: a note whose author cannot be read
    is exactly the thing the field exists to prevent.
    """
    body = str(text or "").strip()
    if not body:
        raise ValueError("a note needs text")
    if origin not in NOTE_ORIGINS:
        raise ValueError(
            f"unknown note origin {origin!r}. Known: {', '.join(NOTE_ORIGINS)}"
        )

    note = {
        "id": f"note_{uuid.uuid4().hex[:12]}",
        "text": body[:MAX_NOTE_CHARS],
        "anchor": normalise_anchor(anchor),
        "origin": origin,
        "status": "queued",
        "created_at": _now(),
        "sent_at": None,
        "batch_id": None,
        "replies": [],
    }
    doc = load_channel(project_dir)
    doc["notes"].append(note)
    save_channel(project_dir, doc)
    return note


def delete_note(project_dir: str | os.PathLike, note_id: str) -> bool:
    """Drop a note that has not been sent. A sent note is a record; it stays."""
    doc = load_channel(project_dir)
    for i, note in enumerate(doc["notes"]):
        if note["id"] == note_id:
            if note["status"] != "queued":
                raise ValueError(f"note {note_id} was already sent")
            doc["notes"].pop(i)
            save_channel(project_dir, doc)
            return True
    return False


def list_notes(
    project_dir: str | os.PathLike,
    view: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    notes = load_channel(project_dir)["notes"]
    if view:
        notes = [n for n in notes if n["anchor"].get("view") == view]
    if status:
        notes = [n for n in notes if n["status"] == status]
    return notes


def send_queued(
    project_dir: str | os.PathLike,
    note_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Send every queued note as ONE batch. This is what wakes the agent.

    Returns the batch, or None when there was nothing queued. `note_ids`
    restricts the send to a subset; the default is everything queued, which
    is the interaction the captain uses: write several notes, send once.
    """
    doc = load_channel(project_dir)
    queued = [n for n in doc["notes"] if n["status"] == "queued"]
    if note_ids is not None:
        wanted = set(note_ids)
        queued = [n for n in queued if n["id"] in wanted]
    if not queued:
        return None

    batch = {
        "id": f"batch_{uuid.uuid4().hex[:12]}",
        "created_at": _now(),
        "note_ids": [n["id"] for n in queued],
        "status": "pending",
        "delivered_at": None,
    }
    for note in queued:
        note["status"] = "sent"
        note["sent_at"] = batch["created_at"]
        note["batch_id"] = batch["id"]
    doc["batches"].append(batch)
    save_channel(project_dir, doc)
    return batch


def pending_batch(project_dir: str | os.PathLike) -> dict[str, Any] | None:
    """The oldest batch no agent has answered yet, with its notes attached."""
    doc = load_channel(project_dir)
    by_id = {n["id"]: n for n in doc["notes"]}
    for batch in doc["batches"]:
        if batch["status"] == "pending":
            payload = dict(batch)
            payload["notes"] = [by_id[i] for i in batch["note_ids"] if i in by_id]
            return payload
    return None


def mark_delivered(project_dir: str | os.PathLike, batch_id: str) -> None:
    """Record that an agent has picked the batch up, for the surface to show."""
    doc = load_channel(project_dir)
    for batch in doc["batches"]:
        if batch["id"] == batch_id and not batch.get("delivered_at"):
            batch["delivered_at"] = _now()
            save_channel(project_dir, doc)
            return


def add_reply(
    project_dir: str | os.PathLike,
    batch_id: str,
    text: str,
    note_ids: list[str] | None = None,
    author: str = "agent",
) -> dict[str, Any]:
    """Reply onto the same surface, next to the anchors the notes point at.

    A reply always lands on notes, never only on the batch: with `note_ids`
    it answers those notes, and without it the same text is threaded under
    every note in the batch. That is the property being adopted - the captain
    reads the answer where they wrote the note, not in a separate chat.
    """
    body = str(text or "").strip()
    if not body:
        raise ValueError("a reply needs text")

    doc = load_channel(project_dir)
    batch = next((b for b in doc["batches"] if b["id"] == batch_id), None)
    if batch is None:
        raise KeyError(f"unknown batch {batch_id}")

    targets = list(note_ids) if note_ids else list(batch["note_ids"])
    unknown = [i for i in targets if i not in set(batch["note_ids"])]
    if unknown:
        raise ValueError(f"notes {unknown} are not in batch {batch_id}")

    reply = {
        "id": f"reply_{uuid.uuid4().hex[:12]}",
        "batch_id": batch_id,
        "author": author,
        "text": body,
        "created_at": _now(),
    }
    for note in doc["notes"]:
        if note["id"] in targets:
            note["replies"].append(reply)
            note["status"] = "answered"

    # The batch closes once every note in it has an answer, so a partial
    # reply leaves the batch pending and the agent keeps the floor.
    answered = {n["id"] for n in doc["notes"] if n["status"] == "answered"}
    if all(i in answered for i in batch["note_ids"]):
        batch["status"] = "answered"
    save_channel(project_dir, doc)
    return reply


def wait_for_batch(
    project_dir: str | os.PathLike,
    timeout_s: float | None = None,
    poll_interval_s: float = 0.5,
) -> dict[str, Any] | None:
    """Block until a batch is waiting, then hand it over. The agent's wake-up.

    `timeout_s=None` waits forever, which is how an agent parks between the
    captain's sends. Returns None only on timeout.
    """
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while True:
        batch = pending_batch(project_dir)
        if batch:
            mark_delivered(project_dir, batch["id"])
            return batch
        if deadline is not None and time.monotonic() >= deadline:
            return None
        time.sleep(poll_interval_s)


# ── Agent CLI ───────────────────────────────────────────────────────

def _describe(batch: dict[str, Any]) -> str:
    lines = [f"batch {batch['id']} - {len(batch['notes'])} note(s)"]
    for note in batch["notes"]:
        anchor = note["anchor"]
        where = anchor.get("view") or "?"
        lines.append(
            f"  {note['id']}  [{where}] <{anchor.get('tag')}> {anchor.get('selector')}"
        )
        if anchor.get("text"):
            lines.append(f"      on screen: {anchor['text']}")
        lines.append(f"      note: {note['text']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.dashboard.review_channel",
        description="Agent side of the review return channel.",
    )
    parser.add_argument("command", choices=["poll", "reply", "list"])
    parser.add_argument("--project", required=True, help="Project directory")
    parser.add_argument("--timeout", type=float, default=None,
                        help="Seconds to wait for a batch (default: wait forever)")
    parser.add_argument("--batch", help="Batch id to reply to")
    parser.add_argument("--note", action="append", default=[],
                        help="Reply to this note only (repeatable)")
    parser.add_argument("--text", help="Reply text")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args(argv)

    if args.command == "list":
        print(json.dumps(list_notes(args.project), indent=2))
        return 0

    if args.command == "poll":
        batch = wait_for_batch(args.project, timeout_s=args.timeout)
        if batch is None:
            print(json.dumps({"status": "timeout"}) if args.json else "timeout")
            return 1
        print(json.dumps(batch, indent=2) if args.json else _describe(batch))
        return 0

    if not args.batch or not args.text:
        parser.error("reply needs --batch and --text")
    reply = add_reply(args.project, args.batch, args.text, note_ids=args.note or None)
    print(json.dumps(reply, indent=2) if args.json else f"replied {reply['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
