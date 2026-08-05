# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## Brand Template Registry
The project uses a structured brand template system to provide stylistic inputs to pipeline steps without raw LLM context stuffing.
- **Brand Template Schema:** `library/schemas/brand_template.py` (has `StyleSlots`, `EffectSlots`, `ContentSlots`)
- **Default Template:** `library/templates/default_brand.yaml`
- **Registry & Loader:** `library/tools/brand_registry.py` (`load_brand_template`, `query_slots`, `validate_template`)
- **Pipeline Integration:** `brand_template` is an optional pipeline parameter in `edit_video/manifest.json`. `gather_step_inputs` injects requested slots (`brand_style`, `brand_effect`, `brand_content`) into steps that declare them in their manifest (e.g. `step_2_01`, `step_4_02`, `step_4_04`, `step_5_01`).

## Preset Library & Indexer
Reusable assets are indexed with companion `.meta.json` files.
- **Presets Directory:** `library/presets/` with subdirectories (`powergrades`, `fusion-macros`, `luts`, `dctls`, `fairlight`)
- **Metadata Schema:** `library/schemas/preset_metadata.py`
- **Indexer & Search:** `library/tools/preset_indexer.py` (`scan_library`, `find_presets`, `find_preset_for_mood`)
