"""Whether a reel's ending follows from what that reel just said.

The captain ruled on 2026-09-19, choosing from a survey after firstmate
measured that six ending clips are pasted bit-identical into 3-8 reels
each - 28 of his 30 reels close on one of six. His choice, verbatim:

> "Require each closer to fit its reel - Keep reusing closers, but the
> build must check the ending actually follows from what that reel just
> said, and flag the ones that don't. ... It re-cuts an unknown subset,
> but it measures that subset BEFORE anything changes, so you see the
> cost before paying it."

Two halves, and the second is the one that gets forgotten. **Reuse is
NOT a defect** - he explicitly declined giving every reel its own
ending, so nothing here de-duplicates closers and nothing here proposes
it. **Topical misfit IS the defect.** And **the subset is measured and
shown to him before anything is re-cut** - he picked this option
precisely because the cost is knowable first.

What this is NOT
---------------
**Not a chooser.** `library/tools/reel_build.py` declares that module
"Not a chooser" - which passage is a good CTA, and which reel it suits,
is the model's judgement and the captain's approval - and that doctrine
stands. Nothing here scores, ranks or matches a closer to a reel.

**No number decides fit.** "Does this ending follow" is a creative
judgement, and the captain's 2026-09-16 ruling forbids a number deciding
a creative outcome. So there is no keyword overlap score and no
threshold here - not even a generous one. This module renders what the
model needs (the reel's own kept words, the closer's words, who shares
the closer) and reads back a verdict WITH A REASON. An empty verdict is
a complete answer: the reel was never asked.

**This never re-cuts.** A misfit is REPORTED - at build time beside
`closer_repeats`, and in the survey this module runs - and the reel is
placed whole whatever it says. A re-cut wave is a separate dispatch
after the captain has seen the number.

Where it is read
----------------
- `rebuild_reels_in_project` prints, per reel, how many reels share
  the closer and the recorded fit verdict for it (or that none was
  recorded), beside the `closer_repeats` echo report. The verdict comes
  from the survey sidecar, never from a fresh judgement at build time:
  a build places, it does not ask.
- The survey (`python3 -m library.tools.closer_fit survey --project
  <path>`) renders one fit context per reel for the model, records the
  answers into the sidecar, and prints the misfit list with its count.
  That list and count are the 2026-09-19 ruling's deliverable.

`tests/test_closer_fit.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

#: The verdicts a model judgement may carry. There is no magnitude -
#: `read_fit_answer` accepts these two words and a reason, and drops
#: anything else, so a score beside a verdict can never read as one.
FIT_VERDICTS = ("follows", "misfit")

#: A judgement that was never made. A reader state, never something a
#: model writes: `read_fit_answer` returns this when the answer carries
#: no usable verdict, WITH the reason it could not be read.
UNJUDGED = "unjudged"

SIDECAR_NAME = "closer_fit.json"


def closer_range(moment) -> Optional[Tuple[float, float]]:
    """This moment's closing CTA range on the MASTER, or None.

    One spelling: `reel_build.cta_range` owns the coercion (a moment
    that never mentioned a CTA reads as "no closer" rather than
    raising), and this reads through it so the two cannot disagree
    about what counts as a closer.
    """
    from library.tools.reel_build import cta_range

    return cta_range(moment)


def _overlaps(first: Tuple[float, float],
              second: Tuple[float, float]) -> bool:
    """Do two master ranges share at least one instant.

    Strict: ranges that merely abut share no speech and are not the
    same clip. Snapped boundaries move by fractions of a second, so a
    shared clip always overlaps properly, never just touches.
    """
    return max(first[0], second[0]) < min(first[1], second[1]) - 1e-9


def reuse_groups(moments: Sequence) -> List[dict]:
    """The reels closing on the same ending clip, as overlap groups.

    One CTA range may close any number of reels (`reel_build`), and
    boundary snaps move a shared clip's edges by fractions of a second
    between reels - so sameness is CONNECTED overlap, not exact
    equality. Two closers whose master ranges share an instant are the
    same clip placed again; groups are the connected components of
    that relation.

    Returns one dict per group, `{"ranges", "reels", "count"}`, groups
    ordered by lowest reel number, reels ascending. A moment with no
    closer belongs to no group - "no ending" is not a shared ending.
    A group of one is a complete answer: that reel closes on its own
    clip. Nothing here says a group is good or bad - reuse is not a
    defect, and the count is reported so the captain sees it, not so a
    gate can fire on it.
    """
    indexed: List[Tuple[int, Tuple[float, float]]] = []
    for moment in moments or ():
        number = int(getattr(moment, "number", 0) or 0)
        span = closer_range(moment)
        if span is None:
            continue
        indexed.append((number, (float(span[0]), float(span[1]))))
    parent = {i: i for i in range(len(indexed))}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(indexed)):
        for j in range(i + 1, len(indexed)):
            if _overlaps(indexed[i][1], indexed[j][1]):
                parent[find(i)] = find(j)
    clustered: Dict[int, List[int]] = {}
    for i in range(len(indexed)):
        clustered.setdefault(find(i), []).append(i)
    groups = []
    for members in clustered.values():
        reels = sorted(indexed[i][0] for i in members)
        ranges = sorted({(round(indexed[i][1][0], 2),
                          round(indexed[i][1][1], 2))
                         for i in members})
        groups.append({"ranges": ranges, "reels": reels,
                       "count": len(reels)})
    groups.sort(key=lambda group: group["reels"][0])
    return groups


def group_for(moment, groups: Sequence[dict]) -> Optional[dict]:
    """The reuse group this moment's closer belongs to, or None."""
    number = int(getattr(moment, "number", 0) or 0)
    for group in groups or ():
        if number in group.get("reels", []):
            return group
    return None


