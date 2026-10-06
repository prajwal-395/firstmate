"""Creative history: `ren commits`, `ren branches`, `ren tag`, `ren cherry-pick`.

Each test names a defect the read (or write) closes:

- a commit list whose rows do not name their timeline, so two reels'
  interleaved chains cannot be told apart;
- a `--timeline` filter that matches by substring, so asking for Reel 1
  leaks Reel 10's commits into its chain;
- a `--timeline` miss that says only "not found", so the captain is
  left guessing the reel's exact name instead of being told what has
  commits;
- a `--show` prefix two commits share that silently returns the first
  match, so a history read shows the WRONG commit;
- a `tag` for a commit id nothing answers, so a dangling pointer lands
  and a later read of the name fails instead of the write;
- a re-`tag` that silently moves a name, so what "v1" means is
  rewritten without anyone saying so;
- a replay with no provenance link, so a re-applied change reads as
  a hand-copied one nobody can trace;
- a replay over a later touch on the same clips, so the old changes
  silently clobber the later work;
- a replay call with two commits that guesses instead of refusing,
  so the second commit is dropped without a word;
- a verb registered in only one of the two front doors (`ren` Python
  vs the native CLI), so it is unreachable even though it is
  implemented.

Reads run against real commit/branch records under `tmp_path` (with a
minimal project.yaml so slug-or-path resolution answers); no Resolve
is touched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import creative_history as ch  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from ren import commands as ren_commands  # noqa: E402


def _project(tmp_path) -> str:
    folder = Path(tmp_path) / "project"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.yaml").write_text("name: History\nslug: history\n",
                                         encoding="utf-8")
    return str(folder)


def _commit(project, timeline, intent, parent=None):
    return uj.record_commit(
        project, parent=parent, intent=intent,
        changes=[{"op": "move", "item": "clip_001"}],
        agent="reel_touchup", timeline=timeline)


def test_list_rows_name_their_timeline(tmp_path, capsys):
    """Every listed commit says which timeline's chain it is on.

    The defect: a history view that interleaves two reels' chains
    without naming the timeline per row, so the captain cannot tell
    Reel 1's commits from Reel 10's.
    """
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    _commit(project, "Reel 10", "other reel work")
    assert ch.main(["commits", project]) == 0
    out = capsys.readouterr().out
    assert "[Reel 1]" in out
    assert "[Reel 10]" in out
    assert ch.main(["commits", project, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["timeline"] for row in rows} == {"Reel 1", "Reel 10"}


def test_timeline_filter_matches_exactly(tmp_path, capsys):
    """`--timeline Reel 1` never returns Reel 10's commits.

    The defect: a substring filter, so asking for one reel's chain
    leaks in the other's - "Reel 1" matching "Reel 10".
    """
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    _commit(project, "Reel 10", "other reel work")
    assert ch.main(["commits", project, "--timeline", "Reel 1"]) == 0
    out = capsys.readouterr().out
    assert "[Reel 1]" in out
    assert "Reel 10" not in out


def test_timeline_miss_names_the_timelines_with_commits(tmp_path, capsys):
    """A `--timeline` with no commits is refused naming what exists.

    The defect: a bare "not found", leaving the captain to guess the
    reel's exact Resolve name instead of being told which timelines
    have commits.
    """
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    _commit(project, "Reel 10", "other reel work")
    assert ch.main(["commits", project, "--timeline", "Reel 2"]) == 1
    err = capsys.readouterr().err
    assert "Reel 2" in err
    assert "Reel 1" in err
    assert "Reel 10" in err


def test_shared_prefix_refuses_naming_the_candidates(tmp_path, capsys):
    """A prefix every commit shares is refused, not first-matched.

    The defect: silently returning the first of several matches, so a
    history read shows the WRONG commit. The empty prefix is the
    deterministic case - every id starts with it.
    """
    project = _project(tmp_path)
    first = _commit(project, "Reel 1", "tighten opening")
    second = _commit(project, "Reel 1", "trim tail", parent=first["id"])
    assert ch.main(["commits", project, "--show", ""]) == 2
    err = capsys.readouterr().err
    assert first["id"][:12] in err
    assert second["id"][:12] in err


def test_show_unknown_id_is_refused_by_name(tmp_path, capsys):
    """`--show` for an id nothing answers exits 2 naming the id."""
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    assert ch.main(["commits", project, "--show", "deadbeef"]) == 2
    assert "deadbeef" in capsys.readouterr().err


def test_tag_to_unknown_commit_refuses_and_leaves_nothing(tmp_path, capsys):
    """A tag for no commit is refused, and no tag file lands.

    The defect: a dangling pointer - the write succeeds for a target
    nothing answers, and the failure surfaces later, on read.
    """
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    assert ch.main(["tag", project, "v1", "deadbeef"]) == 2
    assert "deadbeef" in capsys.readouterr().err
    assert uj.list_tags(project) == []


def test_retag_without_move_refuses_and_keeps_its_target(tmp_path, capsys):
    """Re-tagging a name without `--move` is refused; the target stays.

    The defect: silently moving the name, so what "v1" means is
    rewritten without anyone saying so.
    """
    project = _project(tmp_path)
    first = _commit(project, "Reel 1", "tighten opening")
    second = _commit(project, "Reel 1", "trim tail", parent=first["id"])
    assert ch.main(["tag", project, "v1", first["id"]]) == 0
    capsys.readouterr()
    assert ch.main(["tag", project, "v1", second["id"]]) == 1
    err = capsys.readouterr().err
    assert "v1" in err
    assert uj.read_tag(project, "v1")["commit"] == first["id"]


def test_retag_with_move_repoints_and_leaves_commits_alone(tmp_path, capsys):
    """`--move` re-points the name; the commits are untouched.

    The tag is a pointer, not history: moving it changes what the name
    answers, never the commits it pointed at before.
    """
    project = _project(tmp_path)
    first = _commit(project, "Reel 1", "tighten opening")
    second = _commit(project, "Reel 1", "trim tail", parent=first["id"])
    assert ch.main(["tag", project, "v1", first["id"]]) == 0
    capsys.readouterr()
    assert ch.main(["tag", project, "v1", second["id"], "--move"]) == 0
    out = capsys.readouterr().out
    assert second["id"][:12] in out
    assert uj.read_tag(project, "v1")["commit"] == second["id"]
    assert uj.read_commit(project, first["id"])["intent"] == "tighten opening"
    assert ch.main(["tag", project, "--list"]) == 0
    assert "v1" in capsys.readouterr().out


def test_history_verbs_are_registered_in_both_front_doors():
    """`commits`, `branches`, `tag` and `cherry-pick` reach past both lists.

    The defect the two registries' own comments warn about: a verb the
    Python list knows but the native list does not is unreachable - the
    native front door refuses it before exec'ing the implementation.
    """
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    for name in ("commits", "branches", "tag", "cherry-pick"):
        assert by_name[name].module_argv == (
            "library.tools.creative_history", name)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    for name in ("commits", "branches", "tag", "cherry-pick"):
        assert f'name: "{name}"' in native


def test_cherry_pick_replays_by_prefix_and_records_provenance(tmp_path,
                                                               capsys):
    """`ren cherry-pick` replays the prefixed commit onto the head.

    The defect: a replay path that only takes whole ids, or one that
    records the replay without naming its source - the change arrives
    with no trace of the commit it re-applies.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", "tighten opening")
    head = uj.record_commit(
        project, parent=source["id"], intent="other reel work",
        changes=[{"op": "move", "item": "clip_002"}],
        agent="reel_touchup", timeline="Reel 1")
    assert ch.main(["cherry-pick", project, source["id"][:12]]) == 0
    out = capsys.readouterr().out
    assert "replayed" in out
    assert source["id"][:12] in out
    replayed = uj.latest_commit_for_timeline(project, "Reel 1")
    assert replayed["parent"] == head["id"]
    assert replayed["changes"] == source["changes"]
    assert replayed["artifacts"] == {"cherry_picks": source["id"]}


