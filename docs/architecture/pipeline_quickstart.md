# Video Editing Pipeline — Project 001

## What This Is

An automated video editing pipeline that takes raw `.MOV` footage and produces a fully assembled DaVinci Resolve timeline with subtitles, SFX, transitions, color grading, and music.

## Quick Start

### Prerequisites
- macOS with Apple Silicon (M1/M2/M3)
- DaVinci Resolve Studio (running, with a project open)
- Python 3.11+
- Node.js 18+ (for Remotion subtitle rendering)
- Homebrew: `brew install ffmpeg`
- Pip: `pip install whisperx librosa soundfile scipy torch torchaudio`

### Phase 1: Analysis (runs offline, ~30 min for 17 clips)
```bash
# 1. Run temporal index (speech, energy, motion, onsets)
cd video_analysis_docs
python3 ../process_design/library/steps/step_1_04_temporal_index/step.py < <(echo '{"raw_footage_files": [...]}')

# 2. Run vision analysis (dimension-indexed, ~30-45 min for 17 clips)
python3 vision_pipeline_v3.py --raw-dir ../raw --output-dir ../pipeline_output

# 3. Index SFX library (one-time, ~110 min for full AF-Next mode)
python3 sfx_pipeline.py --sfx-dir "/path/to/sfx library"
```

### Phase 2: Planning (conversational, with Antigravity)
Open Antigravity and work through the planning steps. Paste analysis results, get creative decisions back. Outputs accumulate in `pipeline_data.json`.

### Phase 3: Execution (needs Resolve open)
```bash
# 4. Render subtitle overlays (Remotion → ProRes 4444)
cd video_testing && python3 run_pipeline.py

# 5. Place SFX on timeline
python3 sfx_placer.py

# 6. Import into Resolve
python3 resolve_bridge.py
```

## Project Structure

```
001/
├── raw/                          ← 17 source .MOV files
├── pipeline_data.json            ← Complete pipeline state
├── pipeline_output/
│   ├── assembly_manifest.json    ← Final assembly instruction set
│   ├── fusion_comps/             ← Fusion compositions
│   ├── music/
│   │   ├── music_analysis.json   ← Beat grid, BPM, energy
│   │   └── music_premixed.wav    ← Background music track
│   ├── rendered_transcript.json  ← WhisperX output
│   └── subtitles.json            ← Subtitle groups
├── video_testing/                ← Execution tools
│   ├── resolve_bridge.py         ← Resolve API connection
│   ├── run_pipeline.py           ← Subtitle + mograph rendering
│   ├── sfx_placer.py             ← SFX scoring + Resolve placement
│   └── sfx_semantic_profiler.py  ← (Deprecated)
├── video_analysis_docs/          ← Analysis tools + guides
│   ├── vision_pipeline_v3.py     ← Dimension-indexed video analysis
│   ├── vision_architecture.md    ← Architecture documentation
│   ├── sfx_pipeline.py           ← AF-Next + librosa SFX profiling
│   ├── sfx_query.py              ← FAISS semantic search interface
│   ├── local_video_analysis_guide.md
│   └── sfx_analysis_guide.md
└── remotion-subtitles/           ← Subtitle rendering project
```

## Pipeline Framework

The orchestrated pipeline definition lives at:
```
process design/library/
├── processes/edit_video/
│   ├── dag.json              ← 19-node execution DAG
│   └── manifest.json         ← Process-level manifest
├── steps/                    ← 19 step definitions
│   ├── step_1_01_scan_project/
│   ├── step_1_02_catalog_footage/
│   ├── ...
│   └── step_6_02_validate_output/
└── tools/                    ← Shared utilities
```

## Full Documentation

See [pipeline_master_reference.md](file:///Users/prajwal/Documents/content_stuff/video_editing_pilot/docs/architecture/pipeline_master_reference.md) for the comprehensive guide covering all three layers (analysis, planning, execution).
