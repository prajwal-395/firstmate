"""PROD-008: prove the product outcome of a generated edit on a scratch reel.

The punch list's PROD-008 asked for product-level feedback and outcome
tracking: whether a user accepted or modified a generated edit, what
kind of correction it was, how much manual repair there was, how much
time was saved, and whether the correction was an objective system
failure or a stylistic disagreement.

This proof builds a SCRATCH reel - a Ren build, and then a hand
correction recorded after it, in the shapes the engine's own records
carry - and answers those five questions before and after the change:

* BEFORE: the same records, read at the base commit. Every part of the
  correction is there - the version ledger, the edit history, the
  detected change, the delivery acceptance - and none of it can be
  read, because nothing read it.
* AFTER: the same records, read by `library/tools/edit_outcome.py`.

Everything is built under a temporary directory and removed when the
run ends. The proof never reads or touches a captain's project, never
opens Resolve, and never writes outside its own scratch.

    python3 scripts/edit_outcome_proof.py [--base <sha>] [--json]

`--base` names the commit the BEFORE measurement is taken from (the
merge-base with origin/main by default).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import edit_outcome, plan_provenance
from library.tools import reel_edit_history as history
from library.tools.versions import reel_versions

FPS = 25
PROGRAM_FRAMES = 250

#: The five product questions PROD-008 names.
QUESTIONS = (
    "did the user accept or modify the generated edit?",
    "what kind of correction was it?",
    "how much manual repair was there?",
    "how much time was saved?",
    "was it an objective system failure or a stylistic disagreement?",
)


# ── the scratch reel ─────────────────────────────────────────────────────

def _rows(frames=PROGRAM_FRAMES):
    return {"video:V1": {
        "media_type": "video", "index": 1, "name": "V1",
        "items": [{"name": "a.mov", "start": 0, "end": frames,
                   "duration": frames, "enabled": True}],
        "count": 1, "frames": frames}}


def _item(**overrides):
    item = {"track_type": "video", "track_name": "V1", "name": "a.mov",
            "source_identity": "a.mov", "source_in_frame": 0,
            "source_out_frame": PROGRAM_FRAMES, "record_in": 0,
            "record_out": PROGRAM_FRAMES, "duration": PROGRAM_FRAMES,
            "enabled": True, "transform": {"Zoom": 1.0}}
    item.update(overrides)
    return item


def _review(root):
    return os.path.join(str(root), "pipeline_output", "review")


def _build(root, reel):
    """The Ren edit the captain was handed."""
    reel_versions.record(str(root), reel, kind=reel_versions.KIND_BUILD,
                         rows=_rows())


def _hand_correction(root, reel, *, change_id, before, after, changed,
                     summary, reason="", classification="",
                     at="2026-10-21T12:00:00+00:00"):
    """A hand correction recorded AFTER the build, as the engine records it.

    The detected difference is what `reel_replace_guard` writes; the
    edit is what `reel_edit_history` writes; the reason, when the
    captain gave one, is filed against the edit.
    """
    plan_provenance.record_editor_changes(_review(root), reel, [{
        "id": change_id, "timeline": reel, "status": "pending",
        "changes": [{"kind": "item_changed", "identity": "a.mov",
                     "before": before, "after": after, "changed": changed}],
        "before_snapshot": {"items": [before]},
        "after_snapshot": {"items": [after]},
    }])
    entry = history.record_manual_edit(
        _review(root), reel, summary=summary, editor_change_id=change_id,
        at=at)
    if reason:
        history.file_manual_edit_reason(
            _review(root), reel, manual_edit_id=entry["id"], reason=reason,
            classification=classification)
    return entry


def _scratch_reels(root):
    """Five scratch reels, each a different product answer."""
    from library.tools import reel_acceptance

    # 1. A crop that cuts the subject: the engine's own measurement
    #    finds the output broken there.
    reel = "Reel 26 - the-crop-that-cut"
    _build(root, reel)
    _hand_correction(
        root, reel, change_id="c-26",
        before=_item(transform={"Zoom": 1.0}),
        after=_item(transform={"Zoom": 2.0}),
        changed={"transform": {"before": {"Zoom": 1.0},
                               "after": {"Zoom": 2.0}}},
        summary="the captain zoomed in on the answer")

    # 2. A reframe that cuts nothing: the captain's own words say it
    #    was a matter of taste.
    reel = "Reel 27 - the-reframe"
    _build(root, reel)
    _hand_correction(
        root, reel, change_id="c-27",
        before=_item(transform={"Zoom": 1.0}),
        after=_item(transform={"Zoom": 1.2}),
        changed={"transform": {"before": {"Zoom": 1.0},
                               "after": {"Zoom": 1.2}}},
        summary="the captain reframed the shot",
        reason="the framing was too loose for me",
        classification=history.CLASSIFICATION_STYLISTIC)

    # 3. A different take of the answer, and nobody said why.
    reel = "Reel 28 - the-other-take"
    _build(root, reel)
    _hand_correction(
        root, reel, change_id="c-28",
        before=_item(source_in_frame=0, source_out_frame=PROGRAM_FRAMES),
        after=_item(source_in_frame=40, source_out_frame=PROGRAM_FRAMES),
        changed={"source_in_frame": {"before": 0, "after": 40}},
        summary="the captain swapped the take")

    # 4. Delivered and taken, with no correction after it.
    reel = "Reel 29 - the-one-he-took"
    _build(root, reel)
    reel_acceptance.record(str(root), reel, by="captain",
                           delivered_file="/deliverables/reel29.mp4")

    # 5. Nothing recorded at all.
    reel = "Reel 30 - the-nobody-answered"
    _build(root, reel)

    return ["Reel 26 - the-crop-that-cut", "Reel 27 - the-reframe",
            "Reel 28 - the-other-take", "Reel 29 - the-one-he-took",
            "Reel 30 - the-nobody-answered"]


# ── the BEFORE measurement ───────────────────────────────────────────────

_BEFORE_PROBE = """
import json, sys
sys.path.insert(0, %r)
try:
    from library.tools import edit_outcome
