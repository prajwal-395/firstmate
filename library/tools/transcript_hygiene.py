"""Finding the transcript corrections the captain keeps typing by hand.

Twenty of the 37 field-test notes (2026-09-19) are one shape: the
subtitle should carry what a reader needs, not a phonetic transcript
of what the microphone caught. A disfluency, a stray phoneme and a
false start are accurate and unhelpful; an acronym, a brand and a
product name are wrong when they come back lowercase or misheard.
Every one of the 27 corrections on file was hand-recorded from a
captain note - which is why he is typing the same class of note
fifteen times. This module is the finding half.

Not a word list. A list of filler words, or a list of acronyms,
standing alone in the code, is what the captain forbade on 2026-09-16:
it will not generalise past this one podcast with these two speakers.
What he described instead is measured signal plus the model's
judgement over it, in his precedence order:

1. the project's STATED terms - `correct` forms already on file in
   the store, which a re-transcription may spell a new way;
2. the MODEL reading a sentence and answering whether a token is a
   word the reader needs - through an injected `judge`, or by a human
   reviewing the scan report's proposals;
3. a documented fallback, never a silent one: without a judge the
   model half degrades to an UNEVALUATED list a human judges, rather
   than guessing.

The NOMINATION is deterministic and shape-based, never lexical: a
token is nominated by what can be measured about it, not by what it
says. Two shapes, each with its evidence attached:

* a single-character token that is not a dictionary word - a word
  carrying meaning is longer than one letter, and English has exactly
  two one-letter words ("a" and "I", nominated never: excluding them
  is a dictionary fact, not taste, and re-verifying two hundred
  articles by model call would be the treadmill in a new place). A
  lowercase "i" still nominates: in cased ASR the pronoun is "I", so
  a standalone "i" is a fragment's shape. What the nomination cannot
  see - a two-letter fragment ("qu"), a filler ("um"), a mishearing
  ("aics") - the sentence-level model pass sees, because it reads
  every sentence, not just the nominated tokens;
* an adjacent DUPLICATE ("I I", "company company") - a false start's
  own signature, measurable without knowing the word. Rhetorical
  repeats ("Geo Geo Geo") nominate too and the judge keeps them.

What the nomination is NOT is the verdict: "X, Y and Z" and "B
produces niche content" nominate (single letters) and the judge keeps
them, because a placeholder carrying meaning is a word the reader
needs. Confidence alone must not discriminate either: the default MFA
path carries NO per-word score (`ALIGNMENT_SCORE_ABSENT_MFA`), and a
wrong proper noun scores HIGHER than a right one 11 times out of 12 -
so any claim resting on confidence says which alignment path
produced it (`transcript_confidence.timed_by_mfa`), and the verdict
rests on the sentence, never the score.

The output is PROPOSED corrections recorded in the store with their
evidence and their provenance (`proposed_by="model"`, kind
`mistake_fix`): the same routing, the same retirement, an honest
`said_by`. A wrong model guess retires rather than wearing the
captain's name, and `learned_context.correct` promotes a confirmed
one. A CONFIDENT proposal auto-applies - a stated-term respell because
the project already said so, a judged disfluency suppression because
the captain asked for the cleanup and reviewing thirty single-letter
tokens by hand is the treadmill he is trying to get off. A proposal
whose own `why` flags uncertainty (`is_uncertain`) records PENDING
instead: held, never enforced, until a human promotes it - because on
2026-09-19 four such rows recorded ACTIVE anyway and one rewrote
"Sheehan" to "she even". Each record's detail says which evidence
class proposed it and why.

`tests/test_transcript_hygiene.py`.
"""

from __future__ import annotations

import json
import os
import statistics
import sys

from library.tools.ren_refusal import RenRefusal

#: Nomination is relative to each speaker's own median word duration.
#: Kept as corroborating evidence on each candidate (a 60ms "f" was
#: barely voiced; a 460ms "C" was deliberate) - never as the verdict:
#: duration alone cannot tell a fragment from a function word, which is
#: why the shapes above are the nominators and the sentence is the
#: judge. A fraction, never milliseconds.
DURATION_FRACTION = 0.5

