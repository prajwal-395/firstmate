# Onboarding: Codex

Ren works as a co-editor inside the Codex CLI. This page is the whole
setup: install, sign in, and the two things Ren installs for you.

## Requirements

- The Codex CLI. Ren's setup is **tested against 0.159.3** (measured
  2026-10-05); `ren doctor` reports the version it found and fails a
  host below the tested-against version.
- Signed in with your **ChatGPT account**. An API key does not count -
  Ren is driven by a harness you are signed in to, and no Ren feature
  asks for an API key.

## Install

```sh
npm install -g @openai/codex
```

Or follow <https://openai.com/codex>. Then sign in:

```sh
codex login
```

Sign in with your ChatGPT account. `codex login status` shows the
sign-in state; `ren doctor` reports it too.

## What Ren installs for you

1. **The skills.** Ren's skills live in `.agents/skills/` in the
   engine checkout. Symlink them into Codex's skills directory:

   ```sh
   mkdir -p ~/.codex/skills
   ln -s <engine>/.agents/skills/* ~/.codex/skills/
   ```

2. **The marker hook.** `ren setup` merges a session_start entry into
   `~/.codex/hooks.json` that runs `ren-marker-hook check` at every
   session start. It is disk-only: it lists Resolve marker notes no
   routing record has carried yet, and never probes a closed Resolve.
   To install it by hand:

   ```sh
   ren setup-hooks --app codex --write
   ```

   Without `--write` the command prints what would change and stops.
   The merge is idempotent - an entry naming `ren-marker-hook` is left
   alone - and the original file is backed up to `<file>.bak`.

## Verify

```sh
ren doctor
```

The `chat harness Codex` line reads PASS with the version and the
sign-in, for example:

```
PASS  chat harness Codex  /opt/homebrew/bin/codex 0.159.3; ChatGPT sign-in
```

## Troubleshooting

- **"no chat harness installed"** - the Codex CLI is not on PATH, or
  `ren doctor` cannot see it. Install it and re-run `ren doctor`.
- **"not signed in"** - run `codex login` and sign in with your ChatGPT
  account.
- **"below the tested-against version"** - upgrade the Codex CLI
  (`npm update -g @openai/codex`).
- **Skills not found** - check the `~/.codex/skills` symlink.
- **Marker notes never appear** - re-run
  `ren setup-hooks --app codex --write` and restart Codex.
