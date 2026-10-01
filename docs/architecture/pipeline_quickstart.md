# Pipeline Quickstart

Get a machine ready and run the pipeline end to end. For what the pipeline is
and the full CLI, see the [README](../../README.md); for pipeline operating
rules and the DaVinci Resolve constraints, see [AGENTS.md](../../AGENTS.md).

## Prerequisites

- macOS on Apple Silicon (M1/M2/M3) - the vision and audio models are MLX/Metal backed
- DaVinci Resolve Studio, running with a project open, before any Resolve-dependent step
- Python 3.11+, with `pip install -r requirements.txt`
- Node.js 18+ (Remotion renders the subtitle overlays)
- `ffmpeg` and `ffprobe` on `PATH` (`brew install ffmpeg`)
- The environment variables listed in AGENTS.md section 9 (`PIPELINE_PROJECTS_ROOT`,
  `PIPELINE_SFX_LIBRARY`, `PIPELINE_MUSIC_LIBRARY`, `RESOLVE_SCRIPT_API`,
  `RESOLVE_SCRIPT_LIB`, `HF_TOKEN`)

## Run it

The orchestrator is the supported path. It resolves the DAG at
`library/processes/edit_video/dag.json` (26 nodes across phases 0-6) and runs
every step through to an exported video file.

```bash
# One-time: create the projects root
python3 manage_project.py init-root

# Create a project, then drop the source footage into its raw/ directory
python3 manage_project.py new my-video --name "My Video"

# Run the whole pipeline (add --auto to auto-complete hybrid LLM steps)
python3 manage_project.py run my-video --auto
```

A project that lives outside `PIPELINE_PROJECTS_ROOT` is addressed by its path
instead of its slug and is driven where it sits:

```bash
python3 manage_project.py run "/Volumes/media/client shoot/001"
```

Resume a partial run with `--from <step_id>`, run a single step with
`--step <step_id>`, and preview the execution plan with `--dry-run`.

## Inspect a run

- `pipeline_data.json` at the project root holds all step outputs under `step_outputs`.
- `pipeline_output/assembly_manifest.json` is the compiled instruction set the render consumes.
- `ren status my-video` prints step completion state.
- `ren info my-video` prints the project configuration and run details.

## Full documentation

See [pipeline_master_reference.md](pipeline_master_reference.md) for the
comprehensive guide covering the analysis, planning, and execution layers.
