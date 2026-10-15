"""Generate a blind editorial labelling kit and ingest evaluator-only labels.

The kit manifest is deliberately separate from benchmark/training inputs.  A
pilot spec supplies query windows and EVAL-04 proposal rows; this module never
infers labels or exposes retrieval scores/proposal provenance to the labeller.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

SCHEMA = "ren-blind-editorial-labels-v1"
KINDS = {
    "search": {"present", "absent", "unsure"},
    "edit": {"acceptable", "unacceptable", "unsure"},
}


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_spec(spec: dict, corpus: dict) -> list[str]:
    errors = []
    sources = {row["source_id"]: row for row in corpus["sources"]}
    seen = set()
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        return ["items must be a non-empty list"]
    for i, item in enumerate(items):
        label = f"items[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        item_id = item.get("id")
        if (
            not isinstance(item_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+", item_id)
            or item_id in seen
        ):
            errors.append(f"{label}.id must be unique and use letters, digits, _ or -")
        seen.add(item_id)
        kind = item.get("kind")
        if kind not in KINDS:
            errors.append(f"{label}.kind must be search or edit")
        source_id = item.get("source_id")
        source = sources.get(source_id)
        if source is None or source.get("split") != "calibration":
            errors.append(f"{label} must reference a calibration source")
            continue
        start, end = item.get("start_seconds"), item.get("end_seconds")
        if (
            not isinstance(start, (int, float))
            or isinstance(start, bool)
            or not isinstance(end, (int, float))
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > source["duration_seconds"]
        ):
            errors.append(f"{label} has an invalid calibration time window")
        if not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
            errors.append(f"{label}.prompt is required")
        if kind == "search" and item.get("query") != item.get("prompt"):
            errors.append(f"{label} search prompt must be the query")
        if kind == "edit" and not isinstance(item.get("proposal_id"), str):
            errors.append(f"{label}.proposal_id is required for runner proposals")
    return errors


def _run(args: list[str]) -> str:
    result = subprocess.run(
        args, text=True, encoding="utf-8", capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _probe(path: Path) -> float:
    data = _read_json_text(
        _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    return float(data["format"]["duration"])


def _read_json_text(value: str) -> Any:
    return json.loads(value)


def _self_contained_html(manifest: dict) -> str:
    # The HTML has no external scripts, stylesheets, fonts, or network calls.
    payload = json.dumps(manifest, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Editorial label pilot</title>
<style>body{font:16px system-ui;max-width:900px;margin:2rem auto;padding:0 1rem;color:#17202a}article{border:1px solid #ccd3da;border-radius:10px;padding:1rem;margin:1rem 0}video{width:100%;max-height:480px;background:#111}label{display:block;margin:.5rem 0}textarea{width:100%;min-height:3rem}button{padding:.55rem 1rem;margin:.5rem}.muted{color:#52616b}</style>
<h1>Editorial label pilot</h1><p class="muted">Judge only what the clip window shows. Search: mark present, absent, or unsure. Edit: mark acceptable, unacceptable, or unsure. You may leave a note on edit items. No scores or proposal source are shown.</p><label>Labeller <input id="labeller" required></label><section id="items"></section><button id="save">Save answers JSON</button><input id="load" type="file" accept="application/json"><script>
const kit=__MANIFEST__, answers={}; const root=document.querySelector('#items');
for(const x of kit.items){const a=document.createElement('article');a.innerHTML=`<h2>${x.kind==='search'?'Search judgment':'Edit decision'}</h2><p></p><video controls preload="metadata" src="${x.media}" data-start="0"></video><fieldset><legend>${x.kind==='search'?'Does this content appear in this window?':'Is the proposed edit decision acceptable?'}</legend></fieldset>${x.kind==='edit'?'<label>Optional note <textarea class="note"></textarea></label>':''}`; a.querySelector('p').textContent=x.prompt;const fs=a.querySelector('fieldset');for(const v of (x.kind==='search'?['present','absent','unsure']:['acceptable','unacceptable','unsure'])){const l=document.createElement('label');l.innerHTML=`<input type="radio" name="${x.id}" value="${v}"> ${v}`;fs.append(l)}a.querySelector('video').addEventListener('loadedmetadata',e=>{e.target.currentTime=Number(e.target.dataset.start)});a.querySelectorAll('input[type=radio]').forEach(r=>r.onchange=()=>answers[x.id]=r.value);const n=a.querySelector('.note');if(n)n.oninput=()=>{answers[x.id]={judgment:answers[x.id],note:n.value}};root.append(a)}
document.querySelector('#save').onclick=()=>{const labeller=document.querySelector('#labeller').value.trim();if(!labeller)return alert('Enter labeller name or identifier.');const rows=kit.items.map(x=>{const v=answers[x.id];return {item_id:x.id,judgment:typeof v==='object'?v.judgment:v,...(x.kind==='edit'?{note:typeof v==='object'?v.note:(root.querySelector(`[name="${x.id}"]`)?.closest('article').querySelector('.note')?.value||'')}: {})}});const blob=new Blob([JSON.stringify({schema:'ren-blind-editorial-answers-v1',kit_id:kit.kit_id,labeller,labels:rows},null,2)],{type:'application/json'});const u=URL.createObjectURL(blob),a=document.createElement('a');a.href=u;a.download='blind-editorial-answers.json';a.click();URL.revokeObjectURL(u)};
document.querySelector('#load').onchange=async e=>{const d=JSON.parse(await e.target.files[0].text());if(d.kit_id!==kit.kit_id) return alert('This answer file belongs to a different kit.');for(const row of d.labels){const el=document.querySelector(`[name="${row.item_id}"][value="${row.judgment}"]`);if(el){el.checked=true;el.dispatchEvent(new Event('change'))}const n=el?.closest('article').querySelector('.note');if(n)n.value=row.note||''}};
</script></html>""".replace("__MANIFEST__", payload)


