#!/usr/bin/env python3
"""Resume an interrupted Q Promotion Proof R5 transaction through AXI.

Run this only after the promotion was interrupted and its transaction journal
is still present. It refuses to create a fixture or invent a journal. The
recovery attempt is a fresh brokered qualification session, which models the
restart boundary and invokes the same recovery routine used before promotion.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "Ren Qualification"
FINAL = "Q Promotion Proof R5"
TIMELINE = "Q Spine"
REN = ROOT / "bin" / "ren"


def _init_run_store(path: Path) -> None:
    commands = (
        ["git", "init", str(path)],
        ["git", "-C", str(path), "config", "user.name", "VEP Qualification"],
        ["git", "-C", str(path), "config", "user.email",
         "vep-qualification@localhost"],
        ["git", "-C", str(path), "add", "project.yaml"],
        ["git", "-C", str(path), "commit", "-m", "Initialize qualification run"],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True,
                                encoding="utf-8", check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def _python_script(transaction_id: str) -> str:
    return f'''\
from pathlib import Path
from library.tools import qualification_project
from library.tools.reel_build import recover_interrupted_promotions

if project.GetName() != {PROJECT!r}:
    raise RuntimeError("wrong Resolve project; expected Ren Qualification")
folder = qualification_project.promotion_project_folder()
journal = folder / "pipeline_output" / "review" / "promotion_transactions" / ({transaction_id!r} + ".json")
if not journal.is_file():
    raise RuntimeError("the named interrupted promotion journal is absent")
record = __import__("json").loads(journal.read_text(encoding="utf-8"))
if record.get("project") != {PROJECT!r} or not record.get("rows"):
    raise RuntimeError("journal project or rows do not match qualification")
if any(row.get("final") != {FINAL!r} for row in record["rows"]):
    raise RuntimeError("journal includes a timeline outside the named fixture")
recovered = recover_interrupted_promotions(project, str(folder))
if record["transaction_id"] not in recovered:
    raise RuntimeError("recovery did not report the requested transaction")
if journal.exists():
    raise RuntimeError("recovered promotion journal was not retired")
if not manager.SaveProject():
    raise RuntimeError("could not save recovered qualification project")
result = {{"project": project.GetName(), "transaction_id": {transaction_id!r},
          "recovered": recovered, "journal_retired": True}}
'''


def run(transaction_id: str) -> dict:
    if len(transaction_id) != 32 or any(
            char not in "0123456789abcdef" for char in transaction_id):
        raise ValueError("transaction id must be 32 lowercase hexadecimal characters")
    if not REN.is_file():
        raise RuntimeError(f"Ren CLI not found: {REN}")

    # This is a read-only preflight in the isolated qualification store.
    from library.tools.qualification_project import promotion_project_folder

    folder = promotion_project_folder()
    journal = (folder / "pipeline_output" / "review" /
               "promotion_transactions" / f"{transaction_id}.json")
    if not journal.is_file():
        raise RuntimeError(f"no pending promotion journal at {journal}")
    record = json.loads(journal.read_text(encoding="utf-8"))
    if (record.get("project") != PROJECT or not record.get("rows")
            or any(row.get("final") != FINAL for row in record["rows"])):
        raise RuntimeError("journal is not the exact R5 qualification fixture")

    with tempfile.TemporaryDirectory(prefix="vcr-restart-run-") as temporary:
        project_dir = Path(temporary)
        (project_dir / "project.yaml").write_text(
            "project_name: vcr_promotion_restart_qualification\n",
            encoding="utf-8")
        _init_run_store(project_dir)
        script_path = project_dir / "recover_promotion.py"
        script_path.write_text(_python_script(transaction_id), encoding="utf-8")
        argv = ["run", "--project", PROJECT, "--timeline", TIMELINE,
                "--file", str(script_path), "--unsafe", "--json",
                "--purpose", "Recover interrupted VCR R5 qualification promotion",
                "--scope", "project", "--project-dir", str(project_dir)]
        steps = [{"resolve_axi": argv}]
        completed = subprocess.run(
            [str(REN), "resolved", "submit", "resolve.qualification_session",
             json.dumps({"project": PROJECT, "steps": steps}),
             "--qualification", "--wait", "180"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            timeout=240, check=False)
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        receipt = json.loads(completed.stdout)
        if receipt.get("state") != "done" or not receipt.get("result"):
            raise RuntimeError(f"brokered recovery failed: {receipt}")
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transaction-id", required=True,
                        help="32-character id from the interrupted Q R5 promotion")
    args = parser.parse_args()
    try:
        result = run(args.transaction_id)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"VCR promotion recovery refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
