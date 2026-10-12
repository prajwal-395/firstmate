"""LE-14: address a reel by number everywhere a timeline name is required.

`ren timeline {log,show,clips,markers,diff}` and the recorded-read
commands of `bin/resolve-axi` accept `--reel N` (plus the Ren project
path) instead of `--project "<Resolve name>" --timeline "<exact
name>"`. Resolution goes through LE-07's recorded exact final name
and the project's `resolve.project_name`: never a prefix match, the
final by default, the staging sibling with `--staging`, and an
unknown number refuses with exit 4.

Hermetic: the fixture project and the shadow store both live under
`tmp_path`; ambient `REN_SHADOW_DB` is cleared so the `--db` /
injected store is the only one under test. No Resolve, no real
project (AGENTS.md 8).
"""

import json

import pytest

from library.tools import reel_address, reel_phase_log, resolve_axi, timeline_shadow
from library.tools.timeline_shadow import OBSERVED, ShadowStore

RESOLVE_PROJECT = "LE-14 Fixture Resolve Project"
FINAL = "Reel 07 - number-fixture"
STAGING = FINAL + " (rebuild staging)"


def _project_with_reel7(tmp_path):
    root = tmp_path / "show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Number Fixture"\nslug: number-fixture\n'
        f"resolve:\n  project_name: {RESOLVE_PROJECT!r}\n",
        encoding="utf-8",
    )
    reel_phase_log.file_build_summary(
        str(root),
        7,
        FINAL,
        reel_phase_log.assemble_summary(
            outcome=reel_phase_log.OUTCOME_PLACED,
            staging=STAGING,
            final=FINAL,
            decision="placed without verification",
        ),
    )
    return root


def _snapshot(name):
    return {
        "timeline": name,
        "reported_fps": 24.0,
        "start_frame": 0,
        "end_frame": 49,
        "tracks": [{
            "type": "video", "index": 1, "name": "V1",
            "clips": [{
                "track_type": "video", "track_index": 1,
                "name": "A001.MXF", "record_in": 0, "record_out": 50,
                "duration": 50, "source_in_frame": 0,
                "source_out_frame": 50, "source_file": "/footage/A001.MXF",
            }],
        }],
        "markers": {
            "timeline": [],
            "notes": [{
                "source": "timeline_marker", "frame": 5,
                "timecode": "00:00:00:05", "color": "Green",
                "name": "", "note": "keep", "text": "keep",
            }],
        },
    }


def _shadow_with_reel7(tmp_path):
    db = tmp_path / "shadow.db"
    store = ShadowStore(db)
    store.record(project=RESOLVE_PROJECT, timeline_id="tl-final",
                 timeline_name=FINAL, snapshot=_snapshot(FINAL),
                 source=OBSERVED, expected_head=0)
    store.record(project=RESOLVE_PROJECT, timeline_id="tl-staging",
                 timeline_name=STAGING, snapshot=_snapshot(STAGING),
                 source=OBSERVED, expected_head=0)
    return db


def test_reel_numbers_parse_from_plain_and_padded_forms():
    assert reel_address.parse_reel_number("7") == 7
    assert reel_address.parse_reel_number("07") == 7
    assert reel_address.parse_reel_number(7) == 7
    assert reel_address.parse_reel_number("Reel 07 - x") == 7
    assert reel_address.parse_reel_number("") is None
    assert reel_address.parse_reel_number("x") is None
    assert reel_address.parse_reel_number(0) is None


def test_resolver_returns_the_recorded_exact_names(tmp_path):
    root = _project_with_reel7(tmp_path)

    final = reel_address.resolve_reel_timeline(str(root), 7)
    assert final == {"reel": 7, "resolve_project": RESOLVE_PROJECT,
                     "timeline": FINAL, "final": FINAL,
                     "staging": STAGING}

    staging = reel_address.resolve_reel_timeline(
        str(root), "07", staging=True)
    assert staging["timeline"] == STAGING
    assert staging["resolve_project"] == RESOLVE_PROJECT


def test_resolver_refuses_an_unknown_number(tmp_path):
    from library.tools.reel_state import UnknownReel
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE

    root = _project_with_reel7(tmp_path)
    assert REFUSAL_EXIT_CODE == 4
    with pytest.raises(UnknownReel):
        reel_address.resolve_reel_timeline(str(root), 99)


