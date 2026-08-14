# Step 1.3: Semantic Analysis — Handoff Document

## Step Metadata

| Field | Value |
|-------|-------|
| Step ID | 1.3 |
| Name | Semantic Analysis |
| Determinism | **Deterministic** |
| Archetype | Evaluation & Judgment |
| Encoding Format | LLM Prompt (per-clip, independently) |
| Idempotent | No — judgment may vary between executions |
| Dependencies | Step 1.1 (runs in parallel with Step 1.2 and Step 1.4) |

---

## Where the analysis comes from

This step is deterministic: `step.py` runs the vision analyser in
`library/tools/analysis/vision_pipeline_v3.py`. No model is asked to author the
per-clip analysis document here.

- The schema that analyser emits - `scene[]`, `camera[]`, `actions[]`,
  `objects[]`, `assessment{}` - is documented in
  `docs/architecture/vision_architecture.md`.
- `library/tools/vision_schema_adapter.py` derives the consumer-facing view of
  that document, so downstream steps may address either view.

---

<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->