def generate(
    spec_path: Path, corpus_path: Path, source_root: Path, scratch: Path
) -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    if scratch.resolve() == repo_root or repo_root in scratch.resolve().parents:
        raise ValueError("scratch output must be outside the repository")
    spec, corpus = _read(spec_path), _read(corpus_path)
    errors = validate_spec(spec, corpus)
    if errors:
        raise ValueError("invalid pilot spec: " + "; ".join(errors))
    scratch.mkdir(parents=True, exist_ok=True)
    sources = {row["source_id"]: row for row in corpus["sources"]}
    items = []
    for item in spec["items"]:
        source = sources[item["source_id"]]
        source_path = source_root / source["path"]
        if not source_path.is_file():
            raise FileNotFoundError(f"source missing: {source_path}")
        duration = _probe(source_path)
        if item["end_seconds"] > duration:
            raise ValueError(f"{item['id']} exceeds ffprobe duration")
        media_name = f"{item['id']}.mp4"
        target = scratch / media_name
        _run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                str(item["start_seconds"]),
                "-i",
                str(source_path),
                "-t",
                str(item["end_seconds"] - item["start_seconds"]),
                "-an",
                "-vf",
                "scale='min(960,iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "30",
                "-movflags",
                "+faststart",
                "-y",
                str(target),
            ]
        )
        row = {
            k: v
            for k, v in item.items()
            if k
            not in {
                "source_id",
                "proposal_id",
                "scores",
                "source",
                "rationale",
                "design_stratum",
                "provenance",
            }
        }
        # Media is a basename so the HTML and its proxies travel together.
        row["media"] = media_name
        items.append(row)
    kit_id = (
        spec.get("kit_id")
        or "blind-pilot-" + dt.datetime.now(dt.UTC).date().isoformat()
    )
    page = _self_contained_html({"kit_id": kit_id, "items": items})
    page_path = scratch / "label.html"
    page_path.write_text(page, encoding="utf-8")
    public_items = [
        {k: v for k, v in row.items() if k != "media"} | {"media": row["media"]}
        for row in items
    ]
    (scratch / "kit.json").write_text(
        json.dumps(
            {
                "schema": "ren-blind-editorial-kit-v1",
                "kit_id": kit_id,
                "items": public_items,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return page_path


def validate_answers(answers: dict, kit: dict) -> list[str]:
    errors = []
    if answers.get("schema") != "ren-blind-editorial-answers-v1":
        errors.append("answers schema is missing or unsupported")
    if answers.get("kit_id") != kit.get("kit_id"):
        errors.append("kit_id does not match the kit")
    if not isinstance(answers.get("labeller"), str) or not answers["labeller"].strip():
        errors.append("labeller is required")
    labels = answers.get("labels")
    if not isinstance(labels, list):
        return errors + ["labels must be an array"]
    expected = {row["id"]: row for row in kit["items"]}
    observed = set()
    for index, row in enumerate(labels):
        if not isinstance(row, dict):
            errors.append(f"labels[{index}] must be an object")
            continue
        item_id = row.get("item_id")
        if item_id not in expected:
            errors.append(f"labels[{index}] references an unknown item")
            continue
        if item_id in observed:
            errors.append(f"duplicate label for {item_id}")
        observed.add(item_id)
        kind = expected[item_id]["kind"]
        if row.get("judgment") not in KINDS[kind]:
            errors.append(f"{item_id} has an invalid {kind} judgment")
        if kind == "search" and row.get("note"):
            errors.append(f"{item_id} search labels do not accept notes")
        if (
            kind == "edit"
            and row.get("note") is not None
            and not isinstance(row["note"], str)
        ):
            errors.append(f"{item_id} note must be text")
    missing = sorted(set(expected) - observed)
    if missing:
        errors.append("missing labels: " + ", ".join(missing))
    return errors


def ingest(answers_path: Path, kit_path: Path, destination: Path) -> dict:
    answers, kit = _read(answers_path), _read(kit_path)
    errors = validate_answers(answers, kit)
    if errors:
        raise ValueError("invalid answer file: " + "; ".join(errors))
    today = dt.datetime.now(dt.UTC).date().isoformat()
    items = {row["id"]: row for row in kit["items"]}
    document = {
        "schema": SCHEMA,
        "kit_id": kit["kit_id"],
        "evaluator_only": True,
        "training_data": False,
        "calibration_inputs": False,
        "labeller": answers["labeller"],
        "labelled_date": today,
        "labels": [
            {
                "item": items[row["item_id"]],
                "judgment": row["judgment"],
                **(
                    {"note": row.get("note", "")}
                    if items[row["item_id"]]["kind"] == "edit"
                    else {}
                ),
            }
            for row in answers["labels"]
        ],
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite evaluator records: {destination}")
    destination.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return {
        "valid": True,
        "label_count": len(document["labels"]),
        "stored": str(destination),
    }


def read_evaluator_labels(path: Path | None, *, kind: str | None = None) -> list[dict]:
    """Read evaluator-only labels without merging them into frozen inputs."""
    if path is None:
        return []
    doc = _read(path)
    if doc.get("schema") != SCHEMA or doc.get("evaluator_only") is not True:
        raise ValueError("not an evaluator-only blind-label record")
    if kind not in {None, "search", "edit"}:
        raise ValueError("kind must be search, edit, or None")
    return [row for row in doc["labels"] if kind is None or row["item"]["kind"] == kind]


def label_summary(path: Path | None, *, kind: str) -> dict:
    """Return observed human outcomes for D02 search or F02/F03 edit checks."""
    rows = read_evaluator_labels(path, kind=kind)
    counts: dict[str, int] = {}
    for row in rows:
        value = row["judgment"]
        counts[value] = counts.get(value, 0) + 1
    judged = [row for row in rows if row["judgment"] != "unsure"]
    if kind == "search":
        negatives = [row for row in judged if row["judgment"] == "absent"]
        return {
            "label_count": len(rows),
            "judgment_counts": counts,
            "absent_count": len(negatives),
            "calibration_item_ids": [r["item"]["id"] for r in negatives],
            "source": "evaluator-only human labels; not a frozen benchmark input",
        }
    accepted = sum(row["judgment"] == "acceptable" for row in judged)
    return {
        "label_count": len(rows),
        "judgment_counts": counts,
        "judged_count": len(judged),
        "acceptable_rate": accepted / len(judged) if judged else None,
        "proposal_item_ids": [r["item"]["id"] for r in judged],
        "source": "evaluator-only human labels; not a frozen benchmark input",
    }


def calibrate_abstention(path: Path, scores: dict[str, float]) -> dict:
    """Evaluate score cutoffs against blind search labels (D02 input)."""
    rows = read_evaluator_labels(path, kind="search")
    judged = [
        row
        for row in rows
        if row["judgment"] in {"present", "absent"} and row["item"]["id"] in scores
    ]
    if not any(row["judgment"] == "present" for row in judged) or not any(
        row["judgment"] == "absent" for row in judged
    ):
        return {
            "status": "insufficient labels or scores",
            "judged_count": len(judged),
            "cutoff": None,
            "method": "balanced accuracy over labeled windows",
        }
    values = sorted({float(scores[row["item"]["id"]]) for row in judged})
    candidates = [values[0] - 1e-12]
    candidates.extend((a + b) / 2 for a, b in pairwise(values))
    candidates.append(values[-1] + 1e-12)
    best = None
    for cutoff in candidates:
        tp = sum(
            row["judgment"] == "present" and scores[row["item"]["id"]] >= cutoff
            for row in judged
        )
        tn = sum(
            row["judgment"] == "absent" and scores[row["item"]["id"]] < cutoff
            for row in judged
        )
        positives = sum(row["judgment"] == "present" for row in judged)
        negatives = sum(row["judgment"] == "absent" for row in judged)
        sensitivity, specificity = tp / positives, tn / negatives
        candidate = {
            "cutoff": cutoff,
            "balanced_accuracy": (sensitivity + specificity) / 2,
            "present_retained_rate": sensitivity,
            "absent_abstained_rate": specificity,
            "judged_count": len(judged),
            "method": "balanced accuracy over labeled windows",
        }
        if best is None or candidate["balanced_accuracy"] > best["balanced_accuracy"]:
            best = candidate
    best["status"] = "measured candidate; requires review before policy change"
    best["source"] = "evaluator-only human labels; not a frozen benchmark input"
    return best


def add_runner_proposals(
    spec_path: Path,
    runner_output: Path,
    corpus_path: Path,
    output_path: Path,
    *,
    limit: int = 20,
) -> int:
    """Append blind review windows from actual EVAL-04 OTIO runner outputs."""
    spec, corpus = _read(spec_path), _read(corpus_path)
    sources = {row["source_id"]: row for row in corpus["sources"]}
    proposals = []
    for path in sorted(runner_output.rglob("edit.otio.json")):
        try:
            timeline = _read(path)
        except (OSError, json.JSONDecodeError):
            continue
        for clip in timeline.get("tracks", [{}])[0].get("clips", []):
            source_id = clip.get("source")
            source = sources.get(source_id)
            rng = clip.get("source_range")
            if (
                not source
                or source.get("split") != "calibration"
                or not isinstance(rng, list)
                or len(rng) != 2
            ):
                continue
            start, end = rng
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                continue
            if end <= start or end > source["duration_seconds"]:
                continue
            reason = clip.get("reason")
            rationale = (
                json.dumps(reason, ensure_ascii=False, sort_keys=True)
                if reason
                else "No rationale was recorded."
            )
            proposals.append(
                {
                    "id": "edit-" + str(len(proposals) + 1).zfill(3),
                    "kind": "edit",
                    "proposal_id": str(clip.get("name", len(proposals))),
                    "source_id": source_id,
                    "start_seconds": start,
                    "end_seconds": end,
                    "prompt": "Proposed decision: include this window in the edit. "
                    "Rationale: " + rationale,
                }
            )
    if len(proposals) < limit:
        raise ValueError(
            f"EVAL-04 runner outputs contain {len(proposals)} calibration proposals; {limit} required"
        )
    existing = {row["id"] for row in spec.get("items", [])}
    selected = proposals[:limit]
    if any(row["id"] in existing for row in selected):
        raise ValueError("edit proposal item ids collide with the pilot spec")
    spec["items"].extend(selected)
    issues = validate_spec(spec, corpus)
    if issues:
        raise ValueError("invalid combined pilot spec: " + "; ".join(issues))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return len(selected)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("generate", help="make proxy clips and the offline HTML kit")
    build.add_argument("--spec", type=Path, required=True)
    build.add_argument(
        "--corpus",
        type=Path,
        default=Path("docs/benchmarks/2026-10-15-editorial-eval-corpus.json"),
    )
    build.add_argument("--source-root", type=Path, required=True)
    build.add_argument("--scratch", type=Path, required=True)
    ingest_parser = sub.add_parser(
        "ingest", help="validate and store evaluator-only answers"
    )
    ingest_parser.add_argument("--answers", type=Path, required=True)
    ingest_parser.add_argument("--kit", type=Path, required=True)
    ingest_parser.add_argument("--out", type=Path, required=True)
    summary = sub.add_parser("summary", help="read human labels for evaluator checks")
    summary.add_argument("--labels", type=Path, required=True)
    summary.add_argument("--kind", choices=["search", "edit"], required=True)
    proposals = sub.add_parser(
        "add-eval04-proposals", help="append calibration cuts from runner OTIO outputs"
    )
    proposals.add_argument("--spec", type=Path, required=True)
    proposals.add_argument("--runner-output", type=Path, required=True)
    proposals.add_argument(
        "--corpus",
        type=Path,
        default=Path("docs/benchmarks/2026-10-15-editorial-eval-corpus.json"),
    )
    proposals.add_argument("--out", type=Path, required=True)
    proposals.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "generate":
            print(generate(args.spec, args.corpus, args.source_root, args.scratch))
        elif args.command == "ingest":
            print(json.dumps(ingest(args.answers, args.kit, args.out), indent=2))
        elif args.command == "summary":
            print(json.dumps(label_summary(args.labels, kind=args.kind), indent=2))
        else:
            print(
                json.dumps(
                    {
                        "added": add_runner_proposals(
                            args.spec,
                            args.runner_output,
                            args.corpus,
                            args.out,
                            limit=args.limit,
                        )
                    }
                )
            )
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"blind-label-pilot: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
