"""Local-VLM verification of candidate spans: the last stage of the
footage memory's retrieval (structured first, VLM last - scout report
§3.2 step 3, `data/vep-long-footage-memory-scout/report.md` in
firstmate's home).

A cheap structured signal proposes CANDIDATE spans for one person (M7
`candidates`, e.g. `hand_near_mouth`: a hand joint near the lips); the
pipeline's local VLM (`vision_model`, gemma-4-12b-it-4bit via mlx-vlm)
looks at a few of the span's whole M2 frames (384 px) and answers yes or no to a natural-language statement about them, with a
reason. Only candidates are ever looked at - never a whole episode.

Every verdict is recorded in the source's memory (M8, `verdicts.json`,
keyed by the frames shown, the statement, the model and the prompt
version), so the same question over the same footage costs
nothing the second time and the answer can be read back beside the hit.

Measured on the captain's four Craig angles (17 labelled hand-at-mouth
events, 10,873 frames) for "covers his mouth with his hand" over the
257 `hand_near_mouth` candidates: 14/17 events at span precision 14/15,
257 calls in 17.3 min for the 1.51 h of footage, identical verdicts on a
rerun. Cropping each frame to the person instead recalled 11/17 and is
not done. Limit: a candidate span longer than MAX_FRAMES_PER_SPAN frames
is shown through its four closest frames, which can miss the moment (one
of the three misses). `data/vep-query-hit-verification/eval/` in
firstmate's home has the plan, the verdicts and every number.

Repeat verification is not re-paid (F03). The order is fixed:

1. An exact recorded verdict answers: the legacy frame-times key,
   then the C05 verification key (``c05_signature`` - same source
   identity, model, prompt, statement and shown frames, through
   :mod:`library.tools.cache_keys`).  A legacy hit is migrated to the
   C05 key on read, so the store converges without invalidating what
   is filed.
2. A recorded verdict whose judged frames DECIDE the requested ones
   answers next: a ``no`` over a superset of the requested frames, or
   a ``yes`` whose establishing ``frame_t`` is shown again.  Served
   with ``cached: True`` and ``reuse: "overlap"``.
3. Two recorded verdicts that disagree about the same frames REOPEN
   the question: one fresh VLM call, marked
   ``reopened: "conflicting_verdicts"``.
4. Anything else pays one fresh call - including a recipe change,
   which misses the keys (model, prompt version, selection rule) and
   re-verifies rather than serving a stale answer.

The pre-lock check uses the same lookup, so a batch every recorded
verdict decides loads no model at all.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from library.tools import source_memory

PRODUCER_VERSION = "span-verification/1"
"""The recipe version behind every verdict this module files.

The C05 verification key carries this as the analysis version, so a
change to the filing contract re-verifies rather than serving a stale
answer.  Bump it when the reuse contract changes (what a verdict may
decide, what counts as a conflict); a prompt-wording change stays on
PROMPT_VERSION, and a frame-selection change moves the sampling recipe
in the key itself."""

FAMILY_SPAN = "span"
"""The person-predicate prompt family (``PROMPT``/``PROMPT_VERSION``)."""

FAMILY_INTERVAL = "interval"
"""The whole-interval prompt family
(``INTERVAL_PROMPT``/``INTERVAL_PROMPT_VERSION``).  The families judge
different claims under different prompts, so a verdict of one is never
an answer of the other even when the frame times match."""

SPAN_PREPROCESSING = {"frames": "m2_sample_as_stored", "resize": "none"}
"""What happens to the pixels between the M2 sample and the model.

