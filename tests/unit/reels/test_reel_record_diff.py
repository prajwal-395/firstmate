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

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

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

def test_identical_documents_and_read_noise_hold_what_the_commit_recorded():
    """Resolve read-back noise (transform_drift.UNMOVED = 1e-3) is not a
    hand edit. Remove the tolerance and every reel diffs itself."""
    record = one_reel("Reel 26", make_clip("a.MXF", 100),
                      make_clip("b.MXF", 200))
    live = one_reel("Reel 26", make_clip("a.MXF", 100),
                    make_clip("b.MXF", 200,
                              transform={"Pan": 0.0005, "Tilt": 0.2505}))
    per = rd.diff_record_vs_live(record, live, reel="Reel 26")
    assert per["compared"] is True
    assert per["counts"] == {"agreements": 2, "conflicts": 0,
                             "undetermined": 0}
    assert "hold exactly what the commit recorded" in per["line"]


# ── The pure diff: conflicts ────────────────────────────────────

def test_each_kind_of_change_is_one_conflict_that_names_itself():
    """A move is one conflict naming both spans (not removed-plus-added,
    which would double the triage); a reframe names the axis; an added
    clip is unclaimed; a whole new row is ONE finding, not one per clip."""
    base = make_clip("LC4930.MXF", 590, 1069)
    rows = [
        (one_reel("Reel 13", base),
         one_reel("Reel 13", make_clip("LC4930.MXF", 600, 1079)),
         0, ["moved", "@590..1069", "@600..1079"]),
        (one_reel("Reel 13", base),
         one_reel("Reel 13", make_clip("LC4930.MXF", 590, 1069,
                                       transform={"Pan": -24.0})),
         0, ["transform.Pan 0 -> -24"]),
        (one_reel("Reel 13", base),
         one_reel("Reel 13", base, make_clip("insert.MXF", 2000)),
         1, ["unclaimed by it", "insert.MXF"]),
        (one_reel("Reel 13", base),
         doc("Reel 13", ("video", 1, "V1", [base]),
             ("video", 4, "Captions", [make_clip("cap.mov", 100),
                                       make_clip("cap2.mov", 200)])),
         1, ["Captions", "2 clip(s)"]),
    ]
    for record, live, agreements, needles in rows:
        per = rd.diff_record_vs_live(record, live, reel="Reel 13")
        assert per["counts"] == {"agreements": agreements, "conflicts": 1,
                                 "undetermined": 0}, needles
        detail = per["conflicts"][0]["detail"]
        for needle in needles:
            assert needle in detail


# ── The pure diff: undetermined is a first-class answer ─────────

def test_an_unreadable_record_is_undetermined_never_raised():
    per = rd.diff_record_vs_live({"metadata": {}}, one_reel("R",),
                                 reel="Reel 13")
    assert per["compared"] is False
    assert per["counts"]["undetermined"] == 1
    assert "committed record cannot be read" in per["line"]


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


# ── The set: cross-reel summary, never refusing ────────────────

def test_the_set_reports_changed_unchanged_undetermined(tmp_path):
    """Reel 13 was hand-trimmed after the commit: it reads changed only
    because the record is HEAD, never the worktree file."""
    project = _project_with_commits(tmp_path)
    assert rd.committed_document(project, "Reel 13")["document"][
        "tracks"][0]["clips"][0]["record_out"] == 1069
    assert "no committed snapshot" in rd.committed_document(
        project, "Reel 09")["reason"]
    report = rd.diff_records(project, ["Reel 26", "Reel 13", "Reel 09"])
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]
    assert report["undetermined_reels"] == ["Reel 09"]
    assert report["totals"]["conflicts"] == 1
    assert report["totals"]["agreements"] == 1


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


# ── The CLI: a set in, a printed diff out ──────────────────────

def test_cli_record_diff_json_is_a_set_in_and_json_out(tmp_path, capsys):
    project = _project_with_commits(tmp_path)
    assert rd.main([project, "--record-diff", "--reel", "Reel 26",
                    "--reel", "Reel 13", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["changed"] == ["Reel 13"]
    assert report["unchanged"] == ["Reel 26"]


