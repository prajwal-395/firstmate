"""Part-one dry run of the composed edit path: plan, read, print, stop.

The join under test wires three real pieces without reimplementing any:
`composer` (the plan + route), `timeline_oracle` (live preconditions)
and `reel_touchup.qualify` (the gate). The dry run never executes - not
once, not to check - so the first test pins that no execute, write or
cursor move lives in the module, by AST rather than substring: the
report it prints MUST name the `touch-reel` command and the
`apply_touchup` call it would make, so a substring ban would fail
correct prose.

Every test below names the defect it would catch. Nothing pins a count,
a registry size or a plan shape: Ren is being rebuilt, and a test that
fails on a legitimate rename teaches everyone to update the number
instead of reading the failure. What survives a rebuild is pinned -
the goal composes, runtime context (not the static default) chooses
the route, the live precondition check answers, the gate is consulted,
and the report names both paths while executing neither.

Nothing here reaches Resolve or a real project: tracks are hand-built
in the shape `reel_read.read_tracks` returns, the project is a `tmp_path`
folder carrying only a proposal file, and the replacement media is a
`tmp_path` file.
"""

import ast
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import ren_dry_run as D  # noqa: E402

#: Never called: the execute on the touchup module, every Resolve
#: write/cursor verb, and every lease primitive. The module takes no
#: lease itself - the single live read carries its own shared one. A
#: call hiding here would execute the captain's timeline from a command
#: whose contract is print-and-stop.
BANNED_CALL_ATTRS = (
    "apply_touchup",
    "ImportMedia",
    "SetCurrentTimeline",
    "DeleteClips",
    "DuplicateTimeline",
    "CreateTimeline",
    "AppendToTimeline",
    "SetProperty",
    "AddMarker",
    "ImportFusionComp",
    "SetSetting",
    "under_lease",
    "resolve_lease",
    "cursor_excursion",
)


def _clip(name, start, duration, *, comps=0):
    return {
        "name": name,
        "record_in": start,
        "record_out": start + duration,
        "duration": duration,
        "left_offset": 0,
        "right_offset": 0,
        "fusion": {"comp_count": comps, "comp_names": [], "media_windows": []},
    }


def _tracks(*rows):
    """`read_tracks`-shaped tracks: `[("V7", [clips]), ...]`."""
    tracks = []
    for row, clips in rows:
        tracks.append(
            {
                "type": ("audio" if str(row).upper().startswith("A") else "video"),
                "index": int(str(row)[1:]),
                "name": str(row).upper(),
                "speaker": None,
                "clips": list(clips),
            }
        )
    return tracks


def _reel_26_tracks():
    return _tracks(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V7", [_clip("logo_bulb_23976.mov", 1440, 72)]),
    )


def _project_with_reel_26(tmp_path):
    """A project folder naming only reel 26, approved as `ending`."""
    from library.tools import reel_proposal as proposal

    moment = proposal.ReelMoment(
        number=26,
        slug="ending",
        reason="the closing test reel",
        timeline_start=0.0,
        timeline_end=60.0,
        approval=proposal.Approval.APPROVED,
    )
    proposal.write_proposal(proposal.proposal_path(str(tmp_path)), [moment], {})
    return str(tmp_path)


def _media(tmp_path):
    path = tmp_path / "logo_bulb_lines_23976.mov"
    path.write_bytes(b"RIFF....fake-replacement")
    return str(path)


def _dry_run_til_gate(tmp_path):
    """The off-disk dry run whose gate qualifies the clean swap."""
    return D.dry_run(
        project_folder=_project_with_reel_26(tmp_path),
        project_label="demo",
        reel=26,
        old_clip="logo_bulb_23976.mov",
        new_media=_media(tmp_path),
        tracks=_reel_26_tracks(),
        expected_rows={},
        expected_basis="test: records claim nothing",
    )


# ── The dry run never executes ─────────────────────────────────────