def test_cherry_pick_refuses_unknown_and_conflicted(tmp_path, capsys):
    """Unknown ids exit 2; a clobbering replay exits 1 naming the clip.

    The defects: a replay of nothing recorded as a commit, and a
    replay that silently clobbers a later touch on the same clips.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", "tighten opening")
    _commit(project, "Reel 1", "sharpen cut", parent=source["id"])
    assert ch.main(["cherry-pick", project, "deadbeef"]) == 2
    assert "deadbeef" in capsys.readouterr().err
    assert ch.main(["cherry-pick", project, source["id"]]) == 1
    assert "clip_001" in capsys.readouterr().err


def test_cherry_pick_takes_one_commit_not_two(tmp_path, capsys):
    """Two positionals are refused instead of guessing which replays.

    The defect: silently replaying the first and ignoring the second,
    so the captain's second commit is dropped without a word.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", "tighten opening")
    assert ch.main(["cherry-pick", project, source["id"],
                    source["id"]]) == 2
    assert "one commit" in capsys.readouterr().err


def _proof_tracks(clip_name, record_in=0, record_out=100):
    """A V1 track read with one clip - the destination's currency."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": clip_name, "record_in": record_in,
         "record_out": record_out, "duration": record_out - record_in,
         "source_in_frame": 0,
         "source_out_frame": record_out - record_in,
         "left_offset": 0, "right_offset": 0},
    ]}]


def _proof_file(tmp_path, tracks):
    """The destination's current reads as a `--dest-tracks` file."""
    path = Path(tmp_path) / "dest.json"
    path.write_text(json.dumps(tracks), encoding="utf-8")
    return str(path)


