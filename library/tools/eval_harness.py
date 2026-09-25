"""The standing eval harness: does Ren follow a natural-language edit request.

Productises the execution-frontier scout (2026-09-24, `report.md` in
firstmate's data dir): the 120 corpus requests (`eval_corpus`, board data
verbatim) as fixtures; each run clones a pristine base (analysis done,
paths rewritten), injects the request as a collected-style note, runs the
edit stage, builds under the Resolve driver lock, and reads back.

Two tracks are scored APART and three questions are never blended:

* track A (timeline ops, to the frame): the readback diff vs base, where
  every hunk must be explained by an op the request implies;
* track B (pixels and sound on the export): luma, chroma, LUFS and - where
  the run's own automation resolves - speech-over-bed separation, measured
  base vs run at the same timestamps;
* followed (from track A ops), broke nothing (every diff hunk explained),
  looks good (a human, or a judge that reports its agreement).

Every miss records the first layer that broke (`eval_corpus.LAYERS`).
A report lands per request and per domain x level. The harness GATES
NOTHING and is not part of the test suite or the full gate: `finalize`
refusing an incomplete judgement is a usage error, not a verdict.

Fixtures live OUTSIDE git: project data is never committed. The corpus
(request texts plus scout verdicts) is eval spec and ships in
`eval_corpus.py`; the pristine base (footage plus analysis) is passed as
`--base` and works on a fresh machine. `ren eval howto` says how to run
it per rung and weekly.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from library.tools import eval_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
VEP = REPO_ROOT / "bin" / "vep"
RESOLVE_AXI = REPO_ROOT / "bin" / "resolve-axi"
MANAGE_PROJECT = REPO_ROOT / "manage_project.py"

LOCK_DIR = Path(os.path.expanduser("~/.local/share/vep/resolve-driver.lock"))
SCRATCH_PREFIX = "ren-eval-scratch-"

# The edit chain the scout ran: everything downstream of analysis, minus
# the render and the validate gate. Verified against the DAG at run time:
# a name that is not a node refuses rather than silently not re-running.
EDIT_RERUN_CHAIN = (
    "speech_sequence", "mesh_spine", "assign_aroll", "select_broll",
    "audio_mix", "review_rough_cut", "color_grade", "plan_subtitles",
    "plan_transitions", "plan_vfx", "render_subtitles", "plan_sfx",
    "render_motion_graphics", "creative_cohesion", "compile_manifest",
)

EVAL_NOTE_SOURCE = "timeline_marker"

OUTCOMES = ("followed", "part", "missed")


# ── selection ────────────────────────────────────────────────────────

def select(**filters) -> list:
    """`eval_corpus.select` re-exported so the verb has one address."""
    return eval_corpus.select(**filters)


# ── pure pieces: paths, notes, diffs, verdicts ───────────────────────

def rewrite_text(old: str, new: str, text: str) -> str:
    """One path rewrite. A text without the old path comes back unchanged."""
    if old in text:
        return text.replace(old, new)
    return text


def rewrite_tree_for_clone(tree: Path, old_base: str, new_base: str) -> int:
    """Rewrite the old base path out of every JSON/Markdown/YAML state file.

    Returns the file count rewritten. `project.yaml` is included: the
    timeline name and any absolute path it carries must point at the clone.
    Footage and audio blobs are never touched - only text state.
    """
    count = 0
    candidates = ["pipeline_data.json", "pipeline_run.json", "project.yaml"]
    candidates += sorted(
        str(p) for p in (tree / "pipeline_output").rglob("*.json"))
    candidates += sorted(
        str(p) for p in (tree / "pipeline_output").rglob("*.md"))
    for name in candidates:
        path = tree / name if not os.path.isabs(name) else Path(name)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rewritten = rewrite_text(old_base, new_base, text)
        if rewritten != text:
            path.write_text(rewritten, encoding="utf-8")
            count += 1
    return count


def eval_pull_payload(request: dict, timeline: str) -> dict:
    """The request as a collected-style note pull.

    Same schema `marker_feedback.pull` writes (`marker_feedback/1`), so the
    product's own `route_project` routes it like a whole-piece timeline
    note. `custom_data.eval_request_id`, the filename and note name retain
    eval provenance. Frames are None - the request is about the piece, not
    a marker position - which the router reads as "no span" rather than a
    wrong one.
    """
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    text = request["text"]
    return {
        "filename": f"eval-{request['id']}.{stamp}.markers.json",
        "payload": {
            "format": "marker_feedback/1",
            "pulled_at": _dt.datetime.now(_dt.UTC).isoformat(),
            "resolve_project": "ren-eval-harness",
            "timeline": timeline,
            "timeline_start_frame": 0,
            "timeline_fps": 0.0,
            "note_count": 1,
            "notes": [{
                "source": EVAL_NOTE_SOURCE,
                "name": f"eval {request['id']}",
                "note": text,
                "text": text,
                "frame": None,
                "timecode": None,
                "frame_in_timeline_space": None,
                "unplaced_reason": "",
                "color": "",
                "duration_frames": 1,
                "custom_data": {"eval_request_id": request["id"]},
                "custom_data_raw": "",
                "attachments": [],
            }],
        },
    }


def inject_request(project_dir: str | os.PathLike, request: dict,
                   timeline: str) -> str:
    """Write the request pull into `<project>/marker_feedback/`.

    Returns the pull path. Refuses when a pull for this request already
    sits there: two identical notes would route as two notes.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project_dir = os.fspath(project_dir)
    pulled = eval_pull_payload(request, timeline)
    directory = ProjectLayout(project_dir).read_dir(Area.MARKER_FEEDBACK)
    for existing in sorted(directory.glob(f"eval-{request['id']}.*")):
        raise FileExistsError(
            f"eval pull for {request['id']} already present: {existing}")
    path = ProjectLayout(project_dir).write_path(
        Area.MARKER_FEEDBACK, pulled["filename"])
    path.write_text(json.dumps(pulled["payload"], indent=2,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return str(path)


def normalise_readback(text: str, run_dir: str, timeline: str) -> str:
    """Strip run-specific noise so the base-vs-run diff is signal.

    Timeline names (`EVAL_<batch>`) and the run directory path differ by
    construction; without normalising, every line is a hunk and
    broke-nothing can never be clean.
    """
    if run_dir:
        text = text.replace(run_dir, "<rundir>")
    text = re.sub(r"EVAL_[A-Za-z0-9_.-]+", "<evaltimeline>", text)
    text = re.sub(r"ren-eval-scratch-[A-Za-z0-9_.-]+", "<scratch>", text)
    return text


def timeline_hunks(base_text: str, run_text: str) -> list:
    """Unified-diff hunks between two normalised readbacks.

    Each hunk is `{"index", "header", "lines"}`. Empty means the build
    placed exactly what the base placed - the broke-nothing ideal.
    """
    hunks = []
    lines = list(difflib.unified_diff(
        base_text.splitlines(), run_text.splitlines(),
        fromfile="base", tofile="run", lineterm="", n=1))
    header = None
    body: list = []
    for line in lines:
        if line.startswith("@@"):
            if header is not None:
                hunks.append({"index": len(hunks), "header": header,
                              "lines": body})
            header = line
            body = []
        elif header is not None:
            body.append(line)
    if header is not None:
        hunks.append({"index": len(hunks), "header": header, "lines": body})
    return hunks


def derive_verdict(op_outcomes: list) -> str:
    """F/P/X from op judgements only - never blended with anything else.

    All ops followed is F (lands in full), all missed is X, anything in
    between is P. An empty op list raises: a request with no ops is not
    a scored request, and scoring it X would launder "nothing asked".
    """
    if not op_outcomes:
        raise ValueError("no ops judged: derive_verdict refuses an empty list")
    unknown = [o for o in op_outcomes if o not in OUTCOMES]
    if unknown:
        raise ValueError(f"unknown op outcome(s): {unknown}")
    if all(o == "followed" for o in op_outcomes):
        return "F"
    if all(o == "missed" for o in op_outcomes):
        return "X"
    return "P"


def check_judgement_complete(judgement: dict, hunks: list) -> list:
    """Every problem that keeps a judgement from finalising.

    Empty means finalisable. A miss (part/missed) without the first layer
    that broke is a problem: the report requires the layer, not just the
    miss. An op without evidence is a problem: an assertion without the
    readback or export line that shows it.
    """
    problems = []
    ops = judgement.get("ops") or []
    if not ops:
        problems.append("no ops: one op per clause of the request is required")
    for position, op in enumerate(ops):
        where = f"ops[{position}]"
        if not (op.get("op") or "").strip():
            problems.append(f"{where}: empty op text")
        if op.get("outcome") not in OUTCOMES:
            problems.append(
                f"{where}: outcome must be one of {list(OUTCOMES)}")
        if not (op.get("evidence") or "").strip():
            problems.append(f"{where}: no evidence")
        if (op.get("outcome") in ("part", "missed")
                and (op.get("layer") not in eval_corpus.LAYERS
                     or op.get("layer") == "-")):
            problems.append(
                f"{where}: a part/missed op must name the first layer "
                f"that broke ({sorted(eval_corpus.LAYERS)})")
    reviews = {str(h.get("index")): h
               for h in (judgement.get("hunks") or [])}
    op_texts = {(op.get("op") or "").strip() for op in ops}
    for hunk in hunks:
        review = reviews.get(str(hunk["index"]), {})
        explained_by = (review.get("explained_by") or "").strip()
        break_reason = (review.get("break_reason") or "").strip()
        if bool(explained_by) == bool(break_reason):
            problems.append(
                f"hunk {hunk['index']} ({hunk['header']}): choose exactly "
                "one matching op or break_reason")
        elif explained_by and explained_by not in op_texts:
            problems.append(
                f"hunk {hunk['index']} ({hunk['header']}): explained_by "
                "must exactly match a judged op")
    measured_indices = {str(hunk["index"]) for hunk in hunks}
    for index in reviews.keys() - measured_indices:
        problems.append(f"hunk judgement {index} has no readback diff hunk")
    looks = (judgement.get("looks_good") or {})
    if looks.get("verdict") not in ("yes", "no", "unjudged"):
        problems.append("looks_good.verdict must be yes, no or unjudged")
    if (looks.get("verdict") in ("yes", "no")
            and not (looks.get("looked_by") or "").strip()):
        problems.append("looks_good names a verdict but not who looked")
    looked_by = (looks.get("looked_by") or "").strip().casefold()
    label_agreement = looks.get("captain_label_agreement")
    if (looks.get("verdict") in ("yes", "no")
            and any(marker in looked_by for marker in ("judge", "llm"))
            and label_agreement not in ("agree", "disagree")):
        problems.append(
            "an LLM look-good judge must report agree/disagree with "
            "captain labels")
    return problems


def agreement(harness_verdict: str, scout_verdict: str,
              difference: str = "") -> str:
    """agree | differ-explained | differ-unexplained.

    A difference with the reason written down is data (the product moved
    since the scout ran); a bare difference is a failed proof.
    """
    if harness_verdict == scout_verdict:
        return "agree"
    if (difference or "").strip():
        return "differ-explained"
    return "differ-unexplained"


def aggregate(finalised: list) -> dict:
    """Totals plus separate scores by domain x level over finalised reports."""
    matrix: dict = {}
    for entry in finalised:
        key = f"{entry['domain']} L{entry['level']}"
        cell = matrix.setdefault(key, {
            "F": 0, "P": 0, "X": 0,
            "clean": 0, "BROKE": 0, "break_unjudged": 0,
            "looks_yes": 0, "looks_no": 0, "looks_unjudged": 0,
            "judge_agree": 0, "judge_disagree": 0,
            "judge_unjudged": 0})
        cell[entry["harness_verdict"]] += 1
        break_score = entry.get("broke_nothing", "break_unjudged")
        if break_score not in ("clean", "BROKE"):
            break_score = "break_unjudged"
        cell[break_score] += 1
        look_score = entry.get("looks_good", "unjudged")
        if look_score not in ("yes", "no", "unjudged"):
            look_score = "unjudged"
        cell[f"looks_{look_score}"] += 1
        judge_score = entry.get("looks_good_agreement") or "unjudged"
        if judge_score not in ("agree", "disagree"):
            judge_score = "unjudged"
        cell[f"judge_{judge_score}"] += 1
    totals = {"F": 0, "P": 0, "X": 0}
    for entry in finalised:
        totals[entry["harness_verdict"]] += 1
    return {"n": len(finalised), "totals": totals, "matrix": matrix,
            "agreement": {
                name: sum(1 for e in finalised if e["agreement"] == name)
                for name in ("agree", "differ-explained",
                             "differ-unexplained")}}


# ── rendering ────────────────────────────────────────────────────────

def render_request_report(request: dict, run_ledger: dict, measures: dict,
                          judgement: dict, harness_verdict: str,
                          agree: str) -> str:
    """One request's report. Measured sections first, judgements labelled."""
    lines = [f"# Eval {request['id']} ({request['domain']} L{request['level']})",
             "",
             f"> {request['text']}",
             "",
             (f"Scout: **{request['scout_verdict']}** "
              f"({eval_corpus.LAYERS.get(request['scout_layer'], '?')} / "
              f"{request['scout_gap']}, {request['scout_mode']}). "
              f"Harness: **{harness_verdict}**. Agreement: **{agree}**."),
             ""]
    pinned = (run_ledger.get("clone", {})
              .get("preflight_hashes_pinned", []))
    if pinned:
        steps = ", ".join(item["step_id"] for item in pinned)
        lines += [(f"Fixture analysis reused; clone preflight hashes pinned "
                   f"for: {steps}. See `run.json` for the original and "
                   "eval hashes."), ""]
    if agree == "differ-explained":
        lines += [f"Difference explained: {judgement.get('difference', '')}",
                  ""]
    lines += ["## Followed (track A: timeline ops, to the frame)", ""]
    for op in judgement.get("ops", []):
        lines += [f"- [{op['outcome']}] {op['op']}",
                  f"  evidence: {op.get('evidence', '')}"]
        if op.get("layer") and op["layer"] != "-":
            lines += [(f"  first layer that broke: "
                       f"{eval_corpus.LAYERS.get(op['layer'], op['layer'])}")]
    hunks = measures.get("hunks", [])
    judged = {str(h.get("index")): h for h in judgement.get("hunks", [])}
    lines += ["", "## Broke nothing (readback diff vs base)", "",
              f"{len(hunks)} hunk(s)."]
    for hunk in hunks:
        review = judged.get(str(hunk["index"]), {})
        explains = (review.get("explained_by") or "").strip()
        break_reason = (review.get("break_reason") or "").strip()
        if explains:
            lines += [(f"- hunk {hunk['index']} {hunk['header']}: "
                       f"explained by op: {explains}")]
        elif break_reason:
            lines += [(f"- hunk {hunk['index']} {hunk['header']}: "
                       f"BREAK: {break_reason}")]
        else:
            lines += [f"- hunk {hunk['index']} {hunk['header']}: UNJUDGED"]
    broken = [hunk for hunk in hunks
              if (judged.get(str(hunk["index"]), {}).get("break_reason")
                  or "").strip()]
    lines += ["",
              (f"Broke nothing: **{'BROKE' if broken else 'clean'}** "
               f"({len(broken)} unexplained hunk(s))."),
              "", "## Looks good (track B + human or judge)", ""]
    looks = judgement.get("looks_good") or {}
    lines += [(f"Looks good: **{looks.get('verdict', 'unjudged')}** "
               f"({looks.get('looked_by', 'nobody')}).")]
    if looks.get("captain_label_agreement"):
        lines += [(f"Judge agreement with captain labels: "
                   f"**{looks['captain_label_agreement']}**.")]
    if looks.get("note"):
        lines += [f"Note: {looks['note']}"]
    frames = measures.get("frames", {})
    if frames.get("measured"):
        lines += ["", "Export measures (base vs run, same timestamps):"]
        for row in frames.get("rows", []):
            lines += [(f"- t={row['t']}s: luma {row['base']['luma']} -> "
                       f"{row['run']['luma']}; chroma "
                       f"{row['base']['chroma']} -> {row['run']['chroma']}")]
    elif frames.get("reason"):
        lines += ["", f"Pixels: unmeasured ({frames['reason']})."]
    zoom = measures.get("zoom", {})
    if zoom.get("measured"):
        lines += ["", "Relative zoom estimated from matching export pixels:"]
        for row in zoom.get("rows", []):
            if row.get("measured"):
                lines += [(f"- t={row['t']}s: {row['relative_scale']}x "
                           f"({row['inliers']} inliers / "
                           f"{row['matches']} matches)")]
            else:
                lines += [f"- t={row['t']}s: unmeasured ({row['reason']})"]
    elif zoom.get("reason"):
        lines += ["", f"Zoom: unmeasured ({zoom['reason']})."]
    lufs = measures.get("lufs", {})
    if lufs.get("measured"):
        lines += [(f"Loudness: base {lufs.get('base_lufs')} LUFS, run "
                   f"{lufs.get('run_lufs')} LUFS.")]
    elif lufs.get("reason"):
        lines += [f"Loudness: unmeasured ({lufs['reason']})."]
    sep = measures.get("separation", {})
    if sep.get("measured"):
        lines += ["", "Speech over bed on the export:"]
        for side in ("base", "run"):
            reading = sep.get(side, {})
            if reading.get("measured"):
                windows = reading.get("windows", [])
                margins = ", ".join(
                    f"{w['margin_db']:+.1f} dB"
                    for w in windows if w.get("margin_db") is not None)
                lines += [(f"- {side}: {reading.get('judged', 0)} speech "
                           f"window(s), margins {margins or 'none'}")]
            else:
                lines += [(f"- {side}: unmeasured "
                           f"({reading.get('reason', 'no measurement')})")]
    elif sep.get("reason"):
        lines += [f"Separation: unmeasured ({sep['reason']})."]
    lines += ["", (f"Run: {run_ledger.get('batch')} "
                   f"{run_ledger.get('timeline', '')} "
                   f"{run_ledger.get('finished_at', '')}.")]
    return "\n".join(lines) + "\n"


def render_aggregate_report(entries: list, agg: dict) -> str:
    """Per domain x level over finalised requests, plus agreement."""
    lines = ["# Eval aggregate", "",
             (f"n={agg['n']}: "
              f"F {agg['totals']['F']} / P {agg['totals']['P']} / "
              f"X {agg['totals']['X']}."),
             "",
             "Agreement with the scout: " + ", ".join(
                 f"{k} {v}" for k, v in agg["agreement"].items()) + ".",
             "",
             ("| Request | Scout | Harness | Agreement | Broke | Looks | "
              "Judge agreement |"),
             "|---|---|---|---|---|---|---|"]
    for entry in sorted(entries, key=lambda e: e["request_id"]):
        lines.append(
            f"| {entry['request_id']} | {entry['scout_verdict']} | "
            f"{entry['harness_verdict']} | {entry['agreement']} | "
            f"{entry['broke_nothing']} | {entry['looks_good']} | "
            f"{entry.get('looks_good_agreement') or '-'} |")
    lines += ["", "## Domain x level", "",
              ("| Cell | F | P | X | Clean | Broke | Break unjudged | "
               "Looks yes | Looks no | Looks unjudged | Judge agree | "
               "Judge disagree | Judge unjudged |"),
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cell in sorted(agg["matrix"]):
        counts = agg["matrix"][cell]
        lines.append(
            f"| {cell} | {counts['F']} | {counts['P']} | {counts['X']} | "
            f"{counts['clean']} | {counts['BROKE']} | "
            f"{counts['break_unjudged']} | {counts['looks_yes']} | "
            f"{counts['looks_no']} | {counts['looks_unjudged']} | "
            f"{counts['judge_agree']} | {counts['judge_disagree']} | "
            f"{counts['judge_unjudged']} |")
    return "\n".join(lines) + "\n"


def judgement_template(request: dict, hunks: list) -> dict:
    """The file the operator fills in: ops, hunk attribution, looks-good.

    Attribute each hunk to the exact op text, or record `break_reason`;
    an unexpected hunk is a BROKE score, not a reason to suppress the run.
    An LLM look-good judge also records agreement with captain labels.
    """
    return {
        "request_id": request["id"],
        "ops": [],
        "hunks": [{"index": h["index"], "header": h["header"],
                   "explained_by": "", "break_reason": ""}
                  for h in hunks],
        "looks_good": {"verdict": "unjudged", "looked_by": "", "note": "",
                       "captain_label_agreement": ""},
        "difference": "",
    }


# ── machine coordination: heavy jobs, the Resolve lock ───────────────

def _is_heavy_process(line: str) -> bool:
    """Match process argv, not arbitrary prompt text in an agent command."""
    fields = line.strip().split(None, 2)
    if len(fields) < 2:
        return False
    command = Path(fields[1]).name
    args = fields[2].split() if len(fields) == 3 else []
    cleaned = [token.strip("\"'") for token in args]
    basenames = [Path(token).name for token in cleaned]

    if command in {"ffmpeg", "whisper", "mlx_vlm", "mlx_lm",
                   "llama-server"}:
        return True
    if (command.startswith("python")
            and ("manage_project.py" in basenames and "run" in cleaned
                 or "run_pipeline.py" in basenames
                 or any("step_6_01_render" in token for token in cleaned)
                 or "vision_pipeline_v3.py" in basenames
                 or ("run" in cleaned
                     and any(token == "-m"
                             and cleaned[index + 1]
                             == "library.tools.eval_harness"
                             for index, token
                             in enumerate(cleaned[:-1])))
                 or any(token == "-m"
                        and cleaned[index + 1].split(".", 1)[0]
                        in ("mlx_lm", "mlx_vlm")
                        for index, token in enumerate(cleaned[:-1])))):
        return True
    if command in {"sh", "bash"} and "full_suite_gate.sh" in basenames:
        return True
    if command in {"node", "npx", "remotion", "hyperframes"}:
        for index, token in enumerate(cleaned[:-1]):
            if (token in {"remotion", "hyperframes"}
                    and cleaned[index + 1] == "render"):
                return True
        if command in {"remotion", "hyperframes"} and "render" in cleaned:
            return True
    return False


def heavy_jobs() -> list:
    """Machine-heavy processes from other lanes. One heavy job at a time."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True,
            check=False, encoding="utf-8", timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"eval: cannot inspect active heavy work: {exc}") from exc
    jobs = []
    for line in (out.stdout or "").splitlines():
        fields = line.strip().split(None, 1)
        if fields and fields[0].isdigit() and int(fields[0]) == os.getpid():
            continue
        if _is_heavy_process(line):
            jobs.append(line.strip()[:160])
    return jobs


def memory_free_mb(vm_stat_text: str = "") -> float:
    """Reclaimable memory in MB: free, inactive, and purgeable pages.

    macOS keeps spare memory in file cache, so free pages alone understate
    what a model can use. Pure on given text so the arithmetic is testable;
    reads live `vm_stat` when called bare. Pages are 16K on supported Macs.
    """
    if not vm_stat_text:
        try:
            proc = subprocess.run(
                ["vm_stat"], capture_output=True, check=False,
                encoding="utf-8", timeout=30)
            vm_stat_text = proc.stdout or ""
        except (OSError, subprocess.SubprocessError):
            return 0.0
    pages = 0
    for line in vm_stat_text.splitlines():
        match = re.match(r"Pages (?:free|inactive|purgeable):\s+(\d+)",
                         line)
        if match:
            pages += int(match.group(1))
    return pages * 16384 / 1048576


def wait_for_quiet(timeout_seconds: int = 6 * 3600,
                   min_free_mb: float = 2048.0) -> None:
    """Wait while another lane's model, render or gate is running.

    Heavy means processes AND memory: a quiet ps with 100 MB free still
    OOM-kills the run. Both must clear before the Resolve lock is taken.
    """
    start = time.time()
    while True:
        jobs = heavy_jobs()
        free = memory_free_mb()
        if not jobs and free >= min_free_mb:
            return
        if time.time() - start > timeout_seconds:
            raise TimeoutError(
                f"machine still heavy after {timeout_seconds}s: "
                f"jobs={jobs} free_mb={free:.0f}")
        if jobs:
            print(f"eval: waiting on heavy job: {jobs[0]}", flush=True)
        else:
            print(f"eval: waiting on memory: {free:.0f} MB free "
                  f"(need {min_free_mb:.0f})", flush=True)
        time.sleep(60)


def take_resolve_lock(owner: str, timeout_seconds: int = 3600) -> None:
    """The mkdir lock, first. Waits while held; the owner file says by whom."""
    start = time.time()
    while True:
        try:
            LOCK_DIR.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            holder = ""
            try:
                holder = (LOCK_DIR / "owner").read_text(
                    encoding="utf-8").strip()
            except OSError:
                pass
            if time.time() - start > timeout_seconds:
                raise TimeoutError(
                    f"resolve lock held past timeout by: {holder}")
            print(f"eval: resolve lock held ({holder}); waiting",
                  flush=True)
            time.sleep(20)
    (LOCK_DIR / "owner").write_text(owner + "\n", encoding="utf-8")


def release_resolve_lock() -> None:
    """rmdir after: the lock is a directory, removal is the release."""
    try:
        owner = LOCK_DIR / "owner"
        if owner.exists():
            owner.unlink()
        LOCK_DIR.rmdir()
    except OSError as exc:
        raise RuntimeError(f"eval: could not release resolve lock: {exc}")


# ── Resolve session bracket (captain-safe) ───────────────────────────

def resolve_bracket_start(scratch: str) -> dict:
    """Save the captain's open project first, open the scratch project.

    Refuses a scratch name outside the eval prefix: the bracket deletes
    this project at the end, and it must never be the captain's.
    """
    if not scratch.startswith(SCRATCH_PREFIX):
        raise ValueError(f"scratch project must start with "
                         f"{SCRATCH_PREFIX!r}: {scratch!r}")
    from library.tools.marker_feedback import connect_resolve
    from library.tools.resolve_lock import resolve_lease

    resolve = connect_resolve()
    with resolve_lease(f"open eval scratch {scratch}"):
        manager = resolve.GetProjectManager()
        current = manager.GetCurrentProject()
        if not current:
            raise RuntimeError(
                "eval: Resolve has no open captain project to save and "
                "restore")
        timeline = current.GetCurrentTimeline()
        if not timeline:
            raise RuntimeError(
                "eval: the open captain project has no current timeline "
                "to restore")
        if not manager.SaveProject():
            raise RuntimeError(
                f"eval: could not save open project {current.GetName()!r}")
        state = {
            "project": current.GetName(),
            "timeline": timeline.GetName(),
            "saved": True,
        }
        if (state["project"] or "").startswith(SCRATCH_PREFIX):
            print("eval: current project is already an eval scratch; "
                  "captain record kept from the earlier start", flush=True)
        listed = manager.GetProjectListInCurrentFolder() or []
        project = (manager.LoadProject(scratch) if scratch in listed
                   else manager.CreateProject(scratch))
        if not project:
            raise RuntimeError(
                f"eval: could not open scratch project {scratch}")
        manager.SaveProject()
        return state


def resolve_bracket_end(scratch: str, state: dict) -> dict:
    """Restore the captain's project and timeline, delete the scratch."""
    if not scratch.startswith(SCRATCH_PREFIX):
        raise ValueError(f"refusing to delete non-scratch project: {scratch}")
    from library.tools.marker_feedback import connect_resolve
    from library.tools.resolve_lock import cursor_fence, resolve_lease

    resolve = connect_resolve()
    with resolve_lease(f"restore after eval scratch {scratch}"):
        manager = resolve.GetProjectManager()
        current = manager.GetCurrentProject()
        if current and current.GetName() == scratch:
            manager.SaveProject()
        project_name = state.get("project")
        if not project_name or not state.get("timeline"):
            raise RuntimeError(
                "eval: no saved captain project and timeline to restore")
        back = (current if current and current.GetName() == project_name
                else manager.LoadProject(project_name))
        timeline_ok = False
        if back:
            for index in range(1, (back.GetTimelineCount() or 0) + 1):
                candidate = back.GetTimelineByIndex(index)
                if candidate.GetName() == state["timeline"]:
                    with cursor_fence(
                            back, candidate,
                            f"restore eval timeline {state['timeline']}"):
                        pass
                    timeline_ok = True
                    break
        deleted = (manager.DeleteProject(scratch)
                   if scratch in (manager.GetProjectListInCurrentFolder()
                                  or [])
                   else "absent")
    return {"project_restored": bool(back), "timeline_restored": timeline_ok,
            "scratch_deleted": deleted}


# ── the run loop ─────────────────────────────────────────────────────

def _dag_node_ids() -> set:
    import json as _json

    dag = _json.loads((REPO_ROOT / "library" / "processes" / "edit_video"
                       / "dag.json").read_text(encoding="utf-8"))
    return {node["id"] for node in dag["nodes"]}


def check_edit_chain(chain=EDIT_RERUN_CHAIN) -> None:
    """Every rerun name is a real DAG node, or the run refuses to start."""
    nodes = _dag_node_ids()
    unknown = [name for name in chain if name not in nodes]
    if unknown:
        raise ValueError(f"eval edit chain names no DAG node: {unknown}")


def pin_fixture_preflight_hashes(state: dict, current_hashes: dict) -> list:
    """Keep completed fixture analysis as a fixed input to each eval run.

    A fixture is a frozen analysis snapshot. Its step outputs were produced
    before this eval run, and are deliberately not the subject being scored;
    a changed preflight implementation must not quietly turn a one-request
    eval into a fresh vision pass. Record the original and pinned hashes in
    the returned clone ledger so the report can say which snapshot it used.
    Source identity is still checked separately against the cloned media.
    """
    completed = state.get("preflight_completed") or {}
    recorded = state.get("preflight_code_hashes") or {}
    pinned = []
    for step_id in sorted(completed):
        current = current_hashes.get(step_id)
        if current is None or recorded.get(step_id) == current:
            continue
        pinned.append({"step_id": step_id,
                       "fixture_hash": recorded.get(step_id),
                       "eval_hash": current})
        recorded[step_id] = current
    if pinned:
        state["preflight_code_hashes"] = recorded
    return pinned


def current_preflight_code_hashes() -> dict:
    """Hash edit_video's declared preflight steps as the pipeline does."""
    from library.processes.edit_video import run_pipeline

    dag = run_pipeline.load_dag()
    nodes = {node["id"]: node for node in dag["nodes"]}
    manifests = run_pipeline._manifest_map(nodes)
    stages = run_pipeline._stage_map(manifests)
    directories = {
        node_id: str(run_pipeline.get_step_dir(nodes[node_id]))
        for node_id, stage in stages.items()
        if stage == "preflight"
    }
    return run_pipeline.code_identity.code_hashes_for(
        directories, repo_root=run_pipeline.LIBRARY_ROOT.parent)


def detect_recorded_root(base: str) -> str:
    """The absolute tree the base's own state points at.

    A base cloned from an analysis host (like the scout's `p001b_base`,
    copied off `p001b`) records the HOST's paths, not its own: rewriting
    only the base path rewrites nothing. Existing absolute paths vote
    first. If the host tree is absent, a majority of references spanning
    multiple project-root directories identifies the recorded root from
    the copied state. Returns "" when no root is evidenced.
    """
    strings: list = []
    for name in ("pipeline_data.json", "project.yaml"):
        path = Path(base) / name
        if path.is_file():
            strings += re.findall(r"/[^\"'\s]*", path.read_text(
                encoding="utf-8"))
    references = [s for s in strings
                  if s.startswith("/") and len(Path(s).parts) >= 3]
    existing = [s for s in references if os.path.exists(s)]
    # Majority vote, not common path: a few stray refs (tmp files, the
    # machine's music library) must not drag the root up to `/`.
    votes: dict = {}
    for ref in existing:
        node = Path(ref)
        ancestors = ([node] if node.is_dir() else [node.parent])
        ancestors += list(ancestors[0].parents)
        for parent in ancestors:
            text_parent = str(parent)
            if text_parent in ("/", ""):
                break
            votes[text_parent] = votes.get(text_parent, 0) + 1
    threshold = len(existing) / 2
    candidates = [(len(root.split("/")), root) for root, count
                  in votes.items()
                  if count >= threshold and os.path.isdir(root)
                  and os.path.realpath(root) != os.path.realpath(
                      os.path.abspath(base))
                  # The root is the tree the base was built from - a
                  # project root, not the busiest artifact dir (subtitle
                  # segments would otherwise outvote the project).
                  and any(os.path.isfile(os.path.join(root, marker))
                          for marker in ("pipeline_data.json",
                                         "project.yaml"))]
    if not candidates:
        # A fresh machine has the base but not the host project path. Infer
        # that root only when a majority of recorded paths spans distinct
        # top-level project directories; a busy cache subtree alone cannot
        # outvote the project's raw/media roots.
        root_dirs = {"raw", "music", "assets", "exports", "pipeline_output"}
        votes: dict = {}
        evidence: dict = {}
        for ref in references:
            node = Path(ref)
            for parent in node.parents:
                if str(parent) in ("/", ""):
                    break
                relative = node.relative_to(parent)
                child = relative.parts[0]
                if child in root_dirs:
                    key = str(parent)
                    votes[key] = votes.get(key, 0) + 1
                    evidence.setdefault(key, set()).add(child)
        threshold = len(references) / 2
        inferred = [(len(root.split("/")), root)
                    for root, count in votes.items()
                    if count >= threshold and len(evidence[root]) >= 2
                    and os.path.realpath(root) != os.path.realpath(
                        os.path.abspath(base))]
        if not inferred:
            return ""
        return max(inferred)[1]
    return max(candidates)[1]


def rewrite_eval_resolve_binding(text: str, project_name: str,
                                 timeline_name: str) -> str:
    """Bind the clone to the exact Resolve scratch project and timeline."""
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if "\r\n" in text else "\n"
    resolve_index = next((index for index, line in enumerate(lines)
                          if re.match(r"^resolve:[ \t]*(?:#.*)?(?:\r?\n)?$",
                                      line)), None)
    if resolve_index is None:
        suffix = text.rstrip("\r\n")
        separator = newline * 2 if suffix else ""
        return (f"{suffix}{separator}resolve:{newline}"
                f'  project_name: "{project_name}"{newline}'
                f'  timeline_name: "{timeline_name}"{newline}')

    block_end = len(lines)
    for index in range(resolve_index + 1, len(lines)):
        line = lines[index]
        if (line.strip() and not line.lstrip().startswith("#")
                and not line[0].isspace()):
            block_end = index
            break
    block = lines[resolve_index + 1:block_end]
    indent = next((match.group(1) for line in block
                   if (match := re.match(r"^([ \t]+)\S", line))), "  ")
    values = {"project_name": project_name,
              "timeline_name": timeline_name}
    found = set()
    field_re = re.compile(
        r"^([ \t]+(project_name|timeline_name)[ \t]*:[ \t]*)"
        r"(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^#\s\r\n]+)?"
        r"([ \t]*(?:#.*)?)(\r?\n?)$")
    for index, line in enumerate(block):
        match = field_re.match(line)
        if not match:
            continue
        key = match.group(2)
        block[index] = (f'{match.group(1)}"{values[key]}"'
                        f"{match.group(3)}{match.group(4)}")
        found.add(key)
    insert_at = 0
    for key, value in values.items():
        if key not in found:
            block.insert(insert_at, f'{indent}{key}: "{value}"{newline}')
            insert_at += 1
    lines[resolve_index + 1:block_end] = block
    return "".join(lines)


def clone_base(base: str, dest: str, run_tag: str,
               extra_rewrites: tuple = ()) -> dict:
    """Clone the pristine base: copy the tree, rewrite the paths.

    The base is read-only to the harness: the copy is made first and only
    the copy is ever written. `cp -cR` keeps it an APFS clone (cheap);
    anything else falls back to a full copy. Two rewrites run: the base
    path itself, and the recorded root the base's state points at (see
    `detect_recorded_root`) - plus any explicit OLD=NEW pairs. Returns
    the clone ledger, including refs that still miss on disk.
    """
    # Resolve aliases such as macOS `/tmp` -> `/private/tmp` before
    # rewriting the base's recorded media paths.  The pipeline resolves its
    # project root before enumerating footage; if these two spellings differ,
    # source identity sees every cloned file as a replacement and discards
    # the analysis this fixture is meant to preserve.
    base_path, dest_path = Path(base).resolve(), Path(dest).resolve()
    if not (base_path / "pipeline_data.json").is_file():
        raise ValueError(f"base has no pipeline_data.json: {base}")
    if dest_path.exists():
        raise FileExistsError(f"eval dest exists (pass --force to redo): {dest}")
    try:
        subprocess.run(["cp", "-cR", str(base_path), str(dest_path)],
                       check=True, capture_output=True, encoding="utf-8",
                       timeout=1800)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        shutil.copytree(base_path, dest_path, symlinks=True)
    pairs = [(str(base_path), str(dest_path))]
    recorded = detect_recorded_root(str(base_path))
    if recorded and recorded != str(base_path):
        pairs.append((recorded, str(dest_path)))
    pairs += list(extra_rewrites)
    rewritten = 0
    for old, new in pairs:
        rewritten += rewrite_tree_for_clone(dest_path, old, new)
    roots = [new for _, new in pairs]
    missing = _missing_recorded_refs(str(dest_path), roots)
    state_path = dest_path / "pipeline_data.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    pinned_preflight = pin_fixture_preflight_hashes(
        state, current_preflight_code_hashes())
    if pinned_preflight:
        state_path.write_text(json.dumps(state, indent=2) + "\n",
                              encoding="utf-8")
    timeline = f"EVAL_{run_tag}"
    project_yaml = dest_path / "project.yaml"
    if project_yaml.is_file():
        text = project_yaml.read_text(encoding="utf-8")
        text = re.sub(r'timeline_name:\s*"[^"]*"',
                      f'timeline_name: "{timeline}"', text)
        text = rewrite_eval_resolve_binding(
            text, f"{SCRATCH_PREFIX}{run_tag}", timeline)
        project_yaml.write_text(text, encoding="utf-8")
    answers = dest_path / "eval_answers"
    return {"base": str(base_path), "dest": str(dest_path),
            "timeline": timeline, "files_rewritten": rewritten,
            "recorded_root": recorded, "missing_refs": missing,
            "preflight_hashes_pinned": pinned_preflight,
            "answers": str(answers)}


def seed_answers(answers_src: str, answers_dir: str, old_base: str,
                 new_base: str, path_rewrites: tuple = ()) -> list:
    """Seed the run's answers from a base-answers dir (outside git).

    Untouched steps auto-answer from the base's own answers, so the
    brain only writes steps the request should change: delete the stored
    file for each of those before running. Paths inside answers are
    rewritten to the clone. Returns the seeded step names.
    """
    seeded = []
    dest = Path(answers_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for stored in sorted(Path(answers_src).glob("*.json")):
        text = rewrite_text(old_base, new_base,
                            stored.read_text(encoding="utf-8"))
        for old_path, new_path in path_rewrites:
            text = rewrite_text(old_path, new_path, text)
        (dest / stored.name).write_text(text, encoding="utf-8")
        seeded.append(stored.stem)
    return seeded


def _missing_recorded_refs(tree: str, roots: list) -> list:
    """Recorded paths under the rewritten roots that still miss on disk.

    Only refs under a rewritten root are reported: prompt prose carries
    slashes that are not paths, and a missing export of an older run is
    history either way. Reported, not refused - the run fails loudly on
    a truly missing input.
    """
    refs = set()
    for name in ("pipeline_data.json", "project.yaml"):
        path = Path(tree) / name
        if path.is_file():
            refs.update(re.findall(r"/[^\"'\s]*", path.read_text(
                encoding="utf-8")))
    under = [r for r in refs
             if r.startswith("/") and not os.path.exists(r)
             and any(r == rt or r.startswith(rt + "/") for rt in roots)]
    # The extractor stops at whitespace and JSON escapes, so a recorded
    # path with a space (`Sickick- Infected ...`) arrives truncated and a
    # `\n` escape glues prose on. A candidate that is a strict prefix of
    # something on disk, or carries a backslash, is an artifact, not a
    # missing file.
    import glob as _glob

    return sorted(
        r for r in under
        if "\\" not in r and not _glob.glob(r + "*"))[:20]


def _watch_log_for_request(log_path: Path, seen: int):
    """New LLM_REQUEST_READY lines since `seen`."""
    found = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return seen, found
    markers = [line for line in lines if "LLM_REQUEST_READY" in line]
    for marker in markers[seen:]:
        match = re.search(r"LLM_REQUEST_READY:\s*(\S+)", marker)
        if not match:
            continue
        request_path = match.group(1)
        found.append((Path(request_path).stem.replace(".json", ""),
                      request_path))
    return len(markers), found


def decide_answer(step: str, target_exists: bool, stored_exists: bool,
                  served: set) -> str:
    """What the answer loop does about one model request.

    `copy-stored`: first request and a base answer exists. `have-target`:
    the response is already there (brain or earlier copy). `needs-brain`:
    no stored answer - or the step asks AGAIN after being served, which
    means the stored answer was refused and feeding it twice would loop
    the refusal forever.
    """
    if target_exists:
        return "have-target"
    if stored_exists and step not in served:
        return "copy-stored"
    return "needs-brain"


def parse_run_status(log_text: str) -> str | None:
    """The run summary's status (the LAST one printed), or None."""
    matches = re.findall(r'"status":\s*"([A-Z_]+)"', log_text)
    return matches[-1] if matches else None


def answer_loop(log_path: Path, project_dir: str, answers_dir: str,
                answered_by_harness: set,
                answer_timeout_seconds: int = 3 * 3600,
                allow_partial: bool = False) -> dict:
    """Serve the run's model requests: stored answers auto, else the brain.

    A step whose answer sits in `answers_dir` is answered from disk (the
    base's own answer - the behaviour the request did not touch). A step
    with no stored answer - or one whose stored answer the run refused
    and asked again for - stops the loop LOUDLY: the operator - the
    product's own model, which is what makes host-answered runs the
    understanding upper bound - writes the response file, and the loop
    resumes. A FAILED summary raises: the edit never proceeds to a build.
    A scoped stage may opt into PARTIAL here, then must verify its completed
    steps against `pipeline_run.json` before it proceeds.
    Returns the fresh-answer ledger.
    """
    project_path = Path(project_dir)
    responses = project_path / "pipeline_output" / "llm_responses"
    fresh: list = []
    seen = 0
    start = time.time()
    while True:
        if time.time() - start > answer_timeout_seconds:
            raise TimeoutError("eval: answer loop timed out waiting "
                               "for the run or the brain")
        try:
            text = log_path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        status = parse_run_status(text)
        if status is not None:
            if status == "SUCCESS" or (allow_partial and status == "PARTIAL"):
                return {"run_status": status,
                        "fresh_answers": fresh,
                        "auto_answered": sorted(answered_by_harness)}
            raise RuntimeError(
                f"eval: run ended {status} - see log tail:\n"
                + "\n".join(text.splitlines()[-15:]))
        if re.search(r"✗ FAILED|Traceback", text):
            raise RuntimeError("eval: run failed - see log tail:\n"
                               + "\n".join(text.splitlines()[-15:]))
        seen, requests = _watch_log_for_request(log_path, seen)
        for step, request_path in requests:
            target = responses / f"{step}.json"
            stored = Path(answers_dir) / f"{step}.json"
            action = decide_answer(step, target.is_file(),
                                   stored.is_file(), answered_by_harness)
            if action == "copy-stored":
                shutil.copy(stored, target)
                answered_by_harness.add(step)
                print(f"eval: auto-answered {step}", flush=True)
            elif action == "needs-brain":
                why = ("no stored answer" if not stored.is_file()
                       else "stored answer refused, asks again")
                print(f"eval: NEEDS BRAIN ({why}): {step}\n"
                      f"  request: {request_path}\n"
                      f"  write the answer to: {target}\n"
                      f"  (waiting up to "
                      f"{answer_timeout_seconds // 3600}h)", flush=True)
        # A freshly written brain answer is recorded on the next pass.
        for step, _ in requests:
            target = responses / f"{step}.json"
            if (target.is_file() and step not in answered_by_harness
                    and step not in fresh):
                fresh.append(step)
        time.sleep(5)


def pipeline_summary_from_log(log_text: str) -> dict:
    """Read the run summary printed by the pipeline, not run-control state."""
    required = {"status", "completed", "failed", "outstanding_failures",
                "stranded_failures", "skipped"}
    decoder = json.JSONDecoder()
    summaries = []
    for match in re.finditer(r"(?m)^\{\s*$", log_text):
        try:
            value, _ = decoder.raw_decode(log_text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and required <= value.keys():
            summaries.append(value)
    if not summaries:
        raise ValueError("pipeline log has no complete run summary")
    return summaries[-1]


def validate_scoped_run(log_path: str, stage: str,
                        expected_steps, expected_skipped,
                        run_status: str) -> dict:
    """Accept PARTIAL only when all requested scoped work completed.

    The pipeline calls an invocation PARTIAL when it deliberately skips work
    outside its scope. The eval still refuses missing requested steps, failed
    work, or any unexpected skip before it can build or score the run.
    """
    try:
        summary = pipeline_summary_from_log(
            Path(log_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"eval: {stage} stage has no readable pipeline summary in "
            f"{log_path}: {exc}") from exc

    summary_status = summary["status"].upper()
    if summary_status not in {"SUCCESS", "PARTIAL"}:
        raise RuntimeError(
            f"eval: {stage} stage ended {summary_status}, not a completed "
            "scoped run")
    if run_status != summary_status:
        raise RuntimeError(
            f"eval: {stage} status disagrees: log says {run_status}, "
            f"pipeline summary says {summary_status}")

    failed = summary["failed"]
    outstanding = summary["outstanding_failures"]
    stranded = summary["stranded_failures"]
    if failed or outstanding or stranded:
        raise RuntimeError(
            f"eval: {stage} stage recorded failures: failed={failed}, "
            f"outstanding={outstanding}, stranded={stranded}")

    completed = set(summary["completed"])
    missing_steps = set(expected_steps) - completed
    if missing_steps:
        raise RuntimeError(
            f"eval: {stage} stage did not complete required steps: "
            f"{sorted(missing_steps)}")

    skipped = set(summary["skipped"])
    expected_skipped = set(expected_skipped)
    if skipped != expected_skipped:
        raise RuntimeError(
            f"eval: {stage} stage skipped {sorted(skipped)}, expected "
            f"{sorted(expected_skipped)}")
    return {"completed_steps": sorted(completed),
            "skipped_steps": sorted(skipped)}


def run_pipeline_edit(project_dir: str, log_path: str, answers_dir: str,
                      extra_reruns=()) -> dict:
    """The edit stage: rerun the chain, skip render and validate."""
    check_edit_chain()
    reruns: list = []
    for name in (*extra_reruns, *EDIT_RERUN_CHAIN):
        reruns += ["--rerun", name]
    log = Path(log_path)
    log.write_text("", encoding="utf-8")
    with open(log, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [str(VEP), str(MANAGE_PROJECT), "run", project_dir, *reruns,
             "--skip", "render", "--skip", "validate", "--full-auto",
             "agent", "--llm-timeout", "3600"],
            stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=str(REPO_ROOT))
        try:
            ledger = answer_loop(log, project_dir, answers_dir, set(),
                                 allow_partial=True)
        finally:
            if proc.poll() is None:
                proc.wait(timeout=600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"eval: edit stage did not succeed "
            f"(exit {proc.returncode}, status "
            f"{ledger.get('run_status')}) - see {log}")
    checked = validate_scoped_run(
        str(log), "edit", (*extra_reruns, *EDIT_RERUN_CHAIN),
        ("render", "validate"), ledger["run_status"])
    return {"exit": proc.returncode, **ledger, **checked}


def run_pipeline_render(project_dir: str, log_path: str,
                        answers_dir: str) -> dict:
    """The build: the render step only, under the caller's Resolve lock."""
    log = Path(log_path)
    log.write_text("", encoding="utf-8")
    with open(log, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [str(VEP), str(MANAGE_PROJECT), "run", project_dir,
             "--step", "render", "--rerun", "render",
             "--full-auto", "agent", "--llm-timeout", "3600"],
            stdout=log_file, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, cwd=str(REPO_ROOT))
        try:
            ledger = answer_loop(log, project_dir, answers_dir, set(),
                                 allow_partial=True)
        finally:
            if proc.poll() is None:
                proc.wait(timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"eval: render step did not succeed "
            f"(exit {proc.returncode}, status "
            f"{ledger.get('run_status')}) - see {log}")
    checked = validate_scoped_run(
        str(log), "render", ("render",), (), ledger["run_status"])
    return {"exit": proc.returncode, **ledger, **checked}


def readback_timeline(scratch: str, timeline: str, out_path: str) -> str:
    """Read the built timeline back: items, fusion, audio, markers."""
    out: list = []
    for argv in (["items", "--transforms"], ["fusion"], ["audio", "--full"],
                 ["markers"]):
        proc = subprocess.run(
            [str(RESOLVE_AXI, )] + argv
            + ["--project", scratch, "--timeline", timeline],
            capture_output=True, check=False, encoding="utf-8",
            stdin=subprocess.DEVNULL,
            timeout=600, cwd=str(REPO_ROOT))
        out += [f"## {' '.join(argv)}", proc.stdout, proc.stderr or ""]
    Path(out_path).write_text("\n".join(out), encoding="utf-8")
    return out_path


# ── export measures (track B) ────────────────────────────────────────

def _ffprobe_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, check=False,
        encoding="utf-8", timeout=120)
    return float((proc.stdout or "0").strip() or 0)


def frame_stats(video: str, timestamps: list) -> list:
    """Mean luma, RGB and chroma at each timestamp, via ffmpeg plus numpy.

    Chauffeured by numpy, not PIL: the numbers must be recomputable on a
    fresh machine from requirements.txt alone.
    """
    import numpy as np

    size = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type,width,height", "-of", "json",
         video],
        capture_output=True, check=False, encoding="utf-8", timeout=120)
    if size.returncode != 0:
        raise RuntimeError(f"eval: ffprobe could not read video dimensions: "
                           f"{size.stderr.strip()}")
    try:
        streams = json.loads(size.stdout).get("streams", [])
        video_streams = [row for row in streams
                         if row.get("codec_type") == "video"]
        candidates = video_streams or streams
        stream = next(row for row in candidates
                      if row.get("width") and row.get("height"))
        width, height = int(stream["width"]), int(stream["height"])
    except (json.JSONDecodeError, StopIteration, TypeError, ValueError,
            KeyError) as exc:
        raise RuntimeError("eval: ffprobe returned no video dimensions") from exc

    rows = []
    for stamp in timestamps:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{stamp}", "-i", video,
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=False, timeout=180)
        if proc.returncode != 0 or not proc.stdout:
            raise RuntimeError(f"eval: could not grab frame at {stamp}s "
                               f"of {video}")
        try:
            frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(
                height, width, 3).astype(float)
        except ValueError as exc:
            raise RuntimeError(
                f"eval: decoded frame at {stamp}s does not match "
                f"ffprobe dimensions {width}x{height}") from exc
        r, g, b = frame[..., 0], frame[..., 1], frame[..., 2]
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
        cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
        chroma = float(np.sqrt((cb - 128.0) ** 2
                               + (cr - 128.0) ** 2).mean())
        peak, floor = frame.max(axis=2), frame.min(axis=2)
        saturation = float(((peak - floor) / (peak + 1e-6)).mean())
        rows.append({"t": round(stamp, 3),
                     "luma": round(float(luma.mean()), 1),
                     "rgb": [round(float(r.mean())),
                             round(float(g.mean())),
                             round(float(b.mean()))],
                     "chroma": round(chroma, 2),
                     "saturation": round(saturation, 3)})
    return rows


def _gray_export_frame(video: str, stamp: float):
    """Decode a small grayscale export frame for relative zoom matching."""
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{stamp}", "-i", video,
         "-vf", "scale=640:360:flags=area,format=gray", "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=False, timeout=180)
    if proc.returncode != 0 or len(proc.stdout) != 640 * 360:
        raise RuntimeError(f"could not decode zoom frame at {stamp}s")
    return np.frombuffer(proc.stdout, dtype=np.uint8).reshape(360, 640)


def estimate_relative_zoom(base_video: str, run_video: str,
                           timestamps: list) -> dict:
    """Estimate scale changes between corresponding export frames.

    ORB feature matches and a RANSAC partial-affine fit keep this measure
    on rendered pixels. A cut or unrelated image is explicitly unmeasured
    instead of being reported as a zoom change.
    """
    try:
        import cv2
    except ImportError as exc:
        return {"measured": False,
                "reason": f"OpenCV unavailable for export matching: {exc}"}

    import numpy as np

    orb = cv2.ORB_create(nfeatures=1200)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    rows = []
    for stamp in timestamps:
        try:
            base = _gray_export_frame(base_video, stamp)
            run = _gray_export_frame(run_video, stamp)
            base_points, base_desc = orb.detectAndCompute(base, None)
            run_points, run_desc = orb.detectAndCompute(run, None)
            if base_desc is None or run_desc is None:
                raise ValueError("one frame has no detectable features")
            pairs = matcher.knnMatch(base_desc, run_desc, k=2)
            good = [pair[0] for pair in pairs
                    if len(pair) == 2
                    and pair[0].distance < 0.75 * pair[1].distance]
            if len(good) < 12:
                raise ValueError(f"only {len(good)} feature matches")
            source = np.float32(
                [base_points[m.queryIdx].pt for m in good])
            target = np.float32(
                [run_points[m.trainIdx].pt for m in good])
            transform, inlier_mask = cv2.estimateAffinePartial2D(
                source, target, method=cv2.RANSAC,
                ransacReprojThreshold=3.0, maxIters=2000,
                confidence=0.99, refineIters=10)
            inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
            if transform is None or inliers < 8:
                raise ValueError(f"affine fit had only {inliers} inliers")
            inlier_ratio = inliers / len(good)
            if inlier_ratio < 0.25:
                raise ValueError(f"affine fit inlier ratio {inlier_ratio:.2f}")
            scale = float(np.hypot(transform[0, 0], transform[1, 0]))
            rows.append({"t": round(stamp, 3), "measured": True,
                         "relative_scale": round(scale, 4),
                         "inliers": inliers, "matches": len(good),
                         "inlier_ratio": round(inlier_ratio, 3)})
        except (cv2.error, OSError, RuntimeError, ValueError,
                subprocess.SubprocessError) as exc:
            rows.append({"t": round(stamp, 3), "measured": False,
                         "reason": str(exc)})
    measured = any(row["measured"] for row in rows)
    return {"measured": measured, "rows": rows,
            **({} if measured else {
                "reason": "no sampled frame pair supported a reliable "
                          "same-image zoom estimate"})}


def _separation_inputs(project_dir: str) -> dict:
    """Read the run's declared bed, automation and section from its state."""
    path = Path(project_dir) / "pipeline_data.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        outputs = state.get("step_outputs", {})
        compiled = outputs.get("compile_manifest", {}).get(
            "assembly_manifest", {})
        mix = compiled.get("audio_mix", {})
        selection = outputs.get("music_selection", {}).get(
            "music_selection", {})
    except (OSError, ValueError, TypeError) as exc:
        return {"reason": f"could not read mix state: {exc}"}

    automation = mix.get("music_automation") or []
    bed = mix.get("bed") or {}
    music_path = bed.get("audio_path")
    if not automation:
        return {"reason": "the compiled mix has no music automation windows"}
    if not music_path or not Path(music_path).is_file():
        return {"reason": f"compiled music bed is not on disk: {music_path}"}
    splices = selection.get("splices") or []
    if len(splices) > 1:
        return {"reason": "multiple music splices need piecewise source "
                          "alignment; the current export fit accepts one"}
    section = selection.get("section") or {}
    offset = (splices[0].get("source_in") if splices
              else section.get("source_in", 0.0))
    if not isinstance(offset, (int, float)):
        return {"reason": f"music source offset is not numeric: {offset!r}"}
    return {"music_path": music_path, "automation": automation,
            "offset": float(offset),
            "spine_blocks": compiled.get("_spine_blocks") or []}


def _measure_export_separation(video: str, project_dir: str) -> dict:
    inputs = _separation_inputs(project_dir)
    if "reason" in inputs:
        return {"measured": False, "reason": inputs["reason"]}
    try:
        from library.tools import render_qa

        result = render_qa.measure_speech_above_bed(
            video, inputs["music_path"], inputs["automation"],
            inputs["offset"], spine_blocks=inputs["spine_blocks"],
            gate=False)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError,
            subprocess.SubprocessError) as exc:
        return {"measured": False,
                "reason": f"speech-over-bed measurement failed: {exc}"}
    value = result.value if isinstance(result.value, dict) else None
    if not value or not value.get("windows"):
        return {"measured": False,
                "reason": result.detail or "measurement returned no windows"}
    return {"measured": True, "judged": value.get("judged", 0),
            "failing": value.get("failing", 0),
            "music_offset_seconds": value.get("music_offset_seconds"),
            "windows": [{key: window.get(key) for key in (
                "timeline_start", "timeline_end", "music_behavior",
                "margin_db", "required_margin_db", "required_margin_basis",
                "correlation", "judged", "meets_plan")}
                for window in value["windows"]],
            "detail": result.detail}


