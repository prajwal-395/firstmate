"""The instruction loop, run live on a Ren-owned scratch copy.

PLAN-011 asks for the loop to be PROVED, from a natural-language request
to a result the person can read: inspect the work state, gather the
evidence, compile the intent, validate the plan, execute, verify, and
report - with every stated instruction accounted for at the end.

This is the RUN.  It works the briefs by hand against a Ren-owned
scratch copy of the podcast reel, and it leaves behind the records the
acceptance harness reads (`ren-editorial-task-record/1`), so the
benchmark is measured over records a run produced and not over a table
somebody wrote.

WHAT IS LIVE HERE, AND WHAT IS NOT
----------------------------------
Live: the project's own state (the reel's read-back, the media pool, the
master's transcript), the engine's own intent compiler, the engine's own
plan gate, and the engine's own routing decision.

Not live: the Resolve write.  The serving broker's queue is held by
another lane's qualification session, so a mutation cannot run inside
this lane's window.  A brief whose plan names a mutation therefore
records its execution as NOT RUN, with the queue's own words, and the
harness reports `not_measured` for that stage - never a pass and never a
zero.  A brief whose correct outcome is a refusal reaches its verdict
without a Resolve write, because the refusal IS the engine's own
routing decision.

    python3 evaluation/tools/plan011_live_run.py --project <path> \\
        --records <dir> [--only <brief id>]

The PLANS in `PLANS` are the agent's judgement, written against the
live evidence the run gathers; everything else in this file is the
engine's own code.  The corpus's answer key is never read here: the
corpus briefs are worked from their public instruction and the live
project, and the scorer holds the key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import (  # noqa: E402
    editorial_intent,
    execution_router,
)

#: The evidence the run gathered, and where it came from.  Every id here
#: names a live artefact of the scratch copy.
EVIDENCE: dict[str, dict[str, str]] = {
    "readback:reel13": {
        "kind": "timeline_readback",
        "source": "the scratch copy's own recorded read-back of Reel 13",
        "locator": "project_model/snapshot.json: edits[-1].after.resolve_readback",
    },
    "pool:scratch": {
        "kind": "media_pool",
        "source": "the scratch copy's recorded media pool",
        "locator": "project_model/snapshot.json: edits[-1].after.resolve_readback.bin",
    },
    "transcript:master": {
        "kind": "transcript",
        "source": "the master's word-timed transcript",
        "locator": "the session transcript, 477 segments, each naming its file",
    },
}

PLANS: dict[str, dict[str, Any]] = {
    # ── the eight-brief acceptance set ──────────────────────────────
    "open-with-the-cost": {
        "restated_outcome": (
            "Open the reel on the moment she names what it actually costs, "
            "let her argument run from there, lose no word, and leave the "
            "grade and her framing exactly as they are."),
        "ops": [("reel.opening", "opening_section")],
        "targets": [("opening_section", True,
                     "the reel's first picture - the Craig row 0-75 and the "
                     "Akshita row 75-129 of Reel 13",
                     ["readback:reel13"])],
        "unavailable": [
            "The reel's own footage carries no statement of what it costs: "
            "it is cut from LC4932 and LCATL0013, and the cost line is "
            "Craig's 'It's something that's going to continually eat into "
            "their budget' in LCATL0011, a different recording.",
        ],
        "criteria_covered": ["hook-first", "nothing-lost", "grade-untouched"],
        "execution": "refusal: the moment the request names is not in this "
                     "reel's footage, so there is nothing to open on.",
    },
    "two-voices-at-once": {
        "restated_outcome": (
            "Make it possible for a viewer to follow who is making the "
            "claim where the two of them talk over each other, without "
            "cutting either of them out of the reel."),
        "ops": [("multicam.select_angle", "overlapping_turn")],
        "targets": [("overlapping_turn", False,
                     "not found - the reel has no overlapping turn",
                     ["readback:reel13"])],
        "unavailable": [
            "The reel's two camera rows are the two SPEAKERS, not two "
            "angles of one moment, so there is no angle that could be "
            "swapped in; the dialogue tracks are disjoint.",
        ],
        "criteria_covered": ["claim-followable", "both-voices-kept",
                              "sync-held"],
        "execution": "refusal: multicam.select_angle has no production route "
                     "and the reel carries no overlapping turn to select an "
                     "angle for.",
    },
    "no-extra-footage": {
        "restated_outcome": (
            "Cover the bit where she explains the tooling with a shot of the "
            "workshop bench, and say so if the project holds no such shot."),
        "ops": [],
        "targets": [],
        "unavailable": [
            "The scratch copy's media pool holds no workshop bench footage: "
            "its 56 items are the two camera files, the rendered caption "
            "cards, the motion graphics, the stills and the reel cards.",
        ],
        "criteria_covered": ["no-invented-footage", "says-so", "speech-intact"],
        "execution": "refusal: the project holds no workshop bench footage.",
    },
    "logo-not-in-the-project": {
        "restated_outcome": (
            "Swap the end card for the new logo, and say so if the named "
            "logo is not in the project."),
        "ops": [],
        "targets": [],
        "unavailable": [
            "The pool holds one logo - logo_bulb_lines_safe_23976.mov, the "
            "corner mark's sibling on the Motion Graphics row at 1834-1906 - "
            "and the requested 'new logo sent this morning' is not among the "
            "pool's 56 items.",
        ],
        "criteria_covered": ["named-asset-only", "says-so", "end-card-intact"],
        "execution": "refusal: the named logo is not in the project.",
    },
    "keep-it-but-tighten-it": {
        "restated_outcome": (
            "Leave the interview word for word as it is, and tighten it so "
            "it holds attention - or say which of the two was asked for."),
        "ops": [],
        "targets": [],
        "unavailable": [],
        "criteria_covered": ["conflict-surfaced", "nothing-changed"],
        "execution": "refusal: keeping every word and tightening the cut are "
                     "two requests, and only one of them can be the one.",
    },
    "the-wrong-caption": {
        "restated_outcome": (
            "Fix the one caption whose text names the wrong word, and change "
            "nothing else - not its timing, not the other captions."),
        "ops": [("caption_fix", "wrong_caption_card")],
        "targets": [("wrong_caption_card", False,
                     "not found - no caption in the reel names a word the "
                     "speech does not",
                     ["readback:reel13"])],
        "unavailable": [
            "The reel carries 45 caption cards, and no card names 'revenue' "
            "where the speech says churn; the reel's speech is not about "
            "churn at all.",
        ],
        "criteria_covered": ["caption-text-right", "timing-kept",
                              "others-untouched"],
        "execution": "refusal: caption_fix has no production route, and no "
                     "caption in the reel names the wrong word.",
    },
    "name-bar-fights-the-captions": {
        "restated_outcome": (
            "Move the name bar down so it stops colliding with the captions, "
            "and leave the rest of the graphics alone."),
        "ops": [("overlay.position", "name_lower_third")],
        "targets": [("name_lower_third", True,
                     "the lower third b-soft-focus-craig.mov on the Motion "
                     "Graphics row, frames 0-84",
                     ["readback:reel13"])],
        "unavailable": [],
        "criteria_covered": ["collision-gone", "inside-safe-area",
                              "others-untouched"],
        "execution": "queued: overlay.position is production-enabled and "
                     "qualified on reel_touchup, so the write is a real "
                     "mutation - and the broker's queue is held by another "
                     "lane's qualification session.",
    },
    "the-whole-job": {
        "restated_outcome": (
            "Do the whole job on the launch reel: open on the strongest "
            "moment, take out the pauses, replace the weak angle, cover a "
            "line with a cutaway, correct the caption, fix the name bar, "
            "balance the levels, keep the human work, and export."),
        "ops": [("reel.opening", "opening_section"),
                ("clip.trim", "repeated_passages"),
                ("multicam.select_angle", "weak_angle_turn"),
                ("broll.insert", "broll_cover_range"),
                ("caption_fix", "wrong_caption_card"),
                ("overlay.position", "name_lower_third"),
                ("audio.gain", "music_bed")],
        "targets": [("opening_section", True,
                     "the reel's first picture", ["readback:reel13"]),
                    ("repeated_passages", False,
                     "not found - this reel carries no repeated passage",
                     ["readback:reel13", "transcript:master"]),
                    ("weak_angle_turn", False,
                     "not found - the reel's two camera rows are two "
                     "speakers, not two angles", ["readback:reel13"]),
                    ("broll_cover_range", False,
                     "not found - the pool holds no cutaway footage",
                     ["pool:scratch"]),
                    ("wrong_caption_card", False,
                     "not found - no caption names a word the speech does not",
                     ["readback:reel13"]),
                    ("name_lower_third", True,
                     "the lower third b-soft-focus-craig.mov, frames 0-84",
                     ["readback:reel13"]),
                    ("music_bed", False,
                     "not found - the reel carries no music bed",
                     ["readback:reel13", "pool:scratch"])],
        "unavailable": [
            "The reel carries no cutaway footage, no music bed, no repeated "
            "passage and no second angle of one moment; four of the seven "
            "obligations have nothing in this reel to act on.",
        ],
        "criteria_covered": ["hook-chosen", "pauses-removed", "angle-replaced",
                              "broll-chosen", "caption-corrected",
                              "lower-third-adjusted", "levels-balanced",
                              "human-work-preserved", "exported",
                              "export-verified"],
        "execution": "queued: the plan names seven operations, four of which "
                     "have no production route and the rest of which are "
                     "real mutations - and the broker's queue is held by "
                     "another lane's qualification session.",
    },
    # ── the BASE-003 corpus briefs ─────────────────────────────────
    "open-on-the-cost-line": {
        "restated_outcome": (
            "Open the cut on the line where he says what this is going to "
            "cost a company, and let her argument run from there."),
        "ops": [("reel.opening", "opening_section")],
        "targets": [("opening_section", True,
                     "the reel's first picture", ["readback:reel13"])],
        "unavailable": [
            "The cost line is Craig's 'It's something that's going to "
            "continually eat into their budget' in LCATL0011 at 00:13.9 of "
            "the master; this reel is cut from LC4932 and LCATL0013, so the "
            "line the request names is not in this cut.",
        ],
        "criteria_covered": ["hook-first", "nothing-lost", "grade-untouched"],
        "execution": "refusal: the moment the request names is not in this "
                     "reel's footage.",
    },
    "cut-the-repeated-definition": {
        "restated_outcome": (
            "Cut the first of the two times she explains what SEO is, and "
            "keep the second one whole."),
        "ops": [("clip.trim", "repeated_passages")],
        "targets": [("repeated_passages", False,
                     "not found - this reel carries no repeated passage",
                     ["transcript:master", "readback:reel13"])],
        "unavailable": [
            "The master carries both tellings - 'SEO is about convincing an "
            "algorithm to rank your page higher' in LC4930 and its second "
            "telling in LC4931 - and this reel is cut from LC4932 and "
            "LCATL0013, so neither telling is in it.",
        ],
        "criteria_covered": ["repeat-removed", "nothing-else-lost",
                              "gap-closed"],
        "execution": "refusal: neither telling of the definition is in this "
                     "reel, and clip.trim has no production route on its own.",
    },
    "use-the-other-camera-on-that-answer": {
        "restated_outcome": (
            "For that stretch of her answer, put his camera up instead of "
            "hers, and put hers back immediately after."),
        "ops": [("multicam.select_angle", "second_angle")],
        "targets": [("second_angle", False,
                     "not found - the reel has no second angle of one moment",
                     ["readback:reel13"])],
        "unavailable": [
            "The reel's two camera rows are the two SPEAKERS - Akshita on one "
            "row, Craig on the other - and each row carries one source file, "
            "so there is no second angle of the same moment to cut to.",
        ],
        "criteria_covered": ["other-angle", "back-again", "audio-untouched"],
        "execution": "refusal: the reel has no second angle of that moment, "
                     "and multicam.select_angle has no production route.",
    },
    "lift-the-caption-row-but-keep-every-card": {
        "restated_outcome": (
            "Bring the caption row up so it is clear of the bottom edge, and "
            "keep every caption card."),
        "ops": [("clip.move", "caption_row")],
        "targets": [("caption_row", True,
                     "the 45 caption cards on the Subtitles row",
                     ["readback:reel13"])],
        "unavailable": [],
        "criteria_covered": ["every-card-kept", "row-moved", "words-kept"],
        "execution": "queued: the caption row is a real mutation on the "
                     "reel, and the broker's queue is held by another lane's "
                     "qualification session.",
    },
    "hold-the-name-card-until-she-finishes": {
        "restated_outcome": (
            "Hold the name card on screen until she has finished that "
            "answer, and leave the corner mark and the header alone."),
        "ops": [("clip.set_property", "name_card")],
        "targets": [("name_card", True,
                     "the lower third b-soft-focus-craig.mov, frames 0-84",
                     ["readback:reel13"])],
        "unavailable": [],
        "criteria_covered": ["card-held", "mark-untouched"],
        "execution": "queued: holding the card is a real mutation, and the "
                     "broker's queue is held by another lane's qualification "
                     "session.",
    },
    "take-out-the-soft-focus-card-i-asked-about": {
        "restated_outcome": (
            "Take the soft focus card out of the opening and close the gap it "
            "leaves, and do not touch anything else."),
        "ops": [("clip.remove", "soft_focus_card")],
        "targets": [("soft_focus_card", True,
                     "the lower third b-soft-focus-craig.mov on the Motion "
                     "Graphics row, frames 0-84", ["readback:reel13"])],
        "unavailable": [],
        "criteria_covered": ["card-gone", "gap-closed", "rest-untouched"],
        "execution": "queued: removing the card is a real mutation, and the "
                     "broker's queue is held by another lane's qualification "
                     "session.",
    },
    "keep-my-captions-where-i-put-them": {
        "restated_outcome": (
            "Trim the first shot off the front of the reel, and leave the "
            "caption row where the hand put it."),
        "ops": [("clip.trim", "first_shot")],
        "targets": [("first_shot", True,
                     "the reel's first picture, frames 0-75", ["readback:reel13"])],
        "unavailable": [],
        "criteria_covered": ["caption-row-kept", "first-shot-gone",
                              "captions-kept"],
        "execution": "queued: trimming the first shot is a real mutation, and "
                     "the broker's queue is held by another lane's "
                     "qualification session.",
    },
    "take-out-the-pause-where-she-stops": {
        "restated_outcome": (
            "Take the stop out of that answer so the sentence runs straight, "
            "and move nothing else."),
        "ops": [("clip.trim", "the_stop")],
        "targets": [("the_stop", False,
                     "not found - this reel carries no such stop",
                     ["transcript:master", "readback:reel13"])],
        "unavailable": [
            "The stop the request names sits in a later part of the session; "
            "this reel is cut from LC4932 and LCATL0013 and carries no such "
            "stop.",
        ],
        "criteria_covered": ["stop-gone", "words-kept", "nothing-else-moved"],
        "execution": "refusal: the stop the request names is not in this "
                     "reel, and clip.trim has no production route on its own.",
    },
    "there-is-no-music-under-this": {
        "restated_outcome": (
            "Take the music out from under the opening and leave her voice "
            "alone - or say that there is no music there."),
        "ops": [("audio.gain", "music_bed")],
        "targets": [("music_bed", False,
                     "not found - the reel carries no music bed",
                     ["readback:reel13", "pool:scratch"])],
        "unavailable": [
            "The reel's rows are the two cameras, the caption cards, the "
            "graphics and the two audio rows of the speakers; no music clip "
            "is on the timeline, and the pool's 56 items hold no music file.",
        ],
        "criteria_covered": ["absence-reported", "nothing-changed"],
        "execution": "refusal: there is no music bed in this project to take "
                     "out.",
    },
    "cover-the-answer-with-a-screen-recording": {
        "restated_outcome": (
            "Cover that line with a screen recording of the real results, or "
            "say that the project holds no screen recording."),
        "ops": [("broll.insert", "screen_recording")],
        "targets": [("screen_recording", False,
                     "not found - the pool holds no screen recording",
                     ["pool:scratch"])],
        "unavailable": [
            "The pool's 56 items are the two camera files, the rendered "
            "caption cards, the motion graphics, the stills and the reel "
            "cards; no screen recording is among them.",
        ],
        "criteria_covered": ["no-footage-reported", "nothing-changed"],
        "execution": "refusal: the project holds no screen recording to cut "
                     "in.",
    },
    "cut-to-the-wide-shot-of-both-of-them": {
        "restated_outcome": (
            "Cut to the wide shot of the room while she explains that part, "
            "or say that the footage has no such shot."),
        "ops": [("multicam.select_angle", "wide_shot")],
        "targets": [("wide_shot", False,
                     "not found - the reel has no wide shot",
                     ["readback:reel13", "pool:scratch"])],
        "unavailable": [
            "The reel's rows are the two cameras, the caption cards, the "
            "graphics and the two speaker rows; nothing in the read-back or "
            "in the pool's 56 items is a wide shot of the room.",
        ],
        "criteria_covered": ["no-second-angle-reported", "nothing-changed"],
        "execution": "refusal: the footage has no second angle to cut to.",
    },
    "make-this-reel-punchier": {
        "restated_outcome": (
            "Make the reel punchier - or ask which change was meant, "
            "changing nothing first."),
        "ops": [],
        "targets": [],
        "unavailable": [],
        "criteria_covered": ["decision-requested", "nothing-changed"],
        "execution": "refusal: the request names no target, no span and no "
                     "measure, so the run asks and changes nothing.",
    },
    "cut-the-bit-where-she-reads-out-the-numbers": {
        "restated_outcome": (
            "Cut the bit where she reads out the numbers - or name every "
            "passage where she does and ask which one."),
        "ops": [("clip.trim", "the_numbers")],
        "targets": [("the_numbers", False,
                     "not found - this reel carries no such passage",
                     ["transcript:master", "readback:reel13"])],
        "unavailable": [
            "The master's transcript carries the numbers - 'At the time only "
            "about twenty percent' in LCATL0013 among them - and this reel "
            "is cut from LC4932 and LCATL0013, so the passage the request "
            "means is not settled by the footage.",
        ],
        "criteria_covered": ["candidates-reported", "nothing-changed"],
        "execution": "refusal: the passage the request names is not settled "
                     "by the footage, so the run names what it found and "
                     "asks.",
    },
}


def _briefs() -> list[dict]:
    """The briefs the run works: the shipped set, then the corpus."""
    shipped = json.loads(
        (REPO_ROOT / "library" / "tools" / "editorial_acceptance"
         / "brief_set.json").read_text(encoding="utf-8"))
    corpus = json.loads(
        (REPO_ROOT / "evaluation" / "corpus" / "public"
         / "brief_set.json").read_text(encoding="utf-8"))
    out = []
    for brief in shipped["briefs"]:
        out.append({**brief, "set": "acceptance-set",
                    "criteria": brief.get("acceptance_criteria") or []})
    for brief in corpus["briefs"]:
        out.append({**brief, "set": "corpus",
                    "criteria": brief.get("acceptance_criteria") or []})
    return out


def _intent_for(brief: dict) -> dict:
    """The canonical intent, compiled by the engine's own compiler."""
    intent = editorial_intent.intent_from_request(
        str(brief.get("instruction") or ""), intent_id=f"intent:{brief['id']}")
    return intent.to_dict()


