# Onboarding: OpenCode

Ren works as a co-editor inside OpenCode. This page is the whole
setup: install, sign in, and the two things Ren installs for you.

## Requirements

- OpenCode. Ren's setup is **tested against 1.18.34** (measured
  2026-10-05); `ren doctor` reports the version it found and fails a
  host below the tested-against version.
- Signed in with a **provider account** (OAuth). An API key does not
  count - Ren is driven by a harness you are signed in to, and no Ren
  feature asks for an API key.

## Install

Follow <https://opencode.ai> for the package manager of your choice,
for example:

```sh
brew install opencode
```

Then sign in:

```sh
opencode
```

Sign in with a provider account from the auth flow. OpenCode stores the
sign-in in `~/.local/share/opencode/auth.json` (or
`$XDG_DATA_HOME/opencode/auth.json`); `ren doctor` reads only each
provider's name and type - never a token - and reports the state.

## What Ren installs for you

1. **The skills.** Ren's skills live in `.agents/skills/` in the
   engine checkout. Symlink them into OpenCode's skills directory:

   ```sh
   mkdir -p ~/.config/opencode/skills
   ln -s <engine>/.agents/skills/* ~/.config/opencode/skills/
   ```

2. **The marker hook.** `ren setup` copies the plugin to
   `~/.config/opencode/plugins/ren-marker-hook.js`. It is disk-only: it
   lists Resolve marker notes no routing record has carried yet at
   session start, and never probes a closed Resolve. To install it by
   hand:

   ```sh
   ren setup-hooks --app opencode --write
   ```

   Without `--write` the command prints what would change and stops.
   The copy is idempotent - an existing plugin is backed up to
   `<file>.bak` and replaced only by the newer template.

## Verify

```sh
ren doctor
```

The `chat harness OpenCode` line reads PASS with the version and the
OAuth providers, for example:

```
PASS  chat harness OpenCode  /Users/you/.local/bin/opencode 1.18.34; OAuth: anthropic
```

## Troubleshooting

- **"no chat harness installed"** - OpenCode is not on PATH, or `ren
  doctor` cannot see it. Install it and re-run `ren doctor`.
- **"not signed in"** - run `opencode` and complete the provider sign-in
  flow.
- **"signed in with an API key, not a subscription"** - the auth file
  holds an API key. Sign out and sign in with a provider account.
- **"below the tested-against version"** - upgrade OpenCode.
- **Skills not found** - check the `~/.config/opencode/skills`
  symlink.
- **Marker notes never appear** - re-run
  `ren setup-hooks --app opencode --write` and restart OpenCode.
