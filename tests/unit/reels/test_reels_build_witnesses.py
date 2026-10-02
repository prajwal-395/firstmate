"""The reels build consults what `edit_video` already consults - print-only,
never a gate: `report_layer_coherence`, and `sweep_all_reels_informational`
beside the scoped refusing gate. Why: docs/evidence/reel_build_witnesses.md.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import reel_build as rb  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────

def _clean_project(root: Path) -> str:
    """A project whose layers agree: a transcript, no corrections."""
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text(
        json.dumps({"segments": []}), encoding="utf-8")
    return str(root)


def _stale_project(root: Path) -> str:
    """A project quoting `lucy` where the correction says `Lucie`."""
    folder = _clean_project(root)
    learned = root / "learned_context"
    learned.mkdir(parents=True)
    (learned / "learnings.json").write_text(json.dumps([{
        "id": "lc-0001", "kind": "correction", "status": "active",
        "source": {"correction_type": "transcript_spelling",
                   "heard": "lucy", "correct": "Lucie"},
    }]), encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(
        json.dumps({"moments": [{
            "timeline_name": "Reel 01 - a",
            "call_to_action": {"text": "the lucy visibility system"},
        }]}), encoding="utf-8")
    return folder


def _sweep_project(root: Path) -> str:
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    transcript = scratch / "transcript.json"
    transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")
    return str(root), str(transcript)


# ── layer_coherence on the reels build ────────────────────────────

def test_coherence_names_a_stale_layer_and_is_quiet_when_layers_agree(
        tmp_path, capsys):
    """Remove the witness and a stale layer builds in silence: a wording
    the source transcript no longer speaks is said before anything is
    placed. No owned divergences, no LAYER COHERENCE line."""
    report = rb.report_layer_coherence(_stale_project(tmp_path / "stale"))
    assert "unavailable" not in report
    assert any(row["should_be"] == "Lucie" for row in report["wording"])

    capsys.readouterr()
    report = rb.report_layer_coherence(_clean_project(tmp_path / "clean"))
    owned = sum(len(report.get(key, [])) for key in
                ("wording", "pins", "assets"))
    assert owned == 0
    assert "LAYER COHERENCE" not in capsys.readouterr().err


def test_coherence_reports_rather_than_raises_when_it_cannot_run(tmp_path):
    """A witness that raised would hold the build hostage to itself."""
    with patch("library.tools.layer_coherence.check_project",
               side_effect=RuntimeError("no transcript module")):
        report = rb.report_layer_coherence(str(tmp_path))

    assert report == {"unavailable": "no transcript module"}


# ── The whole-project sweep beside the scoped gate ────────────────

def test_the_sweep_grades_every_reel_in_build_units_beside_the_gate(
        tmp_path):
    """Remove the sweep and nothing ever grades the untouched reels: it
    reaches the verifier with NO scope, reads Pan/Tilt in the build's
    calibration (F12), and writes its own report - the gate's
    `conformance_report.json` is the refusal's evidence and stays
    byte-identical."""
    folder, transcript = _sweep_project(tmp_path)
    review = Path(folder) / "pipeline_output" / "review"
    gate_report = review / "conformance_report.json"
    gate_report.write_text('{"gate": "evidence"}', encoding="utf-8")

    with patch("library.tools.reel_conformance_verifier.run_verification",
               return_value=0) as run:
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript, draw_gain=4.0)

    assert run.call_count == 1
    assert run.call_args[1]["only_reels"] is None
    assert run.call_args[1]["draw_gain"] == 4.0
    assert result["exit_code"] == 0
    assert result["report"].endswith("conformance_sweep_report.json")
    assert run.call_args[1]["json_path"] == result["report"]
    assert gate_report.read_text(encoding="utf-8") == '{"gate": "evidence"}'


def test_the_sweep_reports_findings_but_never_refuses(tmp_path):
    """Findings on reels this build did not touch are news, not a gate.

    A sweep that raised on exit 1 would be the whole-project refusal
    PR #658 removed, wearing a new name.
    """
    folder, transcript = _sweep_project(tmp_path)

    with patch("library.tools.reel_conformance_verifier.run_verification",
               return_value=1):
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript)
    assert result["exit_code"] == 1

    with patch("library.tools.reel_conformance_verifier.run_verification",
               side_effect=RuntimeError("Resolve closed")):
        result = rb.sweep_all_reels_informational(
            project_folder=folder, resolve_project_name="Mock",
            master_timeline_name="Master", plan_path="/x/plan.json",
            transcript_path=transcript)
    assert result == {"unavailable": "Resolve closed"}
