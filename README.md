# Video Editing Pilot

This directory contains the **shortform video editing pipeline** — the first pilot implementation built on top of the [Process Automation Framework](file:///Users/prajwal/Documents/work_stuff/process%20design/README.md).

## What This Is

An end-to-end automated video editing pipeline for shortform (30–60s, vertical 9:16) content. It takes raw footage from a filming session and produces a finished, styled video — handling everything from media cataloging through color grading and rendering.

## Structure

```
├── docs/                      ← Pilot-specific documentation
│   ├── process_capture.md     ← Original process interview/capture
│   ├── process_decomposition.md ← Detailed step-by-step decomposition
│   └── style_specification.md ← Creator's editing style codified
│
├── library/                   ← Pilot step implementations
│   ├── steps/                 ← 28 atomic steps (phases 0–6)
│   ├── processes/edit_video/  ← DAG + manifest for the full pipeline
│   └── tools/                 ← Video-specific utilities
│       ├── frame_utils.py     ← Frame/seconds conversion (single rounding boundary)
│       └── sfx_query_bridge.py ← FAISS-based SFX search bridge
│
├── research/                  ← Reference research
│   ├── drp_reverse_engineering.md ← DaVinci Resolve programmatic control
│   └── pipeline_comparison.md    ← Comparison with Palmier Pro's approach
│
├── palmier-pro/               ← Reference codebase (Palmier Pro video editor)
│
└── tests/
    └── test_pipeline.py       ← Pipeline integration tests
```

## Pipeline Phases

| Phase | Steps | Purpose |
|-------|-------|---------|
| 0. Pre-requisites | 0.01 | Validate SFX library |
| 1. Ingest & Index | 1.01-1.07 | Scan, catalog, analyze, temporally index, prosody analysis, segmentation, OCR |
| 2. Plan | 2.01-2.06 | Creative direction, speech sequencing, music selection, analysis, spine |
| 3. Rough Cut | 3.01-3.03 | A-roll assignment, B-roll selection, rough cut review |
| 4. Polish | 4.01-4.06 | Subtitles, transitions, VFX, SFX planning, subtitle rendering, motion graphics |
| 5. Finish | 5.01-5.04 | Color grading, audio mixing, creative cohesion, manifest compilation |
| 6. Export | 6.01-6.02 | Render and validate output |

> **Note:** Step 2.03 is intentionally skipped in the numbering.
> 2.03 was merged into 2.02 during decomposition.

## Project Management CLI

Manage projects and run the pipeline using `manage_project.py`:

```bash
# Initialize the projects root directory
python3 manage_project.py init-root

# Create a new project
python3 manage_project.py new <slug> --name "Project Name" \
    [--client CLIENT] [--template TEMPLATE] [--source-type TYPE] \
    [--resolution WxH] [--fps FPS] [--resolve-name NAME] \
    [--tags TAGS] [--description DESC]

# List projects
python3 manage_project.py list [--status STATUS] [--client CLIENT]

# Show project status or config
python3 manage_project.py status <slug>
python3 manage_project.py info <slug>

# Run the pipeline
python3 manage_project.py run <slug> \
    [--from STEP] [--step STEP] [--dry-run] [--auto] [--review]

# Start dashboard
python3 manage_project.py dashboard [<slug>] [--port PORT]

# Relink and archive
python3 manage_project.py relink [<slug>] [--scan]
python3 manage_project.py archive <slug>
```

## Architecture Notes

Steps use one of two execution patterns:

| Pattern | Entry Point | Used By |
|---------|-------------|---------|
| **Deterministic** | `step.py` reads stdin JSON, writes stdout JSON | Most steps (0.01, 1.x, 2.x, 3.01, 3.03, 4.01, 4.05, 5.x) |
| **LLM + Bridge** | `handoff.md` (LLM prompt) + `bridge.py` (post-processor) | Creative steps (3.02, 4.02, 4.03, 4.04) |

Step 6.01 is a special case that directly scripts DaVinci Resolve.
Step 6.02 combines automated Python checks with an optional LLM review variant.

## Relationship to the Framework

This pilot uses the generic orchestrator, MCP servers, and library schemas from the [Process Automation Framework](file:///Users/prajwal/Documents/work_stuff/process%20design). The steps here are domain-specific implementations that plug into that framework. See the [framework docs](file:///Users/prajwal/Documents/work_stuff/process%20design/docs) for methodology.