def measure_exports(base_video: str, run_video: str,
                    base_project: str = "", run_project: str = "") -> dict:
    """Track B measures, base vs run at the same timestamps.

    A measure that cannot run records itself unmeasured with the reason -
    a missing number must never read as a passing one.
    """
    measures: dict = {"hunks": []}
    duration = None
    try:
        duration = min(_ffprobe_duration(base_video),
                       _ffprobe_duration(run_video))
        stamps = [round(duration * f, 3) for f in (0.1, 0.5, 0.9)]
        base_rows = frame_stats(base_video, stamps)
        run_rows = frame_stats(run_video, stamps)
        measures["frames"] = {
            "measured": True, "timestamps": stamps,
            "rows": [{"t": b["t"], "base": {
                          "luma": b["luma"], "rgb": b["rgb"],
                          "chroma": b["chroma"],
                          "saturation": b["saturation"]},
                      "run": {"luma": r["luma"], "rgb": r["rgb"],
                              "chroma": r["chroma"],
                              "saturation": r["saturation"]}}
                     for b, r in zip(base_rows, run_rows)]}
    except (ImportError, OSError, RuntimeError, TypeError, ValueError,
            subprocess.SubprocessError) as exc:
        measures["frames"] = {"measured": False,
                              "reason": f"frame stats failed: {exc}"}
    try:
        from library.tools import render_qa

        base_lufs = render_qa.measure_lufs(base_video)
        run_lufs = render_qa.measure_lufs(run_video)
        base_value = (base_lufs.value.get("input_i")
                      if isinstance(base_lufs.value, dict) else None)
        run_value = (run_lufs.value.get("input_i")
                     if isinstance(run_lufs.value, dict) else None)
        measures["lufs"] = {
            "measured": base_value is not None and run_value is not None,
            "base_lufs": base_value, "run_lufs": run_value,
            "reason": ("loudnorm returned no integrated reading"
                       if base_value is None or run_value is None else "")}
    except (ImportError, OSError, RuntimeError, TypeError, ValueError,
            subprocess.SubprocessError) as exc:
        measures["lufs"] = {"measured": False,
                            "reason": f"LUFS failed: {exc}"}
    if duration is not None:
        zoom_stamps = [round(duration * f, 3) for f in (0.1, 0.5, 0.9)]
        measures["zoom"] = estimate_relative_zoom(
            base_video, run_video, zoom_stamps)
    else:
        measures["zoom"] = {
            "measured": False,
            "reason": "export duration was unavailable for zoom sampling"}
    if base_project and run_project:
        base_separation = _measure_export_separation(
            base_video, base_project)
        run_separation = _measure_export_separation(run_video, run_project)
        measures["separation"] = {
            "measured": (base_separation.get("measured", False)
                         or run_separation.get("measured", False)),
            "base": base_separation, "run": run_separation,
            **({} if (base_separation.get("measured")
                     or run_separation.get("measured")) else {
                "reason": ("base: " + base_separation.get("reason", "")
                           + "; run: "
                           + run_separation.get("reason", ""))})}
    else:
        measures["separation"] = {
            "measured": False,
            "reason": "project state paths were not supplied"}
    return measures