#: The model's own uncertainty vocabulary, as it wrote it unprompted
#: on 2026-09-19 ("NEEDS CAPTAIN CONFIRMATION", "LOW CONFIDENCE",
#: "BORDERLINE" - each recorded ACTIVE anyway, and one rewrote a name).
#: A verdict row whose `why` carries one of these is recorded PENDING,
#: never applied, until a human promotes it. This is not a word list
#: deciding a creative outcome (AGENTS.md 10.5's ban): it decides
#: nothing about what the subtitle says, only whether a proposal the
#: model itself flagged as unsure may act before a human confirms it.
#: The judge prompt below declares the contract - "say so plainly in
#: `why` when you are unsure" - so the vocabulary is read off the
#: model's own admissions, not imposed on its prose. Matching is
#: case-insensitive substring on the row's `why` ONLY, never on the
#: composed record: the scanner's own statement template says "retire
#: it if wrong", which must not read as uncertainty.
UNCERTAIN_MARKERS = (
    "needs captain confirmation",
    "needs confirmation",
    "low confidence",
    "not confident",
    "borderline",
    "unsure",
    "not sure",
    "uncertain",
    "not certain",
)


def is_uncertain(why: str) -> bool:
    """Whether a verdict row's `why` flags its own uncertainty.

    Pure: the held-for-confirmation decision in one predicate, so the
    scan and its tests cannot disagree about which rows wait for a
    human. Empty or non-string reasons are certain - silence is not an
    admission, and a row with no reason at all is refused upstream in
    `_dispose_batch` rather than guessed about here.
    """
    if not isinstance(why, str) or not why.strip():
        return False
    lowered = why.lower()
    return any(marker in lowered for marker in UNCERTAIN_MARKERS)
#: The two one-letter English words. A single-character token that is
#: one of these is never nominated: excluding them is a dictionary
#: fact, not taste. Everything else one letter long is a fragment's
#: shape (in cased ASR the pronoun is "I", so a standalone lowercase
#: "i" nominates).
DICTIONARY_SINGLETONS = frozenset({"a", "A", "I"})