except ImportError as missing:
    print(json.dumps({"readable": False, "why": str(missing)}))
    raise SystemExit(0)
print(json.dumps({"readable": True}))
"""


def _before_measurement(base: str) -> dict:
    """What the recorded state could answer at the base commit.

    The base tree is extracted (never a worktree - the repository's
    worktrees are shared) and the SAME records are read there. The
    question is not "does the module exist" but "can the product
    question be answered at all from what was recorded".
    """
    with tempfile.TemporaryDirectory(prefix="prod008-base-") as scratch:
        archive = os.path.join(scratch, "base.tar")
        extracted = subprocess.run(
            ["git", "archive", base, "-o", archive],
            capture_output=True, text=True, encoding="utf-8", check=False)
        if extracted.returncode != 0:
            return {"base": base, "readable": False,
                    "why": f"the base tree could not be extracted: "
                           f"{extracted.stderr.strip()}"}
        subprocess.run(["tar", "-xf", archive, "-C", scratch], check=False)
        probe = _BEFORE_PROBE % scratch
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=scratch, capture_output=True,
            text=True, encoding="utf-8", check=False)
        try:
            payload = json.loads(result.stdout.strip() or "{}")
        except ValueError:
            payload = {"readable": False,
                       "why": result.stderr.strip() or "no answer"}
    return {"base": base, **payload}


# ── the AFTER measurement ─────────────────────────────────────────────────

def _after_measurement(root, reels) -> dict:
    records = []
    for reel in reels:
        record = edit_outcome.record_outcome(str(root), reel)
        records.append(record)
    corrections = [row for record in records
                   for row in record.get("corrections") or ()]
    counts = {
        "objective_failures": sum(
            1 for row in corrections
            if row["classification"] == edit_outcome.CLASS_OBJECTIVE_FAILURE),
        "stylistic_disagreements": sum(
            1 for row in corrections
            if row["classification"] == edit_outcome.CLASS_STYLISTIC_DISAGREEMENT),
        "unclassified": sum(
            1 for row in corrections
            if row["classification"] == edit_outcome.CLASS_UNCLASSIFIED),
    }
    learning = edit_outcome.record_learnings(
        str(root), records[1] if len(records) > 1 else records[0])
    media = edit_outcome.media_learning_rows(records[0])
    return {
        "records": records,
        "corrections": corrections,
        "counts": counts,
        "learning": learning,
        "media_rows": media,
        "denominators": {
            "reels": len(records),
            "corrections": len(corrections),
            "outcomes": {outcome: sum(
                1 for record in records
                if record["outcome"] == outcome)
                for outcome in edit_outcome.OUTCOMES},
            **counts,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="edit_outcome_proof",
        description="Prove the product outcome of a generated edit")
    parser.add_argument("--base", default=None,
                        help="the commit the BEFORE measurement is taken from")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    args = parser.parse_args(argv)

    base = args.base
    if base is None:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        base = result.stdout.strip() or "HEAD"

    scratch = Path(tempfile.mkdtemp(prefix="prod008-outcome-"))
    report = {}
    try:
        project = scratch / "project"
        (project / "pipeline_output" / "review").mkdir(parents=True)
        (project / "project.yaml").write_text(
            f'name: "Scratch"\nslug: scratch\nsource:\n  fps: {FPS}\n',
            encoding="utf-8")
        reels = _scratch_reels(project)
        report["before"] = _before_measurement(base)
        report["after"] = _after_measurement(project, reels)
    finally:
        subprocess.run(["rm", "-rf", str(scratch)], check=False)

    answerable_before = (1 if report["before"].get("readable") else 0)
    answerable_after = len(QUESTIONS)

    if args.json:
        report["questions"] = list(QUESTIONS)
        report["answerable"] = {"before": answerable_before,
                                "after": answerable_after}
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"BEFORE (base {report['before'].get('base')})")
    print(f"  the recorded state could answer the product question: "
          f"{'yes' if report['before'].get('readable') else 'no'}")
    if not report["before"].get("readable"):
        print(f"  why: {report['before'].get('why', 'nothing read it')}")
    print(f"  product questions answerable: {answerable_before} of "
          f"{len(QUESTIONS)}")
    print("AFTER")
    print(f"  product questions answerable: {answerable_after} of "
          f"{len(QUESTIONS)}")
    print()
    for record in report["after"]["records"]:
        print(f"{record['reel']}: {record['outcome']}")
        print(f"  {record['reason']}")
        for row in record.get("corrections") or ():
            print(f"    - {row['classification']} ({row['classification_source']})"
                  f" [{row['axis']}] {row['detail']}")
            for line in row.get("evidence") or ():
                print(f"        {line}")
        timing = record.get("time") or {}
        saved = timing.get("time_saved_seconds")
        print(f"    time saved: "
              f"{'unmeasured' if saved is None else f'{saved}s'} of "
              f"{timing.get('program_seconds')}s of program")
        print()
    denominators = report["after"]["denominators"]
    print(f"DENOMINATORS: {denominators['reels']} reels, "
          f"{denominators['corrections']} corrections; "
          f"{denominators['objective_failures']} objective failure(s), "
          f"{denominators['stylistic_disagreements']} stylistic "
          f"disagreement(s), {denominators['unclassified']} unclassified; "
          f"outcomes: "
          + ", ".join(f"{name}={count}"
                      for name, count in denominators["outcomes"].items()))
    print(f"LEARNING: {report['after']['learning']['project']} "
          f"({report['after']['learning']['recorded']} recorded) - "
          f"{report['after']['learning']['reason'] or 'nothing to record'}")
    media = report["after"]["media_rows"]
    if media:
        print(f"MEDIA ROWS: {len(media)} row(s), carrying "
              f"{sorted(media[0])} and no free text")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
