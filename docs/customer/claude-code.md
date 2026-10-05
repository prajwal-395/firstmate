# Onboarding: Claude Code

Ren works as a co-editor inside Claude Code. This page is the whole
setup: install, sign in, and the two things Ren installs for you.

## Requirements

- Claude Code. Ren's setup is **tested against 2.1.289** (measured
  2026-10-05); `ren doctor` reports the version it found and fails a
  host below the tested-against version.
- Signed in with your **Claude account** (the subscription). An API key
  does not count - Ren is driven by a harness you are signed in to, and
  no Ren feature asks for an API key.

## Install

```sh
npm install -g @anthropic-ai/claude-code
```

Or download from <https://claude.com/claude-code>. Then sign in:

```sh
claude
```

Follow the prompt to sign in with your Claude account. `claude auth
status` shows the sign-in state; `ren doctor` reports it too.

## What Ren installs for you

1. **The skills.** Ren's skills live in `.agents/skills/` in the
   engine checkout. `.claude/skills` in the checkout is a symlink to
   that one source, so Claude Code finds them automatically when you
   open the checkout. If you keep the engine elsewhere, symlink the
   same way:

   ```sh
   ln -s ../.agents/skills ~/.claude/skills
   ```

2. **The marker hook.** `ren setup` merges a SessionStart entry into
   `~/.claude/settings.json` that runs `ren-marker-hook check` at every
   session start. It is disk-only: it lists Resolve marker notes no
   routing record has carried yet, and never probes a closed Resolve.
   To install it by hand:

   ```sh
   ren setup-hooks --app claude-code --write
   ```

   Without `--write` the command prints what would change and stops.
   The merge is idempotent - an entry naming `ren-marker-hook` is left
   alone - and the original file is backed up to `<file>.bak`.

## Verify

```sh
ren doctor
```

The `chat harness Claude Code` line reads PASS with the version and the
sign-in method, for example:

```
PASS  chat harness Claude Code  /opt/homebrew/bin/claude 2.1.289; claude.ai OAuth, max
```

## Troubleshooting

- **"no chat harness installed"** - Claude Code is not on PATH, or
  `ren doctor` cannot see it. Install it and re-run `ren doctor`.
- **"not signed in"** - run `claude` and sign in with your Claude
  account.
- **"signed in with ..., not a subscription"** - the account is an API
  key. Sign out (`claude logout`) and sign in with the subscription.
- **"below the tested-against version"** - upgrade Claude Code
  (`npm update -g @anthropic-ai/claude-code`).
- **Skills not found** - open Claude Code in the engine checkout, or
  check the `~/.claude/skills` symlink.
- **Marker notes never appear** - re-run
  `ren setup-hooks --app claude-code --write` and restart Claude Code.