def _words_with_duration(segment: dict) -> list:
    out = []
    for index, word in enumerate(segment.get("words") or []):
        if not isinstance(word, dict) or not isinstance(
                word.get("word"), str):
            continue
        try:
            duration = float(word["end"]) - float(word["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if duration <= 0:
            continue
        out.append({"index": index, "entry": word, "duration": duration})
    return out


def speaker_median_durations(document: dict) -> dict:
    """Each speaker's median word duration, measured off the document."""
    by_speaker: dict = {}
    for segment in document.get("segments") or []:
        speaker = segment.get("speaker") or ""
        for row in _words_with_duration(segment):
            by_speaker.setdefault(speaker, []).append(row["duration"])
    return {speaker: statistics.median(durations)
            for speaker, durations in by_speaker.items()
            if durations}


def alignment_note(document: dict) -> str:
    """The one sentence every proposal's evidence carries about scores.

    Which path timed these words decides what a score would even mean,
    so the note names it: MFA timed them and carries no per-word score
    (`ALIGNMENT_SCORE_ABSENT_MFA`), or the path that did. A proposal
    that never says this invites reading confidence into evidence that
    has none.
    """
    from library.tools import transcript_confidence as _conf

    try:
        mfa = _conf.timed_by_mfa(document)
    except Exception:  # noqa: BLE001 - the note degrades, not the scan
        mfa = False
    if mfa:
        return ("timed by the MFA path, which carries no per-word "
                "score - confidence is not the discriminator here.")
    return ("not timed by the MFA path; per-word scores, where "
            "present, are corroboration only, never the verdict.")


def nominate(document: dict) -> list:
    """Candidate tokens by measurable shape, with evidence. Read-only.

    Each candidate is `{"seg", "index", "word", "speaker", "duration",
    "prev", "next", "shape", "sentence"}` where `shape` is
    `single_char` and/or `duplicate`, plus the speaker-median-relative
    duration as corroboration. Already-hidden words (`display: False`)
    never nominate: judging them again would propose what the store
    already decided. No word list anywhere in this function - a token
    nominates by its measurements, never by its text.
    """
    from library.tools.transcript_corrections import word_core

    medians = speaker_median_durations(document)
    note = alignment_note(document)
    candidates = []
    segments = document.get("segments") or []
    for seg_no, segment in enumerate(segments):
        speaker = segment.get("speaker") or ""
        rows = _words_with_duration(segment)
        cores = [word_core(r["entry"]["word"]) for r in rows]
        median = medians.get(speaker, 0)
        for pos, row in enumerate(rows):
            entry = row["entry"]
            if entry.get("display") is False:
                continue
            core = cores[pos]
            prev = cores[pos - 1] if pos > 0 else ""
            nxt = cores[pos + 1] if pos + 1 < len(cores) else ""
            shapes = []
            if len(core) == 1 and core not in DICTIONARY_SINGLETONS:
                shapes.append("single_char")
            if (core and prev and core.lower() == prev.lower()):
                shapes.append("duplicate")
            if not shapes:
                continue
            candidates.append({
                "seg": seg_no, "index": row["index"],
                "word": entry["word"], "speaker": speaker,
                "duration": round(row["duration"], 3),
                "median_fraction": (round(row["duration"] / median, 2)
                                    if median else None),
                "prev": prev, "next": nxt,
                "shape": "+".join(shapes),
                "sentence": str(segment.get("text") or "")[:220],
                "alignment": note,
            })
    return candidates


def segments_for_judgement(document: dict) -> list:
    """Every segment as the model reads it. Read-only.

    `[{"seg", "speaker", "words", "text"}]` with `words` the segment's
    token surfaces in order, so a verdict naming `seg`+`index` pins one
    word entry exactly. The judge reads SENTENCES, not candidates: a
    filler ("um"), a two-letter fragment ("qu") and a mishearing
    ("aics") nominate nothing and are found here, in context.
    """
    view = []
    segments = document.get("segments") or []
    for seg_no, segment in enumerate(segments):
        words = [w.get("word") for w in segment.get("words") or []
                 if isinstance(w, dict)
                 and isinstance(w.get("word"), str)]
        if not words:
            continue
        view.append({"seg": seg_no,
                     "speaker": segment.get("speaker") or "",
                     "words": words,
                     "text": str(segment.get("text") or "")})
    return view


def stated_terms(project_folder: str) -> list:
    """Proper forms the project already declared, as matchable terms.

    The store's active spelling corrections, `correct`-side: a verdict
    the captain (or a confirmed model proposal) already gave. A
    re-transcription that spells one a new way ("lusie", "LUCIE") is
    caught here with STATED-TERM evidence - the project said so, no
    judgement required. Returns `{"correct", "id"}` in record order.
    """
    from library.tools import transcript_corrections as _tc

    terms = []
    try:
        corrections = _tc.spelling_corrections(project_folder)
    except Exception:  # noqa: BLE001 - no stated terms, not no scan
        return terms
    for correction in corrections:
        correct = (correction.get("correct") or "").strip()
        if correct and all(t["correct"] != correct for t in terms):
            terms.append({"correct": correct,
                          "id": correction.get("id", "")})
    return terms


def find_stated_term_variants(document: dict, terms: list) -> list:
    """Transcript tokens a stated term already answers. Read-only.

    A token whose core matches a term case-insensitively but is not
    the term itself ("lusie" where the store says "Lucie") proposes a
    respell with stated-term evidence. Tokens the deterministic pass
    already fixes never reach here as proposals - the pass runs first
    and this reads the document AFTER it, so what remains is a genuinely
    new surface for an old verdict.
    """
    from library.tools.transcript_corrections import word_core

    proposals = []
    for seg_no, segment in enumerate(document.get("segments") or []):
        for word in segment.get("words") or []:
            if not isinstance(word, dict) or not isinstance(
                    word.get("word"), str):
                continue
            if word.get("display") is False:
                continue
            core = word_core(word["word"])
            if not core:
                continue
            for term in terms:
                correct = term["correct"]
                if (core.lower() == word_core(correct).lower()
                        and core != word_core(correct)):
                    proposals.append({
                        "heard": core, "correct": correct,
                        "evidence": (
                            f"seg{seg_no} "
                            f"{segment.get('speaker') or '?'} says "
                            f"\"{core}\"; the store already verdicts "
                            f"this term as \"{correct}\" "
                            f"({term['id']}) - a new surface for an "
                            f"old verdict, no judgement required."),
                        "class": "stated_term",
                    })
                    break
    # One proposal per heard surface: thirty segments saying "lusie"
    # are one correction, not thirty.
    seen: dict = {}
    for proposal in proposals:
        key = (proposal["heard"].lower(), proposal["correct"])
        if key not in seen:
            seen[key] = proposal
    return list(seen.values())


def _llm_judge(view: list, candidates: list, terms: list,
               project_folder: str, batch: int = 30) -> dict:
    """The model reads the sentences and disposes the candidates.

    The unit of judgement is the SENTENCE, batched (`batch` segments
    per call): nominated shapes are flagged inside their sentence with
    `[*]`, but the model reads every token - a filler ("um"), a
    two-letter fragment ("qu") and a mishearing ("aics") nominate
    nothing and are found here, in context. Returns `{"suppress":
    [...], "respell": [...], "unevaluated": [...]}`. A suppression is
    `{"seg", "index", "scope", "why"}` (`scope` `"global"` only where
    the token needs no context - the model says so per token, never a
    list here); a respell is `{"heard", "correct", "why"}`.

    Nothing is guessed here: every candidate returns UNEVALUATED with
    its evidence attached. Judgement arrives one of two ways - a caller
    injects a `judge` (tests do; a host harness answers through its own
    channel), or a human reviews the report's proposals and records
    them with `transcript_corrections.record_spelling` /
    `record_display_suppression`. Direct provider API calls
    (`LLMClient`) were removed: Ren works with OAuth harnesses, and a
    keyed judge that posts prompts to an API is out of scope.
    """
    print("  hygiene: judge deferred - "
          f"{len(candidates)} nominated candidate(s) returned "
          f"UNEVALUATED rather than guessed.\n"
          f"    why: no automated judge is wired here; guessing would "
          f"write corrections nobody decided.\n"
          f"    fix: review the unevaluated proposals in the scan report "
          f"(each carries its evidence) and record the verdict with "
          f"transcript_corrections, or pass a judge to scan.",
          file=sys.stderr)
    return {"suppress": [], "respell": [],
            "unevaluated": list(candidates)}


class HygieneError(RenRefusal):
    """A model verdict that cannot be honoured as written.

    Every site raises with the message alone; the fix is uniform
    because every one faults the judge's answer: answer with one JSON
    object carrying `suppress` and `respell` lists, and nothing else.
    """

    def __init__(self, message: str) -> None:
        super().__init__(
            what=message,
            why=("a verdict that cannot be honoured is refused, never "
                 "partially applied - half a judgement is a guess"),
            fix=("answer with one JSON object carrying `suppress` and "
                 "`respell` lists, and nothing else"))


def _parse_verdict(raw: str) -> dict:
    """One JSON object out of a model answer, or a loud refusal.

    Fenced code blocks unwrap; anything else must parse as JSON with
    `suppress` and `respell` lists. A verdict that cannot be honoured
    is refused, never partially applied - half a judgement is a guess.
    """
    import re

    text = str(raw or "").strip()
    if not text or text == "{}":
        raise HygieneError("the model answered nothing.")
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        verdict = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise HygieneError(
            f"the verdict is not JSON ({exc}): {text[:160]!r}.") from exc
    if not isinstance(verdict, dict):
        raise HygieneError("the verdict is not an object.")
    for key in ("suppress", "respell"):
        if not isinstance(verdict.get(key), list):
            raise HygieneError(
                f"the verdict has no {key!r} list.")
    return verdict


def _dispose_batch(view: list, verdict: dict) -> tuple:
    """Sort one batch verdict onto its sentences. Pure.

    Returns `((suppressions, respells), refused)`. A suppression
    names `seg` + `index` into the view's word lists; a row naming no
    segment, a wild index, a wild scope, or no reason is REFUSED and
    the token stands - a judgement that cannot name its token is not a
    judgement. A respell with no heard, no correct form, or no reason
    is refused the same way. Speaker and neighbours resolve off the
    view, so the recorded anchor is the sentence's own words.
    """
    from library.tools.transcript_corrections import word_core

    by_seg = {row["seg"]: row for row in view}
    suppressions = []
    respells = []
    refused = []
    for row in verdict.get("suppress") or []:
        if not isinstance(row, dict):
            refused.append(f"non-object suppress row {row!r}")
            continue
        seg, index = row.get("seg"), row.get("index")
        scope = str(row.get("scope") or "").strip().lower()
        why = str(row.get("why") or "").strip()
        target = by_seg.get(seg) if isinstance(seg, int) else None
        words = target["words"] if target else []
        if (target is None or not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < len(words)):
            refused.append(f"suppress row names no word: {row!r}")
            continue
        if scope not in ("global", "anchored"):
            refused.append(
                f"suppress row scopes {row.get('scope')!r}: "
                f"one of global, anchored")
            continue
        if not why:
            refused.append(f"suppress row gives no reason: {row!r}")
            continue
        cores = [word_core(t) for t in words]
        suppressions.append({
            "seg": seg, "index": index, "word": words[index],
            "speaker": target["speaker"],
            "prev": cores[index - 1] if index > 0 else "",
            "next": cores[index + 1] if index + 1 < len(cores) else "",
            "scope": scope, "why": why,
        })
    for row in verdict.get("respell") or []:
        if not isinstance(row, dict):
            refused.append(f"non-object respell row {row!r}")
            continue
        heard = str(row.get("heard") or "").strip()
        correct = str(row.get("correct") or "").strip()
        why = str(row.get("why") or "").strip()
        if not heard or not correct or not why:
            refused.append(f"respell row names nothing: {row!r}")
            continue
        respells.append({"heard": heard, "correct": correct,
                         "why": why})
    return (suppressions, respells), refused


def _evidence_for(document: dict, seg: int, index: int) -> str:
    """What was measured about one word entry, for a proposal's detail."""
    try:
        entry = (document.get("segments") or [])[seg]["words"][index]
        duration = float(entry["end"]) - float(entry["start"])
        duration_note = f"{duration:.2f}s"
    except (IndexError, KeyError, TypeError, ValueError):
        duration_note = "untimed"
    return f"seg{seg} word {index} ({duration_note}); {alignment_note(document)}"


def scan(project_folder: str, document: dict, judge=None,
         apply: bool = True) -> dict:
    """Nominate, judge, and record transcript hygiene proposals.

    `judge` is `(view, candidates, terms, project_folder) -> verdict`
    (default `_llm_judge`); tests inject a stub, and a keyless
    environment degrades to UNEVALUATED rather than guessing. With
    `apply` (the default), stated-term respells record with
    stated-term evidence and judged suppressions/respells record as
    `mistake_fix` with their measurements and the model's reason -
    confident ones auto-applying, per the module docstring, while a
    row whose own `why` flags uncertainty (`is_uncertain`) records
    PENDING: held for a human, never enforced, until promoted. With
    `apply=False` nothing is recorded and the report is the preview.
    Returns the full report: per-class counts plus every proposal,
    because the proposal LIST is the reviewable artefact, not its
    count. `report["pending"]` names the held ids - a subset of
    `report["recorded"]`, listed apart so the review queue is read off
    the run, not reconstructed from the store.
    """
    from library.tools import learned_context as _lc
    from library.tools import transcript_corrections as _tc

    report: dict = {"candidates": 0, "stated_term": [],
                    "suppress": [], "respell": [],
                    "unevaluated": [], "recorded": [], "pending": []}
    candidates = nominate(document)
    report["candidates"] = len(candidates)
    terms = stated_terms(project_folder)
    variants = find_stated_term_variants(document, terms)
    report["stated_term"] = variants
    view = segments_for_judgement(document)
    verdict = (judge or _llm_judge)(view, candidates, terms,
                                    project_folder)
    report["suppress"] = verdict.get("suppress", [])
    report["respell"] = verdict.get("respell", [])
    report["unevaluated"] = verdict.get("unevaluated", [])
    if not apply:
        return report
    for variant in variants:
        learning = _tc.record_spelling(
            project_folder, variant["heard"], variant["correct"],
            variant["evidence"], proposed_by="captain")
        # A stated term is the project's own verdict, so it records
        # under the project's name even when the scanner found the
        # instance: the captain said the WORD, the scan only found
        # where else it was misheard.
        report["recorded"].append(learning["id"])
    for respell in report["respell"]:
        held = is_uncertain(respell["why"])
        learning = _tc.record_spelling(
            project_folder, respell["heard"], respell["correct"],
            f"model judgement over the transcript sentence: "
            f"{respell['why']} ({alignment_note(document)})",
            proposed_by="model",
            status=_lc.PENDING if held else _lc.ACTIVE)
        report["recorded"].append(learning["id"])
        if held:
            # The model flagged this row unsure: recorded, never
            # enforced, until a human promotes it.
            report["pending"].append(learning["id"])
    for suppression in report["suppress"]:
        scope = (None if suppression["scope"] == "global" else
                 {"speaker": suppression.get("speaker") or "?",
                  "surface": str(suppression.get("word") or ""),
                  "prev": suppression.get("prev") or "?",
                  "next": suppression.get("next") or "?"})
        # An anchor with an empty neighbour cannot match loudly or
        # safely, and `record_display_suppression` refuses one - so a
        # sentence-edge fragment is refused here and returns to
        # unevaluated, loudly, rather than widening to global.
        try:
            held = is_uncertain(str(suppression.get("why") or ""))
            learning = _tc.record_display_suppression(
                project_folder, str(suppression.get("word") or ""),
                f"model judgement: {suppression.get('why') or ''} "
                f"({_evidence_for(document, suppression.get('seg', -1),
                                  suppression.get('index', -1))})",
                scope=scope, proposed_by="model",
                status=_lc.PENDING if held else _lc.ACTIVE)
        except Exception as exc:  # noqa: BLE001 - record refuses loudly
            print(f"  hygiene: suppression of "
                  f"\"{suppression.get('word')}\" refused ({exc}); "
                  f"stays unevaluated.", file=sys.stderr)
            report["unevaluated"].append(suppression)
            continue
        report["recorded"].append(learning["id"])
        if held:
            report["pending"].append(learning["id"])
    return report


