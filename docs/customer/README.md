# Ren customer docs

Ren is an agent-driven video editor for macOS. You drive it from a chat
harness - Claude Code, OpenCode or Codex - that is signed in with its
subscription account. The harness calls the `ren` command; Ren answers
through the same chat.

These pages are for people who operate Ren. Engineering docs - design
notes, measurements, the pipeline's rules - live in
[../](../) and are not needed to run anything.

## Your first video

1. **Install Ren** - [one command](../INSTALLING_REN.md). It supplies the
   Python runtime, Node.js, ffmpeg and the locked dependencies.
2. **Sign in to a chat harness** and follow its onboarding page:
   [Claude Code](claude-code.md), [OpenCode](opencode.md) or
   [Codex](codex.md). `ren setup` configures the one it finds.
3. **Check the machine once**: `ren doctor`. Each line is PASS, FAIL or
   MISS, and every FAIL names its fix. This is the last command you type.
4. **Open your chat in the Ren checkout** and drop your footage into it
   - a file or a folder. Ren reads it where it lies and copies nothing.
5. **Say what you want**: "make 2 reels about pricing from this
   episode". The chat sets up the project, asks only what the footage
   cannot answer, and lists the reels it found. From here on you just
   talk:

| You say | What happens |
| --- | --- |
| "Keep both", "drop the second one" | The reels you keep are approved; the others are set aside with your reason |
| "Show me" | The reels are cut in DaVinci Resolve and a small preview is rendered; the chat gives you the file to watch |
| "The captions are too small", "at 0:12 the cut is early" | The note becomes a change to that reel, checked, and the chat says what changed and where |
| "Undo that" | The last change is reversed |
| "Give me two versions of the opening" | Both versions are built side by side; you pick one |
| "Ship it" | The reels you approved are rendered, checked, and the folder opens in Finder |
| "Where does she talk about pricing?" | The chat answers with the moments, or says it is not in this footage |
| "Hang on" / "carry on" | The run stops at the next safe point / continues |
| "Where are we at?" | What is done, what is running, and what comes next |

A run that takes a while posts a macOS notification when it ends
(`REN_NOTIFY=0` in the environment turns it off).

## The rules that hold for every harness

- **A subscription, not an API key.** Ren is driven by a harness you are
  signed in to. An API key does not count, and no Ren feature asks for
  one.
- **One skill source.** Ren's skills live in `.agents/skills/` in the
  engine checkout. Each harness reaches them through its own skills
  directory - the onboarding page says where.
- **The marker hook.** Ren installs a small disk-only hook into your
  harness that lists Resolve marker notes at session start. It never
  probes a closed Resolve, and `ren setup-hooks --list` shows where it
  goes.
- **`ren setup` configures the detected harness** automatically. To do
  it by hand: `ren setup-hooks --app <host> --write`.

## MCP access (optional)

Ren's verb catalog is also a [typed MCP server](mcp.md): one tool per
verb, read-only and `--dry-run` verbs runnable, write verbs refused
with the shell equivalent named. Nothing starts it automatically - add
it to your host's configuration to opt in.

## Troubleshooting

### Setup

| Symptom | Fix |
| --- | --- |
| `ren doctor`: "no chat harness installed" | Install one of the three hosts, then sign in. |
| `ren doctor`: "signed in with an API key" | Sign out and sign in with the subscription account. |
| `ren doctor`: version "below the ... minimum" | Upgrade the harness. |
| Harness does not find Ren's skills | Check the skills symlink named on its onboarding page. |
| Marker notes do not appear in chat | `ren setup-hooks --app <host> --write`, then restart the harness. |

### When Resolve refuses

Ren never opens a Resolve project or moves around the Project Manager
for you: opening a project changes your session. So when a build needs
Resolve, the chat may ask you to do one of these, and then you say
"carry on".

| The chat says | What to do in Resolve |
| --- | --- |
| Resolve scripting is not connected | Run DaVinci Resolve **Studio** (the free edition does not take scripts). Set DaVinci Resolve > Settings > System > General > External scripting using: **Local**, then restart Resolve. |
| No project named exactly "...", in the current folder | In the Project Manager, open the folder the project lives in. Ren looks in the folder that is open, never a near match. |
| The project is in the folder but is not the open project | Open that project. |
| Resolve is busy, or another Ren process holds it | Wait for the render or the other job to finish; the chat retries. |

### Where is my file?

Ask the chat "where is my file?" - it names every file. On disk:

| What | Where |
| --- | --- |
| Delivered reels (what you post) | `<project>/exports/` - the folder opens in Finder after delivery |
| Previews (to watch, not to post) | `<project>/pipeline_output/previews/<reel>/` |
| Your footage | Where you dropped it - Ren never copies or moves it |
| The project itself | Under your projects root; `ren config` shows where |
