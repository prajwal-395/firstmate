# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.
- **Pipeline Data Contracts**: When modifying pipeline steps, ensure outputs specified in `manifest.json` writes match the DAG edge data mappings (`dag.json`) and the downstream step inputs exactly. Discrepancies (e.g. `timed_spine` vs `audio_spine`, missing `b_roll_interjections`) cause data drops or runtime failures. See root `AGENTS.md` section 6.
## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## Project Rules

The comprehensive project rules (Resolve scripting, Fusion safety, pipeline architecture, brand/preset/VQA systems) live in the root [AGENTS.md](../AGENTS.md). That file is the single source of truth read by all agents (firstmate crewmates, Claude Code via CLAUDE.md symlink, Antigravity, etc.).

Do not duplicate root AGENTS.md content here. This file is for Antigravity-specific workspace notes only.
