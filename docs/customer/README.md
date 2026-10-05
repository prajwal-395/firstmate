# Ren customer docs

Ren is an agent-driven video editor for macOS. You drive it from a chat
harness - Claude Code, OpenCode or Codex - that is signed in with its
subscription account. The harness calls the `ren` command; Ren answers
through the same chat.

These pages are for people who operate Ren. Engineering docs - design
notes, measurements, the pipeline's rules - live in
[../](../) and are not needed to run anything.

## Getting started

1. [Install Ren](../INSTALLING_REN.md) - one command, supplies the
   Python runtime, Node.js, ffmpeg and the locked dependencies.
2. Pick your chat harness and follow its onboarding page:
   - [Claude Code](claude-code.md)
   - [OpenCode](opencode.md)
   - [Codex](codex.md)
3. `ren doctor` checks the machine, including the harness. Each line is
   PASS, FAIL or MISS, with the fix.
4. `ren new <slug> --name "<name>"` creates the first project; copy
   footage into its `raw/`.

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

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ren doctor`: "no chat harness installed" | Install one of the three hosts, then sign in. |
| `ren doctor`: "signed in with an API key" | Sign out and sign in with the subscription account. |
| `ren doctor`: version "below the ... minimum" | Upgrade the harness. |
| Harness does not find Ren's skills | Check the skills symlink named on its onboarding page. |
| Marker notes do not appear in chat | `ren setup-hooks --app <host> --write`, then restart the harness. |