def find_export(project_dir: str) -> str:
    """The run's render output: newest mp4 under the project's exports."""
    candidates = sorted(Path(project_dir).rglob("exports/**/*.mp4"),
                        key=lambda p: p.stat().st_mtime)
    if not candidates:
        raise FileNotFoundError(f"eval: no export under {project_dir}")
    return str(candidates[-1])


# ── per-request run ──────────────────────────────────────────────────

def run_request(request: dict | None, base: str, out_dir: str, batch: str,
                base_readback: str = "", base_export: str = "",
                answers_src: str = "", extra_rewrites: tuple = (),
                force: bool = False,
                answer_timeout_seconds: int = 3 * 3600) -> dict:
    """One request end to end. Returns the run ledger; writes measures plus
    the judgement template. The judgement itself is the operator's.

    `request=None` builds the base reference: no note is injected and no
    judgement template is written.
    """
    request_id = request["id"] if request else "BASE"
    out = Path(out_dir) / request_id
    if out.exists():
        if not force:
            raise FileExistsError(f"eval: {out} exists (pass --force)")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    ledger: dict = {"request_id": request_id, "batch": batch,
                    "started_at": _dt.datetime.now(
                        _dt.UTC).isoformat()}
    clone = clone_base(base, str(out / "run"), batch,
                       extra_rewrites=extra_rewrites)
    ledger["clone"] = clone
    if request is not None:
        pull = inject_request(clone["dest"], request, clone["timeline"])
        ledger["pull"] = pull
    answers = Path(clone["answers"])
    if answers_src:
        path_rewrites = ()
        if clone.get("recorded_root"):
            path_rewrites = ((clone["recorded_root"], clone["dest"]),)
        ledger["seeded_answers"] = seed_answers(
            answers_src, str(answers), clone["base"], clone["dest"],
            path_rewrites=path_rewrites)
    else:
        answers.mkdir(parents=True, exist_ok=True)
    wait_for_quiet()
    edit = run_pipeline_edit(
        clone["dest"], str(out / "edit.log"), str(answers))
    ledger["edit"] = edit
    wait_for_quiet()
    scratch = f"{SCRATCH_PREFIX}{batch}"
    stamp = _dt.datetime.now(_dt.UTC).strftime("%FT%TZ")
    take_resolve_lock(f"ren-eval-harness {batch} {stamp}")
    try:
        captain = resolve_bracket_start(scratch)
        ledger["captain_saved"] = captain
        try:
            build = run_pipeline_render(
                clone["dest"], str(out / "build.log"), str(answers))
            ledger["build"] = build
            timeline = _built_timeline_name(out / "build.log",
                                            clone["timeline"])
            ledger["timeline"] = timeline
            readback_timeline(scratch, timeline, str(out / "readback.txt"))
            ledger["export"] = find_export(clone["dest"])
        finally:
            ledger["restore"] = resolve_bracket_end(scratch, captain)
            restore = ledger["restore"]
            if (not restore.get("project_restored")
                    or not restore.get("timeline_restored")
                    or restore.get("scratch_deleted") is False):
                raise RuntimeError(
                    "eval: Resolve session did not restore the captain's "
                    f"project and timeline cleanly: {restore}")
    finally:
        release_resolve_lock()
    ledger["finished_at"] = _dt.datetime.now(_dt.UTC).isoformat()
    (out / "run.json").write_text(json.dumps(ledger, indent=2)
                                  + "\n", encoding="utf-8")
    measures = build_measures(request_id, out, base_readback, base_export,
                              ledger)
    (out / "measures.json").write_text(json.dumps(measures, indent=2)
                                       + "\n", encoding="utf-8")
    if request is not None:
        (out / "judgement.json").write_text(json.dumps(
            judgement_template(request, measures.get("hunks", [])),
            indent=2) + "\n", encoding="utf-8")
    return ledger


