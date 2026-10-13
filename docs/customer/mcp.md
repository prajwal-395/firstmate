# Ren as MCP tools

Ren's verb catalog is also a typed MCP server: every `ren` verb appears
once as a tool, with its argument schema and effect annotations derived
from the same registry `ren --help --json` prints. An MCP-capable host
calls tools instead of building shell strings or parsing prose.

Nothing starts the server automatically. You opt in by adding it to
your host's configuration below; restarting the host then lists tools
named `ren_status`, `ren_reels`, `ren_search` and the rest.

## What the tools do

- **Read-only verbs run directly**: `ren_status`, `ren_now`,
  `ren_reels`, `ren_search` and everything else whose effects are
  reads. They change nothing.
- **`--dry-run` verbs run with `--dry-run` forced**: they show what
  would change and change nothing. Passing `dry_run: false` is
  refused.
- **Write verbs are listed but refused**: discovery is complete (one
  tool per catalog entry), while a call that could write files,
  Resolve state or renders answers with a structured refusal naming
  the shell equivalent (`run ren <verb> in a shell`). A later Ren
  release enables them on this same server.
- Every call answers with one typed object: whether it worked, the
  exit code, the captured output, and the `REN_RESULT` refusal fields
  when the verb refused - the same codes the shell gets.

## Setup

The server is the engine you already installed. Point your host at it
with the engine's absolute path (the checkout `ren` runs from):

```sh
REN_ENGINE_ROOT=$(ren config --show engine 2>/dev/null || pwd)
echo "$REN_ENGINE_ROOT"
```

Use that path for `<engine>` below. The server needs no model, no
Resolve and no footage to list its tools.

### Claude Code

```sh
claude mcp add ren -- <engine>/bin/vep -m ren.mcp_server
```

`bin/vep` runs the engine's own interpreter, so no virtualenv setup is
needed. Verify with `/mcp` inside Claude Code: the `ren` server lists
its tools.

### Codex

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.ren]
command = "<engine>/bin/vep"
args = ["-m", "ren.mcp_server"]
```

Restart Codex. `ren doctor` reports the harness it found; the MCP
server needs no sign-in beyond the one Codex already has.

### OpenCode

Add to `opencode.json` in your Ren checkout:

```json
{
  "mcp": {
    "ren": {
      "type": "local",
      "command": ["<engine>/bin/vep", "-m", "ren.mcp_server"],
      "enabled": true
    }
  }
}
```

Restart OpenCode. The `ren-co-editor` skill keeps working as before -
the tools are a second route to the same verbs.

### Claude Desktop (or any host taking a JSON server list)

```json
{
  "mcpServers": {
    "ren": {
      "command": "<engine>/bin/vep",
      "args": ["-m", "ren.mcp_server"]
    }
  }
}
```

## Verify

Ask the host to list its MCP tools: one entry per `ren --help` verb,
104 at the time of writing. Then:

```text
Call ren_status on <your project path>.
```

A read verb answers with its output as typed JSON. A write verb (for
example `ren_new`) answers with a refusal that names the shell
command instead - that refusal is the server working, not an error.

## Troubleshooting

- **The host lists no `ren_` tools** - check `<engine>` points at the
  checkout holding `ren/mcp_server.py`, and that `<engine>/bin/vep`
  runs (`<engine>/bin/vep -m ren.mcp_server --help` prints usage).
  Then restart the host: MCP servers load at startup.
- **A tool call times out** - one call never runs past 100 seconds;
  the answer names that. Long pipeline runs stay in the shell
  (`ren edit`), not in a tool call.
- **A read verb refuses** - the refusal carries the fix (usually a
  missing project or index) exactly as the shell refusal does; follow
  it and call again.
- **Nothing here changes Resolve** - the server only runs verbs that
  cannot write. Builds, revisions and deliveries stay with the chat
  driving the shell.
