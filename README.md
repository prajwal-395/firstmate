# Ren

Ren is an agent-driven video editor. It takes the raw footage from a shoot and
builds the edit on a real DaVinci Resolve timeline - selects and orders what is
said, cuts the A-roll and B-roll, subtitles, sound, music, grade and motion
graphics - and cuts reels from it. You drive it from a chat harness (Claude
Code), which calls the `ren` command.

The repository is the engine. Projects - footage, pipeline output, exports -
live outside it, in the projects root.

## Requirements

- A Mac (Apple Silicon) with **DaVinci Resolve Studio**. The free edition
  does not accept external scripts, so Ren cannot drive it. In Resolve:
  Settings > System > General > External scripting using: **Local**.
- Python **3.12** (not 3.13 or 3.14 - `requirements.txt` says why).
- `ffmpeg` and Node.js 18+: `brew install ffmpeg node`.
- Claude Code, signed in with your Claude account. An API key does not count.

## Quickstart

**1. Install.** Build the shared Python environment once per machine
([docs/ML_ENVIRONMENT.md](docs/ML_ENVIRONMENT.md) has the details), then
install `ren` into it from this checkout:

```sh
uv venv --python 3.12 ~/.local/share/vep/venv-py312
uv pip install --python ~/.local/share/vep/venv-py312/bin/python3 -r requirements.txt
~/.local/share/vep/venv-py312/bin/python3 -m pip install -e .
ln -s ~/.local/share/vep/venv-py312/bin/ren /opt/homebrew/bin/ren   # or add the venv's bin/ to PATH
scripts/install_node_deps.sh    # the subtitle renderer's Node dependencies
```

**2. Configure.** Machine paths live in one per-user file,
`~/.config/ren/config.env` - where projects go, where your sound-effect and
music libraries are:

```sh
ren config --init     # writes a commented starter; edit it
ren config            # shows every setting and where it came from
```

An exported variable beats that file, and that file beats a checkout's `.env`
(still read, see `.env.example`).

**3. Check.** `ren doctor` checks the Mac, Resolve (Studio, running, scripting
on), the Python environment, ffmpeg, Node, the local models, your paths and
the chat harness. Each line is PASS or FAIL with the fix. It changes nothing.

```sh
ren doctor
```

**4. First project.**

```sh
ren init                              # create the projects root
ren new my-vlog --name "My Vlog"      # then copy the footage into its raw/
```

## Working with Ren

Open Claude Code in this checkout and say what you want done - "cut a
60-second reel about the parking lot from my-vlog", "swap the second shot
of reel 2". The agent works goal first, through the `ren-co-editor`
skill ([.agents/skills/ren-co-editor/SKILL.md](.agents/skills/ren-co-editor/SKILL.md)):

1. **Inspect state** - `ren status`, `ren check`, `ren drift`.
2. **Search the footage, read Resolve** - `ren analyze` measures footage
   without editing it, `ren search my-vlog "..."` finds where something
   happens, `bin/resolve-axi` reads the live timeline.
3. **Choose a capability** - the narrowest one that does the job:
   `ren touch` for a small change to a built reel, `ren spec` for an
   editor's request, `ren propose` / `ren build` / `ren deliver` for
   reels, a scoped `ren edit --only <step>` to redo one stage.
4. **Execute**, answering any judgement Ren hands the agent as a file.
5. **Verify** - read back the timeline or the render against the goal.

The same verbs work by hand. A whole-pipeline run from raw footage is
still there as the compatibility path:

```sh
ren edit my-vlog                      # run every pipeline step
ren propose my-vlog                   # reel candidates for your approval
ren build my-vlog                     # build the approved reels in Resolve
ren deliver my-vlog 1                 # render reel 1 to a file
```

`ren --help` lists every verb and `ren <verb> --help` its options. A project
kept outside the projects root is addressed by its path instead of its slug.

## Where to read next

- [AGENTS.md](AGENTS.md) - how the engine works and the rules that govern it.
- [docs/](docs/) - design notes and the measurements behind them.
- `library/processes/edit_video/dag.json` - the pipeline's steps and their order.
