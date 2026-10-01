"""The briefing interview, conducted in chat.

Twin of `library/tools/briefing_interview.py`, which is COLLECTED: the
pipeline cannot block on a human, so steps write `briefing_questions`
and the captain answers later by extending the brief. That stays for
API and unattended runs.

Under `--full-auto agent` there IS a human in the loop - the host LLM
driving the run sits beside the user in chat. On such a run the FIRST
handshake request is the interview itself (`kind=briefing_interview`,
step id `briefing_interview`): the host asks the user each question in
conversation, writes the answers back through the handshake, and the
run continues with those answers attached. Questions never land unasked
in the run summary on this path; they are asked, in chat, before the
first planning step decides.

Request (`llm_requests/briefing_interview.json`):

* `kind`: `briefing_interview`.
* `questions`: list of `{step_id, question, why_it_matters,
  what_assumed_instead}` collected from a previous run's
  `state["briefing_questions"]`, or - on a first run with no prior
  questions - a short starter set (who this is for, what it must do,
  what to avoid).
* `prompt`: how to conduct it (one question at a time, in the user's
  words, then write the answers back).

Response (`llm_responses/briefing_interview.json`): a JSON object with
`brief_answers`: list of `{question, answer}`. An empty list means the
user declined - the run proceeds exactly as a brief-less run does
today, and the summary still records what was asked.

The runner calls `should_interview()` at startup; the skill
(`ren-co-editor`) tells the host how to conduct it. The answers land
on `state["brief_answers"]` and are prepended to every step's context
that declares `creative_brief`, so a step reads them the way it reads
an attached brief.
"""

from __future__ import annotations

from typing import Any

#: The handshake step id for the chat interview. Not a DAG node - no
#: step directory, no manifest, no unwired declaration needed.
STEP_ID = "briefing_interview"

#: Starter questions when no previous run banked any. Deliberately
#: small: the host asks these in chat and the user's answers become
#: the brief. Never creative direction disguised as questions - each
#: one has a decision it changes.
STARTER_QUESTIONS = (
    {"step_id": "creative_direction",
     "question": "Who is this video for, and what should they do or feel after watching it?",
     "why_it_matters": "decides the cut's pace, tone and what gets kept",
     "what_assumed_instead": "a general audience and a most-watchable cut"},
    {"step_id": "creative_direction",
     "question": "Is there anything this video must NOT do - topics, jokes, effects, music to avoid?",
     "why_it_matters": "rules out directions the footage alone cannot",
     "what_assumed_instead": "nothing ruled out"},
    {"step_id": "music_selection",
     "question": "Should there be music at all, and if so what kind of energy?",
     "why_it_matters": "decides whether a bed is even wanted and its tempo band",
     "what_assumed_instead": "a neutral bed under speech"},
)


def should_interview(brief_attached: bool, full_auto: str) -> bool:
    """Whether this run opens with the chat interview.

    Only when no brief was attached AND a host is driving (the `agent`
    backend). Every other path keeps the collected behaviour exactly.
    """
    return (not brief_attached) and full_auto == "agent"


def build_prompt(questions) -> str:
    """How the host conducts the interview, as request prompt text."""
    lines = [
        "Conduct the briefing interview with the user, IN CHAT, now.",
        "",
        "Ask each question below in conversation, in plain language, one at a "
        "time. Do not paste this JSON at them. When they have answered (or "
        "declined - an empty answer list is complete, never a failure), write "
        "the response file as a JSON object:",
        "",
        '  {"brief_answers": [{"question": "...", "answer": "..."}, ...]}',
        "",
        "Then the run continues with those answers attached to every planning "
        "step. Questions:",
    ]
    for q in questions:
        if isinstance(q, dict):
            lines.append(f"  - [{q.get('step_id', '?')}] {q.get('question', '')}")
        else:
            lines.append(f"  - {q}")
    return "\n".join(lines) + "\n"


def collect_questions(prior_records) -> list:
    """Prior banked questions, or the starter set on a first run.

    `prior_records` is `state["briefing_questions"]` rows (each with
    `step_id` and `entries`) from a previous run, or falsy. Never
    invents beyond the starters: more questions than these come from
    real steps' real needs, banked by `briefing_interview`.
    """
    out = []
    for row in prior_records or []:
        if not isinstance(row, dict):
            continue
        for entry in row.get("entries") or []:
            if isinstance(entry, dict) and entry.get("question"):
                out.append({
                    "step_id": row.get("step_id", "?"),
                    "question": entry["question"],
                    "why_it_matters": entry.get("why_it_matters", ""),
                    "what_assumed_instead": entry.get(
                        "what_you_assumed_instead", ""),
                })
    return out or [dict(q) for q in STARTER_QUESTIONS]


def parse_answer(raw: Any) -> list:
    """Split the user's answers out of a handshake response.

    Returns the `brief_answers` list (possibly empty - declined). A
    missing or unreadable field refuses with the fix, via the
    handshake's own refusal shape.
    """
    from library.tools.llm_handshake import HandshakeRefusal
    if not isinstance(raw, dict):
        raise HandshakeRefusal(
            STEP_ID, "the interview response is not a JSON object.",
            "<project>/pipeline_output/llm_responses/briefing_interview.json",
            "re-write the file and re-run the same ren edit command")
    answers = raw.get("brief_answers", None)
    if answers is None:
        raise HandshakeRefusal(
            STEP_ID, "the interview response has no `brief_answers` key.",
            "<project>/pipeline_output/llm_responses/briefing_interview.json",
            "re-write the file and re-run the same ren edit command")
    if not isinstance(answers, list):
        raise HandshakeRefusal(
            STEP_ID, "`brief_answers` is not a list.",
            "<project>/pipeline_output/llm_responses/briefing_interview.json",
            "re-write the file and re-run the same ren edit command")
    kept = []
    for entry in answers:
        if isinstance(entry, dict) and entry.get("question"):
            kept.append({"question": entry["question"],
                         "answer": entry.get("answer", "")})
    return kept