def _timed_words(transcript: dict):
    """Every timed word as `(start, end, token, speaker)`, master order."""
    for segment in (transcript or {}).get("segments") or ():
        speaker = segment.get("speaker")
        for word in (segment.get("words") or ()):
            if not word.get("timed"):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = str(word.get("word", ""))
            if token:
                yield start, end, token, speaker


def _snapped(moment, transcript: dict):
    """This moment the way the build ranges it: snapped, in memory.

    The stored proposals file predates the boundary drawer, so a
    stored boundary can sit inside a word - and the build repairs each
    moment on the way through (`reel_proposal.snap_moment_to_speech`)
    without rewriting the file. What a viewer hears is the SNAPPED
    ranges, so this is what the words are read off. A snap that itself
    raises returns the moment as stored: the builder's refusal then
    says why, in its own words.
    """
    try:
        from library.tools.reel_proposal import snap_moment_to_speech

        fixed, _moves = snap_moment_to_speech(moment, transcript)
        return fixed
    except Exception:  # noqa: BLE001 - the builder reports the refusal.
        return moment


def body_kept_words(moment, transcript: dict, *,
                    extra_cuts=(), insisted_spans=()) -> List[dict]:
    """The words this reel PLAYS before its closer, in reel order.

    Read off the PLAYED ranges - the snapped moment through
    `reel_ranges`, minus the trailing closer - through `reel_time`,
    the same arithmetic the picture goes through, so a bad take the
    cutter removed has taken its words with it here too. `extra_cuts`
    and `insisted_spans` are the captain's recorded keep exclusions
    and insistences for this moment (`reel_build.reel_ranges` takes
    the same two): without them a cut the captain already withdrew
    still reads as removed here, and the words are wrong. Each entry
    is `{"word", "speaker", "at"}` with `at` the reel second. A
    moment the builder refuses carries no words rather than a guess:
    the builder's refusal says why, in its own words, and a second
    report beside it would read as a second defect.
    """
    from library.tools.reel_build import closer_seam, reel_ranges, reel_time

    try:
        ranges = reel_ranges(_snapped(moment, transcript), transcript,
                             extra_cuts=extra_cuts,
                             insisted_spans=insisted_spans)
    except Exception:  # noqa: BLE001 - refused spans are the
        # builder's to report, not this instrument's.
        return []
    seam = closer_seam(moment, ranges)
    found: List[Tuple[float, dict]] = []
    for start, end, token, speaker in _timed_words(transcript):
        at = reel_time(start, ranges)
        if at is None:
            continue
        if seam is not None and at >= seam:
            continue
        # A word the range end lands exactly on belongs to the range
        # it closes (`reel_time(at_end=True)`); the half-open read
        # above drops it, so check the closing read before skipping.
        if seam is not None:
            closing = reel_time(end, ranges, at_end=True)
            if closing is not None and closing <= seam:
                found.append((closing, {"word": token,
                                        "speaker": speaker, "at": closing}))
                continue
        found.append((at, {"word": token, "speaker": speaker, "at": at}))
    found.sort(key=lambda pair: pair[0])
    return [word for _, word in found]