def test_no_execute_write_or_lease_lives_in_this_module():
    """AST, not substring: the report names the call it never makes."""
    tree = ast.parse(Path(D.__file__).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            called.add(func.attr)
        elif isinstance(func, ast.Name):
            called.add(func.id)
    # The report it prints names the execute - which is why this is AST:
    assert "apply_touchup" in Path(D.__file__).read_text(encoding="utf-8")
    assert not (called & set(BANNED_CALL_ATTRS)), (
        f"the dry run calls what it must only print: "
        f"{sorted(called & set(BANNED_CALL_ATTRS))}"
    )


# ── The change, stated structurally ────────────────────────────────


def test_the_dry_run_refuses_what_it_cannot_honestly_report(tmp_path):
    """Each defect refuses before a report: an unstated --old-clip
    falling back to a baked-in filename (one project's asset as every
    project's default); a replacement not on disk (the execute would
    refuse before staging); a reel number the plan never described (the
    plan's own refusal surfaces, uncaught)."""
    from library.tools import reel_touchup as touchup_mod

    project = _project_with_reel_26(tmp_path)
    with pytest.raises(D.DryRunRefused, match="--old-clip"):
        D.dry_run(project_folder=project, project_label="demo", reel=26,
                  new_media="/lab/new.mov", tracks=_reel_26_tracks())
    with pytest.raises(D.DryRunRefused, match="not on disk"):
        D.dry_run(project_folder=project, project_label="demo", reel=26,
                  old_clip="logo_bulb_23976.mov",
                  new_media=str(tmp_path / "missing.mov"),
                  tracks=_reel_26_tracks(), expected_rows={},
                  expected_basis="test")
    with pytest.raises(touchup_mod.TouchupRefused, match="no reel"):
        D.dry_run(project_folder=project, project_label="demo", reel=27,
                  old_clip="logo_bulb_23976.mov",
                  new_media=_media(tmp_path), tracks=_reel_26_tracks(),
                  expected_rows={}, expected_basis="test")


# ── The full dry run, off disk ─────────────────────────────────────


def test_dry_run_wires_plan_selection_gate_and_preconditions(tmp_path):
    """The defect: orchestration that drops a piece - preconditions
    never evaluated, the gate never consulted, or the printed
    would-execute naming a different operation than the selection
    chose. Pinned as consistency between the record's own fields, so
    a legitimate rename moves all of them together."""
    from library.tools import operations as ops_mod

    record = _dry_run_til_gate(tmp_path)
    assert record["goal"] == D.REEL_GOAL
    assert record["final"] == "Reel 26 - ending"
    assert record["plan"]["operations"]  # the goal composes to a plan
    assert record["compile_manifest_in_plan"] is False
    (selection,) = record["selection"]
    assert selection["decided_by"] == "selector"
    assert record["selected_operation"] == selection["operation"]
    assert record["gate"]["class"] == "composed"
    # Every precondition the selected operation declares is answered
    # live - the reel goal itself off the screen, the rest off their
    # own checks - and the record carries each answer.
    selected_requires = {
        r.name for r in ops_mod.get(record["selected_operation"]).requires
    }
    assert {D.REEL_GOAL, *selected_requires} == {
        e["precondition"] for e in record["preconditions"]
    }
    by_name = {e["precondition"]: e for e in record["preconditions"]}
    assert by_name[D.REEL_GOAL]["satisfied_by_live_timeline"] is True
    # The project in tmp binds no Resolve project, so the verdict is
    # NO-GO with the missing named - never a pass, and never a crash
    # where an answer was owed.  (The touch-up reads no transcript, so
    # since `consumes` it no longer asks for one.)
    assert "resolve.timeline_binding" in record["preconditions_failed"]
    assert ("timeline_transcript.on_file"
            not in record["preconditions_failed"])
    assert record["preconditions_hold"] is False
    assert record["go"] is False
    # Exactly what WOULD execute - the shipped touch path, printing
    # the selection's own operation - and what the OLD path would
    # have executed instead.
    assert record["would_execute"]["command"].startswith(
        "python3 manage_project.py touch-reel demo 26 --edits "
    )
    assert "swap_pixels" in record["would_execute"]["command"]
    assert record["selected_operation"] in record["would_execute"]["operation"]
    assert record["old_path"]["command"] == (
        "python3 manage_project.py build-reels demo --only-reel 26"
    )
    walk = record["old_path"]["walk"]
    assert walk, "the old path walks no operations at all"
    assert all(set(e) >= {"operation", "would", "why"} for e in walk)
    assert all(e["would"] in ("run", "skip") for e in walk)
    assert any(e["would"] == "run" for e in walk)
    # The loop leaves the caller-supplied ops out and runs the rest,
    # so the walk marks exactly those as skipped.
    from library.tools import capabilities
    from library.tools import processes as processes_mod

    reels = {c.id for c in capabilities.run_order(processes_mod.REELS)}
    assert {e["operation"] for e in walk if e["would"] == "skip"} == {
        op.name for op in ops_mod.all()
        if op.caller_supplied and op.name in reels
    }


def test_dry_run_carries_a_refused_gate_instead_of_crashing(tmp_path):
    """The defect: a `TouchupRefused` propagating out of the dry run as
    a traceback - the gate's refusal is a report field (and a NO-GO),
    the way the selector already treats it."""
    tracks = _tracks(("V7", [_clip("logo_bulb_23976.mov", 1440, 72, comps=3)]))
    record = D.dry_run(
        project_folder=_project_with_reel_26(tmp_path),
        project_label="demo",
        reel=26,
        old_clip="logo_bulb_23976.mov",
        new_media=_media(tmp_path),
        tracks=tracks,
        expected_rows={},
        expected_basis="test: records claim nothing",
    )
    assert record["gate"]["class"] == "refused"
    assert record["gate"]["refused"]
    assert record["go"] is False


# ── CLI off disk ───────────────────────────────────────────────────


def test_cli_off_disk_prints_the_report_and_exits_no_go(tmp_path, capsys):
    """The defect: the captain's entry point failing to reach the join
    - argument plumbing that never arrives at `dry_run`."""
    project = _project_with_reel_26(tmp_path)
    tracks_file = tmp_path / "tracks.json"
    tracks_file.write_text(json.dumps({"tracks": _reel_26_tracks()}), encoding="utf-8")
    code = D.main(
        [
            project,
            "--reel",
            "26",
            "--old-clip",
            "logo_bulb_23976.mov",
            "--new-media",
            _media(tmp_path),
            "--tracks-file",
            str(tracks_file),
        ]
    )
    assert code == 1  # NO-GO: tmp satisfies nothing runner-side
    out = capsys.readouterr().out
    assert "REN DRY RUN - Reel 26 ending swap" in out
    assert "Nothing executed." in out
