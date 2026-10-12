"""LE-07: `ren reels <p> [--reel N] [--json]` - one read of every reel's state."""

import json

from library.tools import reel_phase_log, reel_state
from library.tools.versions import reel_versions


def _fixture_project(tmp_path):
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "Test Show"\nslug: show\n', encoding="utf-8"
    )
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(
        json.dumps(
            {
                "format": "reel_proposal/1",
                "derived_from": {},
                "instruction": "",
                "moment_count": 2,
                "moments": [
                    {
                        "number": 1,
                        "slug": "alpha",
                        "reason": "why one",
                        "timeline_start": 0.0,
                        "timeline_end": 5.0,
                        "approval": "approved",
                    },
                    {
                        "number": 2,
                        "slug": "beta",
                        "reason": "why two",
                        "timeline_start": 10.0,
                        "timeline_end": 15.0,
                        "approval": "proposed",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    reel_phase_log.file_build_summary(
        str(project),
        1,
        "Reel 01 - alpha",
        reel_phase_log.assemble_summary(
            outcome=reel_phase_log.OUTCOME_PLACED,
            staging="Reel 01 - alpha (rebuild staging)",
            final="Reel 01 - alpha",
            decision="placed without verification",
        ),
    )
    reel_phase_log.file_build_summary(
        str(project),
        2,
        "Reel 02 - beta",
        reel_phase_log.assemble_summary(
            outcome=reel_phase_log.OUTCOME_PROMOTED,
            staging="Reel 02 - beta (rebuild staging)",
            final="Reel 02 - beta",
            decision="promoted",
            verify={"verdict": "verified", "passed": True},
        ),
    )
    reel_phase_log.log_event(
        str(project),
        2,
        "Reel 02 - beta",
        reel_phase_log.VERIFIED,
        detail="conformance gate passed",
    )
    (project / "pipeline_data.json").write_text(
        json.dumps(
            {
                "capability_outputs": {
                    "reel.build": {
                        "reel_build": {
                            "picture_motion": [
                                {"reel": 1, "basis": "awaiting_model_answer"},
                            ],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    reel_versions.record(str(project), "Reel 01 - alpha", kind="build", rows={"v1": []})
    from library.tools import reel_signoff

    reel_signoff.sign_off(str(project), "Reel 01 - alpha", note="good")
    exports = project / "exports"
    exports.mkdir(parents=True)
    (exports / "Reel 01 - alpha.deliver.json").write_text(
        json.dumps(
            {
                "timeline_name": "Reel 01 - alpha",
                "reel": 1,
                "render": {"output_path": str(exports / "Reel 01 - alpha.mp4")},
                "verification": {},
                "delivered": True,
            }
        ),
        encoding="utf-8",
    )
    return project


def test_reels_state_composes_all_records_per_reel(tmp_path):
    project = _fixture_project(tmp_path)

    state = reel_state.reels_state(str(project))

    assert state["reel_count"] == 2
    by_reel = {row["reel"]: row for row in state["reels"]}
    one, two = by_reel[1], by_reel[2]

    assert one["approval"] == "approved"
    assert one["outcome"] == "placed_unverified"
    assert "picture_motion" in one["owed_answers"]
    assert one["version"]["count"] == 1
    assert one["signoff"]["note"] == "good"
    assert one["delivery"]["delivered"] is True
    assert one["timeline_name"] == "Reel 01 - alpha"

    assert two["approval"] == "proposed"
    assert two["outcome"] == "promoted"
    assert two["verify"] == {"verdict": "verified", "passed": True}
    assert reel_phase_log.VERIFIED in two["phases"]


def test_reels_text_default_is_one_line_per_reel(tmp_path, capsys):
    project = _fixture_project(tmp_path)

    assert reel_state.main([str(project)]) == 0
    lines = capsys.readouterr().out.splitlines()

    assert len(lines) == 2
    assert lines[0].startswith("Reel 1 (Reel 01 - alpha):")
    assert lines[1].startswith("Reel 2 (Reel 02 - beta):")


def test_reels_json_gives_everything_and_reel_filters(tmp_path, capsys):
    from library.tools import reel_state as _state

    project = _fixture_project(tmp_path)

    assert _state.main([str(project), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reel_count"] == 2
    assert {row["reel"] for row in payload["reels"]} == {1, 2}

    assert _state.main([str(project), "--reel", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [row["reel"] for row in payload["reels"]] == [1]

    assert _state.main([str(project), "--reel", "99"]) == 4


def test_reels_verb_is_registered_in_both_front_doors():
    from pathlib import Path

    from ren.commands import VERBS

    repo = Path(__file__).resolve().parents[3]
    assert any(
        verb.name == "reels" and verb.module_argv == ("library.tools.reel_state",)
        for verb in VERBS
    )
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8"
    )
    assert 'name: "reels"' in native