Nothing: the stored frame files are shown as-is.  Stating it keeps the
key honest - a future resize would measure different pixels and must
move the key."""

PROMPT_VERSION = 2
PROMPT = (
    "These are {n} frames of one person from a video, about half a second "
    "apart, in time order. Statement: \"the person {statement}\". Is the "
    "statement true in these frames - in at least one frame, or across "
    "the sequence of frames? Judge only what is visible. Reply with JSON "
    "only: {{\"answer\": \"yes\" or \"no\", "
    "\"frame\": <1-based number of the clearest frame, or null>, "
    "\"reason\": \"<one short sentence>\"}}")
"""Pre-registered verbatim (eval plan §4). Changing a word is a new
PROMPT_VERSION: cached verdicts were given to the old wording. v2 asks
about the sequence too - the event predicates (person enters/leaves,
object pickup) are transitions no single frame contains, which v1's
"in at least one of these frames" could not judge."""

MAX_FRAMES_PER_SPAN = 4
"""Frames shown per span: all of a short span, else the four the
candidate signal ranks closest (smallest distance), in time order."""

# ── The INTERVAL family ─────────────────────────────────────────────
#
# The span prompt above is about ONE PERSON ("These are {n} frames of
# one person"), because the predicates it was written for are about a
# person.  A retrieval candidate is an INTERVAL of a scene, not a person,
# and the question asked of it is a claim about the whole interval ("the
# laptop is open", "someone opens the laptop").  So the interval family
# has its own prompt and its own version, and its verdicts live beside
# the span verdicts in the same M8 store under their own keys - a
# verdict given to one family is never read as a verdict of the other.

INTERVAL_PROMPT_VERSION = 1
INTERVAL_PROMPT = (
    "These are {n} frames from one video, in time order, about {gap} "
    "seconds apart. Statement: \"{statement}\". Is the statement true "
    "in these frames - in at least one frame, or across the sequence "
    "of frames? Judge only what is visible. Reply with JSON only: "
    "{{\"answer\": \"yes\" or \"no\", "
    "\"frame\": <1-based number of the clearest frame, or null>, "
    "\"reason\": \"<one short sentence>\"}}")
"""Pre-registered verbatim for the interval family.

The wording is the span family's wording with the person removed and the
frames' spacing declared: an interval's frames are sampled seconds
apart, and a reader of the prompt has to know that before judging
"in at least one frame, or across the sequence"."""

MAX_FRAMES_PER_INTERVAL = 4
"""Frames shown per interval.  Four is the span family's number, and the
same reason applies: more frames cost more tokens and more seconds than
the extra evidence is worth at this size."""

INTERVAL_VERDICT_MAX_TOKENS = 120

SPAN_SAMPLING = {"selection": "distance_ranked",
                 "max_frames": MAX_FRAMES_PER_SPAN, "order": "time"}
"""Which frames of a span the model is shown.

The closest-ranked frames (``select_frames``), in time order.  Built
from the live ``MAX_FRAMES_PER_SPAN`` so a change to the selection
moves the key."""

INTERVAL_SAMPLING = {"selection": "time_ordered_head",
                     "max_frames": MAX_FRAMES_PER_INTERVAL}
"""Which frames of an interval the model is shown.

The interval's own frames, head-capped (``select_interval_frames``).
Built from the live ``MAX_FRAMES_PER_INTERVAL``."""

MEASURED_SECONDS_PER_INTERVAL_VERDICT = 81.0
"""The measured cost of ONE interval verification, on this machine.

Measured 2026-10-21 on the GEO podcast reel (seven camera files,
3840x2160 MXF at 23.976 fps, the footage the project actually shot)
with the local vision model (``gemma-4-12b-it-4bit`` through MLX): ten
interval verifications of four frames each, 81.0 s per call, 810 s in
total.  Later runs on the same reel measured 13.1-17.8 s per call, so
81.0 s is the SLOWEST measured and the figure a caller is shown is the
conservative end of the range.

