"""T8: "make 2 reels about pricing" in chat, with no verb, path or step id.

`carefree_two_reels_transcript.json` is a scripted chat. The PERSON speaks
outcomes only. The HOST answers each turn with the `ren` verbs that the
`ren-co-editor` skill's intent table names for that turn's intent. This
file checks the transcript against the skill, then replays the host's
commands through the real front door (`ren.cli.main` -> `manage_project`
-> `run_pipeline`) on the golden podcast's test doubles
(`golden_podcast.py`). Only the transcriber, the diarizer, the model and
the `osascript` notifier are answered, and Resolve is unreachable.

Each defect this pins is a place where the chat would have handed the
person a command, a file or a step id:

- T1: the scaffold brief `ren new` wrote reads as unattached ("Ren will ask").
- T2: the host's `ren edit` names no backend and still runs the agent one.
- T3: "keep both" is a verb; the proposals file is never hand-edited.
- T4: "hang on" / "carry on" are verbs; nobody touches `pipeline.hold`.
- T5: the run says it ended (one notification) and names a real file.
- T6: the episode is read where it lies; nothing is copied into `raw/`.
- T7: a missing projects root is created, not refused.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.scenarios import golden_edit, golden_podcast

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / ".agents" / "skills" / "ren-co-editor" / "SKILL.md"
TRANSCRIPT = Path(__file__).with_name("carefree_two_reels_transcript.json")

INTENT_TABLE_HEADING = "## What the person says -> what you do"

PLAN_INTENTS = ("New video", "Reels about X", "A note at a time", "Undo",
                "Two versions", "Ship it", "Where is X")
"""The rows plan item T8 names; the table may carry more, never fewer."""


def _transcript() -> dict:
    return json.loads(TRANSCRIPT.read_text(encoding="utf-8"))


def _verbs() -> set:
    from ren.commands import VERBS

    return {verb.name for verb in VERBS}


def _step_ids() -> set:
    from library.tools import processes

    ids = set(processes.node_owners())
    steps = REPO / "library" / "steps"
    ids |= {entry.name for entry in steps.iterdir()
            if entry.is_dir() and entry.name.startswith("step_")}
    return ids


def _skill_verbs(text: str) -> set:
    """Every `ren <verb>` the text names in code: a fenced block or a
    code span. Prose ("the ren verbs that deliver it") names none."""
    code = re.findall(r"```.*?```", text, re.DOTALL)
    code += re.findall(r"`[^`\n]+`", re.sub(r"```.*?```", "", text,
                                            flags=re.DOTALL))
    return {verb for chunk in code
            for verb in re.findall(r"(?<![\w-])ren ([a-z][a-z0-9-]*)", chunk)}


def _intent_rows() -> dict:
    """`{intent label: what-you-do cell}` off the skill's intent table."""
    text = SKILL.read_text(encoding="utf-8")
    assert INTENT_TABLE_HEADING in text, (
        f"the skill has no {INTENT_TABLE_HEADING!r} section")
    section = text.split(INTENT_TABLE_HEADING, 1)[1].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        match = re.match(r"\|\s*\*\*(.+?)\*\*(.*?)\|(.*)\|\s*$", line)
        if match:
            rows[match.group(1).strip()] = match.group(3)
    return rows


def _offences(line: str, *, paths_allowed: bool = False) -> list:
    """What in a chat line would make the person learn Ren.

    The person never needs the name `ren` at all. A verb is NAMED by
    invoking it (`ren ...`), by a hyphenated verb (`sign-off`), or in
    code; an English word that happens to spell a verb ("reels", "now")
    is not a verb named. A path is a slash, a home tilde or a file name.
    A step id is a node id or a step directory.
    """
    found = []
    words = re.findall(r"[A-Za-z0-9_./~-]+", line)
    lowered = [word.lower().strip(".,;:!?") for word in words]
    if "ren" in lowered:
        found.append("names ren")
    if "`" in line:
        found.append("code span")
    found += [f"flag {w}" for w in lowered if w.startswith("-")]
    found += [f"verb {w}" for w in lowered
              if "-" in w and w in _verbs()]
    found += [f"step id {w}" for w in lowered if w in _step_ids()]
    if not paths_allowed:
        found += [f"path {w}" for w in words
                  if "/" in w or w.startswith("~")
                  or re.search(r"\w\.[A-Za-z0-9]{2,4}$", w.rstrip(".,;:!?"))]
    return found


# ── the transcript, the skill and the registry agree ──────────────────

def test_person_lines_name_no_verb_path_or_step_id():
    for turn in _transcript()["turns"]:
        if "person" in turn:
            assert _offences(turn["person"]) == [], turn["person"]