def test_cherry_pick_proves_against_dest_tracks(tmp_path, capsys):
    """`--dest-tracks` proves the replay against current reads.

    The defects: a stale destination replayed because the chain was
    clean - the timeline moved on outside the record; and a missing
    proof file succeeding, so a typo proves against nothing.
    """
    from library.tools import timeline_diff

    project = _project(tmp_path)
    current = _proof_tracks("clip_001", 0, 100)
    source = uj.record_commit(
        project, parent=None, intent="tighten opening",
        changes=timeline_diff.diff_timelines(
            current, _proof_tracks("clip_001", 50, 150)),
        agent="reel_touchup", timeline="Reel 1")
    uj.record_commit(
        project, parent=source["id"], intent="other reel work",
        changes=timeline_diff.diff_timelines(
            _proof_tracks("clip_002", 0, 100),
            _proof_tracks("clip_002", 20, 120)),
        agent="reel_touchup", timeline="Reel 1")
    assert ch.main(["cherry-pick", project, source["id"][:12],
                    "--dest-tracks",
                    str(Path(tmp_path) / "missing.json")]) == 2
    assert ch.main(["cherry-pick", project, source["id"][:12],
                    "--dest-tracks",
                    _proof_file(tmp_path,
                                _proof_tracks("clip_001", 70, 170))]) == 1
    assert "clip_001" in capsys.readouterr().err
    assert ch.main(["cherry-pick", project, source["id"][:12],
                    "--dest-tracks", _proof_file(tmp_path, current)]) == 0
    assert "replayed" in capsys.readouterr().out


def test_dest_tracks_belongs_to_cherry_pick_alone(tmp_path, capsys):
    """`--dest-tracks` outside a replay is refused, not ignored.

    The defect: silently dropping the proof flag on a read, so the
    captain believes the listing was proven against current reads
    when nothing was proven.
    """
    project = _project(tmp_path)
    _commit(project, "Reel 1", "tighten opening")
    assert ch.main(["commits", project, "--dest-tracks",
                    _proof_file(tmp_path, [])]) == 2
    assert "cherry-pick" in capsys.readouterr().err
