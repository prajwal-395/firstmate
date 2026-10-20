"""`ren round-page`: the review page per round (plan item T13).

Acceptance: on a fixture round with 2 reels, the page lists both
previews and their creative change summaries. A note posted from the
page appears in `ren notes <p>` with a note id and a file-time anchor,
and `ren revise` consumes it.

"Posted from the page" is the page's own code: the command its note
control builds is produced by running the page's script under Node, then
run through the `ren notes add` parser exactly as the agent (or the
editor) receiving it would. The project, its previews, its shadow
generations and its records all live under `tmp_path`. No Resolve, no
broker, no render.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

QUALIFICATION = "Ren Qualification"
FPS = 30.0
START = 108000
REELS = {1: "Reel 01 - opening", 2: "Reel 02 - pricing"}


def _snapshot(timeline: str) -> dict:
    return {
        "timeline": timeline, "project": QUALIFICATION, "fps": FPS,
        "start_frame": START, "end_frame": START + 900,
        "tracks": [{"type": "video", "index": 1, "name": "A-roll",
                    "clips": [{"unique_id": f"aroll-{timeline[5:7]}",
                               "name": "host.mov", "record_in": START,
                               "record_out": START + 900, "duration": 900,
                               "enabled": True,
                               "transform": {"ZoomX": 1.0, "ZoomY": 1.0},
                               "markers": []}]}],
        "markers": {"timeline": [], "notes": []},
    }


def _preview(project: Path, number: int, timeline: str, key: str) -> Path:
    """A preview as `ren preview` records one: file, sidecar, index."""
    from library.tools import reel_preview

    reel_dir = reel_preview.previews_dir(str(project)) / f"reel-{number:02d}"
    reel_dir.mkdir(parents=True, exist_ok=True)
    path = reel_dir / f"{key}.mp4"
    path.write_bytes(b"\x00" * 16)
    result = {"reel": number, "timeline": timeline, "engine": "filmcraft",
              "content_key": key * 4, "path": str(path),
              "duration_seconds": 30.0, "outcome": "rendered",
              "at": "2026-10-12T09:00:00",
              "verification": {"passed": True, "measured_seconds": 30.0,
                               "measured": {"width": 540,
                                            "height": 960}},
              "losses": [],
              "expected": {"seconds": 30.0, "fps": FPS, "frames": 900}}
    reel_preview._sidecar(path).write_text(json.dumps(result),
                                           encoding="utf-8")
    reel_preview._record_event(str(project), result)
    return path


@pytest.fixture
def round_project(tmp_path, monkeypatch):
    from library.tools import timeline_shadow, undo_journal
    from library.tools.project_layout import ProjectLayout

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("REN_NOTIFY", "0")
    # Hermetic under a chat harness: nothing here reads the host.
    for marker in ("REN_HOST", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        monkeypatch.delenv(marker, raising=False)
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\n'
        f'project_format_version: 1\nresolve:\n  project_name: "{QUALIFICATION}"\n',
        encoding="utf-8")
    ProjectLayout(str(root)).ensure()
    previews = {}
    for number, timeline in REELS.items():
        timeline_shadow.ShadowStore().record(
            project=QUALIFICATION, timeline_id=f"tl-{number:02d}",
            timeline_name=timeline, snapshot=_snapshot(timeline),
            source=timeline_shadow.OBSERVED, expected_head=0)
        previews[number] = _preview(root, number, timeline, f"k{number}")
    undo_journal.record_commit(
        str(root), parent=None, intent="tighten the cold open",
        timeline=REELS[1], agent="ren revise",
        changes=[{"op": "trim", "row": "video:A-roll", "item": "host.mov",
                  "before": {"start": 0, "end": 90},
                  "after": {"start": 12, "end": 90}}],
        artifacts={"fps": FPS})
    undo_journal.record_commit(
        str(root), parent=None, intent="swap the price card",
        timeline=REELS[2], agent="ren revise",
        changes=[{"op": "replace", "row": "video:Overlays",
                  "item": "price-card.png",
                  "before": {"start": 300, "end": 390},
                  "after": {"start": 300, "end": 390}}],
        artifacts={"fps": FPS})
    return root, previews


def _ren(argv: list[str], monkeypatch, capsys) -> str:
    """Run `ren <argv>` through the engine's own parser, in process."""
    import manage_project

    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["manage_project.py", *argv])
    try:
        manage_project.main()
    except SystemExit as stop:
        assert stop.code in (0, None), capsys.readouterr()
    return capsys.readouterr().out


