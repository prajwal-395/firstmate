# Ren

Ren is an agent-driven video editor. It takes the raw footage from a shoot and
builds the edit on a real DaVinci Resolve timeline - selects and orders what is
said, cuts the A-roll and B-roll, subtitles, sound, music, grade and motion
graphics - and cuts reels from it. You drive it from a chat harness (Claude
Code), which calls the `ren` command.

The repository is the engine. Projects - footage, pipeline output, exports -
live outside it, in the projects root.

## Requirements

- Apple Silicon Mac. Ren's installer supplies Python 3.12, Node.js,
  `ffmpeg`/`ffprobe`, and the locked Python and renderer dependencies.
- To build/read/render timelines, install **DaVinci Resolve Studio** and
  set Resolve > Settings > System > General > External scripting using:
  **Local**. The free edition does not accept external scripts.
- To have an agent make creative decisions, use a supported chat harness
  signed in with its subscription account. An API key does not count.

## Quickstart

**1. Install.** Run the installer from Terminal. It downloads the current
main-branch source archive without cloning the repository, installs
Homebrew if needed, then installs Ren's runtime and system prerequisites:

```sh
curl -fsSL https://raw.githubusercontent.com/prajwal-395/video_editing_pilot/main/scripts/install_ren.sh | bash
```

The installer may ask macOS to install command-line tools or for an
administrator password while installing Homebrew. The current installer
builds from GitHub `main`; signed release installation is not available yet.
For an installed copy, `ren setup` repairs the runtime and can install
verified optional capability packs from `docs/DOWNLOAD_INVENTORY.md`:

```sh
ren setup
ren setup --with panns       # sound-event weights
ren setup --with mfa         # pinned forced-alignment models
ren setup --with ecapa       # pinned speaker encoder
ren setup --with deepfilter  # pinned dialogue-cleanup binary and weights
```

These packs are opt-in. The floating, non-commercial `buffalo_l` pack is
not offered by `ren setup`.

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
on), the Python dependency groups, ffmpeg, Node, the local models, your paths
and the chat harness. Each line is PASS, FAIL (every capability needs it) or
MISS (it limits some capabilities), with the fix, and the report ends with
which capabilities this Mac can run, run degraded, or cannot run. It changes
nothing. `--for <capability>` checks only what one operation needs.

```sh
ren doctor
ren doctor --for footage.search
```

**4. First project.**

```sh
ren init                              # create the projects root
ren new my-vlog --name "My Vlog"      # then copy the footage into its raw/
```

`ren uninstall` removes Ren's runtime and cache after confirmation. It keeps
model downloads by default; `ren uninstall --models` includes them. Projects,
footage, exports, shared asset libraries and `~/.config/ren` are always kept.
See [docs/INSTALLING_REN.md](docs/INSTALLING_REN.md) for the owned paths and
recovery details.

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