def closer_words(moment, transcript: dict) -> List[dict]:
    """The words inside this moment's closer range, in master order.

    The closer is placed whole - nothing is cut out of it - so this is
    a straight master-range read, not a played-ranges one, off the
    SNAPPED closer (the build snaps stored CTA boundaries the same way
    it snaps bodies). Each entry is `{"word", "speaker", "at"}` with
    `at` the master second.
    """
    span = closer_range(_snapped(moment, transcript))
    if span is None:
        return []
    out = []
    for start, end, token, speaker in _timed_words(transcript):
        if start >= span[0] and start < span[1]:
            out.append({"word": token, "speaker": speaker, "at": start})
    return out


def words_text(words: Sequence[dict]) -> str:
    """The words as one spoken line."""
    return " ".join(str(word.get("word", "")) for word in words or ()).strip()


def _speakers_in(words: Sequence[dict]) -> List[str]:
    """The voices in these words, first-heard order."""
    speakers: List[str] = []
    for word in words or ():
        speaker = word.get("speaker")
        if speaker and speaker not in speakers:
            speakers.append(speaker)
    return speakers


def _segment_at(transcript: dict, when: float) -> Optional[dict]:
    """The transcript segment speaking at this master second, if any."""
    for segment in (transcript or {}).get("segments") or ():
        try:
            start, end = float(segment["timeline_start"]), float(
                segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start < when < end:
            return segment
    return None


def fit_context(moment, transcript: dict,
                group: Optional[dict] = None, *,
                extra_cuts=(), insisted_spans=()) -> dict:
    """Everything a model needs to judge whether this ending follows.

    The reel's own kept words (what a viewer hears before the ending),
    the closer's words, who speaks each, what the closer opens on, and
    how many reels share the clip. REPORTS, never decides: no score,
    no threshold, no verdict. An empty body or an empty closer is
    reported as empty, not filled in - a transcription gap the model
    cannot see would otherwise read as a judgement about the writing.
    """
    body = body_kept_words(moment, transcript, extra_cuts=extra_cuts,
                           insisted_spans=insisted_spans)
    closer = closer_words(moment, transcript)
    body_text = words_text(body)
    closer_text = words_text(closer)
    # The range as PLACED: snapped like the build snaps it, so the
    # recorded range names what the reel plays rather than what the
    # stored file declares.
    span = closer_range(_snapped(moment, transcript))
    opens_with = words_text(closer[:12])
    cut_from: Optional[str] = None
    if span is not None:
        enclosing = _segment_at(transcript, float(span[0]) + 1e-3)
        if enclosing is not None:
            try:
                begins = float(enclosing["timeline_start"]) >= float(
                    span[0]) - 0.05
            except (KeyError, TypeError, ValueError):
                begins = False
            if not begins:
                cut_from = (enclosing.get("text") or "").strip() or None
    shared_with: List[int] = []
    reuse_count = 1
    if group is not None:
        number = int(getattr(moment, "number", 0) or 0)
        shared_with = [reel for reel in group.get("reels", [])
                       if reel != number]
        reuse_count = int(group.get("count", len(shared_with) + 1))
    tail = body[-50:] if len(body) > 50 else body
    return {
        "reel": int(getattr(moment, "number", 0) or 0),
        "name": str(getattr(moment, "timeline_name", "")
                    or getattr(moment, "name", "") or ""),
        "body_text": body_text,
        "body_speakers": _speakers_in(body),
        "body_word_count": len(body),
        "body_tail": words_text(tail),
        "closer_text": closer_text,
        "closer_speakers": _speakers_in(closer),
        "closer_word_count": len(closer),
        "closer_range": ([round(float(span[0]), 2), round(float(span[1]), 2)]
                         if span is not None else None),
        "closer_opens_with": opens_with,
        # The master sentence the closer's start cuts into, when it
        # cuts one: a closer opening on "So" mid-sentence is a fact
        # about the seam the model can see here rather than infer.
        "closer_cut_from_sentence": cut_from,
        "reuse_count": reuse_count,
        "shared_with": shared_with,
        "content_hash": _content_hash(body_text, closer_text),
    }


def _content_hash(body_text: str, closer_text: str) -> str:
    """A short digest of the words a judgement was made over.

    The survey records this beside each verdict; the build compares it
    against the live words and reports a verdict whose words moved as
    STALE rather than as a verdict about the current reel. Twelve hex
    characters: a judgement key, not a security boundary.
    """
    digest = hashlib.sha256(
        f"{body_text}\n---\n{closer_text}".encode("utf-8")).hexdigest()
    return digest[:12]


def render_fit_prompt(context: dict) -> str:
    """The question for the model, given one reel's fit context.

    Asks for a verdict with a reason, and says what is NOT being asked:
    reuse is not a defect (the captain declined per-reel endings), so a
    closer shared with other reels is never itself a reason to flag.
    """
    lines = [
        "You are judging whether one reel's ending follows from what "
        "that reel just said. Answer with a verdict and a reason.",
        "",
        f"REEL {context.get('reel')}",
        "",
        "What the reel says before its ending (its own kept words, in "
        "the order a viewer hears them):",
        (context.get("body_text") or "(no body words measured)")
        + "",
        "",
        "The ending the reel closes on:",
        (context.get("closer_text") or "(no closer words measured)") + "",
        "",
        "Facts, not suspicions:",
        f"- the ending is shared with {context.get('reuse_count', 1)} "
        "reel(s) in total. Reuse is NOT a defect - the same ending clip "
        "may close any number of reels, and sharing one is never itself "
        "a reason to flag. Judge only whether THIS ending follows from "
        "what THIS reel just said.",
    ]
    cut_from = context.get("closer_cut_from_sentence")
    if cut_from:
        lines.append(
            "- the ending's first word cuts into a longer master "
            f"sentence: {cut_from!r}. An opening shaped like a "
            "continuation ('So ...', 'And ...') with no antecedent in "
            "the reel above is worth saying so about.")
    lines += [
        "",
        "Reply as JSON with exactly two keys:",
        '  {"verdict": "follows" | "misfit", '
        '"reason": "one or two sentences citing the words that decide it"}',
        'Say "follows" when the ending answers, extends or lands the '
        "reel's own point, and \"misfit\" when it starts a thought the "
        "reel never set up or answers a question the reel never asked.",
    ]
    return "\n".join(lines)


def read_fit_answer(data) -> dict:
    """Read a model's fit answer as `{verdict, reason}`.

    Accepts `follows` and `misfit` with a non-empty reason. Anything
    else - a missing verdict, an unknown word, an empty reason, a
    non-dict answer - reads as UNJUDGED with the reason it could not
    be read, never as a low score and never coerced to a verdict.
    Numeric fields beside the verdict (`score`, `confidence`,
    `rating`, ...) are DROPPED unread: there is no magnitude in this
    judgement, and reading one would invent a scale the captain
    refused.
    """
    if not isinstance(data, dict):
        return {"verdict": UNJUDGED,
                "reason": "no answer was recorded for this reel"}
    verdict = data.get("verdict")
    reason = data.get("reason")
    if verdict not in FIT_VERDICTS:
        return {"verdict": UNJUDGED,
                "reason": ("the answer carries no usable verdict "
                           f"({verdict!r} is not follows|misfit)")}
    if not isinstance(reason, str) or not reason.strip():
        return {"verdict": UNJUDGED,
                "reason": (f"the {verdict} verdict carries no reason, "
                           "and a verdict without one cannot be shown "
                           "to the captain")}
    return {"verdict": verdict, "reason": reason.strip()}


def fit_path(project_folder: str) -> Path:
    """Where the survey sidecar lives: review-side, beside proposals."""
    return Path(project_folder) / "pipeline_output" / "review" / SIDECAR_NAME


def read_fit_verdicts(project_folder: str) -> dict:
    """The recorded fit verdicts, keyed by reel number as a string.

    Missing or unreadable is `{}` - "not yet judged", never an error
    at build time. An instrument must never fail the build it
    instruments.
    """
    try:
        with open(fit_path(project_folder), "r",
                  encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    return verdicts if isinstance(verdicts, dict) else {}


def write_fit_verdicts(project_folder: str, records: dict) -> Path:
    """File the survey's verdicts beside the proposals they judge.

    `records` maps reel number to `{verdict, reason, judged_by,
    judged_at, closer_range, content_hash, reuse_count, shared_with}`.
    Returns the sidecar path.
    """
    path = fit_path(project_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"verdicts": records,
               "sidecar": SIDECAR_NAME,
               "why": ("per-reel closer-fit judgements for the 2026-09-19 "
                       "ruling: reuse is not a defect, topical misfit is. "
                       "Recorded by the survey before anything is re-cut.")}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return path


def verdict_lines(number: int, context: dict,
                  verdict: Optional[dict]) -> List[str]:
    """This reel's build-time fit report, as printed lines.

    Three shapes: a recorded verdict (with staleness when the words
    moved since), an explicit not-yet-judged, and no-closer silence -
    a reel with no ending has no fit to report and says nothing.
    """
    if context.get("closer_range") is None:
        return []
    shared = context.get("shared_with") or []
    if shared:
        head = (f"  closer shared with {len(shared)} other reel(s): "
                f"{', '.join(f'reel {reel}' for reel in shared)} "
                "- reuse is not a defect")
    else:
        head = "  closer closes this reel only"
    lines = [head]
    if not verdict:
        lines.append("  closer fit: not yet judged - run the closer-fit "
                     "survey before the re-cut wave")
        return lines
    live_hash = context.get("content_hash")
    if verdict.get("content_hash") and verdict.get("content_hash") \
            != live_hash:
        lines.append(f"  closer fit: STALE {verdict.get('verdict')} "
                     f"({verdict.get('reason', '')} - judged over "
                     "different words; re-survey this reel)")
        return lines
    if verdict.get("verdict") == "misfit":
        lines.append(f"  closer MISFIT: {verdict.get('reason', '')}")
    elif verdict.get("verdict") == "follows":
        lines.append(f"  closer follows: {verdict.get('reason', '')}")
    else:
        lines.append(f"  closer fit: unjudged - {verdict.get('reason', '')}")
    return lines


def summarize(verdicts: dict) -> List[str]:
    """The survey's deliverable: which reels misfit, as lines + count.

    `verdicts` maps reel number to its recorded `{verdict, reason}`.
    Misjudged and unjudged reels are named beside the misfits, so the
    count reads as measured rather than as complete when it is not.
    """
    misfits, follows, unjudged = [], [], []
    for key in sorted(verdicts, key=lambda k: int(k)):
        verdict = (verdicts[key] or {}).get("verdict")
        if verdict == "misfit":
            misfits.append(int(key))
        elif verdict == "follows":
            follows.append(int(key))
        else:
            unjudged.append(int(key))
    judged = len(misfits) + len(follows)
    lines = [f"closer fit: {len(misfits)} of {judged} judged reels misfit"
             + (f": reels {', '.join(map(str, misfits))}" if misfits
                else " - every judged closer follows its reel")]
    for reel in misfits:
        lines.append(f"  reel {reel}: {(verdicts[str(reel)] or {}).get('reason', '')}")
    if unjudged:
        lines.append(f"  unjudged: reels {', '.join(map(str, unjudged))} "
                     "- surveyed but without a usable verdict, or not surveyed")
    return lines


def _load_survey_inputs(project_folder: str):
    """The approved moments and the transcript the survey judges from.

    Reads the live proposals file - what the captain approved, not a
    pipeline snapshot - and the timeline transcript the build reads.
    Only APPROVED moments with a closer are surveyed: a rejected reel
    is going nowhere, and a reel with no ending has no fit to judge.
    Also loads the captain's recorded keep exclusions and insistences,
    so the kept words are what the build plays rather than what a
    raw take-cut pass would leave.
    """
    import json as _json

    from library.tools import transcript_corrections as _tc
    from library.tools.reel_proposal import proposal_path, read_proposal

    moments = read_proposal(str(proposal_path(project_folder)))
    surveyed = [moment for moment in moments
                if str(getattr(getattr(moment, "approval", ""),
                               "value", getattr(moment, "approval", "")))
                == "approved"]
    surveyed = [moment for moment in surveyed
                if closer_range(moment) is not None]
    transcript_path = (Path(project_folder) / "pipeline_output" / "scratch"
                       / "timeline_transcript" / "transcript.json")
    with open(transcript_path, "r", encoding="utf-8") as handle:
        transcript = _json.load(handle)
    return (surveyed, transcript,
            _tc.keep_exclusions(project_folder),
            _tc.keep_insistences(project_folder))


def ranges_inputs(moment, transcript: dict, keep_exclusions,
                  keep_insistences) -> tuple:
    """This moment's recorded cuts + insistences, without the chatter.

    ONE spelling: `reel_build.moment_cuts_and_insistences` owns the
    computation, and this calls it. That function prints what it
    honours for the build log; the survey is not the run that honours
    them, so stdout is held while it runs rather than interleaved
    into prompt files or JSON.
    """
    import contextlib
    import io

    from library.tools.reel_build import moment_cuts_and_insistences

    with contextlib.redirect_stdout(io.StringIO()):
        return moment_cuts_and_insistences(moment, transcript,
                                           keep_exclusions,
                                           keep_insistences)


def survey_contexts(project_folder: str) -> List[dict]:
    """One fit context per surveyed reel, reel-number order."""
    surveyed, transcript, keep_exclusions, keep_insistences = \
        _load_survey_inputs(project_folder)
    groups = reuse_groups(surveyed)
    contexts = []
    for moment in sorted(surveyed, key=moment_number):
        cuts, insisted = ranges_inputs(moment, transcript,
                                       keep_exclusions, keep_insistences)
        contexts.append(fit_context(moment, transcript,
                                    group_for(moment, groups),
                                    extra_cuts=cuts,
                                    insisted_spans=insisted))
    return contexts


def moment_number(moment) -> int:
    """A moment's reel number as an int, for sorting."""
    try:
        return int(getattr(moment, "number", 0) or 0)
    except (TypeError, ValueError):
        return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`survey` renders prompts; `record` files answers; `report` prints.

    - `survey --project <path> [--prompts-dir <dir>]`: one prompt file
      per reel (or contexts as JSON on stdout without the dir). The
      model answers each with `{"verdict": ..., "reason": ...}`.
    - `record --project <path> --answers <json> [--judged-by <name>]`:
      reads `{reel: {verdict, reason}}`, validates each through
      `read_fit_answer`, and files the sidecar the build reports.
    - `report --project <path>`: the misfit list with its count, from
      the sidecar.
    """
    import argparse
    import datetime

    parser = argparse.ArgumentParser(prog="closer_fit")
    sub = parser.add_subparsers(dest="command", required=True)
    survey = sub.add_parser("survey", help="render one prompt per reel")
    survey.add_argument("--project", required=True)
    survey.add_argument("--prompts-dir", default="")
    record = sub.add_parser("record", help="file model answers")
    record.add_argument("--project", required=True)
    record.add_argument("--answers", required=True)
    record.add_argument("--judged-by", default="")
    report = sub.add_parser("report", help="print the misfit list+count")
    report.add_argument("--project", required=True)
    args = parser.parse_args(argv)

    if args.command == "survey":
        contexts = survey_contexts(args.project)
        if args.prompts_dir:
            out_dir = Path(args.prompts_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            for context in contexts:
                path = out_dir / f"reel_{context['reel']:02d}_closer_fit.txt"
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(render_fit_prompt(context))
            print(f"wrote {len(contexts)} prompt(s) to {out_dir}")
        else:
            print(json.dumps(contexts, indent=2))
        return 0
    if args.command == "record":
        with open(args.answers, "r", encoding="utf-8") as handle:
            answers = json.load(handle)
        surveyed, transcript, keep_exclusions, keep_insistences = \
            _load_survey_inputs(args.project)
        groups = reuse_groups(surveyed)
        by_number = {moment_number(m): m for m in surveyed}
        stamped = (datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"))
        records = {}
        for key, answer in (answers or {}).items():
            try:
                number = int(key)
            except (TypeError, ValueError):
                continue
            moment = by_number.get(number)
            if moment is None:
                continue
            cuts, insisted = ranges_inputs(moment, transcript,
                                           keep_exclusions,
                                           keep_insistences)
            context = fit_context(moment, transcript,
                                  group_for(moment, groups),
                                  extra_cuts=cuts,
                                  insisted_spans=insisted)
            read = read_fit_answer(answer)
            records[str(number)] = {
                **read,
                "judged_by": args.judged_by or "model",
                "judged_at": stamped,
                "closer_range": context.get("closer_range"),
                "content_hash": context.get("content_hash"),
                "reuse_count": context.get("reuse_count"),
                "shared_with": context.get("shared_with"),
            }
        path = write_fit_verdicts(args.project, records)
        print(f"filed {len(records)} verdict(s) to {path}")
        return 0
    if args.command == "report":
        for line in summarize(read_fit_verdicts(args.project)):
            print(line)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
