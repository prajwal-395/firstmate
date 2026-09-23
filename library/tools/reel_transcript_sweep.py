"""Every reel's stray sounds, proposed for the captain, never auto-hidden.

The captain's Reel 17 note asked for ALL random transcript artifacts
cleaned, not just the one he marked - and his boundary for "any other
random artifacts like this" is filler sounds and word fragments: stray
single letters, "um" and "uh", half-words. Anything that is a SOUND
rather than a WORD. A real word he or Akshita actually said stays, even
when it reads awkwardly.

What already existed: the display-suppression mechanism
(`library/tools/transcript_corrections.py::record_display_suppression`)
targets a word without falsifying the transcript - the audio, the spine
and every timing stand, only the read text drops the token. What did
not exist is the sweep that finds candidates across every reel. This
module is that sweep.

Two deliberate narrowings, both from the brief:

* REEL-SCOPED, not transcript-scoped. `transcript_hygiene` scans the
  whole timeline transcript; the captain reads reels. A stray "s" in a
  passage no reel plays is not his note. So every candidate here is a
  timed word an APPROVED reel actually plays - membership read off
  `reel_build.reel_ranges` (the same ranges the build places, take cuts
  and recorded keep exclusions/insistences applied), never off the raw
  moment window.
* DETERMINISTIC shapes only, no model judgement. The brief's boundary
  is structural - a filler sound, a single-letter fragment, a
  hyphen-truncated half-word - so the sweep classifies by shape and
  proposes NOTHING ELSE. No respells, no duplicate/false-start
  verdicts, no "reads badly" cleanups: those need a sentence-level
  judge (`transcript_hygiene`) and a real word stays however awkward.
  Nothing here reuses a word list of its own: fillers are
  `retake_scan.FILLERS` (the measurement that already locates edit
  regions), singletons are `transcript_hygiene.DICTIONARY_SINGLETONS`
  (the dictionary fact that "a" and "I" are words).

Propose-and-accept is the whole editorial half. `--apply` records each
grouped candidate as a PENDING `mistake_fix` display suppression
(`proposed_by="model"`, `status=pending`): held, never enforced, until
the captain promotes it - the same shape `transcript_hygiene` gives
its own uncertain rows. A false positive (a placeholder "C", an
emphatic "um" he wants kept) retires from the queue rather than
rewriting a caption. The default previews only and records nothing.

Scopes follow the existing practice: a filler suppresses GLOBALLY (the
"um" the captain already hides everywhere), a fragment suppresses
ANCHORED to its speaker and neighbours (so a stray "s" never touches a
possessive or a placeholder letter). A fragment at a sentence edge has
an empty neighbour the store refuses to anchor on - it is HELD for the
captain's eyes in the report, never widened to global, because a
global single letter is exactly the overreach the anchor exists to
stop.

`tests/test_reel_transcript_sweep.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys


class SweepRefused(RuntimeError):
    """The sweep cannot start, and this says which input is missing."""


def classify_token(core: str, raw: str) -> str | None:
    """One token's artifact shape, or None for a word that stays.

    Pure: no store, no transcript, no judgement - the brief's boundary
    as three structural shapes. `core` is `word_core` of the token,
    `raw` the transcribed surface.

    * `"filler"` - the core is a stall sound (`retake_scan.FILLERS`).
    * `"fragment"` - a single-letter token that is not a word
      (`transcript_hygiene.DICTIONARY_SINGLETONS` are never this).
    * `"truncated"` - a token the transcriber left hyphen-open
      ("goin-", "compa-"): a half-word's own punctuation.

    A real word - however short, however awkward - classifies to None.
    """
    from library.tools import retake_scan as _rs
    from library.tools import transcript_hygiene as _hy

    lowered = (core or "").lower()
    if lowered and lowered in _rs.FILLERS:
        return "filler"
    if len(core) == 1 and core not in _hy.DICTIONARY_SINGLETONS:
        # `DICTIONARY_SINGLETONS` holds "a", "A" and "I": a lone
        # lowercase "i" (cased ASR writes the pronoun "I") is a
        # fragment's shape, and anything else one letter long is one
        # letter long. Non-alpha singletons (a stray "-" or ".") core
        # to "" and never reach here.
        if core.isalpha():
            return "fragment"
        return None
    stripped = (raw or "").strip()
    if stripped and stripped[-1] in ("-", "\u2010", "\u2011", "\u2012",
                                     "\u2013", "\u2014"):
        return "truncated"
    return None


def _overlaps(start: float, end: float, ranges: list) -> bool:
    return any(end > span_start and start < span_end
               for span_start, span_end in ranges)


def words_played_in_ranges(transcript: dict, ranges: list) -> list:
    """Timed, displayed words an approved reel plays, with anchors.

    Each row is `{"seg", "index", "word", "speaker", "start", "end",
    "prev", "next", "sentence"}` where `prev`/`next` are the neighbour
    word cores off the segment's own word list - the same list
    `filter_words` reads at apply time, so the anchor recorded here is
    the anchor that will match there. Untimed words never play a
    placeable span and already-hidden ones (`display: False`) are
    decided, so neither is listed.
    """
    from library.tools.transcript_corrections import word_core

    out = []
    for seg_no, segment in enumerate(transcript.get("segments") or []):
        words = segment.get("words") or []
        speaker = segment.get("speaker") or ""
        cores = [word_core(w.get("word", "")
                           if isinstance(w.get("word"), str) else "")
                 for w in words]
        for index, entry in enumerate(words):
            if not isinstance(entry, dict):
                continue
            surface = entry.get("word")
            if not isinstance(surface, str) or not surface.strip():
                continue
            if entry.get("display") is False:
                continue
            try:
                start, end = float(entry["start"]), float(entry["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not end > start or not _overlaps(start, end, ranges):
                continue
            out.append({
                "seg": seg_no, "index": index,
                "word": surface, "speaker": speaker,
                "start": start, "end": end,
                "prev": cores[index - 1] if index > 0 else "",
                "next": cores[index + 1] if index + 1 < len(cores) else "",
                "sentence": str(segment.get("text") or "")[:220],
            })
    return out


def _record_key(heard: str, scope: dict | None) -> tuple:
    if scope is None:
        return ("global", (heard or "").lower())
    return ("anchored",
            (scope.get("speaker") or "").strip().lower(),
            (scope.get("surface") or "").strip().lower(),
            (scope.get("prev") or "").strip().lower(),
            (scope.get("next") or "").strip().lower())


def _already_recorded_keys(project_folder: str) -> set:
    """Dedupe keys of every suppression on file, active or pending.

    A candidate the store already holds - enforced or awaiting the
    captain - is not proposed again. Pending learnings never reach
    `transcript_corrections.suppressions` (pending enforces nothing),
    so both listings are read: the active suppressions and the pending
    display suppressions beside them.
    """
    from library.tools import learned_context as _lc
    from library.tools import transcript_corrections as _tc

    keys = set()
    try:
        for suppression in _tc.suppressions(project_folder):
            keys.add(_record_key(suppression.get("heard", ""),
                                 suppression.get("scope")))
    except Exception:  # noqa: BLE001 - no store reads as no records
        pass
    try:
        for learning in _lc.pending(project_folder):
            source = (learning or {}).get("source") or {}
            if source.get("correction_type") != _tc.DISPLAY_SUPPRESSION:
                continue
            keys.add(_record_key(str(source.get("heard") or ""),
                                 source.get("scope")))
    except Exception:  # noqa: BLE001 - no store reads as no records
        pass
    return keys


def _spelling_owned_cores(project_folder: str) -> set:
    """Cores an active respell already answers, lowercased.

    Spelling runs BEFORE suppression (`apply_to_document`), so a token
    the store respells ("C" -> "see") is the spelling's business, never
    this sweep's: proposing to hide it would double-decide one token.
    """
    from library.tools import transcript_corrections as _tc

    try:
        corrections = _tc.spelling_corrections(project_folder)
    except Exception:  # noqa: BLE001 - no store reads as no ownership
        return set()
    return {_tc.word_core(c.get("heard", "")).lower()
            for c in corrections
            if _tc.word_core(c.get("heard", ""))}


def sweep_moments(moments, transcript: dict,
                  project_folder: str) -> dict:
    """Group artifact candidates across approved moments. Read-only.

    Returns `{"reels_swept", "words_played", "candidates",
    "moment_errors", "skipped"}` where each candidate carries its
    shape, its scope (`None` for a global filler, the anchor dict for
    a fragment), the reels that play it, and one evidence sentence.
    Identical proposals group to one candidate naming every reel -
    thirty reels saying "um" are one suppression, not thirty. Nothing
    is recorded here; `scan(..., apply=True)` records.
    """
    from library.tools import reel_build as _rb
    from library.tools import transcript_corrections as _tc

    report: dict = {"reels_swept": 0, "words_played": 0,
                    "candidates": [], "moment_errors": [],
                    "skipped": {}}

    def _skip(reason: str) -> None:
        report["skipped"][reason] = report["skipped"].get(reason, 0) + 1

    try:
        exclusions = _tc.keep_exclusions(project_folder)
    except Exception:  # noqa: BLE001 - no store reads as no strikes
        exclusions = []
    try:
        insistences = _tc.keep_insistences(project_folder)
    except Exception:  # noqa: BLE001 - no store reads as no insistence
        insistences = []
    recorded = _already_recorded_keys(project_folder)
    owned = _spelling_owned_cores(project_folder)

    grouped: dict = {}
    for moment in moments or []:
        try:
            number = int(getattr(moment, "number", 0))
        except (TypeError, ValueError):
            number = 0
        label = f"reel {number:02d}" if number else "reel ?"
        try:
            moment_cuts = _tc.grow_cuts_over_wordless_leadin(
                _tc.exclusion_cuts_for_span(
                    moment.timeline_start, moment.timeline_end,
                    exclusions),
                transcript)
            moment_cuts, _ = _tc.grow_cuts_over_wordless_tail(
                moment_cuts, transcript)
            ranges = _rb.reel_ranges(
                moment, transcript, extra_cuts=moment_cuts,
                insisted_spans=_tc.insisted_spans_for_span(
                    moment.timeline_start, moment.timeline_end,
                    insistences))
        except Exception as exc:  # noqa: BLE001 - one reel never stops
            # the sweep; the error is ON the report, not in a log
            # nobody reads, because a skipped reel is missed artifacts.
            report["moment_errors"].append(
                {"reel": label, "error": str(exc)})
            print(f"  sweep: {label} ranges failed ({exc}) - "
                  f"reel skipped, loudly.", file=sys.stderr)
            continue
        # The closer the build lays down last is speech the reel plays
        # too - `reel_ranges` already appends it, so nothing extra to
        # join here.
        report["reels_swept"] += 1
        for row in words_played_in_ranges(transcript, ranges):
            report["words_played"] += 1
            core = _tc.word_core(row["word"])
            if not core:
                _skip("empty core")
                continue
            if core.lower() in owned:
                _skip("owned by an active respell")
                continue
            shape = classify_token(core, row["word"])
            if shape is None:
                continue
            if shape == "filler":
                heard, scope = core, None
            else:
                heard = row["word"].strip()
                scope = {"speaker": (row["speaker"] or "").strip(),
                         "surface": heard,
                         "prev": row["prev"], "next": row["next"]}
                if not all(str(value or "").strip()
                           for value in scope.values()):
                    # A sentence-edge fragment the store refuses to
                    # anchor on. Held for the captain's eyes, never
                    # widened to global: a global single letter is
                    # the overreach the anchor exists to stop.
                    key = ("held-edge", row["speaker"], heard)
                    held = grouped.setdefault(
                        key, {"shape": shape, "heard": heard,
                              "scope": scope, "held_edge": True,
                              "reels": [], "evidence": []})
                    if label not in held["reels"]:
                        held["reels"].append(label)
                        held["evidence"].append(
                            f"{label} {row['start']:.2f}s: "
                            f"{row['sentence']}")
                    continue
            key = _record_key(heard, scope)
            if key in recorded:
                _skip("already recorded (active or pending)")
                continue
            grouped.setdefault(
                key, {"shape": shape, "heard": heard, "scope": scope,
                      "reels": [], "evidence": []})
            entry = grouped[key]
            if label not in entry["reels"]:
                entry["reels"].append(label)
            entry["evidence"].append(
                f"{label} {row['start']:.2f}s: {row['sentence']}")
    held = [entry for key, entry in grouped.items()
            if key[0] == "held-edge"]
    report["candidates"] = [entry for key, entry in grouped.items()
                            if key[0] != "held-edge"]
    report["held_for_captain"] = held
    return report


def scan(project_folder: str, moments=None, transcript: dict | None = None,
         apply: bool = False) -> dict:
    """Sweep approved reels for filler/fragment candidates.

    `moments`/`transcript` are injectable (tests pass fixtures); None
    loads the project's own proposal file and timeline transcript, and
    a missing file REFUSES rather than sweeping an empty set - an empty
    sweep reporting zero would read as "no artifacts", which is the
    finding unmade. With `apply` every non-held candidate records as a
    PENDING model-proposed display suppression and `report["recorded"]`
    carries the new ids; without it nothing is recorded and the report
    is the preview. Held sentence-edge fragments never record: they
    need the captain's eyes first.
    """
    from library.tools import learned_context as _lc
    from library.tools import reel_proposal as _rp
    from library.tools import transcript_corrections as _tc
    from library.tools.timeline_transcript import (
        transcript_path as _transcript_path)

    if transcript is None:
        path = _transcript_path(project_folder)
        if not path.is_file():
            raise SweepRefused(
                f"{path} does not exist - the timeline transcript is "
                f"written by `python3 -m library.tools.timeline_"
                f"transcript <project> --write` with Resolve open on "
                f"the project's own timeline, and there is nothing to "
                f"sweep without it.")
        transcript = json.loads(path.read_text(encoding="utf-8"))
    if moments is None:
        path = _rp.proposal_path(project_folder)
        if not path.is_file():
            raise SweepRefused(
                f"{path} does not exist - step 3.04 has not published "
                f"a reel plan for this project, so there are no reels "
                f"to sweep.")
        moments = _rp.approved_only(_rp.read_proposal(path))
    if not moments:
        raise SweepRefused(
            "no APPROVED reel moments: PROPOSED fails this gate as "
            "REJECTED does - the sweep covers what the captain "
            "approved, never what he has not ruled on yet.")
    report = sweep_moments(moments, transcript, project_folder)
    report["recorded"] = []
    if not apply:
        return report
    for candidate in report["candidates"]:
        scope = candidate["scope"]
        where = ("everywhere" if scope is None else
                 f"where {scope['speaker']} says it between "
                 f"\"{scope['prev']}\" and \"{scope['next']}\"")
        evidence = "; ".join(candidate["evidence"][:4])
        if len(candidate["evidence"]) > 4:
            evidence += (f"; +{len(candidate['evidence']) - 4} more")
        try:
            learning = _tc.record_display_suppression(
                project_folder, candidate["heard"],
                f"sweep over {', '.join(candidate['reels'])}: "
                f"{candidate['shape']} {where} ({evidence})",
                scope=scope, proposed_by="model",
                status=_lc.PENDING)
        except Exception as exc:  # noqa: BLE001 - record refuses loudly
            print(f"  sweep: proposal of {candidate['heard']!r} "
                  f"refused ({exc}); stays held.", file=sys.stderr)
            report.setdefault("held_for_captain",
                              []).append(candidate)
            continue
        report["recorded"].append(learning["id"])
    return report


def main(argv=None) -> int:
    """`python3 -m library.tools.reel_transcript_sweep PROJECT [--apply]`.

    Previews the sweep (per-candidate reels plus evidence) by default;
    `--apply` records the candidates as PENDING model-proposed display
    suppressions for the captain to promote or retire.
    """
    parser = argparse.ArgumentParser(
        description="Sweep every approved reel's played words for "
                    "filler sounds and word fragments, proposing "
                    "display suppressions the captain accepts.")
    parser.add_argument("project_folder")
    parser.add_argument("--apply", action="store_true",
                        help="record the candidates as PENDING "
                             "proposals (the default previews only)")
    args = parser.parse_args(argv)
    try:
        report = scan(args.project_folder, apply=args.apply)
    except SweepRefused as exc:
        print(f"reel_transcript_sweep refused: {exc}", file=sys.stderr)
        return 1
    print(f"reels swept: {report['reels_swept']}")
    print(f"words played: {report['words_played']}")
    print(f"candidates: {len(report['candidates'])}")
    for candidate in report["candidates"]:
        scope = candidate["scope"]
        where = ("global" if scope is None else
                 f"anchored {scope['speaker']} "
                 f"\"{scope['prev']}\" < {scope['surface']} > "
                 f"\"{scope['next']}\"")
        print(f"  {candidate['shape']} {candidate['heard']!r} "
              f"({where}) on {', '.join(candidate['reels'])}")
        for line in candidate["evidence"][:2]:
            print(f"    {line}")
    print(f"held for the captain: "
          f"{len(report.get('held_for_captain') or [])}")
    for held in report.get("held_for_captain") or []:
        print(f"  {held['shape']} {held['heard']!r} on "
              f"{', '.join(held['reels'])} (sentence edge - "
              f"needs an anchor the captain names)")
    if report["skipped"]:
        print("skipped: " + "; ".join(
            f"{count} {reason}"
            for reason, count in sorted(report["skipped"].items())))
    if report["moment_errors"]:
        print("moment errors:")
        for entry in report["moment_errors"]:
            print(f"  {entry['reel']}: {entry['error']}")
    print(f"recorded: {report['recorded']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