The cost is in the MODEL, not in the footage: a call shows four frames
of one interval and asks one question about them, and the model's
answer is the only thing that can establish the claim.  So the number
is what a caller is told before it buys the next tier of evidence, and
it is stated as a MEASUREMENT rather than a guess - a caller that is
asked to pay 810 s of model time deserves to know that is what it is
paying.
"""

VERDICT_MAX_TOKENS = 120

VERIFY_LOCK_OWNER = "span-verification"

CLAIM_TYPES = {
    "verdict": "event.verdict",
}
"""The claims this producer writes, and the registry type each one is
filed under. The class of a claim is a consequence of that type
(``claim_provenance``): the producer says how the value was produced,
and no reader re-guesses it from the value's shape."""
"""New verdicts are VLM inference: taken under the machine's heavy-work
lock. A question answered entirely from recorded verdicts takes none."""

ANSWER_YES = "yes"
ANSWER_NO = "no"
ANSWER_UNPARSED = "unparsed"
"""A reply that is not JSON with a yes/no answer. Counted as NOT
verified - never guessed into a yes."""


def select_frames(span: dict) -> list[dict]:
    """The candidate frames shown for a span (see MAX_FRAMES_PER_SPAN)."""
    frames = span["frames"]
    if len(frames) > MAX_FRAMES_PER_SPAN:
        frames = sorted(frames, key=lambda f: (f["d"], f["t"]))[:MAX_FRAMES_PER_SPAN]
    return sorted(frames, key=lambda f: f["t"])


def parse_verdict(text: str) -> dict:
    """`{answer, frame, reason}` out of the model's reply."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    try:
        body = json.loads(match.group(0)) if match else None
    except ValueError:
        body = None
    answer = str((body or {}).get("answer", "")).strip().lower()
    if not isinstance(body, dict) or answer not in (ANSWER_YES, ANSWER_NO):
        return {"answer": ANSWER_UNPARSED, "frame": None, "reason": None}
    frame = body.get("frame")
    return {"answer": answer,
            "frame": frame if isinstance(frame, int) else None,
            "reason": body.get("reason")}


def verdict_key(statement: str, frame_times: Sequence[float],
                model_id: str) -> str:
    raw = json.dumps([PROMPT_VERSION, model_id, statement.strip().lower(),
                      [round(t, 3) for t in frame_times]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def interval_verdict_key(statement: str, frame_times: Sequence[float],
                         model_id: str, gap_seconds: float = 0.0) -> str:
    """The key an INTERVAL verdict is recorded under.

    The family is in the key, so an interval verdict can never be read
    as a span verdict (or the other way round) even when the statement
    and the frame times happen to match.
    """
    raw = json.dumps(["interval", INTERVAL_PROMPT_VERSION, model_id,
                      statement.strip().lower(), round(float(gap_seconds), 3),
                      [round(t, 3) for t in frame_times]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _normalize_statement(statement: str) -> str:
    """One spelling for one asked question.

    Case and surrounding whitespace never change what is asked of the
    frames, so the reuse lookup compares normalized statements while
    the filed verdict keeps the caller's own words."""
    return " ".join(str(statement or "").strip().lower().split())


def _family_recipe(family: str) -> tuple:
    """The prompt version, template and sampling recipe of one family."""
    if family == FAMILY_INTERVAL:
        return (INTERVAL_PROMPT_VERSION, INTERVAL_PROMPT,
                INTERVAL_SAMPLING, None)
    if family == FAMILY_SPAN:
        return (PROMPT_VERSION, PROMPT, SPAN_SAMPLING,
                SPAN_PREPROCESSING)
    raise ValueError(f"unknown verification family: {family!r}")