def main(argv=None) -> int:
    """`python3 -m library.tools.transcript_hygiene PROJECT [--apply]`.

    Reads the project's timeline transcript, runs the scan, and prints
    the per-class counts plus every proposal. `--apply` records (the
    default previews only): the finding half proposes, a human fires.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Nominate transcript hygiene proposals and, with "
                    "--apply, record them as model-proposed corrections.")
    parser.add_argument("project_folder")
    parser.add_argument("--transcript", default="",
                        help="transcript JSON; default is the project's "
                             "timeline transcript")
    parser.add_argument("--apply", action="store_true",
                        help="record the proposals in learned_context")
    args = parser.parse_args(argv)
    path = args.transcript or os.path.join(
        args.project_folder, "pipeline_output", "scratch",
        "timeline_transcript", "transcript.json")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    from library.tools import transcript_corrections as _tc
    _tc.apply_to_document(document, args.project_folder)
    report = scan(args.project_folder, document, apply=args.apply)
    print(f"candidates: {report['candidates']}")
    print(f"stated-term variants: {len(report['stated_term'])}")
    for variant in report["stated_term"]:
        print(f"  TERM respell \"{variant['heard']}\" -> "
              f"\"{variant['correct']}\": {variant['evidence']}")
    print(f"model suppressions: {len(report['suppress'])}")
    for suppression in report["suppress"]:
        print(f"  SUPPRESS seg{suppression['seg']} "
              f"\"{suppression['word']}\" ({suppression['scope']}): "
              f"{suppression['why']}")
    print(f"model respells: {len(report['respell'])}")
    for respell in report["respell"]:
        print(f"  RESPELL \"{respell['heard']}\" -> "
              f"\"{respell['correct']}\": {respell['why']}")
    print(f"unevaluated: {len(report['unevaluated'])}")
    print(f"recorded: {report['recorded']}")
    print(f"pending (held for confirmation): {report['pending']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())