def test_host_replies_hand_the_person_no_verb_or_step_id():
    """The host may hand over a file to watch, never a command or step."""
    for turn in _transcript()["turns"]:
        if "says" in turn:
            assert _offences(turn["says"], paths_allowed=True) == [], (
                turn["says"])


def test_every_verb_the_skill_names_is_registered():
    named = _skill_verbs(SKILL.read_text(encoding="utf-8"))
    assert named, "the skill names no ren verb at all"
    assert named - _verbs() == set(), (
        f"the skill names verbs ren/commands.py does not register: "
        f"{sorted(named - _verbs())}")


def test_the_skill_opens_with_the_intent_table():
    text = SKILL.read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1]
    first_section = re.search(r"^## .*$", body, re.MULTILINE).group(0)
    assert first_section == INTENT_TABLE_HEADING, (
        f"the skill's first section is {first_section!r}; it must open "
        f"with what the person says")
    rows = _intent_rows()
    missing = [intent for intent in PLAN_INTENTS if intent not in rows]
    assert missing == [], f"the intent table has no row for {missing}"
    for intent, cell in rows.items():
        assert _skill_verbs(cell), f"row {intent!r} names no ren verb"


def test_each_host_turn_runs_what_the_skill_says_for_its_intent():
    rows = _intent_rows()
    turns = _transcript()["turns"]
    for asked, answered in zip(turns[::2], turns[1::2]):
        assert "person" in asked and "host" in answered, (asked, answered)
        intent = asked["intent"]
        assert intent in rows, f"no intent row {intent!r} in the skill"
        prescribed = _skill_verbs(rows[intent])
        for command in answered["host"]:
            verb = shlex.split(command)[1]
            assert verb in prescribed, (
                f"{intent!r}: the host ran `ren {verb}`, which the skill's "
                f"row does not name (it names {sorted(prescribed)})")


# ── the replay ────────────────────────────────────────────────────────

