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
seconds that stay out of every reel - struck by the CAPTAIN ("so what
do they ... feels like a mistake"), or VERDICTED by the model at
selection time (`takes_dropped` on a chosen moment, recorded by step
3.04's post-bridge with the model's reason). Both are enforced TWICE,
at the two layers that each own their half. At SELECTION time,
deterministically in step 3.04's post-bridge: an exclusion at a
moment's edge TRIMS it, an exclusion in its middle DROPS the moment
with the reason - splitting one reel into two would be a new editorial
decision, not an enforcement, and this module never invents taste
(AGENTS.md 10.5). At BUILD time, as cuts inside the approved moment's
own keep ranges (`exclusion_cuts_for_span`, applied by
`reel_build.reel_ranges`): selection never rewrites an approved range
under its approval, and the build never re-decides one - it only stops
playing seconds struck or verdicted, which is obedience rather than
re-decision. A strike the build cannot honour (the whole body gone)
skips the reel with the reason rather than building an empty timeline,
and the no-split rule holds at both layers: one reel in, one reel out,
fewer seconds.

Who decided is ON the record, not in the reader's head: `source`
carries `author` - `captain` for a strike, `model` for a selection
verdict with the reel slug and the reason in `detail`. A verdict that
turns out wrong retires like any learning, with the reason.

And its INVERSE, a third shape
------------------------------
A keep exclusion says seconds stay OUT.  Nothing said seconds stay IN,
and on 2026-09-11 the captain needed exactly that, at frame 270 of
Reel 01: *"this cut here on craig is a little jarring and does't
actually make sense, it's better to just not cut out those few words
inbetween"*.  What he was looking at was the take cutter's work, not a
boundary: Craig's rhetorical *"geo geo geo"* measured as a repeated
take at 0.667 similarity, and four words - "got to get into" - were
removed from the middle of one sentence.

A keep INSISTENCE (`record_keep_insistence`) records those seconds, and
it is enforced at the BUILD only: `reel_build.reel_ranges` withdraws
any take cut overlapping them and SAYS which insistence did it.  It
routes to `build_reels` rather than `select_reels` for the same reason
- an exclusion moves a window selection draws, an insistence withdraws
a cut only the builder makes.  Neither changes what the scan MEASURED;
the scan still reports what it saw.

A fourth shape: the word stays, the READING goes
------------------------------------------------
Every shape above either rewrites a word (`transcript_spelling`) or
strikes audio (`keep_exclusion`).  On 2026-09-19 the captain asked for
neither, twenty times over: the "qu", the stray "i", "f" and "s", the
"um"s - all PRONOUNCED, all rightly still in the audio - only out of
the read text.  The subtitle should carry what a reader needs, not a
phonetic transcript of what the microphone caught.

A DISPLAY SUPPRESSION (`record_display_suppression`) marks matching
word entries `display: False` and drops the token from segment text
and from quoted copy downstream (`display_respell`).  The audio, the
spine and every timing are untouched: the marked entry keeps its
`start`/`end`, so the words either side keep theirs, caption
segmentation still measures the same spans, and the word-level
alignment the caption renderer keys on never shifts.  Downstream the
grouping partitions on ALL words and draws only displayed ones, so a
suppressed token changes the text of its own card and re-keys nothing
after it (`step_4_01_plan_subtitles.split_into_groups`).

Two scopes, because "i" is also a pronoun.  A GLOBAL suppression
matches whole-word, case-insensitively, everywhere ("um" never carries
meaning for a reader).  An ANCHORED one names the surface exactly plus
its neighbours (`speaker`, `prev`, `next` word cores), so Craig's stray
lowercase "i" suppresses without touching the hundreds of "I" the two
speakers mean.  The anchor is words, never seconds: re-transcription
re-times every boundary, and an anchor in seconds would die with the
transcript it was recorded against.

Who proposed it is part of the record.  A captain's note records a
`correction` (the captain said it); the hygiene scanner's judgement
(`library/tools/transcript_hygiene.py`) records a `mistake_fix` (the
pipeline concluded it) with its evidence in `source`.  Same routing,
same retirement, honest `said_by` - a model guess wearing the
captain's name would be the misattribution the store exists to
prevent.  `correct()` promotes a confirmed proposal to a correction.

`tests/test_transcript_corrections.py`,
`tests/test_keep_insistence.py`.
"""

from __future__ import annotations

import re

from library.tools import learned_context

SPELLING = "transcript_spelling"
KEEP_EXCLUSION = "keep_exclusion"
KEEP_INSISTENCE = "keep_insistence"
DISPLAY_SUPPRESSION = "display_suppression"

#: What an anchored display suppression must name. Exactly these: the
#: speaker, the exact transcribed surface, and the word cores either
#: side of it. A partial anchor matches nothing loudly or everything
#: silently, and the record refuses both.
_ANCHOR_KEYS = ("speaker", "surface", "prev", "next")

#: Steps whose model-authored copy must spell it right. Transcript
#: corrections route `read_by: ["*"]` - every planning step - because a
#: motion-graphics text payload is authored, not copied, and only the
#: prompt half can reach authored words. Keep exclusions route to the
#: step that draws the boundaries they move.
SPELLING_READERS = [learned_context.GLOBAL]
KEEP_READERS = ["select_reels"]

#: A keep INSISTENCE is a build-time answer to a build-time measurement
#: - the take cutter's, which selection does not run - so it routes to
#: the step that builds rather than the step that draws windows. An
#: exclusion moves a boundary and is selection's business; an
#: insistence withdraws a cut and is the builder's.
INSIST_READERS = ["build_reels"]

_APPLIED_KEY = "transcript_corrections_applied"


# ── Recording ─────────────────────────────────────────────────────────

#: Who may propose a correction, and the learning kind each becomes.
#: A captain's note is a `correction` (the captain said it); the hygiene
#: scanner's judgement is a `mistake_fix` (the pipeline concluded it) -
#: same routing, same retirement, honest `said_by`. Anything else is
#: refused: a proposal with no provenance cannot be retired on purpose.
PROPOSERS = {"captain": learned_context.CORRECTION,
             "model": learned_context.MISTAKE_FIX}


def _kind_for(proposed_by: str) -> str:
    try:
        return PROPOSERS[proposed_by]
    except KeyError:
        raise learned_context.LearnedContextError(
            f"proposed_by {proposed_by!r} names nobody. One of "
            f"{sorted(PROPOSERS)}: a correction that cannot say who "
            f"proposed it cannot be retired on purpose.") from None

def record_spelling(project_folder: str, heard: str, correct: str,
                    reason: str, proposed_by: str = "captain",
                    status: str = learned_context.ACTIVE) -> dict:
    """The transcriber hears `heard`; the captain hears `correct`.

    `proposed_by` is who said so - "captain" for a note, "model" for the
    hygiene scanner's judgement (`library/tools/transcript_hygiene.py`)
    - and decides the learning kind, never the routing: both route
    `read_by: ["*"]` and both retire the same way.

    `status` is `active`, or `pending` for a model proposal that
    flagged its own uncertainty: pending is recorded, never enforced,
    until a human promotes it. A captain's note is confirmed by being
    said, so pending with `proposed_by="captain"` is refused.
    """
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
    if status == learned_context.PENDING and proposed_by != "model":
        raise learned_context.LearnedContextError(
            f"a pending spelling correction proposed by "
            f"{proposed_by!r} is refused: pending holds an UNCERTAIN "
            f"model proposal for confirmation, and the captain saying "
            f"it IS the confirmation.")
    kind = _kind_for(proposed_by)
    statement = (
        f'Transcript spelling: the audio this project transcribed as '
        f'"{heard}" is "{correct}". Write "{correct}" in every '
        f'caption, motion graphic and plan that quotes these words.')
    if kind != learned_context.CORRECTION:
        statement = (
            f'Model-proposed transcript spelling (not yet confirmed by '
            f'the captain - retire it if wrong): the audio this project '
            f'transcribed as "{heard}" reads as "{correct}". Write '
            f'"{correct}" in every caption, motion graphic and plan '
            f'that quotes these words.')
    if status == learned_context.PENDING:
        statement = (
            f'Model-proposed transcript spelling (UNCERTAIN - recorded '
            f'pending, applies only if the captain confirms it): the '
            f'audio this project transcribed as "{heard}" reads as '
            f'"{correct}".')
    return learned_context.record(
        project_folder, kind=kind,
        statement=statement,
        read_by=list(SPELLING_READERS),
        source={"correction_type": SPELLING, "heard": heard,
                "correct": correct, "proposed_by": proposed_by},
        detail=reason.strip(), status=status)


def record_keep_exclusion(project_folder: str, start: float, end: float,
                           reason: str, author: str = "captain") -> dict:
    """Timeline seconds that stay out of every reel.

    `author` is who decided: the captain striking seconds, or the
    model verdiciting a take at selection time. Recorded on the
    record either way, because a reader who cannot tell a captain's
    strike from a model's verdict cannot judge either.
    """
    start, end = float(start), float(end)
    if not end > start:
        raise learned_context.LearnedContextError(
            f"a keep exclusion of {start}..{end} is not a range.")
    if not (reason or "").strip():
        raise learned_context.LearnedContextError(
            "a keep exclusion with no reason is refused, for the same "
            "cause a reasonless spelling correction is.")
    if author not in ("captain", "model"):
        raise learned_context.LearnedContextError(
            f"a keep exclusion authored by {author!r} names nobody "
            f"accountable: 'captain' or 'model'.")
    who = ("the captain struck" if author == "captain"
           else "the selection model verdicted")
    return learned_context.record(
        project_folder, kind=learned_context.CORRECTION,
        statement=(
            f'Keep exclusion: timeline {start:.2f}..{end:.2f}s is struck - '
            f'no reel keeps it. Trim a moment past it, or drop the moment '
            f'when the struck seconds sit in its middle. ({who}.)'),
        read_by=list(KEEP_READERS),
        source={"correction_type": KEEP_EXCLUSION, "start": start,
                "end": end, "author": author},
        detail=reason.strip())


def record_keep_insistence(project_folder: str, start: float, end: float,
                           reason: str) -> dict:
    """Timeline seconds the captain says STAY IN: no take cut may drop them.

    The exclusion's inverse, and the captain asked for it in those
    words on 2026-09-11, at frame 270 of Reel 01: *"this cut here on
    craig is a little jarring and does't actually make sense, it's
    better to just not cut out those few words inbetween"*.

    The cut he was looking at came from `reel_build.redundant_takes`,
    which measured Craig's rhetorical *"geo geo geo"* as a repeated
    take and removed the four words before it - "got to get into" -
    out of the middle of one sentence.  Nothing in the vocabulary
    could say "leave that alone": a keep EXCLUSION removes seconds and
    there was no shape that keeps them.

    So this is the same store, a third shape, enforced where the cut is
    made rather than where windows are drawn.  It is deliberately NOT a
    change to the cutter's own measurement: the scan still says what it
    saw, and a run says which of its cuts a recorded insistence
    withdrew (`reel_build.reel_ranges`).  A withdrawal nobody can see
    is a content change nobody can see.
    """
    start, end = float(start), float(end)
    if not end > start:
        raise learned_context.LearnedContextError(
            f"a keep insistence of {start}..{end} is not a range.")
    if not (reason or "").strip():
        raise learned_context.LearnedContextError(
            "a keep insistence with no reason is refused, for the same "
            "cause a reasonless spelling correction is.")
    return learned_context.record(
        project_folder, kind=learned_context.CORRECTION,
        statement=(
            f'Keep insistence: timeline {start:.2f}..{end:.2f}s STAYS IN - '
            f'no automatic take cut may drop these seconds. The words were '
            f'measured as a repeated take and they are not one.'),
        read_by=list(INSIST_READERS),
        source={"correction_type": KEEP_INSISTENCE, "start": start,
                "end": end},
        detail=reason.strip())


def record_display_suppression(project_folder: str, heard: str,
                               reason: str, scope: dict | None = None,
                               proposed_by: str = "captain",
                               status: str = learned_context.ACTIVE) -> dict:
    """A pronounced token the reader never sees: mark, don't strike.

    `heard` is the single transcribed token ("um", "qu", "s") - one
    token only, because a suppression matches word entries and a phrase
    is a respell, not a deletion.  `scope` is None for a GLOBAL
    suppression (every whole-word occurrence, case-insensitive - "um"
    never carries meaning) or an ANCHORED one naming the surface
    exactly plus its neighbours::

        {"speaker": "Craig", "surface": "i",
         "prev": "said", "next": "went"}

    matched case-insensitively like everything else here - the
    ANCHOR (speaker plus neighbours) is what keeps a stray "i" from
    touching the pronoun "I", and case-insensitivity is what lets one
    anchor hit cased transcript words and lowercased spine words alike.  The anchor is words, never
    seconds: re-transcription re-times every boundary, and an anchor in
    seconds would die with the transcript it was recorded against.

    `proposed_by` decides the kind the same way `record_spelling` does:
    a captain's note is a correction, the scanner's judgement a
    mistake_fix with its evidence, so a wrong model guess retires
    rather than wearing the captain's name.

    `status` is `active`, or `pending` for a model proposal that
    flagged its own uncertainty - recorded, never enforced, until a
    human promotes it. Pending with `proposed_by="captain"` is refused
    for the same cause `record_spelling` refuses it.
    """
    heard = (heard or "").strip()
    if not heard or len(heard.split()) != 1:
        raise learned_context.LearnedContextError(
            f"a display suppression of {heard!r} is not one token. Name "
            f"the single transcribed token to hide; a phrase is a "
            f"respell (`record_spelling`), not a deletion.")
    if not (reason or "").strip():
        raise learned_context.LearnedContextError(
            "a display suppression with no reason is refused, for the "
            "same cause a reasonless spelling correction is.")
    anchor = None
    if scope is not None:
        if not isinstance(scope, dict):
            raise learned_context.LearnedContextError(
                f"a display-suppression scope of {scope!r} is not an "
                f"anchor. Pass None for a global suppression or a "
                f"{sorted(_ANCHOR_KEYS)} anchor.")
        unknown = sorted(set(scope) - set(_ANCHOR_KEYS))
        missing = [k for k in _ANCHOR_KEYS if k not in scope]
        if unknown or missing:
            raise learned_context.LearnedContextError(
                f"a display-suppression scope names {sorted(scope)}; an "
                f"anchored suppression names exactly "
                f"{sorted(_ANCHOR_KEYS)}. A partial anchor matches "
                f"nothing loudly or everything silently, and neither "
                f"is acceptable.")
        anchor = {key: str(scope[key]).strip() for key in _ANCHOR_KEYS}
        if not all(anchor.values()):
            raise learned_context.LearnedContextError(
                "an anchored display suppression with an empty speaker, "
                "surface or neighbour is refused: it would match "
                "nothing and report zero, which reads as absence.")
    kind = _kind_for(proposed_by)
    if status == learned_context.PENDING and proposed_by != "model":
        raise learned_context.LearnedContextError(
            f"a pending display suppression proposed by "
            f"{proposed_by!r} is refused: pending holds an UNCERTAIN "
            f"model proposal for confirmation, and the captain saying "
            f"it IS the confirmation.")
    where = ("everywhere it is transcribed" if anchor is None else
             f"where {anchor['speaker']} says it between "
             f"\"{anchor['prev']}\" and \"{anchor['next']}\"")
    statement = (
        f'Display suppression: the transcribed token "{heard}" '
        f'{where} is pronounced and stays in the audio - hide it from '
        f'captions and quoted copy, and never re-quote it back in.')
    if kind != learned_context.CORRECTION:
        statement = (
            f'Model-proposed display suppression (not yet confirmed by '
            f'the captain - retire it if wrong): the transcribed token '
            f'"{heard}" {where} is pronounced and stays in the audio - '
            f'hide it from captions and quoted copy.')
    if status == learned_context.PENDING:
        statement = (
            f'Model-proposed display suppression (UNCERTAIN - recorded '
            f'pending, applies only if the captain confirms it): the '
            f'transcribed token "{heard}" {where} is pronounced and '
            f'stays in the audio.')
    return learned_context.record(
        project_folder, kind=kind,
        statement=statement,
        read_by=list(SPELLING_READERS),
        source={"correction_type": DISPLAY_SUPPRESSION, "heard": heard,
                "scope": anchor, "proposed_by": proposed_by},
        detail=reason.strip(), status=status)


# ── Reading ───────────────────────────────────────────────────────────

def _typed(project_folder: str, want: str) -> list:
    """Active learnings of one correction shape, in record order.

    Both attributions: a captain's `correction` and the scanner's
    `mistake_fix` carry the same `correction_type` source shapes and the
    same routing - the kinds differ in who said it (`said_by`), never
    in what is enforced.  An unrelated mistake_fix carries no
    `correction_type` and never matches.
    """
    out = []
    for learning in learned_context.active_for_step(project_folder, "*"):
        if not isinstance(learning, dict):
            continue
        if learning.get("kind") not in (learned_context.CORRECTION,
                                        learned_context.MISTAKE_FIX):
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
                           "author": str(source.get("author")
                                        or "captain"),
                           "reason": str(learning.get("detail") or "")})
    return exclusions


def keep_insistences(project_folder: str) -> list:
    """Every active keep insistence: timeline seconds that stay IN."""
    insisted = []
    for learning in _typed(project_folder, KEEP_INSISTENCE):
        source = learning.get("source") or {}
        try:
            start, end = float(source["start"]), float(source["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not end > start:
            continue
        insisted.append({"id": learning.get("id", ""), "start": start,
                         "end": end,
                         "reason": str(learning.get("detail") or "")})
    return insisted


def suppressions(project_folder: str) -> list:
    """Every active display suppression: tokens hidden, audio kept.

    Each is `{"id", "heard", "scope", "reason"}` where `scope` is None
    (global: every whole-word occurrence) or the anchor dict
    (`speaker`, `surface`, `prev`, `next` word cores).
    """
    out = []
    for learning in _typed(project_folder, DISPLAY_SUPPRESSION):
        source = learning.get("source") or {}
        heard = (source.get("heard") or "").strip()
        if not heard or len(heard.split()) != 1:
            continue
        scope = source.get("scope")
        if scope is not None:
            if not isinstance(scope, dict):
                continue
            if sorted(scope) != sorted(_ANCHOR_KEYS):
                continue
            scope = {key: str(scope[key]).strip() for key in _ANCHOR_KEYS}
            if not all(scope.values()):
                continue
        out.append({"id": learning.get("id", ""),
                    "heard": heard, "scope": scope,
                    "reason": str(learning.get("detail") or "")})
    return out

def insisted_spans_for_span(start: float, end: float,
                            insistences: list) -> list:
    """Insisted seconds inside one span, as `(start, end, id)` triples.

    Clipped to `[start, end]` and in order, the shape
    `exclusion_cuts_for_span` returns - so the builder joins both
    kinds to a moment the same way, and the id travels so a run can
    SAY which insistence withdrew which cut.
    """
    out = []
    for insistence in insistences or ():
        try:
            a, b = float(insistence["start"]), float(insistence["end"])
        except (KeyError, TypeError, ValueError):
            continue
        lo, hi = max(a, float(start)), min(b, float(end))
        if hi > lo:
            out.append((lo, hi, str(insistence.get("id", ""))))
    return sorted(out)


# ── The deterministic spelling pass ───────────────────────────────────

_WORD_CORE_RE = re.compile(
    r"^[^A-Za-z0-9\u00c0-\u024f\u1e00-\u1eff]+|"
    r"[^A-Za-z0-9\u00c0-\u024f\u1e00-\u1eff]+$")


def word_core(token: str) -> str:
    """A transcribed token without its edge punctuation.

    `"um,"` cores to `"um"`, `"s"` stays `"s"`, `"D."` cores to `"D"`.
    One predicate for every suppression match, so the transcript pass
    and the caption planner cannot disagree about what a word IS.
    """
    return _WORD_CORE_RE.sub("", str(token or ""))


def suppression_matches(word: str, speaker: str | None,
                        prev_core: str, next_core: str,
                        suppression: dict) -> bool:
    """Whether one word entry falls under one display suppression.

    GLOBAL (`scope` None): the cores agree case-insensitively - "um"
    never carries meaning for a reader, however it was cased. ANCHORED:
    the speaker agrees case-insensitively, the SURFACE agrees
    case-insensitively, and the neighbour cores agree
    case-insensitively. The ANCHOR is the guard, not the casing: a
    stray lowercase "i" suppresses without touching the pronoun "I"
    because the neighbours differ, and case-insensitivity is what lets
    the same anchor hit the transcript's cased words AND the spine's
    lowercased ones (step 1.04 lowercases every temporal word, so an
    exact-case anchor would fix the transcript while silently missing
    every caption - found on 2026-09-19 via a zero-count suppression
    report). Casing is the transcriber's accident; position is the
    anchor.
    """
    heard = (suppression.get("heard") or "").strip()
    if not heard:
        return False
    scope = suppression.get("scope")
    if scope is None:
        return word_core(word).lower() == heard.lower()
    if (speaker or "").strip().lower() != \
            str(scope.get("speaker") or "").strip().lower():
        return False
    if str(word or "").strip().lower() != \
            str(scope.get("surface") or "").strip().lower():
        return False
    return (str(prev_core or "").lower()
            == str(scope.get("prev") or "").strip().lower()
            and str(next_core or "").lower()
            == str(scope.get("next") or "").strip().lower())


def filter_words(words: list, speaker: str | None,
                 suppressions: list) -> tuple:
    """Split word entries into `(kept, dropped)` by display suppression.

    The ONE predicate the transcript pass and the caption planner
    share (`suppression_matches`): neighbour cores are read off the
    list itself, so the planner needs no transcript - only the block's
    speaker and its own word order. Timings are never touched: entries
    are partitioned, never edited, so the words either side keep their
    `start`/`end` byte-identical.
    """
    if not suppressions:
        return list(words or []), []
    kept, dropped = [], []
    cores = [word_core(w.get("word", "")) for w in (words or [])]
    for index, entry in enumerate(words or []):
        prev_core = cores[index - 1] if index > 0 else ""
        next_core = cores[index + 1] if index + 1 < len(cores) else ""
        if any(suppression_matches(entry.get("word", ""), speaker,
                                   prev_core, next_core, suppression)
               for suppression in suppressions):
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


def _drop_token_occurrences(text: str, heard: str,
                            ordinals: list) -> tuple:
    """Drop the `ordinals`-th whole-word occurrences of `heard`.

    Case-insensitive, whole words only (the same rule `apply_spelling`
    uses); leftover runs of whitespace collapse to one and the ends
    strip. Commas and periods stand: removing punctuation is taste, and
    this pass only hides pronounced tokens. Returns `(new_text, n)`.
    """
    pattern = re.compile(
        rf"(?P<lead>(?:^|[^\w\u00c0-\u024f\u1e00-\u1eff'\u2019]))"
        rf"(?P<word>{re.escape(heard)})"
        rf"(?![\w\u00c0-\u024f\u1e00-\u1eff])",
        re.IGNORECASE)
    hits = list(pattern.finditer(text or ""))
    drop = {hits[o].span() for o in ordinals
            if 0 <= o < len(hits)}
    if not drop:
        return text, 0
    out, cursor, n = [], 0, 0
    for match in hits:
        if match.span() in drop:
            out.append(text[cursor:match.start("word")])
            cursor = match.end("word")
            n += 1
        # A kept occurrence is copied verbatim by the join below.
    out.append(text[cursor:])
    new_text = re.sub(r"\s{2,}", " ", "".join(out)).strip()
    # Debris, not punctuation: the comma belonged to the hidden token
    # ("Um, and" - the "Um" leaves and its comma cannot stay, or a
    # card opens on ","), and a gap left before a mark ("same , same")
    # is spacing, not taste. A period mid-sentence stands: only the
    # sentence knows whether it ends one.
    new_text = re.sub(r"^[,;:]+\s*", "", new_text)
    new_text = re.sub(r"\s+([,.!?;:])", r"\1", new_text)
    # Two commas meet where two removed tokens stood ("you know, um,
    # same f um, same" - the fillers leave and their commas collide).
    # Commas only: no other mark doubles this way, and collapsing
    # periods or bangs would decide what emphasis means.
    new_text = re.sub(r",\s*,", ",", new_text)
    return new_text, n


def apply_suppressions(document: dict, suppressions: list) -> dict:
    """Hide suppressed tokens from read text. The display half.

    Every matching word entry is marked `display: False` and the token
    leaves its segment's `text`; everything else on the entry -
    `start`, `end`, `timed`, alignment scores - stands untouched, and
    so does every neighbouring entry. The audio span, the spine and the
    caption segmentation's timing inputs are therefore byte-identical:
    only drawn text changes, the way a respell changes only text.

    Which occurrence leaves which text is ordinal-mapped: a suppressed
    entry drops the occurrence at its own position among same-core
    entries, so an anchored "s" removes one "s" and a global "um"
    removes every "um". Idempotent: a second pass re-marks the same
    entries and finds no text left to drop.

    A suppression that empties a segment's text (a lone "Um" card)
    still stands - the words and their timings stay for the audio, and
    the empty text SAYS the card draws nothing. Reports
    `{"suppressed", "segments_touched", "applied"}` with per-learning
    counts, the same shape the spelling pass reports, so a suppression
    that stopped matching reads as zero rather than as absent.
    """
    report = {"suppressed": 0, "segments_touched": 0, "applied": []}
    if not suppressions:
        return report
    touched_segments = set()
    for suppression in suppressions:
        made = 0
        heard = (suppression.get("heard") or "").strip()
        for index, segment in enumerate(document.get("segments") or []):
            words = segment.get("words") or []
            speaker = segment.get("speaker")
            cores = [word_core(w.get("word", "")
                               if isinstance(w.get("word"), str) else "")
                     for w in words]
            # Ordinal of each entry among same-core entries: the map
            # from a marked entry to the text occurrence it owns.
            seen: dict = {}
            ordinals: dict = {}
            for word_index in range(len(words)):
                key = cores[word_index].lower()
                ordinals[word_index] = seen.get(key, 0)
                seen[key] = seen.get(key, 0) + 1
            drop_ordinals = []
            for word_index, entry in enumerate(words):
                if not isinstance(entry.get("word"), str):
                    continue
                prev_core = cores[word_index - 1] if word_index > 0 else ""
                next_core = (cores[word_index + 1]
                             if word_index + 1 < len(cores) else "")
                if suppression_matches(entry["word"], speaker,
                                       prev_core, next_core, suppression):
                    entry["display"] = False
                    drop_ordinals.append(ordinals[word_index])
                    made += 1
                    touched_segments.add(index)
            if drop_ordinals and isinstance(segment.get("text"), str):
                new_text, _ = _drop_token_occurrences(
                    segment["text"], heard, drop_ordinals)
                segment["text"] = new_text
        report["applied"].append({"id": suppression.get("id", ""),
                                 "heard": heard,
                                 "suppressed": made})
        report["suppressed"] += made
    report["segments_touched"] = len(touched_segments)
    return report


def _word_heard_forms(token: str) -> tuple:
    """The matchable body of one timed word, plus any possessive clitic.

    `apply_spelling` lets a possessive `'s` survive a respell ("ai's"
    reads "AI's"); the word-level pass owes the same word the same
    verdict. Returns `(body, clitic)`: `"AI's"` -> `("AI", "'s")`,
    `"RMs,"` -> `("RMs,", "")` (trailing punctuation is not a clitic -
    the core still has to agree whole).
    """
    text = str(token or "")
    for clitic in ("'s", "'S", "\u2019s", "\u2019S"):
        if text.endswith(clitic) and len(text) > len(clitic):
            return text[:-len(clitic)], text[-len(clitic):]
    return text, ""


def _edge_punct(text: str) -> tuple:
    """Edge punctuation a respell must carry over, `(lead, trail)`.

    A respell rewrites the word's CORE ("chronicle," -> "Chronicle")
    but the commas, periods and quotes either side of it are the
    surface the audio measured, not the error being fixed - dropping
    them turns "Chronicle," into "Chronicle" on the caption that
    proves the fix. The core predicate is `word_core`'s own, so the
    two cannot disagree about where the word ends.
    """
    text = str(text or "")
    core = _WORD_CORE_RE.sub("", text)
    if not core:
        return "", ""
    start = text.find(core)
    end = text.rfind(core) + len(core)
    return text[:start], text[end:]


def apply_spelling_to_words(words: list, corrections: list) -> tuple:
    """Respell timed word entries, phrase-aware. Returns `(words, made)`.

    The word-level twin of `apply_spelling`: the transcript pass fixes
    segment TEXT, but the caption planner (step 4.01) groups TIMED
    WORDS from the spine - a second ASR product the text pass never
    touches - so a "C RMs" fixed in the transcript still captions "c
    rms" without this. One correction at a time, in record order; a
    multi-token `heard` ("jim and i") matches a consecutive run and
    merges it into ONE entry spanning first start to last end (no
    timing is invented - the span is the span the audio measured);
    a single-token `correct` for a single-token `heard` rewrites the
    entry in place. `made` counts matched entries per correction in
    the caller's report. Pure: the input is not mutated.
    """
    out = [dict(w) for w in (words or [])]
    made: dict = {}
    for correction in corrections or []:
        heard = (correction.get("heard") or "").strip()
        correct = (correction.get("correct") or "").strip()
        if not heard or not correct:
            continue
        heard_cores = [word_core(t).lower() for t in heard.split()]
        heard_cores = [c for c in heard_cores if c]
        if not heard_cores:
            continue
        count = 0
        index = 0
        merged = []
        while index < len(out):
            run = out[index:index + len(heard_cores)]
            bodies = []
            clitic = ""
            ok = len(run) == len(heard_cores)
            if ok:
                for entry in run:
                    body, tail = _word_heard_forms(entry.get("word", ""))
                    bodies.append(word_core(body).lower())
                    clitic = tail  # only the run's last tail survives
                ok = bodies == heard_cores
            if ok:
                first, last = run[0], run[-1]
                fixed = _cased(correct, str(first.get("word", "")))
                first_lead, _ = _edge_punct(
                    str(first.get("word", "")))
                _, last_trail = _edge_punct(
                    str(last.get("word", "")))
                entry = dict(first)
                entry["word"] = (
                    first_lead + fixed
                    + (clitic if len(run) == 1 else "")
                    + last_trail)
                entry["end"] = last.get("end", entry.get("end"))
                merged.append(entry)
                count += 1
                index += len(run)
            else:
                merged.append(run[0])
                index += 1
        out = merged
        if count:
            made[correction.get("id", "")] = count
    return out, made


def apply_to_words(words: list, speaker: str | None,
                   project_folder: str) -> tuple:
    """Correct timed words for DISPLAY: respell, then suppress.

    What step 4.01 runs on each block's timeline words after the case
    and reading transforms and before `split_into_groups`: the same
    store the transcript pass enforces, applied to the timed words the
    text pass cannot reach. Spelling first (a respelled "CRMs" is gone
    before suppression looks for a stray "C"), suppression second via
    `filter_words`. Neighbour timings are never edited - entries are
    rewritten, merged or dropped, never retimed. Returns
    `(words, report)`; empty store returns the input untouched. Never
    raises: a correction pass must not refuse a caption plan (the
    transcript pass already reported what each learning did).
    """
    import sys

    current = list(words or [])
    report: dict = {"replacements": 0, "suppressed": 0, "applied": []}
    if not project_folder:
        return current, report
    try:
        corrections = spelling_corrections(project_folder)
        active = suppressions(project_folder)
    except Exception as exc:  # noqa: BLE001 - caption plan must survive
        print(f"WARNING: transcript corrections unreadable ({exc}); "
              f"caption words stand uncorrected.", file=sys.stderr)
        return current, report
    if not corrections and not active:
        return current, report
    try:
        if corrections:
            current, made = apply_spelling_to_words(current, corrections)
            for correction in corrections:
                count = made.get(correction.get("id", ""), 0)
                if count:
                    report["applied"].append(
                        {"id": correction.get("id", ""),
                         "heard": correction.get("heard", ""),
                         "correct": correction.get("correct", ""),
                         "replacements": count})
                    report["replacements"] += count
        if active:
            kept, _dropped = filter_words(current, speaker, active)
            # Per-learning counts, so a suppression that stopped
            # matching reads as zero rather than as absent.
            for suppression in active:
                _kept, dropped_check = filter_words(
                    current, speaker, [suppression])
                if dropped_check:
                    report["applied"].append(
                        {"id": suppression.get("id", ""),
                         "heard": suppression.get("heard", ""),
                         "suppressed": len(dropped_check)})
                    report["suppressed"] += len(dropped_check)
            current = kept
    except Exception as exc:  # noqa: BLE001 - caption plan must survive
        print(f"WARNING: transcript corrections failed ({exc}); "
              f"caption words stand uncorrected.", file=sys.stderr)
        return list(words or []), {"replacements": 0, "suppressed": 0,
                                   "applied": []}
    return current, report


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
    if new_text == (text or ""):
        # Every match respelled to itself ("CMOS" hearing "cmos" for a
        # "CMOs" verdict): nothing moved, so nothing is reported. A
        # stamp that counts no-ops as replacements reads as work on
        # every rerun and breaks the rerun-zero idempotence the store
        # promises.
        return text or "", 0
    return new_text, total


def apply_to_document(document: dict, project_folder: str) -> dict:
    """Respell and suppress a transcript document in place. The guarantee half.

    Rewrites every segment's `text` and its word entries, stamps which
    learnings were applied under `_APPLIED_KEY` (with per-learning
    counts, so a correction that stopped matching reads as zero rather
    than as absent), and reports `{"replacements", "segments_touched",
    "suppressed", "suppression_segments", "applied"}`. Idempotent:
    corrected text contains nothing to match, and a second suppression
    pass re-marks the same entries while dropping no further text.

    Spelling runs BEFORE suppression, on purpose: a "C RMs" the store
    respells to "CRMs" is gone before the suppression pass looks for a
    stray "C", so a fixed mishearing is never also counted as hidden.
    """
    corrections = spelling_corrections(project_folder)
    active_suppressions = suppressions(project_folder)
    report = {"replacements": 0, "segments_touched": 0, "applied": [],
              "suppressed": 0, "suppression_segments": 0}
    if not corrections and not active_suppressions:
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
    if active_suppressions:
        suppression_report = apply_suppressions(document,
                                                active_suppressions)
        report["suppressed"] = suppression_report["suppressed"]
        report["suppression_segments"] = \
            suppression_report["segments_touched"]
        report["applied"].extend(suppression_report["applied"])
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
    the model can quote the verdict rather than re-derive it. Active
    display suppressions follow as their own lines - "never re-quote
    it back in" only works when the prompt SAYS what is hidden. Empty
    when the project recorded none. Read by the reel semantic-visual
    request (`reel_semantic_visual.bridge_context`) and, through
    `project_context`, by every planning step routed `read_by: ["*"]`.
    """
    corrections = spelling_corrections(project_folder)
    active = suppressions(project_folder)
    if not corrections and not active:
        return ""
    lines = ["Recorded transcript corrections (quote the corrected "
             "spelling in every copy, subject and anchor_phrase):"]
    for correction in corrections:
        lines.append(f"- [{correction['id']}] heard "
                     f"\"{correction['heard']}\", write "
                     f"\"{correction['correct']}\"")
    for suppression in active:
        scope = suppression.get("scope")
        where = ("everywhere" if scope is None else
                 f"where {scope['speaker']} says it between "
                 f"\"{scope['prev']}\" and \"{scope['next']}\"")
        lines.append(f"- [{suppression['id']}] hidden token "
                     f"\"{suppression['heard']}\" {where}: pronounced, "
                     f"stays in the audio, never write it in a caption, "
                     f"quote, subject or anchor_phrase")
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


def exclusion_cuts_for_span(start: float, end: float,
                             exclusions: list) -> list:
    """Struck seconds inside one span, as `(start, end)` cuts.

    The BUILD-time half of keep enforcement, and deliberately NOT a
    trim-or-drop. `apply_keep_exclusions` above draws proposal windows:
    there an interior exclusion drops the moment, because selection can
    only offer ONE window and two windows would be a new reel. The
    builder does not offer windows - it lays keep ranges end to end
    (`reel_build.reel_ranges`), and a take cut already removes middle
    seconds the same way. A recorded exclusion reaching an APPROVED
    moment's build is cut out of its ranges exactly like one, so the
    reel stays one reel and plays fewer seconds. Nothing is split, so
    the no-split rule this module states above is kept, not bent.

    Why approval does not block this is the distinction the guard
    exists to draw. Approval stops the ENGINE re-deciding a range under
    the captain - re-running selection, re-ranking, re-drawing a span
    on its own judgement. A keep exclusion IS the captain's judgement,
    recorded with their reason and their id: applying it is obedience,
    not re-decision, and a guard that reads obedience as re-decision
    makes their edits do nothing on exactly the reels they annotated -
    an unapproved reel is one they have not looked at yet, and an
    approved one is one they HAVE looked at and left notes on. The
    mechanism was backwards relative to how it gets used, and this is
    what turns it round: selection still never rewrites an approved
    range, and the build still never re-decides one - it only stops
    playing seconds the captain struck.

    Returns the overlaps clipped to `[start, end]`, in order, as
    `(start, end, id)` triples - the id so the build can SAY which
    recorded strike removed which seconds, because a cut the operator
    cannot see is a silent content change. Empty where nothing recorded
    touches the span - the common case, and the caller then builds
    exactly what it built before.
    """
    cuts = []
    for exclusion in exclusions or []:
        try:
            ex_start = float(exclusion["start"])
            ex_end = float(exclusion["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not ex_end > ex_start:
            continue
        cut_start = max(float(start), ex_start)
        cut_end = min(float(end), ex_end)
        if cut_end > cut_start:
            cuts.append((cut_start, cut_end,
                         str(exclusion.get("id", ""))))
    cuts.sort()
    return cuts


#: How far back a strike's start may reach for the previous word's end.
#: Past this the extension stops being edge-dust and becomes editorial
#: scope, and the operator re-records it explicitly instead.
LEADIN_GROWTH_LIMIT = 2.0


def grow_cuts_over_wordless_leadin(intervals: list,
                                   transcript: dict) -> list:
    """Extend each cut's start back over a short wordless lead-in.

    A strike is recorded at word boundaries - 'so' starts at 653.421s -
    but the master clip starts earlier (653.137s here: every edit
    leaves lead-in handles). Cutting at the word strands the lead-in
    as a sub-floor nub the F7 readability check refuses, so the build
    that honours the strike fails on 6 frames of room tone. Where the
    previous timed word ends within `LEADIN_GROWTH_LIMIT` and nothing
    was timed between it and the cut, the cut starts there instead:
    only silence moves, no word is touched, and nothing placeable is
    left behind. Past the limit, or where a word starts inside the
    gap, the interval stands as recorded - a nub that really forms
    refuses loudly at the gate instead of being silently eaten.

    Returns new `(start, end, id)` triples; the input is untouched.
    Empty transcript (or None) grows nothing.
    """
    words = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                words.append((word_start, word_end))
    if not words:
        return list(intervals or [])
    grown = []
    for interval in intervals or []:
        s, e = float(interval[0]), float(interval[1])
        ident = str(interval[2]) if len(interval) > 2 else ""
        earlier = [we for _, we in words if we <= s + 1e-9]
        if earlier:
            prev_end = max(earlier)
            if (prev_end < s
                    and s - prev_end <= LEADIN_GROWTH_LIMIT
                    and not any(s > ws > prev_end for ws, _ in words)):
                s = prev_end
        grown.append((s, e, ident))
    return grown


#: Why one strike's END was left exactly where it was recorded.
#: Enumerated rather than free text, because the build PRINTS these and
#: a reader has to be able to tell "there was nothing to grow" from
#: "there was room tone and I could not take it".
TAIL_HELD_NEXT_WORD_AT_THE_EDGE = "next_word_at_the_edge"
TAIL_HELD_GAP_EXCEEDS_LIMIT = "gap_exceeds_limit"
TAIL_HELD_WORD_ENDS_INSIDE_THE_GAP = "word_ends_inside_the_gap"
TAIL_HELD_NO_TIMED_WORDS_AFTER = "no_timed_words_after"

TAIL_HELD_REASONS = (
    TAIL_HELD_NEXT_WORD_AT_THE_EDGE,
    TAIL_HELD_GAP_EXCEEDS_LIMIT,
    TAIL_HELD_WORD_ENDS_INSIDE_THE_GAP,
    TAIL_HELD_NO_TIMED_WORDS_AFTER,
)


def grow_cuts_over_wordless_tail(intervals: list,
                                 transcript: dict) -> tuple:
    """Extend each cut's END forward over a short wordless tail.

    The exact mirror of :func:`grow_cuts_over_wordless_leadin`, and it
    exists because the failure that function's docstring describes
    happens at BOTH edges and only one of them was covered.

    Measured 2026-09-12 on `lucie/geo-podcast`, Reel 13.  Keep
    exclusion `lc-0006` ends at 899.400 - Craig's word "company" ends
    exactly there, so the strike is recorded on a real word edge.  The
    master's angle switch back to Akshita is at 899.482.  Resuming the
    keep range at 899.400 therefore admitted 82ms of Craig's camera at
    the head of the second range, and the build gate refused it::

        video item 8 'LCATL0013.MXF' is 2 frames (0.083s), under the
        0.5s readability floor (12 frames at 23.976fps)

    Akshita's next timed word starts at 899.570, so 170ms of room tone
    sits between the struck words and her first word.  Growing the end
    there removes the flash and clears the angle switch by 88ms without
    touching a syllable.

    **The growth stops at the next timed word edge, always.**  That is
    the one way this could do damage: trading a two-frame flash for a
    clipped word is a worse edit than the flash, and this project has
    already paid for that lesson once - the Reel 30 seam that left "a
    web" mid-phrase (`learned_context` lc-0005).  So the end may reach
    the next word's START and never past it, and where there is no room
    tone to take the interval is HELD and the reason SAID rather than
    the word being swallowed.

    Bounded by the same :data:`LEADIN_GROWTH_LIMIT` as the head half:
    past it the extension stops being edge-dust and becomes editorial
    scope, which the operator re-records explicitly.

    Returns `(grown, held)`.  `grown` is new `(start, end, id)` triples
    in the input's order and the input is untouched.  `held` is one
    `{id, end, reason, next_word_start}` per interval whose end did not
    move, so a strike that may strand a nub says so on the run that
    honours it instead of failing silently at the gate.  An empty
    transcript grows nothing and holds nothing - there is no
    measurement to hold it against.
    """
    words = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                word_start = float(word["start"])
                word_end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if word_end > word_start:
                words.append((word_start, word_end))
    if not words:
        return list(intervals or []), []
    grown = []
    held = []
    for interval in intervals or []:
        s, e = float(interval[0]), float(interval[1])
        ident = str(interval[2]) if len(interval) > 2 else ""
        later = [ws for ws, _ in words if ws >= e - 1e-9]
        if not later:
            held.append({"id": ident, "end": e,
                         "reason": TAIL_HELD_NO_TIMED_WORDS_AFTER,
                         "next_word_start": None})
            grown.append((s, e, ident))
            continue
        next_start = min(later)
        if not next_start > e:
            reason = TAIL_HELD_NEXT_WORD_AT_THE_EDGE
        elif next_start - e > LEADIN_GROWTH_LIMIT:
            reason = TAIL_HELD_GAP_EXCEEDS_LIMIT
        elif any(e < we < next_start for _, we in words):
            reason = TAIL_HELD_WORD_ENDS_INSIDE_THE_GAP
        else:
            reason = ""
        if reason:
            held.append({"id": ident, "end": e, "reason": reason,
                         "next_word_start": next_start})
        else:
            e = next_start
        grown.append((s, e, ident))
    return grown, held
