"""`ren round-page`: one generated review page per review round.

Why this exists
---------------
After a build the editor had to open DaVinci Resolve, find each reel's
timeline by name and play it, then type notes as Resolve markers (plan
item T13, "watch, don't operate"). `ren preview` (T12) made a file to
watch per reel; this page puts the round in front of the editor in one
place, with nothing to operate:

- each reel's newest PREVIEW, playable in the page;
- the CREATIVE CHANGE SUMMARY per reel: the creative commits recorded
  for that reel since the round opened, each in `timeline_diff.
  creative_summary`'s own words (the sentence `ren commits` prints);
- the notes that OPENED the round on that reel and what was done with
  each - the outcome report's own cards (`feedback_outcomes.
  render_cards`), not a second renderer;
- the OUTSTANDING QUESTIONS: an open revision's question, and every
  note on these reels that no revision has taken up yet;
- a "note at this time" control per reel that stores a note at the
  moment the editor paused on.

How a note gets from the page into the note store
-------------------------------------------------
The page is a static file - Ren runs no server (AGENTS.md 4: the
dashboard is retired). A note leaves the page as ONE command,

    ren notes add <project> --reel N --file <preview> --at <s> '<note>'

which `delivered_note_anchor.add_note` runs: the preview's second maps
to the timeline frame through the preview's record and the timeline's
own clock, and the note lands in the T11 store (a pull file in the
marker-feedback area) with a stable note id, so `ren notes <p>` lists it
with its file-time anchor and `ren revise <p> --note-id <id>` changes
the reel. Opened through the Lavish review loop (`--open`), the control
queues that command as a prompt for the agent polling the session
(`window.lavish.queuePrompt`); opened as a plain file, it copies the
command for the editor to paste. Either way the command is shown.

One page per round
------------------
The round is `versions.rounds`' own: discovered from the note ledger and
the promotions, never declared. The page is `pipeline_output/previews/
round-<N>.html`, beside the previews it plays, so its video sources are
relative and it opens the same way from Finder, a browser or Lavish.
Regenerating the same round rewrites the same page; `ren preview`
regenerates it after every preview.

`tests/scenarios/test_round_page.py`.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

FORMAT = "ren_round_page/1"
PAGE_PREFIX = "round-"
NOTE_PROMPT_KIND = "ren_round_note"
"""`data.kind` on the prompt the page queues into a Lavish session."""


class RoundPageRefused(RenRefusal):
    """The round's page cannot be written, and this says why."""

    CODE = "REN_ROUND_PAGE_REFUSED"


# ── What the round holds ────────────────────────────────────────────


def _round(project: str) -> dict:
    from library.tools.versions import rounds

    try:
        current = rounds.discover(project)[-1]
    except rounds.RoundsUnreadable as unreadable:
        raise RoundPageRefused(
            "the project's rounds record cannot be read",
            str(unreadable),
            "restore pipeline_output/review/rounds.json from the project's "
            "git history, then write the page again") from None
    return {"number": int(current["round"]),
            "opened_at": current.get("opened_at") or "",
            "opened_by": list(current.get("opened_by") or ()),
            "why": current.get("why") or ""}