@pytest.fixture
def chat(tmp_path, monkeypatch):
    """A host session: a harness drives, the projects root does not exist
    yet, the episode sits in the person's own folder."""
    import manage_project
    from library.tools import paths, project_registry
    from ren import hosts

    transcript = _transcript()
    monkeypatch.delenv(hosts.REN_HOST_ENV_VAR, raising=False)
    for _, markers in hosts.HOST_RUNTIME_MARKERS:
        for marker in markers:
            monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv(hosts.REN_HOST_ENV_VAR, transcript["harness"])
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.setenv("PIPELINE_HOST_HARNESS", "")

    root = tmp_path / "projects"
    for module in (paths, project_registry, manage_project):
        monkeypatch.setattr(module, "PROJECTS_ROOT", root)

    footage = tmp_path / "Downloads" / "geo-ep12"
    episode = golden_podcast.media(footage)

    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    notices = tmp_path / "osascript_calls.jsonl"
    stub = bindir / "osascript"
    stub.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{notices}"\n',
                    encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("REN_NOTIFY", raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    project = root / transcript["slug"]
    golden_edit.in_process_steps(monkeypatch)
    calls = golden_podcast.install(monkeypatch, lambda: project)
    return {"root": root, "project": project, "footage": footage,
            "episode": episode, "notices": notices, "calls": calls,
            "runs": []}


def _ren(chat: dict, monkeypatch, command: str) -> tuple:
    """Run one host command through the real front door, in process.

    `ren.cli.main` resolves the verb off the registry and would exec
    `manage_project.py <subcommand>`; that exec runs here instead, and
    `ren edit`'s exec of the runner runs `run_pipeline.main` here too.
    """
    import manage_project
    from library.processes.edit_video import run_pipeline
    from library.tools import native_refresh
    from ren import cli

    argv = shlex.split(command.format(footage=chat["footage"]))
    assert argv[0] == "ren", command
    real_run = subprocess.run

    def in_process(entry: list) -> int:
        saved = sys.argv
        sys.argv = entry
        try:
            if entry[0] == "-m":
                # A module verb's `__main__` is `raise SystemExit(main())`.
                sys.argv = entry[1:]
                raise SystemExit(importlib.import_module(entry[1]).main())
            elif Path(entry[0]).name == "run_pipeline.py":
                run_pipeline.main()
            else:
                manage_project.main()
            return 0
        except SystemExit as exit_:
            return exit_.code if isinstance(exit_.code, int) else int(
                exit_.code is not None)
        finally:
            sys.argv = saved

    def exec_vep(_vep, _root, entry):
        raise SystemExit(in_process(list(entry)))

    def run(cmd, *args, **kwargs):
        if (isinstance(cmd, list) and len(cmd) > 1
                and Path(str(cmd[1])).name == "run_pipeline.py"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = in_process([str(c) for c in cmd[1:]])
            chat["runs"].append({"argv": [str(c) for c in cmd[1:]],
                                 "stdout": out.getvalue()})
            return subprocess.CompletedProcess(cmd, code)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(cli, "_exec_vep", exec_vep)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(native_refresh, "ensure_current",
                        lambda *_a, **_k: None)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv[1:])
        except SystemExit as exit_:
            code = exit_.code if isinstance(exit_.code, int) else int(
                exit_.code is not None)
    monkeypatch.setattr(subprocess, "run", real_run)
    return code, out.getvalue(), err.getvalue()


def test_two_reels_about_pricing_end_to_end(chat, monkeypatch):
    from library.tools import reel_proposal, run_control, timeline_transcript
    from library.tools.reel_proposal import Approval

    transcript = _transcript()
    project = chat["project"]
    episode = chat["episode"]
    before = episode.stat()
    assert not chat["root"].exists()

    said = {}
    turns = transcript["turns"]
    for asked, answered in zip(turns[::2], turns[1::2]):
        for command in answered["host"]:
            code, out, err = _ren(chat, monkeypatch, command)
            verb = shlex.split(command)[1]
            said[verb] = (code, out, err)
            assert code == 0, (
                f"`{command}` exited {code}\nstdout:\n{out}\nstderr:\n{err}")

        if "proposes" in answered:
            moments = reel_proposal.read_proposal(
                str(reel_proposal.proposal_path(str(project))))
            assert len(moments) == answered["proposes"], moments
            assert {m.approval for m in moments} == {Approval.PROPOSED}
            text = json.loads(timeline_transcript.transcript_path(project)
                              .read_text(encoding="utf-8"))
            for moment in moments:
                spoken = golden_podcast.said_between(
                    text, moment.timeline_start, moment.timeline_end)
                assert golden_podcast.TOPIC in spoken, (moment, spoken)
        if "held" in answered:
            assert (run_control.hold_requested(str(project)) is not None) is (
                answered["held"])
        if "approved" in answered:
            approved = reel_proposal.approved_only(reel_proposal.read_proposal(
                str(reel_proposal.proposal_path(str(project)))))
            assert sorted(int(m.number) for m in approved) == (
                answered["approved"])

    # T7: `ren new` made the projects root it was missing.
    assert chat["root"].is_dir() and (project / "project.yaml").is_file()

    # T6: the episode was read where it lies; nothing was copied.
    config = yaml.safe_load((project / "project.yaml").read_text(
        encoding="utf-8"))
    assert config["source"]["footage_root"] == str(chat["footage"])
    raw = project / "raw"
    assert not raw.exists() or not any(raw.iterdir())
    after = episode.stat()
    assert (after.st_ino, after.st_size) == (before.st_ino, before.st_size)
    derived = json.loads(timeline_transcript.transcript_path(project)
                         .read_text(encoding="utf-8"))["derived_from"]
    assert derived["kind"] == "source" and derived["timeline"] is None
    assert chat["calls"]["asked"] == {"select_reels": 1, "judge_reels": 1}

    # T2: the host named no backend, and the run is the agent's.
    [edit_run] = chat["runs"]
    argv = edit_run["argv"]
    assert argv[argv.index("--full-auto") + 1] == "agent", argv
    assert "drives this run" in said["edit"][2]

    # T1: the scaffold brief is not an attached one, and the run says so.
    assert "brief.md is the unfilled scaffold, so Ren will ask" in (
        said["edit"][2])
    state = json.loads((project / "pipeline_data.json").read_text(
        encoding="utf-8"))
    assert state["creative_brief_attachment"]["reading"] == "none_declared"

    # T5: one notice for the run that ended, naming it; a progress event
    # per completed step; the runner's last line names a real file.
    notices = ([line for line in chat["notices"].read_text(
        encoding="utf-8").splitlines() if line.strip()]
        if chat["notices"].is_file() else [])
    assert len(notices) == 1, (
        f"the reels run must post exactly one completion notice: {notices}")
    assert "PARTIAL" in notices[0] and project.name in notices[0]
    events = run_control.read_run_status(str(project)).get(
        "progress_events") or []
    assert {e.get("step") for e in events} >= {"select_reels", "judge_reels"}
    last = edit_run["stdout"].strip().splitlines()[-1]
    assert any(Path(token.rstrip(".,;")).exists()
               for token in last.split() if "/" in token), last

    # T4: the handbrake was a verb both ways; nobody touched the file.
    assert "ren resume" in said["pause"][1]
    assert not (project / run_control.HOLD_FILE).exists()

    # "Where are we at?" after "keep both": building is what comes next,
    # which is what the host offers.
    assert f"Next: ren build {transcript['slug']}" in said["now"][1], (
        said["now"][1])