def _built_timeline_name(build_log: Path, fallback: str) -> str:
    """The timeline the render wrote: `EVAL_<batch>_<seconds>s`, else the
    eval timeline name the clone declared."""
    try:
        text = Path(build_log).read_text(encoding="utf-8")
    except OSError:
        return fallback
    names = re.findall(r"EVAL_[A-Za-z0-9_.-]+_\d+s", text)
    return names[-1] if names else fallback


def build_measures(request_id: str, out: Path, base_readback: str,
                   base_export: str, ledger: dict) -> dict:
    """Diff the readback vs base; measure the export vs base export."""
    measures: dict = {"hunks": []}
    if base_readback and Path(base_readback).is_file():
        run_dir = ledger["clone"]["dest"]
        base_project = _base_project_for_readback(
            base_readback, out, ledger["clone"]["base"])
        base_text = normalise_readback(
            Path(base_readback).read_text(encoding="utf-8"),
            base_project, ledger.get("timeline", ""))
        run_text = normalise_readback(
            (out / "readback.txt").read_text(encoding="utf-8"), run_dir,
            ledger.get("timeline", ""))
        measures["hunks"] = timeline_hunks(base_text, run_text)
    else:
        measures["hunks_missing_reason"] = (
            "no base readback: pass --base-readback or build the base first")
    run_export = ledger.get("export", "")
    if base_export and run_export and Path(base_export).is_file():
        base_project = _base_project_for_readback(
            base_readback, out, ledger["clone"]["base"])
        track_b = measure_exports(
            base_export, run_export,
            base_project=base_project,
            run_project=ledger["clone"]["dest"])
        track_b.pop("hunks", None)
        measures.update(track_b)
    else:
        measures["frames"] = {
            "measured": False,
            "reason": "no base export: pass --base-export or build it first"}
        measures["lufs"] = measures["frames"]
    return measures


