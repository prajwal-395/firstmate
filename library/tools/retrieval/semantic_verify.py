"""Semantic verification as an explicit typed stage (R06).

**A verified clip range is not a visually verified action.**  Planning
checks a search hit's span against the catalog
(`search_in_planning.verify_span`): inside the file, non-empty.  That
check is STRUCTURAL - it says the span can be placed, never that the
picture shows what the slot asked for.  The retrieval lane puts the
candidate to a vision model (`span_verification.verify_intervals`):
yes or no about a statement over the candidate's frames.  That check
is SEMANTIC - it says the picture shows the thing.  Until now the two
travelled under one word ("verified"), and a reader could not tell
which one a result had passed.

This module is the stage where the two meet and stay named:

* `structural` - the catalog's verdict on the span (delegated, never
  reimplemented): is the span inside the source's boundaries?
* `semantic` - the model's verdict on the claim (delegated to an
  injected verifier, or to the shipped interval verifier): is the
  claimed event, identity or action visible - `verified`, `rejected`,
  or `uncertain`?
* `refined` - for edit-critical events, the timestamps the evidence
  establishes, narrowed to the frames that established them.  A
  verified event with no timestamp the editor can cut on is not a
  verification: it is `uncertain`.
* `evidence` - the exact frames, statement, model, prompt version and
  reply behind the verdict, so a reader can re-look at what the model
  saw.
* `cost` - what this verdict spent: model calls, cache hits, seconds.

**`uncertain` is a verdict, not a missing value.**  A question the
model answered with unparseable JSON, a candidate with no frame to
inspect, a span the catalog cannot place - none of these is a "no".
Recording them as rejections would let a broken question read as
evidence of absence.  Uncertain candidates are never presented, and
they are counted apart from rejections so a reader can see how much
of the pass actually judged anything.

**The cache is keyed by candidate identity, model, task and stage
version.**  The same question over the same footage costs nothing the
second time - including across callers: a verdict the retrieval lane
recorded answers the planning lane's identical question.  The records
live in the source's OWN memory slot (`verdicts.json`, M8) beside the
interval verdicts they are derived from, under namespaced keys - the
existing store is extended, never duplicated.  A new stage VERSION is
a new key namespace: verdicts given to old semantics are never read
as verdicts of the new ones.

**This module never loads a model.**  The default verifier is the
shipped interval verifier, which reaches the vision model through the
shared shim inside the machine's heavy-work lock; a caller that wants
a different judge injects it.  A verifier that raises, a project with
no frame sample, a span the catalog refuses - all of these are
`uncertain`, never a guess.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence

STAGE = "semantic-verify"
VERSION = 1
"""The semantics this stage verifies under.  Changing what `verified`
means - the verdict mapping, the refinement rule, the task table - is
a new VERSION, and verdicts recorded under an old one are never read
as verdicts of the new one."""

TASKS = ("appearance", "action", "event", "identity")
"""The assertion kinds the stage verifies.  An appearance is in the
frame; an action happens in it; an event is an action the editor must
cut on (so it needs refined timestamps); an identity names WHO, which
one interval can support only against the frames it was shown."""

VERDICTS = ("verified", "rejected", "uncertain")
"""The stage's verdicts.  `uncertain` means the verification did not
establish the claim either way - not that the claim is false."""

ANSWER_YES = "yes"
ANSWER_NO = "no"

PRIMARY_TO_TASK = {
    "appearance_matching": "appearance",
    "action_recognition": "action",
    "identity": "identity",
    "temporal_order": "action",
    "object_relationships": "appearance",
}
"""The retrieval classification's primary kind -> the verification
task.  An ORDER claim judged at one interval is judged as the action
it shows (the lane already reports that one interval cannot establish
the order itself); a RELATIONSHIP claim is judged as the appearance of
its members."""


def task_for_statement(statement: str, classification=None) -> str:
    """The verification task for a claim about the picture.

    An action with no state beside it names something that HAPPENS -
    an edit-critical event, which needs refined timestamps.  Anything
    else is judged as the kind the classification names.
    """
    from library.tools.retrieval import temporal_rerank

    classification = (classification if classification is not None
                      else temporal_rerank.classify(statement))
    if bool(classification.get("event")):
        return "event"
    primary = classification.get("primary") or "appearance_matching"
    return PRIMARY_TO_TASK.get(primary, "appearance")


def map_answer(answer) -> str:
    """The stage verdict for one verifier answer.

    `yes` establishes the claim, `no` rejects it, and anything else -
    unparsed JSON, a missing answer, a verifier that returned nothing
    usable - is `uncertain`.  Only an explicit "no" is a rejection.
    """
    text = str(answer or "").strip().lower()
    if text == ANSWER_YES:
        return "verified"
    if text == ANSWER_NO:
        return "rejected"
    return "uncertain"


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def candidate_identity(candidate: Mapping) -> str:
    """The identity of a verification candidate: which span of what.

    Defaults to `clip_id@start-end` when the caller names no id, so
    two callers asking about the same span share the cache entry even
    when only one of them named it.
    """
    named = candidate.get("candidate_id")
    if named:
        return str(named)
    clip_id = candidate.get("clip_id", "?")
    start = _num(candidate.get("start"))
    end = _num(candidate.get("end"))
    return f"{clip_id}@{start}-{end}"


def verification_key(*, candidate_id: str, statement: str,
                     frame_times: Sequence[float], model_id: str,
                     task: str) -> str:
    """The cache key: candidate identity, model, task and version.

    The key carries the STAGE and its VERSION, so a change to what
    `verified` means never inherits verdicts given to the old meaning.
    """
    raw = json.dumps([STAGE, VERSION, str(task),
                      str(model_id or "unknown"),
                      str(candidate_id),
                      str(statement or "").strip().lower(),
                      [round(float(t), 3) for t in (frame_times or ())]])
    return f"{STAGE}/v{VERSION}/" + hashlib.sha256(
        raw.encode("utf-8")).hexdigest()[:24]


_MEMORY_CACHE: dict = {}
"""The stage cache when no digest names a store.  Keyed exactly like
the M8 records, and cleared by `clear_memory_cache` (tests)."""


def clear_memory_cache() -> None:
    """Empty the fallback cache (tests; production uses the M8 store)."""
    _MEMORY_CACHE.clear()


def _read_recorded(content_digest: str | None,
                   memory_root=None) -> dict:
    if not content_digest:
        return dict(_MEMORY_CACHE)
    from library.tools import span_verification
    return span_verification.read_verdicts(content_digest, memory_root)


def _write_recorded(content_digest: str | None, recorded: dict,
                    memory_root=None) -> None:
    if not content_digest:
        _MEMORY_CACHE.clear()
        _MEMORY_CACHE.update(recorded)
        return
    from library.tools import source_memory, span_verification
    path = (source_memory.source_dir(content_digest, memory_root)
            / source_memory.SLOT_VERDICTS)
    try:
        current = span_verification.read_verdicts(content_digest,
                                                  memory_root)
    except (OSError, ValueError):
        current = {}
    current.update(recorded)
    source_memory.write_json(path, {"content_digest": content_digest,
                                    "verdicts": current})


def default_model_id() -> str:
    """The model id verdicts are recorded under, without loading it."""
    from library.tools import vision_model
    return str(getattr(vision_model, "MODEL_ID", "unknown"))


def _default_verify(project_folder: str):
    """The shipped interval verifier, bound to one project.

    Reuses `temporal_rerank._verify_candidate` - the catalog lookup,
    the digest, the source-memory frames and the interval family of
    the M8 store - so this stage asks exactly what the retrieval lane
    asks, and shares its recorded verdicts.
    """
    from library.tools.retrieval import temporal_rerank

    def verify(candidates, statement):
        return [temporal_rerank._verify_candidate(project_folder, candidate,
                                                  statement, None)
                for candidate in candidates]
    return verify


def _times(values) -> list[float]:
    """Frame times out of raw times or `{t, ...}` frame mappings."""
    out: list[float] = []
    for value in values or ():
        if isinstance(value, Mapping):
            value = value.get("t")
        moment = _num(value)
        if moment is not None:
            out.append(moment)
    return out


DEFAULT_REFINE_HALF_S = 0.5
"""Half the verification stride: the fallback neighbourhood of one
establishing frame.  The shipped verification reads the interval at
one frame per second (`verify.DENSE_STEP_SECONDS`), so half a second
either side of the establishing frame is the neighbourhood the judge
actually saw."""


def refine_event_span(candidate: Mapping, answer: Mapping) -> dict | None:
    """The timestamps an established EVENT is cut on.

    The establishing frame (`frame_t`, the model's own pointer)
narrows the claim to the neighbourhood the evidence covers - half
the smallest shown gap either side, the judge's own resolution.
Without a pointer the shown frames' own span is the refinement.
`None` means the answer names no timestamp at all - and an event
with no timestamp is not refined, it is not a verification.

    The refinement is anchored in the EVIDENCE, never clipped to the
    candidate window: the shown frames are sampled around the
    window's edge (the nearest frames to its start), so the frame
    that establishes the event can sit just outside it, and clipping
    there inverts the range.  The window keeps the lane's geometry;
    the refinement says where the evidence is.
    """
    start = _num(candidate.get("start"))
    end = _num(candidate.get("end"))
    if start is None or end is None:
        return None
    frame_t = _num(answer.get("frame_t"))
    shown = _times(answer.get("frames_shown") or answer.get("frames"))
    if frame_t is not None:
        half = DEFAULT_REFINE_HALF_S
        if len(shown) > 1:
            ordered = sorted(shown)
            gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
            if gaps:
                half = min(gaps) / 2.0
        low, high = frame_t - half, frame_t + half
    elif shown:
        low, high = min(shown), max(shown)
    else:
        return None
    if not high > low:
        return None
    return {"start": round(low, 3), "end": round(high, 3)}


VerifyFn = Callable[[Sequence[Mapping], str], Sequence[Mapping]]


def verify_candidate(candidate: Mapping, claim: Mapping,
                     catalog: Sequence[Mapping],
                     *, task: str | None = None,
                     verify: VerifyFn | None = None,
                     project_folder: str | None = None,
                     content_digest: str | None = None,
                     model_id: str | None = None,
                     memory_root=None) -> dict:
    """Verify one candidate's claim, structurally AND semantically.

    `candidate` names the span (`clip_id`, `start`, `end`, and the
    `frames` shown, each `{t, file?}`); `claim` names what is asserted
    (`statement`); `catalog` is the clip catalog the structural check
    runs against.  `verify` is the judge
    (`(candidates, statement) -> [answers]`, each with `answer`,
    `reason`, and - for established events - `frame_t`); the default
    is the shipped interval verifier on `project_folder`.

    The structural check runs first and the claim is NEVER put to the
    model about a span the file cannot hold: that verdict is
    `uncertain`, with the structural refusal as its reason.  The
    returned `verdict` is the SEMANTIC one - a structurally sound span
    whose picture was never judged is `uncertain`, never `verified`.
    """
    from library.tools import search_in_planning

    clip_id = candidate.get("clip_id")
    start = _num(candidate.get("start"))
    end = _num(candidate.get("end"))
    statement = str((claim or {}).get("statement") or "")
    resolved_task = task or task_for_statement(
        statement or str(candidate.get("statement") or ""))
    if resolved_task not in TASKS:
        resolved_task = "appearance"
    model = model_id or default_model_id()
    identity = candidate_identity(candidate)
    frames = list(candidate.get("frames") or ())
    frame_times = _times(frames)

    structural = search_in_planning.verify_span(
        clip_id, start, end, list(catalog or ()))
    structural_block = {
        "ok": bool(structural.get("ok")),
        "reason": structural.get("reason"),
        "what": ("the span lies inside the source file: "
                  "a verified clip range, never a verified action"),
    }
    if not structural_block["ok"]:
        # The span cannot be placed, so the claim is never put to the
        # model about it.  That is not a rejection of the claim.
        return {
            "candidate_id": identity,
            "verdict": "uncertain",
            "structural": structural_block,
            "semantic": {
                "verdict": "uncertain",
                "reason": ("the span failed the structural check, so the "
                            "claim was not put to the model: "
                            f"{structural_block['reason']}"),
                "task": resolved_task,
                "statement": statement,
            },
            "refined": None,
            "evidence": {
                "clip_id": clip_id, "start": start, "end": end,
                "statement": statement, "model": model,
                "frames_shown": [],
            },
            "cost": {"calls": 0, "cached_calls": 0, "seconds": 0.0},
            "cache": {"hit": False, "key": None},
        }

    key = verification_key(candidate_id=identity, statement=statement,
                           frame_times=frame_times, model_id=model,
                           task=resolved_task)
    recorded = _read_recorded(content_digest, memory_root)
    hit = recorded.get(key)
    if isinstance(hit, dict) and hit.get("verdict") in VERDICTS:
        cost = dict(hit.get("cost") or {})
        cost.update({"calls": 0,
                     "cached_calls": int(cost.get("cached_calls") or 1)})
        return {**hit, "candidate_id": identity,
                "cached": True, "cost": cost,
                "cache": {"hit": True, "key": key}}

    judge = verify
    if judge is None:
        # The shipped verifier judges the extracted frames, so without
        # them there is nothing to put to the model.  An INJECTED judge
        # is called regardless: it may judge from text or metadata the
        # frames never carried, and its answer still has to survive the
        # mapping (and the event's refinement) below.
        if not project_folder:
            return _uncertain_no_judge(
                identity, structural_block, resolved_task, statement,
                clip_id, start, end, model, key,
                "no verifier and no project folder: the shipped verifier "
                "needs the project to find the source memory")
        if not frame_times:
            return _uncertain_no_judge(
                identity, structural_block, resolved_task, statement,
                clip_id, start, end, model, key,
                "no extracted frame to inspect: the source memory holds "
                "no frame sample for this span")
        judge = _default_verify(project_folder)
    try:
        answers = judge([{**candidate, "frames": frames}], statement)
        answer = (answers or [{}])[0] or {}
    except Exception as error:  # noqa: BLE001 - a failed verify is data
        return _uncertain_no_judge(
            identity, structural_block, resolved_task, statement,
            clip_id, start, end, model, key,
            f"the judge failed: {error}", frames=frames)
    verdict = map_answer(answer.get("answer"))
    seconds = _num(answer.get("seconds")) or 0.0
    cached = answer.get("cached") is True
    evidence = {
        "clip_id": clip_id, "start": start, "end": end,
        "statement": statement, "model": model,
        "frames_shown": _times(frames),
        "frame_files": [f.get("file") for f in frames
                        if f.get("file")],
        "frame_t": _num(answer.get("frame_t")),
        "prompt_version": answer.get("prompt_version"),
        "reply": answer.get("reply"),
        "reason": answer.get("reason"),
    }
    refined = None
    if verdict == "verified" and resolved_task == "event":
        refined = refine_event_span(candidate, {
            **answer,
            "frames_shown": evidence["frames_shown"],
            "frames": evidence["frames_shown"],
        })
        if refined is None:
            # An edit-critical event with no timestamp the editor can
            # cut on is not established - however strongly worded the
            # yes was.
            verdict = "uncertain"
            semantic_reason = ("the judge said yes but named no frame, so "
                                "there is no timestamp the editor can cut "
                                "on: an unverified refinement is not a "
                                "verification")
        else:
            semantic_reason = str(answer.get("reason") or "")
    else:
        semantic_reason = str(answer.get("reason") or "")
        if verdict == "uncertain" and not semantic_reason:
            semantic_reason = ("the judge returned no usable answer "
                                f"({answer.get('answer')!r}): the claim is "
                                "neither established nor rejected")
    record = {
        "candidate_id": identity,
        "verdict": verdict,
        "structural": structural_block,
        "semantic": {
            "verdict": verdict,
            "reason": semantic_reason,
            "task": resolved_task,
            "statement": statement,
        },
        "refined": refined,
        "evidence": evidence,
        "cost": {"calls": (0 if cached else 1),
                 "cached_calls": (1 if cached else 0),
                 "seconds": round(seconds, 3)},
        "cache": {"hit": False, "key": key},
        "stage": STAGE,
        "stage_version": VERSION,
        "model": model,
        "task": resolved_task,
    }
    _write_recorded(content_digest, {key: _storable(record)}, memory_root)
    return {**record, "cached": cached}


def _storable(record: Mapping) -> dict:
    """The record as the cache keeps it (the live `cached` flag excluded:
    a reread is a hit by construction)."""
    kept = dict(record)
    kept.pop("cached", None)
    kept.pop("cache", None)
    return kept


def _uncertain_no_judge(identity, structural_block, resolved_task,
                        statement, clip_id, start, end, model, key,
                        reason, frames=()) -> dict:
    return {
        "candidate_id": identity,
        "verdict": "uncertain",
        "structural": structural_block,
        "semantic": {
            "verdict": "uncertain",
            "reason": reason,
            "task": resolved_task,
            "statement": statement,
        },
        "refined": None,
        "evidence": {
            "clip_id": clip_id, "start": start, "end": end,
            "statement": statement, "model": model,
            "frames_shown": _times(frames),
        },
        "cost": {"calls": 0, "cached_calls": 0, "seconds": 0.0},
        "cache": {"hit": False, "key": key},
    }
