#!/usr/bin/env python3
"""QA-007: a BLIND review pack over two scratch deliverables, and two
simulated reviewers who DISAGREE.

The captain's punch list (2026-10-20) asks for human editorial review on
a blind evaluation set: reviewers inspect delivered videos without
knowing which candidate or model produced them; they are given the
original brief, the constraints and the reference material; they use a
structured rubric alongside an overall acceptance decision; disagreement
among reviewers is recorded rather than one subjective opinion being
treated as absolute truth; and acceptable work is distinguished from work
that requires significant human repair.

This is the PROOF of that surface, measured on two real deliverables and
two simulated reviewers.  No reviewer is contacted: the reviewers here are
SIMULATED, and the numbers below are the RECORD's structure - that two
readings can disagree and the record can say so - and not an editorial
finding about the deliverables.

The two deliverables are Ren's own scratch export and a copy of it with
the SAME two defects QA-006 seeded (2.08s cut out of the middle of a
spoken sentence, and a 0.709s undeclared black flash at the seam), so the
two candidates differ in exactly one thing.

Usage::

    python3 docs/benchmarks/blind_review_pack_proof.py [--export <file>]
                                                        [--workdir <dir>]
                                                        [--keep]

``--export`` defaults to Ren's own ``ren-scratch-bench-demo`` export. The
file is READ; every artefact this writes lands in ``--workdir`` (a
temporary directory unless one is named).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

REPO_ROOT = Path(__file__).resolve().parents[2]

from library.tools.editorial_acceptance import blind_review, judgement

DEFAULT_EXPORT = (Path.home() / "Documents" / "content_stuff" / "video_projects"
                   / "ren-scratch-bench-demo" / "exports"
                   / "vep-bench-demo-export.mp4")

#: The seam the defect is seeded at, and the size of the two defects. The
#: QA-006 proof (PR 3088) seeded the same two numbers on this same file,
#: so the two lanes' measurements are comparable.
SEAM_START = 47.10
CUT_SECONDS = 2.08
BLACK_SECONDS = 0.709

#: The scratch export's ending: a black card and silence under it. Both
#: are DECLARED, exactly as the QA-006 proof declared them, so the two
#: candidates differ in their EDIT and not in the export's own ending.
DECLARED_BLACK = "75.94:76.50"
DECLARED_SILENCE = "75.70:79.53"

BRIEF = {
    "id": "scratch-pricing-brief",
    "title": "The pricing reel",
    "instruction": "Open on the claim, then the proof, and keep the "
                   "sentence whole.",
    "project_state": {"footage_profile": "one speaker, one camera",
                      "existing_adjustments": [],
                      "media_conditions": []},
    "acceptance_criteria": [
        {"id": "hook-first", "statement": "it opens on the claim"},
        {"id": "sentence-intact",
         "statement": "no spoken sentence is cut in half"},
    ],
}

CONSTRAINTS = [
    "delivered at 1080x1920, 9:16",
    "the level is brought to the delivery spec's -14.0 LUFS",
    "no undeclared black frames",
]

REFERENCES = [
    {"kind": "text", "label": "the house cut",
     "text": "open on the claim, cut on the beat, and never in the middle "
             "of a sentence"},
]

#: The five things the punch list asks of a blind evaluation set, and the
#: capability each one needs in the tree.  The BEFORE column counts the
#: files that carry the capability at the base commit; the AFTER column
#: counts them in this tree.
CAPABILITIES = (
    ("the acceptance-decision vocabulary", "requires_significant_repair"),
    ("a blind review pack", "ren-blind-review-pack"),
    ("a reviewer's form", "review-form.html"),
    ("a blind results record", "ren-blind-review-results"),
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, encoding="utf-8",
                          check=False, cwd=str(REPO_ROOT))


def _base_ref() -> str:
    """The commit the BEFORE column is measured against."""
    for ref in ("origin/main", "main"):
        result = _git("rev-parse", "--verify", ref)
        if result.returncode == 0:
            return result.stdout.strip()
    return "HEAD"


def _files_at(ref: str, needle: str) -> int:
    """How many files carried this capability at that commit."""
    result = _git("grep", "-l", "--fixed-strings", needle, ref, "--", "library")
    if result.returncode not in (0, 1):
        return 0
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _files_here(needle: str) -> int:
    """How many files carry this capability in this tree."""
    count = 0
    for path in (REPO_ROOT / "library").rglob("*.py"):
        try:
            if needle in path.read_text(encoding="utf-8"):
                count += 1
        except (OSError, UnicodeDecodeError):
            continue
    return count


def _before_surface() -> dict:
    """What the engine's EXISTING blind surfaces carried, measured.

    ``blind_comparison`` is the blind head-to-head that existed before
    this change.  Its evaluator view is read here, and what it carries is
    what a reviewer was given: no delivered video, no brief, no rubric.
    """
    from library.tools import blind_comparison

    record = blind_comparison.comparison(
        "req-1", "Cut the reel",
        {"treatment_id": "producer-alpha", "plan_summary": "opens on the claim"},
        {"treatment_id": "producer-beta", "plan_summary": "opens on the proof"},
        criteria=[{"criterion": "the brief asks"}])
    view = blind_comparison.evaluator_view(record)
    return {
        "carries_a_delivered_video": any(
            "media" in side or "video" in side for side in view["sides"]),
        "carries_the_brief": "brief" in view,
        "carries_the_rubric": "rubric" in view or "legs" in view,
        "carries_an_acceptance_decision": "decision" in view,
    }


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, encoding="utf-8",
                            check=False, cwd=str(REPO_ROOT))
    if result.returncode:
        raise SystemExit(f"{' '.join(args)} failed:\n{result.stderr}")


def _ffmpeg(args: list[str]) -> None:
    _run(["ffmpeg", "-nostdin", "-y", *args])


def _measure_loudness(path: Path) -> dict[str, float]:
    """The file's own loudness, measured so the copy can be brought to it."""
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
         "-vn", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
         "-f", "null", "-"],
        capture_output=True, encoding="utf-8", check=False)
    text = result.stderr or ""
    start = text.rfind("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise SystemExit(f"ffmpeg measured no loudness for {path}")
    payload = json.loads(text[start:end + 1])
    return {key: float(payload[key]) for key in
            ("input_i", "input_tp", "input_lra", "input_thresh",
             "target_offset")}


def _bring_to_spec(source: Path, target: Path) -> None:
    """A copy of the delivered file at the delivery spec's loudness.

    The scratch export ships at -23.18 LUFS, which the engine's own audio
    spec reports as a failure - a real finding about that scratch reel,
    reported as such.  Both candidates are brought to the spec so the two
    differ in their EDIT and not in their level.
    """
    measured = _measure_loudness(source)
    _ffmpeg([
        "-i", str(source),
        "-af", (f"loudnorm=I=-14:TP=-1.5:LRA=11:"
                f"measured_I={measured['input_i']}:"
                f"measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:"
                f"measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:"
                f"linear=true:print_format=summary"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", str(target),
    ])


def _seed_defects(source: Path, target: Path, width: int, height: int,
                  fps: str) -> None:
    """The same two defects QA-006 seeded, on this same file.

    2.08s is cut out of the MIDDLE of a spoken sentence, and a 0.709s
    undeclared black flash is inserted at the seam.
    """
    cut_end = SEAM_START + CUT_SECONDS
    _ffmpeg([
        "-i", str(source),
        "-filter_complex", (
            f"[0:v]trim=start=0:end={SEAM_START},setpts=PTS-STARTPTS[va];"
            f"[0:a]atrim=start=0:end={SEAM_START},asetpts=PTS-STARTPTS[aa];"
            f"[0:v]trim=start={cut_end},setpts=PTS-STARTPTS[vb];"
            f"[0:a]atrim=start={cut_end},asetpts=PTS-STARTPTS[ab];"
            f"color=c=black:s={width}x{height}:r={fps}:d={BLACK_SECONDS}[vk];"
            f"anullsrc=r=48000:cl=stereo,atrim=duration={BLACK_SECONDS}[ak];"
            "[va][aa][vk][ak][vb][ab]concat=n=3:v=1:a=1[v][a]"),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-ar", "48000", "-ac", "2", str(target),
    ])


def _leg(leg: str, status: str, rationale: str, observation: str) -> dict:
    return {
        "leg": leg,
        "status": status,
        "rationale": rationale,
        "evidence": [{"artifact": "the delivered file the reviewer watched",
                      "observation": observation}],
        "uncertainty": {"level": "medium",
                        "note": "one reader's reading of one viewing; a "
                                "second reader might weigh the seam "
                                "differently"},
    }


def _answers(pack_id: str, reviewer: str, readings: list[tuple[str, bool, str]]
             ) -> dict:
    """One SIMULATED reviewer's answer file, covering every candidate."""
    entries = []
    for entry_id, broken, decision in readings:
        legs = []
        for leg in judgement.REVIEW_LEGS:
            status = "pass"
            rationale = "the deliverable reads as the brief asks"
            observation = "the delivered file plays through the section"
            if broken and leg.id == "narrative_coherence":
                status = "fail"
                rationale = ("the sentence breaks in the middle: the subject "
                             "of the clause is gone")
                observation = "the splice at 47.1s cuts the sentence in half"
            if broken and leg.id == "shot_quality":
                status = "fail"
                rationale = "a black flash sits at the seam"
                observation = "the picture goes black for 0.709s at the seam"
            legs.append(_leg(leg.id, status, rationale, observation))
        entries.append({
            "entry_id": entry_id,
            "legs": legs,
            "decision": decision,
            "notes": "simulated reviewer: this is the SURFACE being "
                     "proved, not an editorial finding",
        })
    return {
        "schema": blind_review.ANSWERS_SCHEMA,
        "pack_id": pack_id,
        "reviewer": reviewer,
        "entries": entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", default=str(DEFAULT_EXPORT),
                        help="the delivered file the candidates are cut from")
    parser.add_argument("--workdir", default=None,
                        help="where the proof's artefacts are written")
    parser.add_argument("--keep", action="store_true",
                        help="keep the workdir after the run")
    args = parser.parse_args(argv)

    source = Path(args.export)
    if not source.is_file():
        raise SystemExit(f"there is no delivered file at {source}")
    workdir = Path(args.workdir) if args.workdir else Path(
        tempfile.mkdtemp(prefix="ren-qa007-blind-review-"))
    workdir.mkdir(parents=True, exist_ok=True)

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "json", str(source)],
        capture_output=True, encoding="utf-8", check=False).stdout)
    stream = probe["streams"][0]
    width = int(stream["width"])
    height = int(stream["height"])
    fps = str(stream["r_frame_rate"])

    print(f"source: {source}")
    clean = workdir / "candidate-clean.mp4"
    defect = workdir / "candidate-defect.mp4"
    print("bringing the clean candidate to the delivery spec's loudness...")
    _bring_to_spec(source, clean)
    print("seeding the defect candidate's two defects...")
    _seed_defects(clean, defect, width, height, fps)

    candidates = [
        {"candidate_id": "cand-alpha", "origin": "producer-alpha",
         "path": str(clean)},
        {"candidate_id": "cand-beta", "origin": "producer-beta",
         "path": str(defect)},
    ]
    candidates_file = workdir / "candidates.json"
    candidates_file.write_text(json.dumps(candidates, indent=2),
                                encoding="utf-8")
    brief_file = workdir / "brief.json"
    brief_file.write_text(json.dumps(BRIEF, indent=2), encoding="utf-8")
    constraints_file = workdir / "constraints.json"
    constraints_file.write_text(json.dumps(CONSTRAINTS, indent=2),
                                encoding="utf-8")
    references_file = workdir / "references.json"
    references_file.write_text(json.dumps(REFERENCES, indent=2),
                               encoding="utf-8")

    pack_dir = workdir / "pack"
    print("building the blind pack through the shipped CLI...")
    _run([sys.executable, "-m", "library.tools.editorial_acceptance",
          "--blind-pack",
          "--candidates", str(candidates_file),
          "--brief-file", str(brief_file),
          "--constraints", str(constraints_file),
          "--references", str(references_file),
          "--pack-dir", str(pack_dir),
          "--pack-id", "qa007-proof",
          "--platform", "tiktok",
          "--declared-black", DECLARED_BLACK,
          "--declared-silence", DECLARED_SILENCE])
    pack = json.loads((pack_dir / "pack.private.json").read_text(
        encoding="utf-8"))

    by_origin = {row["origin"]: entry_id
                 for entry_id, row in pack["blinding"].items()}
    clean_entry = by_origin["producer-alpha"]
    defect_entry = by_origin["producer-beta"]

    answers_dir = workdir / "answers"
    answers_dir.mkdir(exist_ok=True)
    # Two SIMULATED reviewers, ONE answers file each. They agree about
    # the clean candidate and disagree about the defect one: the strict
    # reader sees the broken sentence and the black flash, and the
    # lenient reader sees a reel that plays.
    payloads = {
        "reader-strict": _answers("qa007-proof", "reader-strict", [
            (clean_entry, False, blind_review.ACCEPTABLE),
            (defect_entry, True, blind_review.SIGNIFICANT_REPAIR),
        ]),
        "reader-lenient": _answers("qa007-proof", "reader-lenient", [
            (clean_entry, False, blind_review.ACCEPTABLE),
            (defect_entry, False, blind_review.ACCEPTABLE),
        ]),
    }
    for name, answer in payloads.items():
        path = answers_dir / f"{name}.json"
        path.write_text(json.dumps(answer, indent=2), encoding="utf-8")

    report = workdir / "results.json"
    print("reading the answers back through the shipped CLI...")
    _run([sys.executable, "-m", "library.tools.editorial_acceptance",
          "--blind-results",
          "--pack", str(pack_dir / "pack.private.json"),
          "--answers", str(answers_dir),
          "--report", str(report)])
    record = json.loads(report.read_text(encoding="utf-8"))

    print("\n--- the record ---")
    for candidate in record["candidates"]:
        print(f"\n{candidate['entry_id']}  "
              f"(candidate {candidate['candidate_id']}, "
              f"producer {candidate['origin']})")
        print(f"  standing: {candidate['standing']}")
        print(f"  reason:   {candidate['standing_reason']}")
        for row in candidate["reviewers"]:
            print(f"  {row['reviewer']}: score {row['subjective_score']}, "
                  f"decision {row['decision']}, verdict {row['review_verdict']}")
        for row in candidate["leg_disagreements"]:
            print(f"  DISAGREED {row['id']}: {row['statuses']}")
        print(f"  objective failures: {candidate['objective_failures']}")
    print("\n--- counts ---")
    print(json.dumps(record["counts"], indent=2))
    print(json.dumps(record["denominators"], indent=2))
    print(json.dumps(record["decision_counts"], indent=2))

    base = _base_ref()
    print("\n--- BEFORE (the base commit) ---")
    print(f"base: {base}")
    for label, needle in CAPABILITIES:
        print(f"  files carrying {label}: {_files_at(base, needle)}")
    for key, value in _before_surface().items():
        print(f"  the blind head-to-head {key}: {value}")

    print("\n--- AFTER (this tree, measured on the proof) ---")
    for label, needle in CAPABILITIES:
        print(f"  files carrying {label}: {_files_here(needle)}")
    view = blind_review.reviewer_view(pack)
    print(f"  pack entries (delivered videos, producer hidden): "
          f"{len(view['entries'])}")
    print("  origin leaks in the reviewer view: 0 (the pack refuses one)")
    print("  the brief the reviewer is given: "
          f"{bool(view['brief']['instruction'])}")
    print(f"  constraints the reviewer is given: {len(view['constraints'])}")
    print(f"  references the reviewer is given: {len(view['references'])}")
    print(f"  rubric legs: {len(view['rubric'])}")
    print(f"  acceptance decisions: {len(view['decisions'])}")
    print(f"  reviewers who answered: {record['counts']['reviewer_count']}")
    print(f"  leg readings: {record['counts']['leg_rows_read']}")
    print(f"  leg readings that agreed: "
          f"{record['denominators']['leg_agreement']}")
    print(f"  candidates that reached one standing: "
          f"{record['denominators']['candidate_decision']}")
    print(f"  candidates in disagreement: "
          f"{record['counts']['candidates_disagreed']}/"
          f"{record['counts']['candidate_count']}")
    print(f"  decisions recorded: {record['decision_counts']}")

    if not args.keep and args.workdir is None:
        print(f"\nworkdir kept at {workdir} (it holds the pack and the "
              "reviewer's answers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
