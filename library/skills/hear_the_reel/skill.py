"""hear_the_reel: what a rendered reel SAYS, against what it was planned to say.

The captain's ask, verbatim: *"we could also optionally turn it into a
skill that or tool of some kind that the LLM can call on demand to be
able to get the info"*. This is that surface. A model working on a reel
asks it what the render actually sounds like against the plan and gets
an answer back; nothing has to have run it first, and nothing has to run
it at all.

Deterministic, and REPORTS. The whole comparison -
`library/tools/reel_hearing.py` - is transcribe, align, diff, measure.
No model is called at any point and none is needed: on the known-answer
case it found six uncaptioned words, a caption card a full second late
and the drift run that explains both, on its own. The MODEL's job is the
judgement the answer is handed to: which of these divergences matter.

So this is a `report` skill, not a `gate`: `reel_hearing.GATES` is False,
no build reads its record and a `passed: false` row here fails nothing.
That is the conservative direction on purpose - see the module docstring.

Shell invocation:

    python3 -m library.skills.hear_the_reel.skill \
        --project-folder /path/to/project --step-id <your-step-id> \
        [--reel 26 | --video /path/to/reel.mp4] \
        [--timeline /path/to/reel.timeline.json] [--announce]

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/hear_the_reel.json` and
the full record beside the render as `<render>.hearing.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

SKILL_NAME = "hear_the_reel"


def _load(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def locate(project_folder: str,
           reel: Optional[Any] = None,
           video_path: str = "",
           timeline_path: str = "") -> Dict[str, str]:
    """The render and the plan that belong to each other.

    The pairing is read off `deliver-reel`'s own sidecar, which is the
    only evidence that an mp4 in `exports/` is a reel's render, and off
    the serialized timeline `build_reels` wrote under
    `pipeline_output/review/`. Neither is guessed from a filename: an
    unpaired render is refused by name rather than heard against
    whatever plan sorts next to it.
    """
    from library.tools import reel_hearing

    name = ""
    if not video_path:
        row = _delivered(project_folder, reel)
        video_path = row["video_path"]
        name = row["timeline_name"]
    video_path = os.path.abspath(video_path)
    if not os.path.isfile(video_path):
        raise reel_hearing.NothingWasHeard(
            f"there is no rendered file at {video_path}. Hearing a reel "
            f"reads the file `deliver-reel` already wrote; it renders "
            f"nothing.")

    if not timeline_path:
        if not name:
            sidecar = os.path.splitext(video_path)[0] + ".deliver.json"
            if os.path.exists(sidecar):
                name = str(_load(sidecar).get("timeline_name") or "")
        if not name:
            raise reel_hearing.NothingWasHeard(
                f"nothing says which timeline {os.path.basename(video_path)} "
                f"was rendered from - there is no .deliver.json beside it. "
                f"Name the plan with --timeline.")
        timeline_path = _timeline_for(project_folder, name)
    timeline_path = os.path.abspath(timeline_path)
    if not os.path.isfile(timeline_path):
        raise reel_hearing.NothingWasHeard(
            f"there is no serialized timeline at {timeline_path}. "
            f"`build_reels` writes one per reel under "
            f"pipeline_output/review/.")
    return {"video_path": video_path, "timeline_path": timeline_path,
            "timeline_name": name}


def _delivered(project_folder: str, reel: Optional[Any]) -> Dict[str, Any]:
    """Which delivered render to hear: a number, a timeline name, or the
    most recent.

    `render_watch.delivered_reels` is the roster - a delivery that
    happened is the only evidence an mp4 in `exports/` is a reel - and
    a number goes through `delivered_reel` so the two verbs answer a
    number identically. The other two forms are this verb's own, so
    `--all` and "just hear the last one" do not need a number the
    caller has to go and look up.
    """
    from library.tools import render_watch

    if reel is None:
        rows = [r for r in render_watch.delivered_reels(project_folder)
                if os.path.isfile(r["video_path"])]
        if not rows:
            raise render_watch.NotDelivered(
                "this project has no delivered reel to hear",
                "a reel becomes a file when you run `deliver-reel`; "
                "hearing one renders nothing",
                "run `ren deliver <project> <reel>` first, then hear it")
        return rows[0]
    text = str(reel).strip()
    if text.isdigit():
        return render_watch.delivered_reel(project_folder, int(text))
    named = [r for r in render_watch.delivered_reels(project_folder)
             if r["timeline_name"] == text]
    if not named:
        known = sorted({r["timeline_name"]
                        for r in render_watch.delivered_reels(project_folder)})
        raise render_watch.NotDelivered(
            f"no delivered reel is named {text!r}",
            f"delivered so far: {known or '(none)'}",
            "deliver the reel first (`ren deliver <project> <reel>`), "
            "or hear one of the delivered ones")
    return named[0]


def _timeline_for(project_folder: str, timeline_name: str) -> str:
    """The serialized plan for one timeline, found by what it NAMES.

    By the name the document RECORDS, never by its filename: a reel's
    file is slugged and its variants slug alike, which is why
    `reel_divergence.snapshots_from_review` keys the same directory the
    same way.
    """
    from library.tools import reel_hearing

    review = os.path.join(project_folder, "pipeline_output", "review")
    if os.path.isdir(review):
        for entry in sorted(os.listdir(review)):
            if not entry.endswith(".timeline.json"):
                continue
            path = os.path.join(review, entry)
            try:
                doc = _load(path)
            except (OSError, ValueError):
                continue
            recorded = str((doc.get("metadata") or {}).get("name") or "")
            if recorded == timeline_name:
                return path
    raise reel_hearing.NothingWasHeard(
        f"no serialized timeline under {review} names {timeline_name!r}, "
        f"so there is no plan to hear this render against. `build_reels` "
        f"writes one per reel.")


def run(project_folder: str,
        step_id: str,
        reel: Optional[Any] = None,
        video_path: str = "",
        timeline_path: str = "",
        announce: bool = False,
        dials: Optional[Dict[str, Any]] = None,
        decline: Optional[List[str]] = None) -> Dict[str, Any]:
    """Hear one reel. Returns an OBSERVATION; gates nothing.

    `available: false` with the reason when the render, the plan or the
    transcriber is missing - an unheard reel is never a clean one, and
    saying "nothing diverged" because nobody listened is the
    gate-that-cannot-fail (AGENTS.md 10.4).

    `dials` and `decline` are this ONE run's answers, over whatever the
    project declares; `library/tools/hearing_settings.py` owns which of
    them is a real dial and which is a measured property that records
    having been moved. A malformed one refuses the run by name rather
    than being quietly ignored.
    """
    from library.tools import (
        hearing_settings,
        heard_speech,
        pipeline_skills,
        qa_findings,
        reel_hearing,
        render_watch,
        timeline_transcript,
    )

    started = time.time()

    def refuse(reason: str) -> Dict[str, Any]:
        observation = {"skill": SKILL_NAME, "available": False,
                       "gates": reel_hearing.GATES, "reason": reason,
                       "findings": [],
                       "elapsed_seconds": round(time.time() - started, 2)}
        observation["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, observation)
        return observation

    try:
        settings = hearing_settings.resolve(project_folder, dials, decline)
    except hearing_settings.MalformedHearingDeclaration as bad:
        return refuse(str(bad))

    try:
        where = locate(project_folder, reel, video_path, timeline_path)
    except (reel_hearing.NothingWasHeard,
            render_watch.NotDelivered) as absent:
        return refuse(str(absent))

    transcript_file = timeline_transcript.transcript_path(project_folder)
    if not os.path.exists(transcript_file):
        return refuse(
            f"this project has no timeline transcript at {transcript_file}, "
            f"so there is nothing to say what the plan says. It is written "
            f"by library/tools/timeline_transcript.py.")

    installed, detail = heard_speech.available()
    if not installed:
        return refuse(detail)

    try:
        spoken = heard_speech.transcribe(where["video_path"])
    except heard_speech.TranscriberUnavailable as refused:
        return refuse(str(refused))

    try:
        hearing = reel_hearing.hear(
            _load(where["timeline_path"]), _load(transcript_file), spoken,
            project_folder=project_folder, settings=settings,
            timeline_path=where["timeline_path"],
            video_path=where["video_path"])
    except (reel_hearing.NothingWasHeard, ValueError) as refused:
        return refuse(str(refused))

    record = reel_hearing.write_record(hearing)
    read = reel_hearing.read_findings(hearing)
    observation: Dict[str, Any] = {
        "skill": SKILL_NAME,
        "available": True,
        "gates": reel_hearing.GATES,
        "timeline_name": hearing.timeline_name,
        "video_path": hearing.video_path,
        "timeline_path": hearing.timeline_path,
        "record": record,
        "heard_by": hearing.engine,
        "counts": read.counts(),
        "findings": qa_findings.findings_for_review(read)["findings"],
        "readings": qa_findings.findings_for_review(read)["readings"],
        "divergences": hearing.divergences,
        "drift": hearing.drift,
        "drift_runs": hearing.runs,
        "caption_coverage": hearing.coverage,
        "unfitted_transcript_rows": hearing.unfitted_transcript_rows,
        "transcript_fit": hearing.transcript_fit,
        "settings": hearing.settings.as_dict(),
        "transcriber_anomalies": hearing.anomalies,
        "skipped": hearing.skipped,
        "summary": reel_hearing.summary_lines(hearing),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    if announce:
        observation["announced"] = [
            {"hook": f.hook, "outcome": f.outcome, "detail": f.detail}
            for f in reel_hearing.announce(project_folder, hearing)]
    observation["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, observation)
    return observation


def add_dial_arguments(parser) -> None:
    """This run's own answers, registered FROM the dial enumeration.

    Registered rather than listed, so a dial added to
    `hearing_settings.DIALS` reaches the command line without anyone
    remembering to add it - the shape `run_scope` establishes for the
    pipeline's own flags (AGENTS.md section 3).
    """
    from library.tools import hearing_settings, reel_hearing

    group = parser.add_argument_group(
        "dials", "This run's answers, over the project's own declaration. "
                 "A MEASURED dial moved here is RECORDED as moved and the "
                 "record says the hearing is not comparable to one that "
                 "used the measurement.")
    for dial in hearing_settings.DIALS:
        group.add_argument(
            f"--{dial.name.replace('_', '-')}", dest=dial.name,
            type=type(dial.default), default=None,
            help=f"[{dial.kind}] {dial.what} Default {dial.default}.")
    group.add_argument(
        "--decline-check", action="append", default=[],
        choices=list(reel_hearing.METRICS),
        help="Do not make this check. Repeatable. It is recorded as "
             "SKIPPED with the reason, never left out.")


def dials_from(args) -> Dict[str, Any]:
    """The dial values one parsed command line asks for."""
    from library.tools import hearing_settings

    return {dial.name: getattr(args, dial.name, None)
            for dial in hearing_settings.DIALS}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Hear a rendered reel against its plan "
                    "(skill: hear_the_reel). Reports; never gates.")
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--reel", default=None,
                        help="Which delivered reel, by its number or "
                             "timeline name. Omit for the most recent.")
    parser.add_argument("--video", default="",
                        help="A rendered file, instead of a delivered reel.")
    parser.add_argument("--timeline", default="",
                        help="The serialized plan, when it cannot be found "
                             "from the render's own sidecar.")
    parser.add_argument("--announce", action="store_true",
                        help="Raise each finding on the project's hook "
                             "layer, which is how one reaches the review "
                             "channel. Nothing fires unless the project "
                             "declares a hook.")
    add_dial_arguments(parser)
    args = parser.parse_args(argv)

    observation = run(args.project_folder, args.step_id, reel=args.reel,
                      video_path=args.video, timeline_path=args.timeline,
                      announce=args.announce, dials=dials_from(args),
                      decline=args.decline_check)
    print(json.dumps(observation, indent=2, default=str))
    # Exit zero whatever it found: a report is not a failure. The caller
    # reads the findings.
    return 0 if observation["available"] else 1


if __name__ == "__main__":
    sys.exit(main())