def c05_signature(content_digest: str, family: str, statement: str,
                  frame_times: Sequence[float], model_id: str,
                  extra_context: dict | None = None) -> str:
    """The C05 verification key one verdict is filed under.

    Same source identity, model, prompt (version and template),
    statement and shown frames is the same question, asked through
    :mod:`library.tools.cache_keys` rather than the ad-hoc frame-times
    hash.  A recipe change - model, prompt version, selection rule -
    moves the key, so the verdict is re-measured rather than served
    stale."""
    from library.tools import cache_keys

    prompt_version, template, sampling, preprocessing = _family_recipe(
        family)
    return cache_keys.verification_key(
        source_identity=content_digest, model=model_id, family=family,
        prompt_version=prompt_version, prompt_template=template,
        statement=statement, frame_times=list(frame_times),
        producer_version=PRODUCER_VERSION,
        preprocessing=preprocessing, sampling=sampling,
        extra_context=extra_context).signature()


def find_deciding_verdict(recorded: dict, *, family: str, statement: str,
                          model_id: str, prompt_version: int,
                          times: Sequence[float],
                          extra_context: dict | None = None
                          ) -> tuple[str, dict | None]:
    """A recorded verdict that already decides these frames, if any.

    The scan only reads verdicts the reuse contract filed: the family's
    own prompt version, model and statement, carrying a ``family`` tag
    (legacy verdicts predate the tag and are served exactly, never by
    overlap, so a span verdict can never answer an interval question).
    A verdict DECIDES the requested frames when its own judgement
    covers them:

    * ``no`` over a superset: every requested frame was judged false;
    * ``yes`` whose establishing ``frame_t`` is one of the requested
      frames: the frame that settled it is shown again.

    Anything else - a ``yes`` whose frame is not shown, an ``unparsed``
    reply - decides nothing and the question stays open.  A recorded
    ``yes`` and a recorded ``no`` that both decide the frames are
    CONTRADICTORY EVIDENCE: the lookup reports ``("conflict", ...)``
    and the caller re-verifies once rather than serving either side.
    """
    wanted = {round(float(t), 3) for t in times}
    if not wanted:
        return ("none", None)
    asking = _normalize_statement(statement)
    # The interval prompt declares the frames' spacing, so two verdicts
    # over the same frames at different spacings answered different
    # questions.  Legacy interval verdicts carry no gap and are served
    # exactly, never by overlap.
    want_gap = (extra_context or {}).get("gap_seconds")
    yes: dict | None = None
    no: dict | None = None
    for verdict in recorded.values():
        if not isinstance(verdict, dict):
            continue
        if verdict.get("family") != family:
            continue
        if verdict.get("model") != model_id:
            continue
        if verdict.get("prompt_version") != prompt_version:
            continue
        if _normalize_statement(verdict.get("statement")) != asking:
            continue
        if want_gap is not None and verdict.get("gap_seconds") != want_gap:
            continue
        shown = {round(float(t), 3)
                 for t in (verdict.get("frames") or ())}
        if not shown:
            continue
        answer = verdict.get("answer")
        if answer == ANSWER_NO and wanted <= shown:
            if no is None:
                no = verdict
        elif answer == ANSWER_YES:
            try:
                frame_t = (round(float(verdict["frame_t"]), 3)
                           if verdict.get("frame_t") is not None else None)
            except (TypeError, ValueError):
                frame_t = None
            if (frame_t is not None and frame_t in wanted
                    and yes is None):
                yes = verdict
    if yes is not None and no is not None:
        return ("conflict", {"yes": yes, "no": no})
    if yes is not None:
        return ("overlap", yes)
    if no is not None:
        return ("overlap", no)
    return ("none", None)


