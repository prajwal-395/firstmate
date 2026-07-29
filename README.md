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
│   ├── steps/                 ← 20 atomic steps (phases 1–6)
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
| 1. Ingest & Index | 1.01–1.04 | Scan, catalog, analyze, and temporally index raw footage |
| 2. Plan | 2.01–2.05 | Creative direction, speech sequencing, music selection, spine assembly |
| 3. Rough Cut | 3.01–3.03 | A-roll assignment, B-roll selection, rough cut review |
| 4. Polish | 4.01–4.04 | Subtitles, transitions, VFX, SFX planning |
| 5. Finish | 5.01–5.04 | Color grading, audio mixing, manifest compilation |
| 6. Export | 6.01–6.02 | Render and validate output |

## Running the Pipeline

```bash
# From the repo root
python3 -m orchestrator run \
    --dag video_editing_pilot/library/processes/edit_video/dag.json \
    --library video_editing_pilot/library/ \
    --state '{"project_dir": "/path/to/footage"}' \
    --llm-backend openai \
    --llm-model gpt-4o
```

## Relationship to the Framework

This pilot uses the generic orchestrator, MCP servers, and library schemas from the [Process Automation Framework](file:///Users/prajwal/Documents/work_stuff/process%20design). The steps here are domain-specific implementations that plug into that framework. See the [framework docs](file:///Users/prajwal/Documents/work_stuff/process%20design/docs) for methodology.
