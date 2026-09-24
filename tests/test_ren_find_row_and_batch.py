"""Ren locates the named clip and runs the swap per reel, not per row.

Three probe findings off the captain's first real Ren test (geo-podcast:
27 of 30 reels carry the end logo on V6, so 27 dry runs refused with the
V7 default): Ren took `--row` as given instead of locating the named
clip, it ran one reel per invocation, and its refusal sent the model
back to re-check a name that was right when the row was wrong.

Nothing here reaches Resolve or a real project: tracks are hand-built
in the shape `reel_read.read_tracks` returns, the project is a
`tmp_path` folder carrying only a proposal file, and the touchup batch
runs against fake reader/applier seams.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import reel_touchup as T  # noqa: E402
from library.tools import ren_dry_run as D  # noqa: E402


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
    """`read_tracks`-shaped tracks: `[("V6", [clips]), ...]`."""
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


def _project_with_reels(tmp_path, *numbers):
    """A project folder naming the given reels, each approved."""
    from library.tools import reel_proposal as proposal

    moments = [
        proposal.ReelMoment(
            number=int(number),
            slug=f"reel-{number}",
            reason="the batch test reel",
            timeline_start=0.0,
            timeline_end=60.0,
            approval=proposal.Approval.APPROVED,
        )
        for number in numbers
    ]
    proposal.write_proposal(proposal.proposal_path(str(tmp_path)), moments, {})
    return str(tmp_path)


def _media(tmp_path):
    path = tmp_path / "logo_bulb_lines_23976.mov"
    path.write_bytes(b"RIFF....fake-replacement")
    return str(path)


# ── Finding 1: the row is located, not taken as given ───────────────


def test_search_finds_the_named_clip_off_the_default_row():
    """The defect: 27 dry runs refused because Ren looked only on V7
    while the logo sat on V6 - the search itself crosses the rows."""
    tracks = _tracks(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ("V7", [_clip("caption_overlay.mov", 1440, 72)]),
    )
    spec = D.build_change_spec(
        tracks,
        reel=26,
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
    )
    assert spec["edits"][0]["row"] == "V6"
    assert spec["edits"][0]["item"] == 0


def test_narrowed_row_still_addresses_that_row():
    """The defect's other half: `--row` as a narrowing keeps working -
    it stops being a requirement, not a capability."""
    tracks = _tracks(
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
    )
    spec = D.build_change_spec(
        tracks,
        reel=26,
        row="V6",
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
    )
    assert spec["edits"][0]["row"] == "V6"


# ── Finding 3: the refusal names rows, not the name ─────────────────


def test_wrong_row_refusal_names_the_row_it_looked_in():
    """The defect: the refusal said 're-read the reel and state the
    clip by its current name' when the name was right and the row was
    wrong - the model re-checked the name in circles."""
    tracks = _tracks(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
    )
    with pytest.raises(D.DryRunRefused) as caught:
        D.build_change_spec(
            tracks,
            reel=26,
            row="V7",
            old_clip="logo_bulb_23976.mov",
            new_media="/lab/new.mov",
        )
    message = str(caught.value)
    assert "V7" in message  # the row it looked in
    assert "V6" in message  # the row it did not search but holds the name
    assert "state the clip by its current name" not in message


def test_absent_clip_refusal_lists_the_rows_searched():
    """The defect: a bare 'nothing to swap' that never said where it
    looked - the refusal carries the searched rows."""
    tracks = _tracks(
        ("V1", [_clip("interview_a.mp4", 0, 1440)]),
        ("V6", [_clip("something_else.mov", 1440, 72)]),
    )
    with pytest.raises(D.DryRunRefused, match="no item named"):
        D.build_change_spec(
            tracks,
            reel=26,
            old_clip="logo_bulb_23976.mov",
            new_media="/lab/new.mov",
        )


def test_clip_on_two_rows_refuses_naming_both_positions():
    """The defect: one spec meaning two items on different rows -
    qualifying one while addressing whichever the position lands on."""
    tracks = _tracks(
        ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ("V7", [_clip("logo_bulb_23976.mov", 0, 72)]),
    )
    with pytest.raises(D.DryRunRefused, match="2 times") as caught:
        D.build_change_spec(
            tracks,
            reel=26,
            old_clip="logo_bulb_23976.mov",
            new_media="/lab/new.mov",
        )
    assert "V6" in str(caught.value) and "V7" in str(caught.value)


# ── Finding 2: the batch, dry ───────────────────────────────────────


def test_batch_dry_run_reports_each_reel_and_never_stops(tmp_path):
    """The defect: one reel per invocation, so a refused reel ended
    the whole change - the batch carries every reel's answer."""
    project = _project_with_reels(tmp_path, 26, 27)
    tracks_by_reel = {
        26: _tracks(
            ("V1", [_clip("interview_a.mp4", 0, 1440)]),
            ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
        ),
        27: _tracks(
            ("V1", [_clip("interview_b.mp4", 0, 1440)]),
            ("V6", [_clip("unrelated.mov", 1440, 72)]),
        ),
    }
    summary = D.dry_run_all_reels(
        project_folder=project,
        project_label="demo",
        new_media=_media(tmp_path),
        tracks_by_reel=tracks_by_reel,
        offline=True,
    )
    by_reel = {entry["reel"]: entry for entry in summary["reels"]}
    assert set(by_reel) == {26, 27}
    assert by_reel[26]["ok"] is True
    assert by_reel[26]["record"] is not None
    assert by_reel[26]["record"]["change"]["row"] == "V6"
    assert by_reel[27]["ok"] is False
    assert "no item named" in by_reel[27]["refused"]
    assert summary["go"] is False