def _base_project_for_readback(base_readback: str, request_out: Path,
                               fallback: str) -> str:
    """Find the project that produced the reference readback/export.

    The normal eval path writes `<out>/BASE/readback.txt` beside
    `<out>/BASE/run/`. Also accept a reference in that same shape outside
    the current output directory. The pristine input base is only a fallback:
    it may not be the project whose rendered timeline is the reference.
    """
    candidates = [request_out.parent / "BASE" / "run",
                 Path(base_readback).parent / "run"]
    for candidate in candidates:
        if (candidate / "pipeline_data.json").is_file():
            return str(candidate)
    return fallback


def finalize_request(out_dir: str, request_id: str) -> dict:
    """Validate the judgement, derive the verdict, write the report.

    Refuses an incomplete judgement (exit comes from the CLI): finalising
    half a judgement would print a verdict the evidence does not carry.
    """
    out = Path(out_dir) / request_id
    request = eval_corpus.select(request_id=request_id)[0]
    ledger = json.loads((out / "run.json").read_text(encoding="utf-8"))
    measures = json.loads(
        (out / "measures.json").read_text(encoding="utf-8"))
    judgement = json.loads(
        (out / "judgement.json").read_text(encoding="utf-8"))
    problems = check_judgement_complete(judgement, measures.get("hunks", []))
    if problems:
        raise ValueError("judgement incomplete:\n- " + "\n- ".join(problems))
    verdict = derive_verdict([op["outcome"] for op in judgement["ops"]])
    agree = agreement(verdict, request["scout_verdict"],
                      judgement.get("difference", ""))
    report = render_request_report(request, ledger, measures, judgement,
                                   verdict, agree)
    (out / "report.md").write_text(report, encoding="utf-8")
    reviewed_hunks = {str(h.get("index")): h
                      for h in judgement.get("hunks", [])}
    broken = [hunk for hunk in measures.get("hunks", [])
              if (reviewed_hunks.get(str(hunk["index"]), {}).get(
                  "break_reason") or "").strip()]
    return {"request_id": request_id, "domain": request["domain"],
            "level": request["level"], "scout_verdict": request[
                "scout_verdict"], "harness_verdict": verdict,
            "agreement": agree,
            "broke_nothing": "BROKE" if broken else "clean",
            "looks_good": (judgement.get("looks_good") or {}).get(
                "verdict", "unjudged"),
            "looks_good_agreement": (judgement.get("looks_good") or {}).get(
                "captain_label_agreement", "")}


