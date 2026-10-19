# Resolve control

All production Resolve reads and writes use [resolve-axi](../../resolve-axi/SKILL.md). Discover the current command with `bin/resolve-axi --help` and the relevant subcommand's `--help`; use the generated capability references to determine whether the requested operation is implemented, qualified and production-enabled.

Do not call `DaVinciResolveScript`, `dvr.scriptapp`, Resolve MCP tools or UI controls as alternate production paths. AXI owns target resolution, authorization, broker/lease coordination, execution and the receipt. Low-level scripts are for explicitly authorized diagnostics or registered backend implementation work, not ordinary edits.

The operation-specific result must be read back against the requested target and effect. If the operation cannot be expressed by an enabled capability, report that limitation rather than substituting a rebuild or a different editorial result.
