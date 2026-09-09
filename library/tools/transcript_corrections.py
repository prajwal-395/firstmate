"""Corrections that survive re-transcription and every regeneration.

The captain's question: *"where does a correction live so that a
re-render carries it?"* Editing a caption's props is undone by the next
render; editing the transcript is undone by the next transcription.
Neither is the root. A correction lives HERE:

`<project>/learned_context/learnings.json`, as a `correction` kind
(`library/tools/learned_context.py`, PR 806) with a machine-readable
`source`. That area is pipeline-owned and never scratch, so it
survives a fresh transcription, a rebind and every regeneration.

Why `learned_context` fits, and what it cannot do alone
-------------------------------------------------------
The store fits: durable, per-project, attributed (the captain said it),
with retirement instead of silent edits, and a reader on every
learning. What does NOT fit is relying on the prompt half alone. A
`correction` routed `read_by: ["*"]` reaches every planning prompt
through `project_context` - which is load-bearing for MODEL-AUTHORED
copy (a motion-graphics text payload the model writes can still say
"lucy" while reading corrected lines). But prompt hinting cannot
guarantee a spelling, so MEASURED text is fixed deterministically: a
post-transcription pass over the transcript document rewrites segment
text and word entries before anything downstream reads them. Two
halves, one store:

* deterministic pass - what the microphone heard, respelled;
* prompt routing - what the model authors, told once.

Biasing at decode time
----------------------
`faster_whisper` accepts `initial_prompt` and `hotwords` (measured on
the pinned 1.2.1: both are parameters of `WhisperModel.transcribe`,
and `transcribe_audio` forwards them - see the stub-model test). Both
are biases, not guarantees: they move the decoder, they do not decide
it. The post-transcription pass is the guarantee, and it runs even
when biasing worked, because a pass that finds nothing is the proof
biasing held. A transcript that still says "lucy" after the pass is a
correction that stopped matching, and that is reported, not hidden.

Keep-range corrections
----------------------
The same store, a second shape. A keep exclusion names timeline
seconds the captain struck ("so what do they ... feels like a
mistake") and is enforced deterministically in step 3.04's
post-bridge: an exclusion at a moment's edge TRIMS it, an exclusion
in its middle DROPS the moment with the reason - splitting one reel
into two would be a new editorial decision, not an enforcement, and
this module never invents taste (AGENTS.md 10.5). Enforcement lives
in the post-bridge so every regenerated proposal carries it; moments
approved BEFORE the exclusion was recorded are re-decided by
re-running selection, not rewritten under their approval.

`tests/test_transcript_corrections.py`.
"""

from __future__ import annotations

import re

from library.tools import learned_context

SPELLING = "transcript_spelling"
KEEP_EXCLUSION = "keep_exclusion"

#: Steps whose model-authored copy must spell it right. Transcript
#: corrections route `read_by: ["*"]` - every planning step - because a
#: motion-graphics text payload is authored, not copied, and only the
#: prompt half can reach authored words. Keep exclusions route to the
#: step that draws the boundaries they move.
SPELLING_READERS = [learned_context.GLOBAL]
KEEP_READERS = ["select_reels"]

_APPLIED_KEY = "transcript_corrections_applied"


# ── Recording ─────────────────────────────────────────────────────────

def record_spelling(project_folder: str, heard: str, correct: str,
                    reason: str) -> dict:
    """The transcriber hears `heard`; the captain hears `correct`."""
    heard = (heard or "").strip()
    correct = (correct or "").strip()
    if not heard or not correct:
        raise learned_context.LearnedContextError(
            "a spelling correction with no heard or no correct form "
            "cannot be applied: name both.")
    if not (reason or "").strip():
        raise learned_context.LearnedContextError(
            "a spelling correction with no reason is refused: the next "
            "run cannot tell a captain's verdict from tidying.")
    return learned_context.record(
        project_folder, kind=learned_context.CORRECTION,
        statement=(
            f'Transcript spelling: the audio this project transcribed as '
            f'"{heard}" is "{correct}". Write "{correct}" in every '
            f'caption, motion graphic and plan that quotes these words.'),
        read_by=list(SPELLING_READERS),
        source={"correction_type": SPELLING, "heard": heard,
                "correct": correct},
        detail=reason.strip())


def record_keep_exclusion(project_folder: str, start: float, end: float,
                          reason: str) -> dict:
    """Timeline seconds the captain struck stay out of every reel."""
    start, end = float(start), float(end)
    if not end > start:
        raise learned_context.LearnedContextError(
            f"a keep exclusion of {start}..{end} is not a range.")
    if not (reason or "").strip():
        raise learned_context.LearnedContextError(
            "a keep exclusion with no reason is refused, for the same "
            "cause a reasonless spelling correction is.")
    return learned_context.record(
        project_folder, kind=learned_context.CORRECTION,
        statement=(
            f'Keep exclusion: timeline {start:.2f}..{end:.2f}s is struck - '
            f'no reel keeps it. Trim a moment past it, or drop the moment '
            f'when the struck seconds sit in its middle.'),
        read_by=list(KEEP_READERS),
        source={"correction_type": KEEP_EXCLUSION, "start": start,
                "end": end},
        detail=reason.strip())