def test_batch_dry_run_marks_a_reel_with_no_offline_tracks(tmp_path):
    """The defect's batch half: a reel the tracks directory never
    described must be REPORTED refused, never live-read, and never
    stop the reels that were described."""
    project = _project_with_reels(tmp_path, 26, 27)
    summary = D.dry_run_all_reels(
        project_folder=project,
        project_label="demo",
        new_media=_media(tmp_path),
        tracks_by_reel={
            26: _tracks(
                ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]),
            ),
        },
        offline=True,
    )
    by_reel = {entry["reel"]: entry for entry in summary["reels"]}
    assert by_reel[26]["ok"] is True
    assert by_reel[27]["ok"] is False
    assert "no off-disk tracks" in by_reel[27]["refused"]


def test_batch_dry_run_report_says_nothing_executed(tmp_path, capsys):
    """The defect the batch must not introduce: an all-reels loop
    that executes - the report is still print-and-stop per reel."""
    project = _project_with_reels(tmp_path, 26, 27)
    tracks_dir = tmp_path / "tracks"
    tracks_dir.mkdir()
    (tracks_dir / "26.json").write_text(
        json.dumps(
            {"tracks": _tracks(
                ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]))}),
        encoding="utf-8",
    )
    (tracks_dir / "27.json").write_text(
        json.dumps(
            {"tracks": _tracks(
                ("V6", [_clip("unrelated.mov", 1440, 72)]))}),
        encoding="utf-8",
    )
    code = D.main(
        [
            project,
            "--all-reels",
            "--new-media",
            _media(tmp_path),
            "--tracks-dir",
            str(tracks_dir),
        ]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert "Reel 26" in out and "Reel 27" in out
    assert "REFUSED" in out
    assert "BATCH VERDICT" in out
    assert "Nothing executed." in out


# ── Finding 2: the batch, real ──────────────────────────────────────


def test_touchup_batch_continues_past_a_refused_reel(tmp_path):
    """The defect: one reel's refusal ending the change for every
    reel after it - the refused reel is reported, the rest still
    land, in plan order."""
    project = _project_with_reels(tmp_path, 26, 27)
    reads = {
        26: _tracks(("V6", [_clip("unrelated.mov", 1440, 72)])),
        27: _tracks(("V6", [_clip("logo_bulb_23976.mov", 1440, 72)])),
    }
    applied = []

    def reader(number, _final):
        return reads[int(number)]

    def applier(folder, spec):
        applied.append(spec)
        return {"reel": spec["reel"], "landed": True}

    summary = T.touchup_all_reels(
        project,
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
        reader=reader,
        applier=applier,
    )
    assert [entry["reel"] for entry in summary["reels"]] == [26, 27]
    assert summary["reels"][0]["ok"] is False
    assert "no item named" in summary["reels"][0]["refused"]
    assert summary["reels"][1]["ok"] is True
    assert applied and applied[0]["reel"] == 27
    assert applied[0]["edits"][0]["row"] == "V6"
    assert summary["ok"] is False


def test_touchup_batch_reports_an_execute_refusal_without_raising(
    tmp_path,
):
    """The defect: the applier's own refusal propagating as a
    traceback - it lands in the per-reel report instead."""
    project = _project_with_reels(tmp_path, 26, 27)

    def reader(_number, _final):
        return _tracks(
            ("V6", [_clip("logo_bulb_23976.mov", 1440, 72)]))

    def applier(_folder, spec):
        raise T.TouchupRefused(
            f"REFUSING: reel {spec['reel']} is signed off - declare "
            f"it with --supersede.")

    summary = T.touchup_all_reels(
        project,
        old_clip="logo_bulb_23976.mov",
        new_media="/lab/new.mov",
        reader=reader,
        applier=applier,
    )
    assert all(entry["ok"] is False for entry in summary["reels"])
    assert all("REFUSING" in entry["refused"]
               for entry in summary["reels"])
    assert summary["landed"] == 0