def _page_command(page: Path, timeline: str, seconds: float,
                  words: str) -> str:
    """The command the page's note control builds, by the page's code."""
    text = page.read_text(encoding="utf-8")
    data = text.split('<script type="application/json" id="ren-round">')[1]
    data = data.split("</script>")[0]
    script = text.split("<script>")[-1].split("</script>")[0]
    driver = (
        "const window = globalThis;\n"
        "globalThis.CSS = {escape: (s) => s};\n"
        f"const document = {{getElementById: () => ({{textContent: "
        f"{json.dumps(data)}}}), querySelector: () => null}};\n"
        f"{script}\n"
        "const data = JSON.parse(document.getElementById().textContent);\n"
        f"const reel = data.reels.find((r) => r.timeline === "
        f"{json.dumps(timeline)});\n"
        f"process.stdout.write(window.renRoundNote(reel, {seconds}, "
        f"{json.dumps(words)}));\n")
    done = subprocess.run(["node", "-e", driver], capture_output=True,
                          encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_page_lists_both_previews_and_their_change_summaries(round_project):
    """Catches: a reel of the round missing from its page, a preview the
    page cannot play (a source that does not resolve to the file), or a
    reel shown without what changed on it."""
    from library.tools import round_page

    root, previews = round_project
    written = round_page.write(str(root))
    page = Path(written["page"])
    assert page == round_page.page_path(str(root), 1)
    text = page.read_text(encoding="utf-8")

    assert [r["timeline"] for r in written["reels"]] == [REELS[1], REELS[2]]
    for number, timeline in REELS.items():
        assert f'data-reel-timeline="{timeline}"' in text
        source = text.split(f'data-reel-timeline="{timeline}"')[1]
        source = source.split('src="')[1].split('"')[0]
        assert (page.parent / source).resolve() == previews[number].resolve()
    # The change summary is `ren commits`' own sentence for each reel.
    assert "Adjusted host.mov at 00:00:00:00." in text
    assert "Replaced price-card.png at 00:00:10:00." in text
    reel_one, reel_two = written["reels"]
    assert [c["intent"] for c in reel_one["changes"]] == [
        "tighten the cold open"]
    assert [c["intent"] for c in reel_two["changes"]] == [
        "swap the price card"]


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="the page's note control is run under Node")
def test_a_note_from_the_page_reaches_ren_notes_and_ren_revise(
        round_project, monkeypatch, capsys):
    """Catches: a note the page sends that the note store cannot take,
    one that lands without a file-time anchor or an id `ren notes`
    shows, one `ren revise` cannot find, and a noted-but-unrevised note
    missing from what the page says is waiting."""
    from library.tools import revise, round_page

    root, previews = round_project
    page = Path(round_page.write(str(root))["page"])
    words = "the price card lands early - hold it, it's rushed"
    command = _page_command(page, REELS[2], 12.0, words)
    argv = shlex.split(command)
    assert argv[:3] == ["ren", "notes", "add"]

    added = _ren(argv[1:], monkeypatch, capsys)
    assert "timeline frame 108360" in added
    note_id = added.split("note id: ")[1].split()[0]

    listed = _ren(["notes", str(root)], monkeypatch, capsys)
    assert note_id in listed
    assert f"watched  : 0:12.0 in {previews[2].name} (preview)" in listed
    assert words in listed

    rewritten = round_page.build(str(root))
    [waiting] = [q for q in rewritten["questions"] if q["kind"] == "note"]
    assert waiting["note_id"] == note_id
    assert waiting["reel"] == REELS[2]

    assert revise.main([str(root), "--note-id", note_id]) == 0
    assert "LLM_REQUEST_READY" in capsys.readouterr().out
    [record] = revise.records(str(root))
    assert record["note_id"] == note_id
    assert record["note"] == words
    assert record["reel"] == REELS[2]
    assert record["status"] == revise.STATUS_AWAITING
    assert not [q for q in round_page.build(str(root))["questions"]
                if q["kind"] == "note"]


def test_a_note_on_a_superseded_preview_is_refused(round_project):
    """Catches: a note typed on a preview the reel has moved past being
    anchored anyway, onto frames of a cut that is no longer there."""
    from library.tools import delivered_note_anchor as anchor

    root, previews = round_project
    newer = _preview(root, 2, REELS[2], "k9")
    with pytest.raises(anchor.AnchorRefused, match="not the newest preview"):
        anchor.add_note(str(root), "too late", at="0:12", reel="2",
                        file=str(previews[2]))
    added = anchor.add_note(str(root), "on time", at="0:12", reel="2",
                            file=str(newer))
    assert added["timeline_frame"] == START + 360
    assert added["watched"] == anchor.WATCHED_PREVIEW
    assert added["source_clip"]["unique_id"] == "aroll-02"