def _lookup(recorded: dict, *, content_digest: str, family: str,
            statement: str, model_id: str,
            times: Sequence[float],
            extra_context: dict | None = None
            ) -> tuple[str, Any]:
    """The recorded answer to this question, or why there is none.

    One source of truth for the pre-lock check (no model is loaded
    while a recorded answer decides every span) and the per-span loop:
    the legacy exact key, then the C05 verification key (filed by newer
    runs and by migration), then an overlapping verdict that decides
    the frames.  Returns ``("legacy"|"c05"|"overlap", verdict)``,
    ``("conflict", {"yes": ..., "no": ...})`` when recorded
    evidence disagrees about the same frames, or ``("none", None)``.
    A conflict and a miss both pay one fresh call; only the conflict
    marks the new verdict reopened."""
    prompt_version, _template, _sampling, _pre = _family_recipe(family)
    if family == FAMILY_INTERVAL:
        gap = (extra_context or {}).get("gap_seconds", 0.0)
        legacy = interval_verdict_key(statement, list(times), model_id,
                                      gap)
    else:
        legacy = verdict_key(statement, list(times), model_id)
    hit = recorded.get(legacy)
    if isinstance(hit, dict):
        return ("legacy", hit)
    sig = c05_signature(content_digest, family, statement, list(times),
                        model_id, extra_context=extra_context)
    hit = recorded.get(sig)
    if isinstance(hit, dict):
        return ("c05", hit)
    return find_deciding_verdict(recorded, family=family,
                                 statement=statement, model_id=model_id,
                                 prompt_version=prompt_version,
                                 times=list(times),
                                 extra_context=extra_context)


def _migrate_legacy_hit(recorded: dict, content_digest: str, family: str,
                        stored: dict, statement: str,
                        times: Sequence[float], model_id: str,
                        root: Path | None,
                        extra_context: dict | None = None) -> None:
    """File an exact legacy hit under its C05 key, once.

    The verdict answered the legacy question, which IS the C05 question
    (same source, model, prompt, statement, shown frames), so it is
    filed under the converged key with the family tag the overlap scan
    reads.  Later runs find it through the C05 key; legacy verdicts
    never migrate twice because the C05 entry is then present."""
    sig = c05_signature(content_digest, family, statement, list(times),
                        model_id, extra_context=extra_context)
    if sig in recorded:
        return
    if stored.get("family") == family:
        recorded[sig] = stored
    else:
        recorded[sig] = {**stored, "family": family}
    _write_verdicts(content_digest, recorded, root)


def _verdicts_path(content_digest: str, root: Path | None) -> Path:
    return source_memory.source_dir(content_digest, root) / source_memory.SLOT_VERDICTS


