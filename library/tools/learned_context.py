"""What the run writes back so it stops repeating its own mistakes.

The captain: *"the LLM can record new bits of information that it learns
like feedback into the context for the project."* The repo's law says
captain inputs are never written to (`project_layout.Kind.INPUT`), so
the write-back is a SEPARATE area - `learned_context/`, pipeline-owned
(`project_layout.Area.LEARNED_CONTEXT`), read alongside the captain's
`context/` on every later run.

What a learning IS
------------------
One of three kinds. The kind decides who said it and who reads it:

``correction``
    The captain corrected the model - a routed timeline note that
    overturned a decision, review feedback, a grillme answer that
    settled an argument. Said by the CAPTAIN, read by the step that
    owns the overturned decision (`read_by` names it).
``mistake_fix``
    The pipeline caught its own error and how it fixed it - a QA
    finding answered, a retry that worked. Concluded by the PIPELINE,
    read by the step that made the mistake.
``settled_decision``
    A choice now fixed, asked once and never again - a series identity
    settled, a caption style approved. Concluded TOGETHER (the captain
    said it, the pipeline recorded it), read by every step deciding
    that thing.

Every kind names the step that reads it, and a learning that names
none is REFUSED at record time - AGENTS.md 10.4's rule ("a learning
nothing consumes is the same defect" as a QA finding with no reader).
`kinds_and_readers()` is the report; `unread_kinds()` names any kind
nothing reads (today: none, and the test pins that).

Retirement and correction
-------------------------
A learning that turns out wrong is RETIRED or CORRECTED, never
silently edited. `retire()` marks it retired with a reason;
`correct()` supersedes it with a new learning linked by
`supersedes`. Both append to the learning's own `history`, so the file
is an account of what the project believed and when, not an
append-only pile of stale conclusions. Only `active` learnings reach
a prompt.

Storage
-------
`<project>/learned_context/learnings.json` - one JSON list, written
atomically through `ProjectLayout.write_path`, so the area law holds
for the writer too. ids are `lc-NNNN`, allocated monotonically and
never reused, so a retired id still names the thing it named.

`tests/test_learned_context.py`.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile

LEARNINGS_FILE = "learnings.json"

CORRECTION = "correction"
MISTAKE_FIX = "mistake_fix"
SETTLED_DECISION = "settled_decision"

KINDS = (CORRECTION, MISTAKE_FIX, SETTLED_DECISION)

#: Who said it, per kind. Rendered on every learning so the captain can
#: always tell what they said from what the pipeline concluded.
SAID_BY = {
    CORRECTION: "the captain",
    MISTAKE_FIX: "the pipeline",
    SETTLED_DECISION: "the captain, recorded by the pipeline",
}

ACTIVE = "active"
RETIRED = "retired"
SUPERSEDED = "superseded"

#: `read_by: ["*"]` - a settled fact every planning step should know
#: (the series this video is - or is not - in).
GLOBAL = "*"


class LearnedContextError(ValueError):
    """A learning that cannot be acted on, refused by name."""


def _node_ids() -> set:
    """Every DAG node id, in either vocabulary. Imported lazily so this
    module stays importable from a subprocess with only `library/` on
    its path (the same reason `briefing_interview._node_id` is lazy)."""
    try:
        from library.tools.project_layout import STEPS, node_id_for
        ids = {s.node_id for s in STEPS}
        ids.add(GLOBAL)
        return ids
    except Exception:  # noqa: BLE001 - validated below, not assumed
        return {GLOBAL}


def _store_path(project_folder: str):
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_folder).read_path(
        Area.LEARNED_CONTEXT, LEARNINGS_FILE)


def _load(project_folder: str) -> list:
    # An empty folder holds no learnings: the read half of the stated
    # absence `project_context_for_step` promises, checked before the
    # layout (which deliberately refuses an empty folder) is asked.
    # Writes still go through `_store_path` and refuse, loudly.
    if not project_folder or not str(project_folder).strip():
        return []
    path = _store_path(project_folder)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise LearnedContextError(
            f"{path} holds {type(data).__name__}, not a list of learnings. "
            f"A store that is not a list cannot be appended to; move it "
            f"aside by hand and let the next record start a new one."
        )
    return data


def _save(project_folder: str, learnings: list) -> None:
    from library.tools.project_layout import Area, ProjectLayout
    dest = ProjectLayout(project_folder).write_path(
        Area.LEARNED_CONTEXT, LEARNINGS_FILE)
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=str(dest.parent),
        prefix=".learnings.", suffix=".tmp", delete=False)
    try:
        json.dump(learnings, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        tmp.close()
        os.replace(tmp.name, dest)
    except BaseException:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _now() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise LearnedContextError(
            f"unknown learning kind {kind!r}. One of {list(KINDS)}: "
            f"a learning whose kind nothing recognises has no reader."
        )


def _check_readers(read_by) -> list:
    readers = list(read_by or [])
    if not readers:
        raise LearnedContextError(
            "a learning with no reader is refused: `read_by` must name at "
            "least one step (or \"*\" for every planning step). A learned "
            "fact nothing consumes is the defect AGENTS.md 10.4 names."
        )
    known = _node_ids()
    unknown = [r for r in readers if r not in known]
    if unknown:
        raise LearnedContextError(
            f"unknown reading step(s) {unknown}. Name DAG node ids - "
            f"`read_by` is a routing, not prose, and a route to nowhere "
            f"is the same defect as no route."
        )
    return readers


def record(project_folder: str, kind: str, statement: str, read_by,
           source: dict | None = None, detail: str = "") -> dict:
    """Record what this run learned. Returns the learning as stored."""
    _check_kind(kind)
    readers = _check_readers(read_by)
    text = (statement or "").strip()
    if not text:
        raise LearnedContextError(
            "an empty statement is refused: a learning that says nothing "
            "cannot be acted on."
        )
    learnings = _load(project_folder)
    taken = {l.get("id") for l in learnings if isinstance(l, dict)}
    n = 1
    while f"lc-{n:04d}" in taken:
        n += 1
    learning = {
        "id": f"lc-{n:04d}",
        "kind": kind,
        "statement": text,
        "read_by": readers,
        "status": ACTIVE,
        "said_by": SAID_BY[kind],
        "source": dict(source or {}),
        "created_at": _now(),
        "history": [{"at": _now(), "event": "recorded"}],
    }
    if detail and str(detail).strip():
        learning["detail"] = str(detail).strip()
    learnings.append(learning)
    _save(project_folder, learnings)
    return learning


def _find(learnings: list, learning_id: str) -> dict:
    for learning in learnings:
        if isinstance(learning, dict) and learning.get("id") == learning_id:
            return learning
    raise LearnedContextError(
        f"no learning {learning_id!r} is on file. Name one that is - "
        f"retiring or correcting an id nothing holds would invent history."
    )


def retire(project_folder: str, learning_id: str, reason: str) -> dict:
    """Retire a learning that turned out wrong. Recorded, not deleted."""
    if not (reason or "").strip():
        raise LearnedContextError(
            f"retiring {learning_id!r} with no reason is refused: a "
            f"retirement nobody explained reads as tidying, and the next "
            f"run cannot tell a wrong conclusion from an inconvenient one."
        )
    learnings = _load(project_folder)
    learning = _find(learnings, learning_id)
    if learning.get("status") != ACTIVE:
        raise LearnedContextError(
            f"learning {learning_id!r} is already "
            f"{learning.get('status')}. Only an active learning retires."
        )
    learning["status"] = RETIRED
    learning.setdefault("history", []).append(
        {"at": _now(), "event": "retired",
         "reason": reason.strip()})
    _save(project_folder, learnings)
    return learning


def correct(project_folder: str, learning_id: str, new_statement: str,
            reason: str) -> dict:
    """Supersede a learning with a corrected one. The old record stands,
    marked superseded and linked; the new one carries the fix."""
    if not (reason or "").strip():
        raise LearnedContextError(
            f"correcting {learning_id!r} with no reason is refused, for "
            f"the same cause a reasonless retirement is."
        )
    text = (new_statement or "").strip()
    if not text:
        raise LearnedContextError(
            f"correcting {learning_id!r} with an empty statement exchanges "
            f"a wrong conclusion for silence."
        )
    learnings = _load(project_folder)
    old = _find(learnings, learning_id)
    if old.get("status") != ACTIVE:
        raise LearnedContextError(
            f"learning {learning_id!r} is already "
            f"{old.get('status')}. Only an active learning is corrected."
        )
    new = record(
        project_folder, kind=old["kind"], statement=text,
        read_by=list(old.get("read_by", [])),
        source=dict(old.get("source", {})))
    # `record` saved already; now link both halves and save again, so a
    # crash between the two still leaves two honest records rather than
    # a dangling pointer.
    learnings = _load(project_folder)
    stored_old = _find(learnings, learning_id)
    stored_old["status"] = SUPERSEDED
    stored_old.setdefault("history", []).append(
        {"at": _now(), "event": "superseded",
         "reason": reason.strip(), "by": new["id"]})
    for stored_new in learnings:
        if stored_new.get("id") == new["id"]:
            stored_new["supersedes"] = learning_id
            stored_new.setdefault("history", []).append(
                {"at": _now(), "event": "recorded as a correction",
                 "reason": reason.strip(), "of": learning_id})
            new = stored_new
            break
    _save(project_folder, learnings)
    return new


def active_for_step(project_folder: str, node_id: str) -> list:
    """Every active learning this step is routed. `read_by: ["*"]`
    reaches every step; anything else reaches only the steps it names.
    Retired and superseded learnings never travel - a prompt that
    carries a conclusion the project already withdrew is the stale pile
    the captain complained about, arriving one run later. `node_id`
    `"*"` is the whole active file (the captain-facing report, not a
    routing)."""
    mine = []
    whole_file = node_id in (GLOBAL, "")
    for learning in _load(project_folder):
        if not isinstance(learning, dict):
            continue
        if learning.get("status") != ACTIVE:
            continue
        readers = learning.get("read_by") or []
        if whole_file or GLOBAL in readers or node_id in readers:
            mine.append(learning)
    return mine


def render_for_prompt(project_folder: str, node_id: str) -> str:
    """The learnings as the model reads them: short, attributed, each
    naming who said it. Empty when the project has learned nothing this
    step is routed - an absence the caller states, not one it hides."""
    mine = active_for_step(project_folder, node_id)
    if not mine:
        return ""
    lines = ["What this project already learned (act on these; do not "
             "re-ask what they settle):"]
    for learning in mine:
        lines.append(
            f"- [{learning['id']}] ({learning['kind']}, said by "
            f"{learning.get('said_by', 'unknown')}): "
            f"{learning.get('statement', '')}")
    return "\n".join(lines) + "\n"


def kinds_and_readers() -> dict:
    """Every kind, and the steps that read one. The report AGENTS.md
    10.4 asks for: a kind with an empty reader list is printed here
    rather than discovered months later. These are the TYPICAL readers
    (a learning's own `read_by` is the binding route, checked at record
    time); each is intersected with the STEPS table so a renamed step
    shows up as a shrinkage here, not as silence."""
    from library.tools.project_layout import STEPS
    known = {s.node_id for s in STEPS}
    typical = {
        # A correction overturns a decision: read by the steps that make
        # the decisions a captain most often overturns.
        CORRECTION: {"plan_transitions", "plan_vfx", "plan_sfx",
                     "review_rough_cut", "creative_cohesion"},
        # A fixed mistake: read by the steps whose errors QA catches.
        MISTAKE_FIX: {"music_selection", "render", "validate",
                      "render_subtitles", "compile_manifest"},
        # A settled choice: read by the steps deciding that thing.
        SETTLED_DECISION: {"creative_direction", "mesh_spine",
                           "music_selection", "plan_subtitles",
                           "color_grade"},
    }
    return {k: sorted(v & known) for k, v in typical.items()}


def unread_kinds() -> list:
    """Kinds nothing reads. Empty today; the test pins that, so a kind
    that loses its last reader fails rather than fading."""
    return [k for k, readers in kinds_and_readers().items() if not readers]


def main(argv=None) -> int:
    """The agent's write path: `python3 -m library.tools.learned_context
    <project> record --kind correction --read-by plan_transitions
    --statement "..."`. The model has a shell (brief_reference's
    premise); this is the command it runs to write back what it learned,
    and `--read-by` refused empty is what keeps a readerless fact from
    landing."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Record, retire, correct or list a project's learnings.")
    parser.add_argument("project", help="Project folder.")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="Record a learning.")
    rec.add_argument("--kind", required=True, choices=list(KINDS))
    rec.add_argument("--statement", required=True)
    rec.add_argument("--read-by", nargs="+", default=[],
                     help="Reading step ids, or * for every planning step.")
    rec.add_argument("--detail", default="")
    rec.add_argument("--source-note", default="")
    ret = sub.add_parser("retire", help="Retire a learning.")
    ret.add_argument("learning_id")
    ret.add_argument("--reason", required=True)
    cor = sub.add_parser("correct", help="Supersede a learning.")
    cor.add_argument("learning_id")
    cor.add_argument("--new-statement", required=True)
    cor.add_argument("--reason", required=True)
    sub.add_parser("list", help="Print every learning, active or not.")
    args = parser.parse_args(argv)
    if args.command == "record":
        learning = record(
            args.project, kind=args.kind, statement=args.statement,
            read_by=args.read_by, detail=args.detail,
            source={"note": args.source_note} if args.source_note else None)
        print(json.dumps(learning, indent=2, ensure_ascii=False))
    elif args.command == "retire":
        print(json.dumps(retire(args.project, args.learning_id,
                                reason=args.reason),
                         indent=2, ensure_ascii=False))
    elif args.command == "correct":
        print(json.dumps(correct(args.project, args.learning_id,
                                 new_statement=args.new_statement,
                                 reason=args.reason),
                         indent=2, ensure_ascii=False))
    else:
        print(json.dumps(_load(args.project), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
