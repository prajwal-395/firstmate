"""Creative history reads: `ren commits`, `ren branches`, `ren tag`.

Each test names a defect the read (or tag write) closes:

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
    """`commits`, `branches` and `tag` reach past both verb lists.

    The defect the two registries' own comments warn about: a verb the
    Python list knows but the native list does not is unreachable - the
    native front door refuses it before exec'ing the implementation.
    """
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    for name in ("commits", "branches", "tag"):
        assert by_name[name].module_argv == (
            "library.tools.creative_history", name)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    for name in ("commits", "branches", "tag"):
        assert f'name: "{name}"' in native