def read_verdicts(content_digest: str, root: Path | None = None) -> dict:
    """`{key: verdict}` recorded for a digest; `{}` when none."""
    try:
        with open(_verdicts_path(content_digest, root), encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("content_digest") != content_digest:
        return {}
    return doc.get("verdicts") or {}


def _write_verdicts(content_digest: str, verdicts: dict,
                    root: Path | None) -> None:
    source_memory.write_json(_verdicts_path(content_digest, root), {
        "content_digest": content_digest, "verdicts": verdicts})


def write_verdicts(content_digest: str, verdicts: dict,
                   root: Path | None = None) -> None:
    """Record verdicts in the M8 store.

    The store is one file for every verdict family (spans, intervals,
    relations); each family keys its own verdicts, so a verdict given
    to one family is never read as a verdict of another.
    """
    _write_verdicts(content_digest, verdicts, root)


def select_interval_frames(interval: dict) -> list[dict]:
    """The frames an interval is judged on, in time order.

    The interval's own frames are the ones the caller chose (they are
    spread across the interval, not a neighbourhood of its centre), so
    this only enforces the order and the cap.
    """
    frames = sorted(interval.get("frames") or (), key=lambda f: f["t"])
    return list(frames[:MAX_FRAMES_PER_INTERVAL])


def verify_intervals(content_digest: str, intervals: list[dict], statement: str,
                     gap_seconds: float = 0.0,
                     root: Path | None = None,
                     model=None,
                     progress: Callable[[int, int, dict], None] | None = None
                     ) -> list[dict]:
    """One verdict per INTERVAL, in order.

    An interval is a stretch of a scene, and the question asked of it is
    a claim about the whole stretch ("the laptop is open", "someone opens
    the laptop").  The verdicts are recorded in the same M8 store as the
    span verdicts and under their own keys, so the same question over
    the same footage costs nothing the second time.
    """
    from library.tools import vision_model
    from library.tools.heavy_work_lock import heavy_work_lock

    model_id = vision_model.MODEL_ID
    recorded = read_verdicts(content_digest, root)
    shown = [[f["t"] for f in select_interval_frames(i)]
             for i in intervals]
    keys = [interval_verdict_key(statement, times, model_id, gap_seconds)
            for times in shown]
    gap_ctx = {"gap_seconds": round(float(gap_seconds), 3)}
    if all(_lookup(recorded, content_digest=content_digest,
                   family=FAMILY_INTERVAL, statement=statement,
                   model_id=model_id, times=times,
                   extra_context=gap_ctx)[0]
           in ("legacy", "c05", "overlap") for times in shown):
        return _verify_intervals(content_digest, intervals, keys, statement,
                                  gap_seconds, recorded, model_id, root, model,
                                  progress)
    with heavy_work_lock(VERIFY_LOCK_OWNER):
        return _verify_intervals(content_digest, intervals, keys, statement,
                                  gap_seconds, recorded, model_id, root, model,
                                  progress)


def _verify_intervals(content_digest, intervals, keys, statement, gap_seconds,
                      recorded, model_id, root, model, progress):
    from library.tools import vision_model

    paths_by_t = None
    gap_ctx = {"gap_seconds": round(float(gap_seconds), 3)}
    out = []
    for n, (interval, key) in enumerate(zip(intervals, keys)):
        frames = select_interval_frames(interval)
        times = [f["t"] for f in frames]
        verdict = recorded.get(key)
        if verdict is not None:
            _migrate_legacy_hit(recorded, content_digest,
                                FAMILY_INTERVAL, verdict, statement, times,
                                model_id, root, extra_context=gap_ctx)
            verdict = {**verdict, "cached": True}
        else:
            kind, found = _lookup(
                recorded, content_digest=content_digest,
                family=FAMILY_INTERVAL, statement=statement,
                model_id=model_id, times=times,
                extra_context=gap_ctx)
            if kind in ("c05", "overlap"):
                verdict = {**found, "cached": True, "reuse": kind}
                if kind == "overlap":
                    verdict["reused_from"] = list(
                        found.get("frames") or [])
            else:
                if paths_by_t is None:
                    paths_by_t = _frame_files(content_digest, root)
                if model is None:
                    model = vision_model.get_model()
                images = [paths_by_t[round(t, 3)] for t in times]
                started = time.perf_counter()
                reply = model.analyze_images(
                    images,
                    INTERVAL_PROMPT.format(n=len(images), statement=statement,
                                           gap=round(float(gap_seconds), 1)),
                    max_tokens=INTERVAL_VERDICT_MAX_TOKENS)
                seconds = time.perf_counter() - started
                parsed = parse_verdict(reply)
                verdict = {**parsed,
                           "frame_t": (times[parsed["frame"] - 1]
                                       if parsed["frame"] and 1 <= parsed["frame"] <= len(times)
                                       else None),
                           "frames": times, "statement": statement,
                           "model": model_id,
                           "prompt_version": INTERVAL_PROMPT_VERSION,
                           "gap_seconds": round(float(gap_seconds), 3),
                           "family": FAMILY_INTERVAL,
                           "producer_version": PRODUCER_VERSION,
                           "seconds": round(seconds, 2), "reply": reply}
                if kind == "conflict":
                    verdict["reopened"] = "conflicting_verdicts"
                recorded[key] = verdict
                recorded[c05_signature(
                    content_digest, FAMILY_INTERVAL, statement, times,
                    model_id, extra_context=gap_ctx)] = verdict
                _write_verdicts(content_digest, recorded, root)
                verdict = {**verdict, "cached": False}
        out.append(verdict)
        if progress is not None:
            progress(n, len(intervals), verdict)
    return out


def _frame_files(content_digest: str, root: Path | None) -> dict:
    m2 = source_memory.read_m2(content_digest, root)
    if m2 is None:
        raise RuntimeError("no M2 frame sample; run "
                           "`python3 -m library.tools.source_memory frames`")
    return {round(f["t"], 3): source_memory.frame_abspath(content_digest, f, root)
            for f in m2["frames"]}


def verify_spans(content_digest: str, spans: list[dict], statement: str,
                 root: Path | None = None,
                 model=None,
                 progress: Callable[[int, int, dict], None] | None = None
                 ) -> list[dict]:
    """One verdict per span, in order. A verdict already recorded for the
    same frames, statement, model and prompt is reused (`cached:
    true`); a new one costs one VLM call and is recorded at once."""
    from library.tools import vision_model
    from library.tools.heavy_work_lock import heavy_work_lock

    model_id = vision_model.MODEL_ID
    recorded = read_verdicts(content_digest, root)
    shown = [[f["t"] for f in select_frames(span)] for span in spans]
    keys = [verdict_key(statement, times, model_id) for times in shown]
    if all(_lookup(recorded, content_digest=content_digest,
                   family=FAMILY_SPAN, statement=statement,
                   model_id=model_id, times=times)[0]
           in ("legacy", "c05", "overlap") for times in shown):
        return _verify(content_digest, spans, keys, statement, recorded,
                       model_id, root, model, progress)
    with heavy_work_lock(VERIFY_LOCK_OWNER):
        return _verify(content_digest, spans, keys, statement, recorded,
                       model_id, root, model, progress)


def _verify(content_digest, spans, keys, statement, recorded, model_id,
            root, model, progress) -> list[dict]:
    from library.tools import vision_model

    paths_by_t = None
    out = []
    for n, (span, key) in enumerate(zip(spans, keys)):
        frames = select_frames(span)
        times = [f["t"] for f in frames]
        verdict = recorded.get(key)
        if verdict is not None:
            _migrate_legacy_hit(recorded, content_digest, FAMILY_SPAN,
                                verdict, statement, times, model_id, root)
            verdict = {**verdict, "cached": True}
        else:
            kind, found = _lookup(
                recorded, content_digest=content_digest,
                family=FAMILY_SPAN, statement=statement,
                model_id=model_id, times=times)
            if kind in ("c05", "overlap"):
                verdict = {**found, "cached": True, "reuse": kind}
                if kind == "overlap":
                    verdict["reused_from"] = list(
                        found.get("frames") or [])
            else:
                if paths_by_t is None:
                    paths_by_t = _frame_files(content_digest, root)
                if model is None:
                    model = vision_model.get_model()
                images = [paths_by_t[round(t, 3)] for t in times]
                started = time.perf_counter()
                reply = model.analyze_images(
                    images, PROMPT.format(n=len(images), statement=statement),
                    max_tokens=VERDICT_MAX_TOKENS)
                seconds = time.perf_counter() - started
                parsed = parse_verdict(reply)
                verdict = {**parsed,
                           "frame_t": (times[parsed["frame"] - 1]
                                       if parsed["frame"] and 1 <= parsed["frame"] <= len(times)
                                       else None),
                           "frames": times, "statement": statement,
                           "model": model_id, "prompt_version": PROMPT_VERSION,
                           "family": FAMILY_SPAN,
                           "producer_version": PRODUCER_VERSION,
                           "seconds": round(seconds, 2), "reply": reply}
                if kind == "conflict":
                    verdict["reopened"] = "conflicting_verdicts"
                recorded[key] = verdict
                recorded[c05_signature(content_digest, FAMILY_SPAN,
                                       statement, times, model_id)] = verdict
                _write_verdicts(content_digest, recorded, root)
                verdict = {**verdict, "cached": False}
        out.append(verdict)
        if progress is not None:
            progress(n, len(spans), verdict)
    return out
