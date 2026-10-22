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
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from library.tools import source_memory

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
    keys = [interval_verdict_key(statement,
                                 [f["t"] for f in select_interval_frames(i)],
                                 model_id, gap_seconds)
            for i in intervals]
    if all(key in recorded for key in keys):
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
    out = []
    for n, (interval, key) in enumerate(zip(intervals, keys)):
        frames = select_interval_frames(interval)
        times = [f["t"] for f in frames]
        verdict = recorded.get(key)
        if verdict is not None:
            verdict = {**verdict, "cached": True}
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
                       "seconds": round(seconds, 2), "reply": reply}
            recorded[key] = verdict
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
    keys = [verdict_key(statement, [f["t"] for f in select_frames(span)],
                        model_id) for span in spans]
    if all(key in recorded for key in keys):
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
            verdict = {**verdict, "cached": True}
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
                       "seconds": round(seconds, 2), "reply": reply}
            recorded[key] = verdict
            _write_verdicts(content_digest, recorded, root)
            verdict = {**verdict, "cached": False}
        out.append(verdict)
        if progress is not None:
            progress(n, len(spans), verdict)
    return out