# ── CLI ──────────────────────────────────────────────────────────────

def howto_text() -> str:
    """How to run the harness per rung and weekly. Printed, not scheduled:
    the weekly schedule is out of scope - this text is the runbook."""
    return """ren eval: the standing request-following harness (report only).

Per rung (after the rung lands, on a machine with Resolve standing by):
  ren eval run --base <pristine-base> --out <eval-dir> --rung 7
  # The host model answers each NEEDS BRAIN request from that request's
  # prompt and context. Without --answers, every model step is asked.
  # Optional: --answers <base-answers-dir> reuses stored answers, so remove
  # any step answer that should be reconsidered for this request first.
  ren eval finalize --out <eval-dir> --request <id>   # per request, after judging
  ren eval report --out <eval-dir>                    # REPORT.md + domain x level

Subsets: --request ST1.1 (repeatable), --domain CO, --level 3,
  --cluster K1, --verdict X, --rung 0 (the 40 judged: the regression set),
  --rung 6 (all 120). `ren eval list` prints what a filter selects.
Each readback hunk must name its exact op in `explained_by`, or carry a
`break_reason`; breaks remain reportable and score BROKE. An LLM
look-good judge records `captain_label_agreement` as agree/disagree.

Weekly (not scheduled here - run by hand or from cron on the edit Mac):
  ren eval run --base <base> --out eval-$(date +%F) --rung 0
  Heavy compute runs one job at a time: the harness waits while another
  lane's model, render or gate is running. Resolve is standing-authorized:
  the harness takes ~/.local/share/vep/resolve-driver.lock first, saves
  the captain's open project first, restores project plus timeline after,
  never touches his timelines, and deletes its scratch projects.
  The captain's footage is read-only: the base is cloned, never written.

The base: a built project with analysis done (any checkout that built it),
  passed by path. Completed fixture analysis is reused: each clone pins its
  preflight code hashes to the running checkout and records both hashes in
  `run.json`; source fingerprints still check the cloned media. Fixtures live
  outside git; only this tool ships.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ren eval",
        description="Standing eval: does Ren follow edit requests (report "
                    "only; gates nothing).")
    sub = parser.add_subparsers(dest="command", required=True)
    listed = sub.add_parser("list", help="print what a filter selects")
    _add_filters(listed)
    runner = sub.add_parser("run", help="run requests end to end")
    _add_filters(runner)
    runner.add_argument("--base", required=True,
                        help="pristine base project (analysis done)")
    runner.add_argument("--out", required=True, help="eval dir (new)")
    runner.add_argument("--batch", default="",
                        help="batch tag (default: UTC stamp)")
    runner.add_argument("--base-readback", default="",
                        help="base timeline readback (else the base is built)")
    runner.add_argument("--base-export", default="",
                        help="base export mp4 (else measured as missing)")
    runner.add_argument("--answers", default="",
                        help="base-answers dir to seed untouched steps from "
                             "(outside git); delete a step's file to force "
                             "a fresh brain answer")
    runner.add_argument("--rewrite", action="append", default=[],
                        metavar="OLD=NEW",
                        help="extra path rewrite pair (repeatable); the "
                             "base path and its recorded root rewrite "
                             "without this")
    runner.add_argument("--force", action="store_true",
                        help="redo requests already in --out")
    runner.add_argument("--base-only", action="store_true",
                        help="build the base reference only, then stop")
    final = sub.add_parser("finalize",
                           help="validate a judgement, write the report")
    final.add_argument("--out", required=True)
    final.add_argument("--request", required=True)
    rep = sub.add_parser("report", help="aggregate finalised requests")
    rep.add_argument("--out", required=True)
    sub.add_parser("howto", help="how to run per rung and weekly")
    return parser


def _add_filters(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--request", action="append", default=None)
    parser.add_argument("--domain", action="append", default=None)
    parser.add_argument("--level", action="append", default=None, type=int)
    parser.add_argument("--rung", default=None, type=int)
    parser.add_argument("--cluster", action="append", default=None)
    parser.add_argument("--verdict", action="append", default=None)


def _claim_out_dir(out: Path) -> None:
    """One harness per out dir: a live pidfile refuses a second runner.

    Two harnesses sharing an out dir interleave their logs and answer
    loops and corrupt both runs; the second one must not start.
    """
    pidfile = out / "harness.pid"
    if pidfile.is_file():
        try:
            other = int(pidfile.read_text(encoding="utf-8").strip())
            os.kill(other, 0)
            raise FileExistsError(
                f"eval: harness pid {other} still holds {out}")
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    pidfile.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def _filters(args) -> dict:
    filters = {}
    if args.request:
        filters["request_id"] = (args.request[0] if len(args.request) == 1
                                 else args.request)
    if args.domain:
        filters["domain"] = (args.domain[0] if len(args.domain) == 1
                             else args.domain)
    if args.level:
        filters["level"] = (args.level[0] if len(args.level) == 1
                            else args.level)
    if args.rung is not None:
        filters["rung"] = args.rung
    if args.cluster:
        filters["cluster"] = (args.cluster[0] if len(args.cluster) == 1
                              else args.cluster)
    if args.verdict:
        filters["verdict"] = (args.verdict[0] if len(args.verdict) == 1
                              else args.verdict)
    return filters


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "howto":
        print(howto_text())
        return 0
    if args.command == "list":
        for request in select(**_filters(args)):
            print(f"{request['id']} {request['domain']} L{request['level']} "
                  f"{request['persona']} {request['scout_verdict']}: "
                  f"{request['text']}")
        return 0
    if args.command == "run":
        requests = select(**_filters(args))
        if not requests and not args.base_only:
            print("eval: filter selects nothing", file=sys.stderr)
            return 2
        batch = args.batch or _dt.datetime.now(
            _dt.UTC).strftime("%Y%m%dT%H%M%S")
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        _claim_out_dir(out)
        base_readback, base_export = args.base_readback, args.base_export
        rewrites = []
        for pair in args.rewrite:
            old, _, new = pair.partition("=")
            if not old or not new:
                print(f"eval: bad --rewrite {pair!r} (want OLD=NEW)",
                      file=sys.stderr)
                return 2
            rewrites.append((old, new))
        if not base_readback:
            print("eval: no --base-readback: building the base reference "
                  "first (no note, same build path)", flush=True)
            ledger = run_request(None, args.base, str(out), batch,
                                 answers_src=args.answers,
                                 extra_rewrites=tuple(rewrites),
                                 force=args.force)
            base_readback = str(out / "BASE" / "readback.txt")
            base_export = ledger.get("export", "")
            (out / "base.json").write_text(json.dumps(
                {"readback": base_readback,
                 "export": base_export}, indent=2) + "\n",
                encoding="utf-8")
        else:
            stored = out / "base.json"
            if not stored.is_file():
                stored.write_text(json.dumps(
                    {"readback": base_readback,
                     "export": base_export}, indent=2) + "\n",
                    encoding="utf-8")
        if args.base_only:
            print(f"eval: base reference ready: {base_readback} "
                  f"{base_export}")
            return 0
        failures = 0
        for request in requests:
            try:
                run_request(request, args.base, str(out), batch,
                            base_readback=base_readback,
                            base_export=base_export,
                            answers_src=args.answers,
                            extra_rewrites=tuple(rewrites),
                            force=args.force)
            except Exception as exc:  # noqa: BLE001 - keep running other selected requests
                print(f"eval: {request['id']} FAILED: {exc}",
                      file=sys.stderr)
                failures += 1
        print(f"eval: {len(requests) - failures}/{len(requests)} ran; "
              f"judge each in <out>/<id>/judgement.json, then finalize")
        return 1 if failures else 0
    if args.command == "finalize":
        try:
            entry = finalize_request(args.out, args.request)
        except (ValueError, FileNotFoundError, KeyError) as exc:
            print(f"eval: cannot finalize {args.request}: {exc}",
                  file=sys.stderr)
            return 2
        print(json.dumps(entry, indent=2))
        return 0
    if args.command == "report":
        out = Path(args.out)
        entries = []
        for child in sorted(out.iterdir()):
            final = child / "report.md"
            if child.is_dir() and final.is_file():
                judgement = json.loads(
                    (child / "judgement.json").read_text(encoding="utf-8"))
                request = eval_corpus.select(
                    request_id=child.name)[0]
                verdict = derive_verdict(
                    [op["outcome"] for op in judgement["ops"]])
                broken = [h for h in judgement.get("hunks", [])
                          if not (h.get("explained_by") or "").strip()]
                entries.append({
                    "request_id": child.name,
                    "domain": request["domain"], "level": request["level"],
                    "scout_verdict": request["scout_verdict"],
                    "harness_verdict": verdict,
                    "agreement": agreement(
                        verdict, request["scout_verdict"],
                        judgement.get("difference", "")),
                    "broke_nothing": "BROKE" if broken else "clean",
                    "looks_good": (judgement.get("looks_good") or {}).get(
                        "verdict", "unjudged"),
                    "looks_good_agreement": (judgement.get("looks_good")
                        or {}).get("captain_label_agreement", "")})
        agg = aggregate(entries)
        (out / "REPORT.md").write_text(
            render_aggregate_report(entries, agg), encoding="utf-8")
        print(f"eval: {agg['n']} finalised -> {out / 'REPORT.md'}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