def context_block(answers) -> str:
    """The answers, rendered as DATA for steps declaring `creative_brief`."""
    if not answers:
        return ""
    lines = ["## What the commissioner said (briefing interview, in chat):", ""]
    for entry in answers:
        lines.append(f"- {entry.get('question', '')}")
        if entry.get("answer"):
            lines.append(f"  Said: {entry['answer']}")
    return "\n".join(lines) + "\n"


def answers_for(project_folder: str) -> list:
    """This run's interview answers, off the state file (best-effort)."""
    import json as _json
    import os as _os
    try:
        from library.tools.project_layout import ProjectLayout as _Layout
        state_path = str(_Layout(project_folder).pipeline_data_path)
        with open(state_path, encoding="utf-8") as handle:
            return _json.load(handle).get("brief_answers") or []
    except Exception:  # noqa: BLE001 - no answers is a normal run
        return []


def append_to_context(current_context: str, project_folder: str,
                      manifest) -> str:
    """Prepend interview answers for steps declaring `creative_brief`.

    Only steps whose manifest declares the brief read them - a step
    that never asked for a brief is not handed one. No answers, or no
    declaration, leaves the context untouched.
    """
    inputs = (manifest or {}).get("interface", {}).get("inputs", [])
    if not any(i.get("name") == "creative_brief" for i in inputs or []):
        return current_context
    return prepend_answers(current_context, project_folder)


def prepend_answers(current_context: str, project_folder: str) -> str:
    """Prepend interview answers, for an invocation that reads the brief.

    No answers leaves the context untouched. Whether the invocation
    reads the brief is its caller's declaration (`append_to_context`
    reads a manifest; `model_task.ModelTask.reads_brief` carries it).
    """
    answers = answers_for(project_folder or "")
    if not answers:
        return current_context
    return context_block(answers) + "\n" + (current_context or "")


def conduct_if_needed(project_folder: str, state: dict, full_auto,
                      llm_timeout: int = 300, save=None):
    """Open the run with the chat interview when one is owed.

    When no brief was attached and a host drives (`agent` backend),
    the FIRST handshake request is the interview: it is written to
    `llm_requests/briefing_interview.json`, `LLM_REQUEST_READY` is
    printed, and the runner polls for
    `llm_responses/briefing_interview.json` - which the host writes
    after asking the user in chat (see the `ren-co-editor` skill).
    The answers land on `state["brief_answers"]` (persisted via `save`
    when given, else written to the state file directly) and the run
    continues. An empty answer list means the user declined: the run
    proceeds exactly as a brief-less run does today.

    Every other path - a brief attached, any other backend, a dry run,
    answers already recorded - returns `state` untouched. The
    collected interview (`briefing_interview.py`) is unchanged.
    """
    from library.tools import llm_handshake as _handshake
    if state is None:
        return state
    attached = bool(state.get("creative_brief"))
    if not should_interview(attached, full_auto or ""):
        return state
    if state.get("brief_answers") is not None:
        return state  # asked already; a resume must not re-ask

    import datetime as _dt
    import json as _json
    import os as _os
    import sys as _sys
    import time as _time

    questions = collect_questions(state.get("briefing_questions"))
    prompt = build_prompt(questions)
    payload = {
        "step_id": STEP_ID,
        "kind": "briefing_interview",
        "prompt": prompt,
        "questions": questions,
        "project_folder": project_folder,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    req_path = _handshake.request_path(project_folder, STEP_ID)
    res_path = _handshake.response_path(project_folder, STEP_ID)
    _os.makedirs(_os.path.dirname(req_path), exist_ok=True)
    _os.makedirs(_os.path.dirname(res_path), exist_ok=True)
    if _os.path.exists(res_path):
        _os.unlink(res_path)
    with open(req_path, "w", encoding="utf-8") as handle:
        _json.dump(payload, handle, indent=2)
    print(f"{_handshake.READY_MARKER}: {req_path}", flush=True)
    print(f"  Briefing interview: ask the user in chat, then write "
          f"{res_path} "
          f"(timeout {llm_timeout}s)...", file=_sys.stderr)
    deadline = _time.time() + (300 if llm_timeout is None
                                 else llm_timeout)
    answers = None
    while _time.time() < deadline:
        if _os.path.exists(res_path):
            _time.sleep(0.5)
            with open(res_path, "r", encoding="utf-8") as handle:
                raw = handle.read()
            parsed = _handshake.validate_response(STEP_ID, raw,
                                                  project_folder)
            answers = parse_answer(parsed)
            break
        _time.sleep(2)
    if answers is None:
        # No host answered: proceed brief-less, exactly as today. The
        # collected questions still land in the summary downstream.
        print(f"  Briefing interview unanswered - continuing without "
              f"a brief.", file=_sys.stderr)
        state["brief_answers"] = []
    else:
        state["brief_answers"] = answers
        print(f"  Briefing interview: {len(answers)} answer(s) recorded.",
              file=_sys.stderr)
    if save is not None:
        save(project_folder, state)
    else:
        try:
            from library.tools.project_layout import ProjectLayout as _L
            state_path = str(_L(project_folder).pipeline_data_path)
            with open(state_path, "w", encoding="utf-8") as handle:
                _json.dump(state, handle, indent=2)
        except Exception:  # noqa: BLE001 - the run still continues
            pass
    return state
