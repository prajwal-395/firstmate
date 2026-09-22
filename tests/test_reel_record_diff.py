"""git diff for a reel: live projection vs last committed .timeline.json.

The captain's version-control judgement (2026-09-21) makes the FILE the
unit of revert and the COMMIT the unit of change - and what that needs
is a printed diff of each reel's live projection against its last
committed `.timeline.json` that NEVER refuses. Each test below names
which half of that it would catch the removal of.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import reel_divergence as rd  # noqa: E402


# ── Fakes: serializer-shaped documents ──────────────────────────

def make_clip(name, record_in, record_out=None, **overrides):
    clip = {
        "name": name,
        "record_in": record_in,
        "record_out": (record_out if record_out is not None
                       else record_in + 100),
        "source_in": 0,
        "source_out": 100,
        "enabled": True,
        "file_path": f"/footage/{name}",
        "transform": {"Pan": 0.0, "Tilt": 0.25, "ZoomX": 2.307,
                      "ZoomY": 2.307},
        "crop": {"CropLeft": 0.0, "CropRight": 0.0, "CropTop": 0.0,
                 "CropBottom": 0.0, "CropSoftness": 0.0},
        "composite": {"Opacity": 100.0, "CompositeMode": "Normal"},
        "retime": {"process": "", "motion_estimation": "", "speed_ratio": 1.0},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(clip.get(key), dict):
            merged = dict(clip[key])
            merged.update(value)
            clip[key] = merged
        else:
            clip[key] = value
    return clip


def doc(name, *tracks):
    """A serializer-shaped document.

    Each track is `(type, index, track_name, [clips])`.
    """
    return {
        "metadata": {"name": name},
        "tracks": [{"type": kind, "index": index, "name": track_name,
                    "clips": list(clips)}
                   for kind, index, track_name, clips in tracks],
    }


def one_reel(name, *clips):
    return doc(name, ("video", 1, "V1", list(clips)))


# ── The pure diff: agreements ───────────────────────────────────

def test_identical_documents_hold_what_the_commit_recorded():
    record = one_reel("Reel 26", make_clip("a.MXF", 100),
                      make_clip("b.MXF", 200))
    live = one_reel("Reel 26", make_clip("a.MXF", 100),
                    make_clip("b.MXF", 200))
    per = rd.diff_record_vs_live(record, live, reel="Reel 26")
    assert per["compared"] is True
    assert per["counts"] == {"agreements": 2, "conflicts": 0,
                             "undetermined": 0}
    assert "hold exactly what the commit recorded" in per["line"]


def test_read_noise_within_tolerance_is_still_agreement():
    """Resolve read-back noise (transform_drift.UNMOVED = 1e-3) is not a
    hand edit. Remove the tolerance and every reel diffs itself."""
    record = one_reel("Reel 26", make_clip("a.MXF", 100))
    live = one_reel("Reel 26",
                    make_clip("a.MXF", 100,
                              transform={"Pan": 0.0005, "Tilt": 0.2505}))
    per = rd.diff_record_vs_live(record, live, reel="Reel 26")
    assert per["counts"]["conflicts"] == 0
    assert per["counts"]["agreements"] == 1


# ── The pure diff: conflicts ────────────────────────────────────

def test_a_moved_clip_reads_as_moved_not_removed_plus_added():
    """Same name, new record window: one conflict naming both spans.
    Splitting it into removed-plus-added would double the triage."""
    record = one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069))
    live = one_reel("Reel 13", make_clip("LC4930.MXF", 600, 1079))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["conflicts"] == 1
    assert per["counts"]["agreements"] == 0
    detail = per["conflicts"][0]["detail"]
    assert "moved" in detail and "@590..1069" in detail
    assert "@600..1079" in detail


def test_a_trimmed_clip_names_the_field():
    record = one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069))
    live = one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1100))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["conflicts"] == 1
    assert "record_out 1069 -> 1100" in per["conflicts"][0]["detail"]


def test_a_reframed_clip_names_the_axis():
    record = one_reel("Reel 13", make_clip("LC4930.MXF", 590))
    live = one_reel("Reel 13",
                    make_clip("LC4930.MXF", 590,
                              transform={"Pan": -24.0}))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["conflicts"] == 1
    assert "transform.Pan 0 -> -24" in per["conflicts"][0]["detail"]


def test_an_added_clip_is_unclaimed_by_the_commit():
    record = one_reel("Reel 13", make_clip("a.MXF", 100))
    live = one_reel("Reel 13", make_clip("a.MXF", 100),
                    make_clip("insert.MXF", 200))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"] == {"agreements": 1, "conflicts": 1,
                             "undetermined": 0}
    assert "unclaimed by it" in per["conflicts"][0]["detail"]
    assert "insert.MXF" in per["conflicts"][0]["detail"]


def test_a_removed_clip_is_unclaimed_by_the_live_reel():
    record = one_reel("Reel 13", make_clip("a.MXF", 100),
                      make_clip("gone.MXF", 200))
    live = one_reel("Reel 13", make_clip("a.MXF", 100))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["conflicts"] == 1
    assert "gone.MXF" in per["conflicts"][0]["detail"]


def test_a_track_on_one_side_is_one_row_not_a_clip_explosion():
    """A whole new overlay row is one finding. One row per clip would
    turn a single added track into dozens of conflicts."""
    record = one_reel("Reel 13", make_clip("a.MXF", 100))
    live = doc("Reel 13", ("video", 1, "V1", [make_clip("a.MXF", 100)]),
               ("video", 4, "Captions", [make_clip("cap.mov", 100),
                                         make_clip("cap2.mov", 200)]))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["conflicts"] == 1
    assert "Captions" in per["conflicts"][0]["detail"]
    assert "2 clip(s)" in per["conflicts"][0]["detail"]


# ── The pure diff: undetermined is a first-class answer ─────────

def test_an_unreadable_record_is_undetermined_never_raised():
    per = rd.diff_record_vs_live({"metadata": {}}, one_reel("R",),
                                 reel="Reel 13")
    assert per["compared"] is False
    assert per["counts"]["undetermined"] == 1
    assert "committed record cannot be read" in per["line"]


def test_a_missing_live_side_is_undetermined():
    per = rd.diff_record_vs_live(one_reel("Reel 13"), None, reel="Reel 13")
    assert per["compared"] is False
    assert "live projection cannot be read" in per["line"]


def test_a_clip_with_no_name_cannot_be_keyed():
    record = one_reel("Reel 13", make_clip("", 100))
    live = one_reel("Reel 13", make_clip("", 100))
    per = rd.diff_record_vs_live(record, live, reel="Reel 13")
    assert per["counts"]["undetermined"] == 2
    assert per["counts"]["conflicts"] == 0


# ── The git half: the record is HEAD, never the worktree ───────

def _git(project, *args):
    result = subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True, encoding="utf-8", check=False, timeout=120)
    assert result.returncode == 0, result.stderr
    return result


def _project_with_commits(tmp_path):
    """A project repo: Reel 26 committed, Reel 13 committed then
    hand-trimmed on disk, Reel 09 never committed."""
    root = tmp_path / "project"
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "Reel_26.timeline.json").write_text(
        json.dumps(one_reel("Reel 26", make_clip("a.MXF", 100))),
        encoding="utf-8")
    (review / "Reel_13.timeline.json").write_text(
        json.dumps(one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069))),
        encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@localhost")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "round: promote Reel 26, Reel 13")
    # A hand trim after the commit: live on disk, not in HEAD.
    (review / "Reel_13.timeline.json").write_text(
        json.dumps(one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1100))),
        encoding="utf-8")
    (review / "Reel_09.timeline.json").write_text(
        json.dumps(one_reel("Reel 09", make_clip("x.MXF", 10))),
        encoding="utf-8")
    return str(root)


def test_the_record_is_head_not_the_worktree(tmp_path):
    """The hand trim above must read as a conflict. Reading the
    worktree file as the record would report agreement with itself."""
    project = _project_with_commits(tmp_path)
    found = rd.committed_document(project, "Reel 13")
    assert found["document"] is not None
    clips = found["document"]["tracks"][0]["clips"]
    assert clips[0]["record_out"] == 1069


def test_an_uncommitted_reel_has_no_record_and_says_so(tmp_path):
    project = _project_with_commits(tmp_path)
    found = rd.committed_document(project, "Reel 09")
    assert found["document"] is None
    assert "no committed snapshot" in found["reason"]


def test_a_reel_nothing_names_is_undetermined_not_absent(tmp_path):
    project = _project_with_commits(tmp_path)
    found = rd.committed_document(project, "Reel 99")
    assert found["document"] is None
    assert "names this reel" in found["reason"]


def test_committed_lookup_outside_any_repo_never_raises(tmp_path):
    found = rd.committed_document(str(tmp_path), "Reel 13")
    assert found["document"] is None
    assert found["reason"]


# ── The set: cross-reel summary, never refusing ────────────────

def test_the_set_reports_changed_unchanged_undetermined(tmp_path):
    project = _project_with_commits(tmp_path)
    report = rd.diff_records(project, ["Reel 26", "Reel 13", "Reel 09"])
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]
    assert report["undetermined_reels"] == ["Reel 09"]
    assert report["totals"]["conflicts"] == 1
    assert report["totals"]["agreements"] == 1


def test_a_single_reel_is_a_set_of_one(tmp_path):
    project = _project_with_commits(tmp_path)
    report = rd.diff_records(project, ["Reel 13"])
    assert report["changed"] == ["Reel 13"]
    assert set(report["reels"]) == {"Reel 13"}


def test_a_live_doc_override_beats_the_worktree(tmp_path):
    """A fresher live read (fixture, recorded projection, later a
    Resolve read) replaces the worktree file as the live side."""
    project = _project_with_commits(tmp_path)
    live = one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069),
                    make_clip("insert.MXF", 1200))
    report = rd.diff_records(project, ["Reel 13"],
                             live_by_reel={"Reel 13": live})
    per = report["reels"]["Reel 13"]
    assert per["counts"]["conflicts"] == 1
    assert "insert.MXF" in per["conflicts"][0]["detail"]


def test_an_unreadable_live_doc_is_undetermined_not_a_refusal(tmp_path):
    project = _project_with_commits(tmp_path)
    report = rd.diff_records(
        project, ["Reel 13"],
        live_notes={"Reel 13": "the live document cannot be read"})
    per = report["reels"]["Reel 13"]
    assert per["compared"] is False
    assert report["undetermined_reels"] == ["Reel 13"]


def test_diff_records_outside_any_repo_reports_never_raises(tmp_path):
    report = rd.diff_records(str(tmp_path), ["Reel 13"])
    assert report["undetermined_reels"] == ["Reel 13"]
    assert report["changed"] == []


# ── The render: summary first, capped detail ───────────────────

def _big_live(name, count):
    """The committed Reel 13 clip untouched, plus `count` added ones -
    so the diff is exactly `count` conflicts against one agreement."""
    clips = [make_clip(f"added-{index:02d}.MXF", 2000 + 100 * index)
             for index in range(count)]
    return one_reel(name, make_clip("LC4930.MXF", 590, 1069), *clips)


def test_the_render_leads_with_the_triage_and_caps_the_wall(tmp_path):
    """39 rows must not bury the summary: the first line says who
    changed, and the detail stops at the limit with an overflow line."""
    project = _project_with_commits(tmp_path)
    live = _big_live("Reel 13", 39)
    report = rd.diff_records(project, ["Reel 26", "Reel 13"],
                             live_by_reel={"Reel 13": live})
    text = rd.render_record_diff(report)
    first = text.splitlines()[0]
    assert "1 changed, 1 unchanged, 0 undetermined" in first
    assert "changed: Reel 13 (39)" in text
    assert "unchanged: Reel 26" in text
    assert text.count("CONFLICT") == rd.RECORD_DETAIL_LIMIT
    assert "... and 29 more" in text


def test_report_record_diff_prints_and_never_raises(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    report = rd.report_record_diff(project, ["Reel 26", "Reel 13"])
    assert report["changed"] == ["Reel 13"]
    printed = capsys.readouterr().out
    assert "Record-vs-reel diff" in printed
    assert "Reel 13" in printed


# ── The CLI: a set in, a printed diff out ──────────────────────

def test_cli_record_diff_json_is_a_set_in_and_json_out(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    assert rd.main([project, "--record-diff", "--reel", "Reel 26",
                    "--reel", "Reel 13", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]


def test_cli_record_diff_takes_a_live_doc_per_reel(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    fixture = tmp_path / "live-13.json"
    fixture.write_text(
        json.dumps(one_reel("Reel 13",
                            make_clip("LC4930.MXF", 590, 1069))),
        encoding="utf-8")
    assert rd.main([project, "--record-diff", "--reel", "Reel 13",
                    f"--live-doc=Reel 13={fixture}"]) == 0
    assert "0 changed, 1 unchanged" in capsys.readouterr().out


def test_cli_record_diff_with_a_broken_live_doc_stays_zero(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    assert rd.main([project, "--record-diff", "--reel", "Reel 13",
                    "--live-doc=Reel 13=/does/not/exist.json"]) == 0
    assert "1 undetermined" in capsys.readouterr().out