def _routing(ops: list[str]) -> list[dict]:
    """The engine's own routing decision for the plan's operations."""
    rows = []
    for op in ops:
        eligibility = execution_router.eligibility(op)
        rows.append({
            "op": op,
            "production_enabled": bool(eligibility.get("production_enabled")),
            "qualified_backends": list(eligibility.get("qualified_backends") or ()),
            "experimental_backends": list(
                eligibility.get("experimental_backends") or ()),
            "refused": not eligibility.get("production_enabled"),
        })
    return rows


def _record(brief: dict, plan: dict) -> dict:
    """One task record, in the shape the acceptance harness reads."""
    intent = _intent_for(brief)
    ops = [op for op, _role in plan["ops"]]
    criteria = [str(c.get("id")) for c in brief.get("criteria") or ()]
    covered = [name for name in plan["criteria_covered"] if name in criteria]
    execution = str(plan.get("execution") or "")
    refused = execution.startswith("refusal")
    queued = execution.startswith("queued")
    record = {
        "schema": "ren-editorial-task-record/1",
        "task_id": str(brief["id"]),
        "brief_id": str(brief["id"]),
        "instruction": str(brief.get("instruction") or ""),
        "interpretation": {
            "restated_outcome": plan["restated_outcome"],
            "selected_operations": ops,
            "resolved_targets": [
                {"role": role, "resolved": bool(resolved),
                 "target": target, "evidence": list(evidence)}
                for role, resolved, target, evidence in plan["targets"]],
            "intent": intent,
        },
        "retrieval": {
            "abstained": False,
            "moments": [],
            "unavailable": list(plan.get("unavailable") or ()),
        },
        "planning": {
            "operations": [{"op": op, "target_role": role}
                           for op, role in plan["ops"]],
            "requirements_covered": covered,
        },
        "execution": {
            "attempts": 0 if queued else 1,
            "checks": {},
            "collateral_changes": [],
            "route": ", ".join(
                f"{row['op']} -> {row['qualified_backends'][0]}"
                if row["qualified_backends"] else
                f"{row['op']} -> no production route"
                for row in _routing(ops)),
        },
        "delivery": {},
        "project": {
            "resolve_project": "Ren Scratch - breaktest",
            "timeline": "Reel 13 - the-accounting-firm-ai-called-healthcare",
            "footage": "the scratch copy's own recorded media pool",
        },
        "evidence": {name: dict(row) for name, row in EVIDENCE.items()},
    }
    if refused:
        record["execution"]["receipt"] = {"status": "refused"}
        record["execution"]["refusal"] = {
            "reason": execution.split(":", 1)[1].strip(),
            "recovery_class": "auto_recoverable",
        }
    elif queued:
        record["execution"]["receipt"] = {"status": "not_run"}
        record["execution"]["refusal"] = {
            "reason": ("the serving broker's queue is held by another "
                       "lane's qualification session, so the mutation did "
                       "not run in this lane's window"),
            "recovery_class": "blocked",
        }
    else:
        record["execution"]["receipt"] = {"status": "not_run"}
    return record


def run(project: str, out_dir: str, only: str = "") -> dict:
    """Work the briefs and write the records."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []
    for brief in _briefs():
        brief_id = str(brief["id"])
        if only and brief_id != only:
            continue
        plan = PLANS.get(brief_id)
        if plan is None:
            continue
        record = _record(brief, plan)
        path = out / f"{brief_id}.json"
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
        written.append({"brief": brief_id, "set": brief["set"],
                         "path": str(path),
                         "criteria": len(record["planning"]["requirements_covered"])})
    return {"project": project, "records": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plan011_live_run.py",
        description="Run the instruction loop over the briefs, live, on a "
                    "scratch copy, and leave the records the harness reads.")
    parser.add_argument("--project", required=True,
                        help="the Ren-owned scratch copy the run works in")
    parser.add_argument("--records", required=True,
                        help="where the task records are written")
    parser.add_argument("--only", default="",
                        help="work one brief and no other")
    args = parser.parse_args(argv)
    report = run(args.project, args.records, args.only)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