# ── Reading ───────────────────────────────────────────────────────────

def _typed(project_folder: str, want: str) -> list:
    """Active learnings of one correction shape, in record order."""
    out = []
    for learning in learned_context.active_for_step(project_folder, "*"):
        if not isinstance(learning, dict):
            continue
        if learning.get("kind") != learned_context.CORRECTION:
            continue
        source = learning.get("source") or {}
        if source.get("correction_type") != want:
            continue
        out.append(learning)
    return out


def spelling_corrections(project_folder: str) -> list:
    """Every active spelling correction: `heard` -> `correct`."""
    corrections = []
    for learning in _typed(project_folder, SPELLING):
        source = learning.get("source") or {}
        heard = (source.get("heard") or "").strip()
        correct = (source.get("correct") or "").strip()
        if not heard or not correct:
            continue
        corrections.append({"id": learning.get("id", ""),
                            "heard": heard, "correct": correct})
    return corrections


def keep_exclusions(project_folder: str) -> list:
    """Every active keep exclusion: timeline seconds that stay out."""
    exclusions = []
    for learning in _typed(project_folder, KEEP_EXCLUSION):
        source = learning.get("source") or {}
        try:
            start, end = float(source["start"]), float(source["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not end > start:
            continue
        exclusions.append({"id": learning.get("id", ""), "start": start,
                           "end": end,
                           "reason": str(learning.get("detail") or "")})
    return exclusions


# ── The deterministic spelling pass ───────────────────────────────────

_WORD_CHAR = r"[A-Za-z0-9\u00c0-\u024f\u1e00-\u1eff]"  # incl. diacritics


def _cased(correct: str, token: str) -> str:
    """`correct`, wearing the heard token's capitalisation.

    A proper noun is recorded once ("Lucie") and the transcript shouts,
    whispers or title-cases it; the casing of the HEARING is accident,
    the casing of the RECORD is the verdict - except where the hearing
    itself carries case (ALL CAPS), which is kept rather than flattened.
    """
    if token.isupper():
        return correct.upper()
    if token[:1].isupper():
        return correct[:1].upper() + correct[1:]
    return correct


def apply_spelling(text: str, corrections: list) -> tuple:
    """Respell whole words. Returns `(new_text, replacements)`.

    Whole words only (`hallucinate` never matches `lucy`); leading and
    trailing punctuation and a possessive `'s` survive; timings are not
    this function's business and it never touches them.
    """
    new_text = text or ""
    total = 0
    for correction in corrections:
        heard = correction["heard"]
        correct = correction["correct"]
        pattern = re.compile(
            rf"(?P<lead>(?:^|[^\w\u00c0-\u024f\u1e00-\u1eff'\u2019]))"
            rf"(?P<word>{re.escape(heard)})"
            rf"(?P<tail>(?:['\u2019][sS])?)(?![\w\u00c0-\u024f\u1e00-\u1eff])",
            re.IGNORECASE)

        def _one(match, _correct=correct):
            fixed = _cased(_correct, match.group("word"))
            return match.group("lead") + fixed + match.group("tail")

        new_text, n = pattern.subn(_one, new_text)
        total += n
    return new_text, total


def apply_to_document(document: dict, project_folder: str) -> dict:
    """Respell a transcript document in place. The guarantee half.

    Rewrites every segment's `text` and its word entries, stamps which
    learnings were applied under `_APPLIED_KEY` (with per-learning
    counts, so a correction that stopped matching reads as zero rather
    than as absent), and reports `{"replacements", "segments_touched",
    "applied"}`. Idempotent: corrected text contains nothing to match.
    """
    corrections = spelling_corrections(project_folder)
    report = {"replacements": 0, "segments_touched": 0, "applied": []}
    if not corrections:
        document[_APPLIED_KEY] = []
        return report
    touched_segments = set()
    # One correction at a time, so the stamp can say what EACH learning
    # did: a learning with zero replacements is carried with its zero,
    # and "the store holds it but the transcript never said it" reads
    # differently from "the store lost it".
    for correction in corrections:
        made = 0
        for index, segment in enumerate(document.get("segments") or []):
            if isinstance(segment.get("text"), str):
                new_text, n = apply_spelling(segment["text"], [correction])
                if n:
                    segment["text"] = new_text
                    made += n
                    touched_segments.add(index)
            for word in segment.get("words") or []:
                if not isinstance(word.get("word"), str):
                    continue
                new_word, n = apply_spelling(word["word"], [correction])
                if n:
                    word["word"] = new_word
                    made += n
                    touched_segments.add(index)
        report["applied"].append({"id": correction["id"],
                                  "heard": correction["heard"],
                                  "correct": correction["correct"],
                                  "replacements": made})
        report["replacements"] += made
    report["segments_touched"] = len(touched_segments)
    document[_APPLIED_KEY] = report["applied"]
    return report


# ── Decode-time bias (the hint, not the guarantee) ────────────────────
def bias_strings(project_folder: str) -> tuple:
    """`(initial_prompt, hotwords)` for the transcriber, from the store.

    Empty pair when nothing is on file: no correction means no bias,
    and an empty prompt is not sent (a call with nothing to ask is not
    made - AGENTS.md 10.1).
    """
    corrections = spelling_corrections(project_folder)
    if not corrections:
        return "", ""
    names = []
    for correction in corrections:
        if correction["correct"] not in names:
            names.append(correction["correct"])
    prompt = ("Proper nouns heard in this audio: "
              + ", ".join(names) + ". Transcribe them exactly as written.")
    return prompt, " ".join(names)


def render_for_model(project_folder: str) -> str:
    """Active spelling corrections as a model reads them.

    One line per learning, heard and corrected, each naming its id so
    the model can quote the verdict rather than re-derive it. Empty
    when the project recorded none. Read by the reel semantic-visual
    request (`reel_semantic_visual.bridge_context`) and, through
    `project_context`, by every planning step routed `read_by: ["*"]`.
    """
    corrections = spelling_corrections(project_folder)
    if not corrections:
        return ""
    lines = ["Recorded transcript corrections (quote the corrected "
             "spelling in every copy, subject and anchor_phrase):"]
    for correction in corrections:
        lines.append(f"- [{correction['id']}] heard "
                     f"\"{correction['heard']}\", write "
                     f"\"{correction['correct']}\"")
    return "\n".join(lines)


# ── Keep-exclusion enforcement ────────────────────────────────────────

def apply_keep_exclusions(moments: list, exclusions: list,
                          segments: list | None = None) -> tuple:
    """Trim or drop kept ranges past struck seconds.

    `moments` are `{"start", "end", ...}` dicts; `exclusions` are
    `{"start", "end", "id", ...}`. Returns `(kept, dropped)`: an
    exclusion touching a moment's EDGE moves the edge (recording
    `trimmed_by`), an exclusion strictly INSIDE drops the moment with
    the reason (splitting one reel into two would be a new editorial
    decision, and this module never makes one). A moment the exclusion
    covers entirely is dropped the same way. Untouched moments pass
    through unchanged - no key added.

    `segments` is the optional `[(start, end), ...]` of whole speech
    segments: a trimmed edge that lands mid-segment is moved ONTO the
    segment grid AWAY from the exclusion (a trimmed start forward to
    the next segment start, a trimmed end back to the previous segment
    end), because a reel may never open or close mid-sentence. A trim
    that leaves no whole segment drops the moment with the reason.
    Without `segments` the raw trimmed edge stands (the caller snaps).
    """
    kept, dropped = [], []
    grid = sorted((float(s), float(e)) for s, e in (segments or [])
                  if float(e) > float(s))
    for moment in moments:
        start = float(moment["start"])
        end = float(moment["end"])
        trimmed_by = []
        trimmed_head = trimmed_tail = False
        dead = None
        for exclusion in exclusions:
            ex_start = float(exclusion["start"])
            ex_end = float(exclusion["end"])
            if ex_end <= start or ex_start >= end:
                continue
            covers_head = ex_start <= start < ex_end < end
            covers_tail = start < ex_start < end <= ex_end
            if covers_head:
                start = ex_end
                trimmed_head = True
                trimmed_by.append(exclusion.get("id", ""))
            elif covers_tail:
                end = ex_start
                trimmed_tail = True
                trimmed_by.append(exclusion.get("id", ""))
            else:
                dead = exclusion
                break
        if dead is not None:
            reason = (f"keep exclusion {dead.get('id', '')} "
                      f"({dead.get('start')}-{dead.get('end')}s) sits "
                      f"inside this moment ({moment.get('start')}-"
                      f"{moment.get('end')}s): trimming it would split "
                      f"one reel into two, which is an editorial "
                      f"decision, not an enforcement. "
                      f"{dead.get('reason', '')}".strip())
            dropped.append({**moment, "reason": reason,
                            "excluded_by": dead.get("id", "")})
            continue
        if grid and (trimmed_head or trimmed_tail):
            # Back onto whole segments, AWAY from the struck seconds:
            # a reel never opens or closes mid-sentence, and snapping
            # outward would swallow the exclusion again.
            if trimmed_head:
                following = [s for s, _ in grid if s >= start - 1e-6]
                start = min(following) if following else end
            if trimmed_tail:
                preceding = [e for _, e in grid if e <= end + 1e-6]
                end = max(preceding) if preceding else start
        if not end > start:
            dropped.append({**moment, "reason": (
                "trimmed to nothing by keep "
                f"exclusion(s) {trimmed_by}"),
                "excluded_by": trimmed_by})
            continue
        if trimmed_by:
            kept.append({**moment, "start": start, "end": end,
                         "trimmed_by": trimmed_by})
        else:
            kept.append(dict(moment))
    return kept, dropped