def _previews(project: str) -> dict:
    """`{timeline: preview}` for every reel's newest preview on disk."""
    from library.tools import reel_preview

    index = reel_preview.previews_dir(project) / reel_preview.INDEX_FILE
    try:
        doc = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    events = list(doc.get("events") or ())
    found = {}
    for timeline, latest in (doc.get("latest") or {}).items():
        path = Path(str(latest.get("path") or ""))
        if not path.is_file():
            continue
        event = next((e for e in reversed(events)
                      if e.get("timeline") == timeline
                      and e.get("path") == latest.get("path")), {})
        try:
            sidecar = json.loads(reel_preview._sidecar(path).read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            sidecar = {}
        found[timeline] = {
            "number": event.get("reel", sidecar.get("reel")),
            "timeline": timeline,
            "path": str(path),
            "engine": latest.get("engine") or sidecar.get("engine") or "",
            "at": latest.get("at") or "",
            "duration_seconds": (sidecar.get("duration_seconds")
                                 or event.get("duration_seconds")),
            "headless_refused": sidecar.get("headless_refused") or "",
            "losses": list(sidecar.get("losses") or ()),
            # The measured frame, so the player has its shape before a
            # byte of the file is fetched.
            "size": [((sidecar.get("verification") or {}).get("measured")
                      or {}).get(k) for k in ("width", "height")],
        }
    return found


def _unpreviewed(project: str, previewed: set) -> list[dict]:
    """Approved reels with no preview yet, and the verb that makes one."""
    from library.tools import reel_preview

    try:
        targets = reel_preview.reel_targets(project)
    except RenRefusal:
        return []
    quoted = shlex.quote(project)
    missing = []
    for target in targets:
        if target["timeline"] in previewed:
            continue
        missing.append({
            "number": target["number"], "timeline": target["timeline"],
            "why": ("built, not previewed yet" if target["built"]
                    else "approved, not built yet"),
            "command": (f"ren preview {quoted} --reel {target['number']}"
                        if target["built"] else f"ren build {quoted}")})
    return missing


def _after(stamp: str, opened_at: str) -> bool:
    """Whether a record stamped `stamp` landed in a round opened at
    `opened_at` ("" opens at the beginning of time)."""
    if not opened_at:
        return True
    from library.tools.feedback_outcomes import _parse_time

    when, since = _parse_time(stamp), _parse_time(opened_at)
    if when is None or since is None:
        return True
    return when >= since


def _changes(project: str, timeline: str, opened_at: str) -> list[dict]:
    """The creative commits on this reel since the round opened."""
    from library.tools import creative_history, undo_journal

    changes = []
    for commit in undo_journal.list_commits(project):
        if commit.get("timeline") != timeline:
            continue
        if not _after(str(commit.get("recorded_at") or ""), opened_at):
            continue
        creative = creative_history._creative_summary(commit)
        changes.append({
            "commit": str(commit.get("id") or "")[:12],
            "intent": str(commit.get("intent") or ""),
            "summary": creative["summary"],
            "changed": list(creative["changed_categories"]),
            "at": str(commit.get("recorded_at") or "")})
    return changes


def _assignment_reels(project: str) -> dict:
    """`{timeline: reel entry}` off the assignment records."""
    from library.tools import assignment_lifecycle

    found = {}
    for record in assignment_lifecycle.list_records(project):
        for reel in (record.get("reels") or {}).values():
            if reel.get("name"):
                found[reel["name"]] = reel
    return found


def _next_act(project: str, timeline: str, reel: dict | None) -> dict:
    quoted = shlex.quote(project)
    status = (reel or {}).get("status") or ""
    if status == "built":
        return {"status": status, "why": "built and waiting for your yes",
                "command": f"ren sign-off {quoted} {shlex.quote(timeline)}"}
    if status == "signed_off":
        return {"status": status, "why": "approved, not delivered yet",
                "command": f"ren deliver {quoted} {reel['number']}"}
    if status:
        return {"status": status, "why": status.replace("_", " "),
                "command": ""}
    return {"status": "", "why": "", "command": ""}


def _questions(project: str, timelines: set) -> list[dict]:
    """What the round still waits on the editor for.

    An open revision's question first (the revision cannot move until
    it is answered), then every note on these reels no revision has
    taken up yet.
    """
    from library.tools import marker_routing, revise

    quoted = shlex.quote(project)
    questions = []
    for record in revise.open_records(project):
        asked = (record.get("question") or {}).get("question")
        if record.get("status") != revise.STATUS_QUESTION or not asked:
            continue
        questions.append({
            "kind": "revise", "reel": record.get("reel") or "",
            "note": record.get("note") or "", "question": asked,
            "command": (f"ren revise {quoted} --id "
                        f"{shlex.quote(record['revise_id'])} --answer "
                        f"'<your answer>'")})
    taken = {str(record.get("note_id") or "")
             for record in revise.records(project)}
    for note in marker_routing.route_project(project):
        if timelines and note.timeline not in timelines:
            continue
        if note.edit_link_id in taken:
            continue
        questions.append({
            "kind": "note", "reel": note.timeline, "note": note.text,
            "note_id": note.edit_link_id,
            "where": (marker_routing._watched_line(note)
                      or note.timecode or ""),
            "command": (f"ren revise {quoted} --note-id "
                        f"{shlex.quote(note.edit_link_id)}")})
    # Post-delivery creative QA (T17) asks here, never on the timeline:
    # a subjective observation is an editorial call, so it becomes a
    # question with no edit behind it. Delivered reels need no preview
    # for their questions to show - delivery is the stronger signal.
    for entry in _qa_questions(project):
        questions.append({
            "kind": "creative_qa", "reel": entry["timeline"],
            "note": entry["question"],
            "where": entry.get("where") or "",
            "command": entry["command"]})
    return questions


def _qa_questions(project: str) -> list[dict]:
    """The open creative-QA questions, with their file-time span."""
    from library.tools import post_delivery_qa

    try:
        found = post_delivery_qa.open_questions(project)
    except Exception:  # noqa: BLE001 - questions never sink the page
        return []
    shaped = []
    for entry in found:
        span = entry.get("span") or {}
        where = ""
        try:
            if "start" in span and "end" in span:
                where = (f"{float(span['start']):.2f}-"
                         f"{float(span['end']):.2f}s")
        except (TypeError, ValueError):
            where = ""
        shaped.append({"timeline": entry.get("timeline") or "",
                       "question": entry.get("question") or "",
                       "where": where,
                       "command": entry.get("command") or ""})
    return [entry for entry in shaped if entry["question"]]


def _round_notes(project: str, opened_by: list) -> dict:
    """`{reel: [outcome note]}` for the notes that opened this round."""
    from library.tools import feedback_outcomes

    wanted = set(opened_by)
    if not wanted:
        return {}
    found: dict = {}
    for note in feedback_outcomes.build(project)["notes"]:
        if note["identity"] in wanted:
            found.setdefault(note["reel"], []).append(note)
    return found


def _project_name(project: str) -> str:
    """The name the project declares, else its folder's."""
    try:
        from library.schemas.project_config import load_project_config
        config = load_project_config(Path(project) / "project.yaml",
                                     migrate_format=False, strict=False)
        return str(getattr(config, "name", "") or Path(project).name)
    except Exception:  # noqa: BLE001 - a title is never worth a refusal
        return Path(project).name


def build(project: str) -> dict:
    """Everything the round's page shows, as data."""
    from library.tools.feedback_ledger import base_reel_name

    project = str(Path(project).expanduser().resolve())
    current = _round(project)
    previews = _previews(project)
    assignment = _assignment_reels(project)
    round_notes = _round_notes(project, current["opened_by"])
    reels = []
    for timeline, preview in previews.items():
        reels.append({
            "number": preview["number"],
            "timeline": timeline,
            "preview": preview,
            "changes": _changes(project, timeline, current["opened_at"]),
            "next": _next_act(project, timeline, assignment.get(timeline)),
            "notes": round_notes.get(base_reel_name(timeline), []),
            "note_command": (
                f"ren notes add {shlex.quote(project)} --reel "
                f"{preview['number']} --file "
                f"{shlex.quote(preview['path'])}"),
        })
    reels.sort(key=lambda r: (r["number"] is None, r["number"] or 0,
                              r["timeline"]))
    return {
        "format": FORMAT,
        "project": project,
        "project_name": _project_name(project),
        "round": current,
        "reels": reels,
        "unpreviewed": _unpreviewed(project, set(previews)),
        "questions": _questions(project, set(previews)),
    }


# ── The page ────────────────────────────────────────────────────────


def page_path(project: str, number: int) -> Path:
    from library.tools import reel_preview

    return reel_preview.previews_dir(project) / f"{PAGE_PREFIX}{number}.html"


_PAGE_STYLE = (
    ".round{color:var(--muted);margin:0 0 6px;font-size:.85rem;"
    "text-transform:uppercase;letter-spacing:.08em}"
    "h2{font-size:1.35rem;margin:36px 0 8px;letter-spacing:-.01em}"
    ".reel-card{background:var(--card);border:1px solid var(--line);"
    "border-radius:16px;padding:20px;margin:16px 0;"
    "box-shadow:0 3px 14px #18211e0a;display:grid;"
    "grid-template-columns:minmax(0,300px) minmax(0,1fr);gap:22px}"
    ".reel-card video{width:100%;border-radius:10px;background:#000;"
    "display:block}"
    ".reel-card h3{margin:0 0 4px;font-size:1.15rem;overflow-wrap:anywhere}"
    ".meta{color:var(--muted);font-size:.85rem;margin:0 0 12px;"
    "overflow-wrap:anywhere}"
    ".changes{margin:0 0 14px;padding-left:20px;color:var(--ink)}"
    ".changes li{margin:4px 0}.changes .intent{color:var(--muted)}"
    ".next{font-size:.9rem;margin:0 0 14px}"
    ".note-form{border-top:1px solid var(--line);padding-top:14px;"
    "display:grid;gap:8px}"
    ".note-form textarea{font:inherit;min-height:70px;padding:9px 11px;"
    "border:1px solid var(--line);border-radius:10px;resize:vertical;"
    "width:100%}"
    ".row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}"
    "button{font:inherit;font-size:.9rem;border-radius:999px;"
    "padding:7px 14px;border:1px solid var(--green);cursor:pointer;"
    "background:var(--card);color:var(--green)}"
    "button[type=submit]{background:var(--green);color:#fff}"
    ".status{color:var(--muted);font-size:.85rem;margin:0;min-height:1.2em}"
    "pre.command{margin:0;white-space:pre-wrap;overflow-wrap:anywhere;"
    "font-size:.8rem;background:#edf3ee;border-radius:8px;padding:8px 10px}"
    ".questions li{margin:10px 0;overflow-wrap:anywhere}"
    ".questions code,.next code{background:#edf3ee;border-radius:6px;"
    "padding:1px 5px}"
    ".warn{color:var(--amber);font-size:.85rem}"
    "@media(max-width:700px){.reel-card{grid-template-columns:minmax(0,1fr)}}"
)

_PAGE_SCRIPT = r"""
(function () {
  const data = JSON.parse(document.getElementById('ren-round').textContent);
  const shq = (s) => "'" + String(s).replace(/'/g, "'\\''") + "'";
  const clock = (t) => {
    const m = Math.floor(t / 60), s = t - m * 60;
    return m + ':' + (s < 10 ? '0' : '') + s.toFixed(1);
  };
  window.renRoundNote = function (reel, seconds, words) {
    return reel.note_command + ' --at ' + seconds.toFixed(3) + ' ' + shq(words);
  };
  for (const reel of data.reels) {
    const card = document.querySelector('[data-reel-timeline="' + CSS.escape(reel.timeline) + '"]');
    if (!card) continue;
    const video = card.querySelector('video');
    const form = card.querySelector('form.note-form');
    const stamp = form.querySelector('button.stamp');
    const status = form.querySelector('.status');
    const shown = form.querySelector('pre.command');
    let at = null;
    const label = () => { stamp.textContent = 'Note at ' + clock(at === null ? video.currentTime : at); };
    video.addEventListener('timeupdate', () => { if (at === null) label(); });
    // Playing on means the stamped moment was not the one: follow the picture again.
    video.addEventListener('play', () => { at = null; label(); });
    stamp.addEventListener('click', () => { video.pause(); at = video.currentTime; label(); form.note.focus(); });
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const words = form.note.value.trim();
      if (!words) { status.textContent = 'Type what should change first.'; return; }
      const seconds = at === null ? video.currentTime : at;
      const command = window.renRoundNote(reel, seconds, words);
      shown.textContent = command;
      shown.hidden = false;
      if (window.lavish && typeof window.lavish.queuePrompt === 'function') {
        window.lavish.queuePrompt(
          'Store this review note, then revise the reel. Run: `' + command +
          '` - it prints the note id; then run `ren revise ' + data.project_quoted +
          ' --note-id <that id>`.',
          { tag: 'ren-note', text: reel.timeline + ' @ ' + clock(seconds) + ': ' + words,
            element: form,
            queueKey: 'ren-note-' + reel.timeline + '-' + seconds.toFixed(3),
            data: { kind: data.prompt_kind, project: data.project, reel: reel.number,
                    timeline: reel.timeline, file: reel.file, at_seconds: Number(seconds.toFixed(3)),
                    note: words, command: command } });
        status.textContent = 'Queued at ' + clock(seconds) + ' - send it to the agent from the Lavish bar.';
      } else {
        try { await navigator.clipboard.writeText(command); status.textContent = 'Copied - paste it into your terminal or chat.'; }
        catch (e) { status.textContent = 'Copy the command below into your terminal or chat.'; }
      }
      form.note.value = '';
      at = null;
      label();
    });
    label();
  }
})();
"""


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def _clock(seconds) -> str:
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return "?"
    minutes, rest = divmod(total, 60.0)
    return f"{int(minutes)}:{rest:04.1f}"


def _aspect(preview: dict) -> str:
    width, height = (preview.get("size") or [None, None])[:2]
    if not (isinstance(width, int) and isinstance(height, int)
            and width > 0 and height > 0):
        return ""
    return f' style="aspect-ratio:{width}/{height}"'


def _reel_card(reel: dict, page_dir: Path) -> str:
    preview = reel["preview"]
    source = os.path.relpath(preview["path"], page_dir)
    meta = [f"preview by {preview['engine'] or 'unknown engine'}",
            f"{_clock(preview['duration_seconds'])} long"]
    if preview.get("at"):
        meta.append(f"made {preview['at']}")
    warn = ""
    if preview.get("losses"):
        warn = ('<p class="warn">Not drawn in this preview: '
                + _esc("; ".join(str(x) for x in preview["losses"])) + "</p>")
    if reel["changes"]:
        changes = "".join(
            f"<li>{_esc(change['summary'])} "
            f"<span class=\"intent\">({_esc(change['intent'])})</span></li>"
            for change in reel["changes"])
        changes = f"<ul class=\"changes\">{changes}</ul>"
    else:
        changes = ('<p class="meta">No change recorded on this reel this '
                   'round - this is the cut as built.</p>')
    nxt = reel["next"]
    next_html = ""
    if nxt["command"]:
        next_html = (f"<p class=\"next\">{_esc(nxt['why'].capitalize())}: "
                     f"<code>{_esc(nxt['command'])}</code></p>")
    elif nxt["why"]:
        next_html = f"<p class=\"next\">{_esc(nxt['why'].capitalize())}.</p>"
    from library.tools import feedback_outcomes

    notes = feedback_outcomes.render_cards(reel["notes"])
    notes_html = (f"<h4>Notes that opened this round</h4>{notes}"
                  if notes else "")
    title = reel["timeline"]
    if reel["number"] is not None:
        meta.insert(0, f"reel {reel['number']}")
    # `preload="none"`: nothing is fetched until the editor presses play.
    # Lavish loads a page more than once while it settles, and a video
    # fetch a superseded load abandons is reported to the polling agent as
    # a fatal "asset unavailable" - measured on this page, gone with this.
    return (
        f"<article class=\"reel-card\" data-reel-timeline=\"{_esc(reel['timeline'])}\">"
        f"<div><video controls preload=\"none\" playsinline{_aspect(preview)} "
        f"src=\"{_esc(Path(source).as_posix())}\"></video></div>"
        f"<div><h3>{_esc(title)}</h3>"
        f"<p class=\"meta\">{_esc(' · '.join(meta))}</p>{warn}"
        f"<h4>What changed</h4>{changes}{next_html}{notes_html}"
        "<form class=\"note-form\" data-lavish-question=\"note-"
        f"{_esc(reel['timeline'])}\">"
        "<div class=\"row\"><button type=\"button\" class=\"stamp\">"
        "Note at 0:00.0</button></div>"
        "<textarea name=\"note\" aria-label=\"Your note at this moment\" "
        "placeholder=\"Pause where something should change, then say what.\">"
        "</textarea>"
        "<div class=\"row\"><button type=\"submit\">Send note</button></div>"
        "<p class=\"status\" role=\"status\" aria-live=\"polite\"></p>"
        "<pre class=\"command\" hidden></pre></form></div></article>")


def _questions_html(document: dict) -> str:
    items = []
    for question in document["questions"]:
        if question["kind"] == "revise":
            items.append(
                f"<li><strong>{_esc(question['reel'] or 'A revision')}</strong>"
                f" asks: {_esc(question['question'])}<br>"
                f"<span class=\"meta\">About: {_esc(question['note'])}</span>"
                f"<br>Answer: <code>{_esc(question['command'])}</code></li>")
        else:
            where = f" @ {question['where']}" if question.get("where") else ""
            items.append(
                f"<li><strong>{_esc(question['reel'])}</strong>{_esc(where)}:"
                f" {_esc(question['note'])}<br>"
                f"Noted, not revised yet: <code>{_esc(question['command'])}"
                f"</code></li>")
    for reel in document["unpreviewed"]:
        items.append(
            f"<li><strong>{_esc(reel['timeline'])}</strong>: "
            f"{_esc(reel['why'])} - <code>{_esc(reel['command'])}</code></li>")
    if not items:
        return "<p class=\"empty\">Nothing is waiting on you.</p>"
    return f"<ul class=\"questions\">{''.join(items)}</ul>"


def render(document: dict, page_dir: Path) -> str:
    from library.tools import feedback_outcomes

    current = document["round"]
    cards = "".join(_reel_card(reel, page_dir) for reel in document["reels"])
    if not cards:
        cards = ("<p class=\"empty\">No reel has a preview yet. Run "
                 f"<code>ren preview {_esc(shlex.quote(document['project']))}"
                 "</code>, then write this page again.</p>")
    embedded = {
        "project": document["project"],
        "project_quoted": shlex.quote(document["project"]),
        "prompt_kind": NOTE_PROMPT_KIND,
        "round": current["number"],
        "reels": [{"number": reel["number"], "timeline": reel["timeline"],
                   "file": reel["preview"]["path"],
                   "note_command": reel["note_command"]}
                  for reel in document["reels"]],
    }
    data = json.dumps(embedded, ensure_ascii=False).replace("</", "<\\/")
    title = f"Round {current['number']} - {document['project_name']}"
    return (
        "<!doctype html><html lang=\"en\" data-lavish-annotate=\"off\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_esc(title)}</title>"
        f"<style>{feedback_outcomes.STYLE}{_PAGE_STYLE}</style></head>"
        "<body><main>"
        f"<p class=\"round\">Review round {current['number']}</p>"
        f"<h1>{_esc(document['project_name'])}</h1>"
        f"<p class=\"sub\">{_esc(current['why'])}. Watch each reel, pause "
        "where something should change, and send a note from that moment."
        "</p>"
        f"<h2>Reels</h2>{cards}"
        f"<h2>Waiting on you</h2>{_questions_html(document)}"
        "</main>"
        f"<script type=\"application/json\" id=\"ren-round\">{data}</script>"
        f"<script>{_PAGE_SCRIPT}</script>"
        "</body></html>")


def write(project: str) -> dict:
    """Build the round's document and write its page. Returns both."""
    document = build(project)
    path = page_path(document["project"], document["round"]["number"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(render(document, path.parent), encoding="utf-8")
    temporary.replace(path)
    return {**document, "page": str(path)}


def open_page(path: str) -> str:
    """Put the page in front of the editor: Lavish when it is installed
    (notes then reach the agent polling the session), else the system
    browser (notes are copied as commands). Returns what opened it."""
    lavish = shutil.which("lavish-axi")
    if lavish:
        done = subprocess.run([lavish, path], capture_output=True,
                              encoding="utf-8", check=False)
        if done.returncode == 0:
            return (done.stdout or "").strip() or "lavish"
    opener = shutil.which("open") or shutil.which("xdg-open")
    if opener:
        subprocess.run([opener, path], capture_output=True,
                       encoding="utf-8", check=False)
        return "browser"
    return ""


# ── The verb ────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ren round-page",
        description="Write this review round's page: each reel's newest "
                    "preview, what changed, what is waiting on you, and "
                    "a note-at-this-time control.")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--open", action="store_true", dest="open_page",
                        help="open the page (in Lavish when installed, so "
                             "notes reach the agent; else the browser)")
    parser.add_argument("--json", action="store_true",
                        help="print the page's document as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    args = build_parser().parse_args(argv)
    try:
        from library.tools.reel_preview import _project_folder

        project = _project_folder(args.project)
        written = write(project)
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    if args.json:
        print(json.dumps(written, indent=2, default=str))
    else:
        print(f"Round {written['round']['number']}: "
              f"{len(written['reels'])} reel(s) to watch, "
              f"{len(written['questions']) + len(written['unpreviewed'])} "
              "thing(s) waiting on you.")
        print(f"Review page: {written['page']}")
    if args.open_page:
        opened = open_page(written["page"])
        if opened and not args.json:
            print(opened)
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