def test_timeline_clips_reads_the_final_by_default(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_SHADOW_DB", raising=False)
    root = _project_with_reel7(tmp_path)
    db = _shadow_with_reel7(tmp_path)

    assert timeline_shadow._main(
        ["--db", str(db), "clips", str(root), "--reel", "7"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generation"]["timeline"] == FINAL
    assert payload["generation"]["project"] == RESOLVE_PROJECT
    assert [clip["name"] for clip in payload["clips"]] == ["A001.MXF"]


def test_timeline_clips_reads_the_staging_sibling_with_the_flag(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_SHADOW_DB", raising=False)
    root = _project_with_reel7(tmp_path)
    db = _shadow_with_reel7(tmp_path)

    assert timeline_shadow._main(
        ["--db", str(db), "clips", str(root), "--reel", "7",
         "--staging"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generation"]["timeline"] == STAGING


def test_timeline_unknown_reel_refuses_with_exit_4(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_SHADOW_DB", raising=False)
    root = _project_with_reel7(tmp_path)
    db = _shadow_with_reel7(tmp_path)

    assert timeline_shadow._main(
        ["--db", str(db), "clips", str(root), "--reel", "99"]) == 4
    assert "no reel" in capsys.readouterr().err


def test_timeline_reel_without_a_project_is_a_usage_error(
        tmp_path, monkeypatch):
    monkeypatch.delenv("REN_SHADOW_DB", raising=False)
    _shadow_with_reel7(tmp_path)
    with pytest.raises(SystemExit) as exc:
        timeline_shadow._main(["clips", "--reel", "7"])
    assert exc.value.code == 2


def test_timeline_old_project_and_timeline_form_still_works(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_SHADOW_DB", raising=False)
    _project_with_reel7(tmp_path)
    db = _shadow_with_reel7(tmp_path)

    assert timeline_shadow._main(
        ["--db", str(db), "clips", "--project", RESOLVE_PROJECT,
         "--timeline", FINAL]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["generation"]["timeline"] == FINAL


def _point_axi_at(store, monkeypatch):
    from library.tools import timeline_read

    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)


def test_axi_markers_reads_the_final_by_reel(
        tmp_path, capsys, monkeypatch):
    root = _project_with_reel7(tmp_path)
    _point_axi_at(ShadowStore(_shadow_with_reel7(tmp_path)), monkeypatch)

    assert resolve_axi.main(
        ["markers", "--reel", "7",
         "--project-folder", str(root)]) == 0
    out = capsys.readouterr().out
    assert FINAL in out
    assert STAGING not in out


def test_axi_items_reads_the_staging_sibling_with_the_flag(
        tmp_path, capsys, monkeypatch):
    root = _project_with_reel7(tmp_path)
    _point_axi_at(ShadowStore(_shadow_with_reel7(tmp_path)), monkeypatch)

    assert resolve_axi.main(
        ["items", "--reel", "7", "--staging",
         "--project-folder", str(root)]) == 0
    out = capsys.readouterr().out
    assert STAGING in out


def test_axi_timeline_get_reads_the_final_by_reel(
        tmp_path, capsys, monkeypatch):
    root = _project_with_reel7(tmp_path)
    _point_axi_at(ShadowStore(_shadow_with_reel7(tmp_path)), monkeypatch)

    assert resolve_axi.main(
        ["timeline", "get", "--reel", "7",
         "--project-folder", str(root)]) == 0
    assert FINAL in capsys.readouterr().out


def test_axi_unknown_reel_is_an_error_naming_the_fix(
        tmp_path, capsys, monkeypatch):
    root = _project_with_reel7(tmp_path)
    _point_axi_at(ShadowStore(_shadow_with_reel7(tmp_path)), monkeypatch)

    assert resolve_axi.main(
        ["markers", "--reel", "99",
         "--project-folder", str(root)]) == 1
    assert "no reel" in capsys.readouterr().out


def test_axi_reel_and_timeline_together_are_refused(
        tmp_path, capsys, monkeypatch):
    root = _project_with_reel7(tmp_path)
    _point_axi_at(ShadowStore(_shadow_with_reel7(tmp_path)), monkeypatch)

    assert resolve_axi.main(
        ["markers", "--reel", "7", "--timeline", FINAL,
         "--project-folder", str(root)]) == 1
    assert "--project-folder instead of --timeline" in capsys.readouterr().out
