# Multimodal model-upgrade baseline (2026-10-16)

## Frozen run manifest

- **Code:** `bbb0ec85f0b3cac4ff7b4e5bea9dc2b018bb8b95` (`Document EVAL-04 visual acquisition results`, 2026-10-15). This is the current main commit on which the current visual and speech scorer reruns were made.
- **Machine:** MacBook Pro Mac17,2, Apple M5, 4 performance + 6 efficiency cores, 24 GB unified memory, macOS 26.3 (25D125), arm64.
- **Runtime:** `bin/vep` Python 3.12.13. The system `python3` is 3.14.4 and was not used for pipeline measurements.
- **Dependency lock:** `requirements/lock/macos-arm64-py312.txt`, SHA-256 `e0be88d09b206656ccf13310c5d2d4ef2588b4421a92b707320097abcbde8e73`.
- **Model IDs:** CLIP `openai/clip-vit-large-patch14`; OWLv2 `google/owlv2-base-patch16-ensemble`; SAM 2.1 `facebook/sam2.1-hiera-small`; Gemma `mlx-community/gemma-4-12b-it-4bit`; Voz 0.1.1; MFA `english_us_arpa` 3.0.0. The existing CLIP, OWLv2, SAM, and Gemma records do not pin an immutable publisher revision. This manifest records that as unknown; no checkpoint was downloaded or hashed.
- **Frozen fixtures:** visual held-out set, reviewed speech spans, FI-01 corpus manifest/report, E2E utility ledger, and object-continuity report are enumerated with SHA-256 in the [machine-readable manifest](2026-10-16-model-upgrade-baseline.json).
- **Evaluation code:** `library/tools/retrieval_eval.py`, `library/tools/speech_eval.py`, object detector/tracker records in `docs/benchmarks/object-detector-bakeoff-2026-10-13.md` and `docs/SUBJECT_MASKING_MEASURED.md`, EVAL-04 workflow report, and FI-01 manifest/harness.
- **Execution:** heavy visual retrieval used `heavy_work_lock` profile `semantics.analyse:inference`. `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` prevented downloads. Podcast/Apple 001 sources were read-only; no Resolve bundled files were inspected.

## Current-main reproductions

### BASE-010: visual retrieval

Reproduced the frozen 8-query visual evaluation on the current commit against the existing 1,092-frame, 10-second CLIP index. The index and embeddings were reused unchanged. Four queries have present labels, three are absent, and one expected-action query should be refused.

- Present-query macro recall: **0.1473** (95% query-bootstrap CI 0.0-0.2945, n=4).
- Macro precision over all present queries: **0.2000** (the three queries with returned intervals alone give 0.6667; keep denominators distinct).
- Absent queries: **0/3 abstained**, false-return rate **1.0**. The visual index has no supported score floor.
- Expected action: **1/1 correctly refused**.
- V02 recall 0.2558; V03 0.0 (no positive frame in top 40); V06 0.3333; V07 0.0 (two positive candidate frames, none survived interval ranking/merging).
- Cold first query: **5.411 s**. Seven subsequent same-process queries: **0.016-0.045 s**. Process peak RSS **1,054.2 MB**. Four useful intervals took 5.604 query-seconds, **1.401 s per useful interval**.
- Seeded random baseline: recall 0.0053, precision 0.0243 (500 samples, seed 20261016).

The result is in the JSON manifest, including every query latency and index digest. Reproduce with:

```sh
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 bin/vep -m library.tools.heavy_work_lock run \
  --owner vep-mu-baseline --profile semantics.analyse:inference -- \
  bin/vep -m library.tools.retrieval_eval \
  "$HOME/Documents/content_stuff/video_projects/lucie/geo-podcast" \
  --families visual --set library/tools/visual_retrieval_heldout_set.json \
  --out-json /tmp/visual-retrieval-heldout.json
```

The calibration-selected 60-second merge setting was tested on held-out once in the earlier benchmark and was not adopted: it raised recall but reduced useful intervals from 4 to 2 and widened ranges. No held-out tuning was done here.

### BASE-011: object tracking and costs

Current code's committed measurements reproduce these incumbent costs; the long full-source model pass was not repeated during this baseline lane. This report distinguishes saved measurements from a fresh current-main model run.

| Stage | Existing measurement | Coverage / memory | Separate stage timing |
|---|---|---|---|
| OWLv2 detection | 145.55 s for 40 sampled frames; recall 132/148 at IoU >= 0.5 | peak process RSS 1,174.56 MB | 3.95 s/frame median; load 1.6 s |
| Apple Vision foreground proposals | 6.53 s for same 40 frames | 36/148 boxes; peak RSS 26.73 MB | 0.21 s/frame |
| Still extraction/preprocessing | 40 stills extracted read-only | dimensions/indexing described in benchmark | not separately timed |
| SAM 2.1 propagation, Apple 001 | 1,615 samples over 17 clips / 807.0 s | 1,544/1,615 selected-subject samples (95.6%); 58.3 MB serialized masks | 6,558.7 s total (8.1x footage); max worker RSS 4.6 GB, with Metal memory outside RSS and 38.0 GB swap used at worst |
| Short clip propagation, Apple 001 clip_008 | 18 frames at 2 fps, 9 seeded objects | largest subject track 12/18 | 75.2 s, 4.18 s/frame |
| Seeded podcast laptop track | LCATL0013 30-40 s, 20 samples | OWLv2 seed and SAM mask cover 20/20 | extraction to 960-wide scratch video was not separately timed |
| SAM mask serialization | archive total 58.3 MB across the 001 run | external per-clip files, not state JSON | serialization time not separately retained |

Current GEO Podcast and Apple 001 project artifacts have zero `object_segmentation` outputs, so these are historical measurement artifacts, not live project evidence. The 001 aggregate is selected-track coverage, not detection recall across all objects. Full run details and caveats are in [subject masking measurements](SUBJECT_MASKING_MEASURED.md) and [object tracking coverage](2026-10-15-object-tracking-coverage.md).

### BASE-012: speech and diarization

Re-ran the pure scorer over the committed reviewed-span fixture with the current locked interpreter. The four reviewed correction spans contain 8 reference words: **WER 12.5%, CER 22.5%, speaker attribution 8/8, zero failures/abstentions**. This is a targeted diagnostic, not a corpus estimate. All speech conditions are unknown; start/end boundary errors remain unmeasured.

A recorded 0.84-second transcript reindex used Voz (3.050 s), language ID (0.156 s), and MFA (36.193 s), total **39.399 s** or 2,814.2 seconds per audio minute; audio extraction is excluded. The MFA setup dominates this tiny correction, so it is not full-file throughput.

Overlap has a separate constructed-podcast measurement: on held-out seed 22, per-camera energy reached precision 0.8168 / recall 0.8315. On the unmodified episode's 4.87 seconds of transcript-labelled overlap, it measured zero precision and recall; the overlaps may be duplicated words or mic bleed. The ECAPA single-track arm reached held-out precision 0.42 / recall 0.36 and did not ship. This synthetic overlap does not measure naturally noisy speech. Cross-recording speaker identity accuracy is unmeasured; M6 clock alignment is not identity evidence.

Three-family P4 agent labels now provide word, timing, overlap, condition, and speaker-relation agreement measurements. They do not establish speech-system accuracy: natural overlap, noisy-speech accuracy, and cross-recording speaker identity remain unmeasured.

### BASE-013: semantic-analysis cost

The cached Gemma EVAL-08 real-footage probe used **3 calls** over three source windows (10.0 s, 3.57 s, 10.0 s). Calls took 24.105, 19.940, and 24.527 s, 68.572 s total. Extraction used 8.048 s; scheduler execution was 106.050 s; peak RSS was 5,042.86 MB. Each call returned parseable `scene`, `camera`, `actions`, and `assessment` sections (12 section outputs total). That is parseability, not independently validated usefulness.

Input frame count and token counts were not emitted by the saved telemetry and remain null. A separate one-source ingest record has 24 Gemma calls in 835.58 s cold (3,078.6 MB peak RSS) and zero calls in a 10.09 s warm run with 12 reuses (2,296.5 MB). Both records are partial ingest; the warm pass changed the project stream configuration and is not a strict same-input comparison. No quality measure is inferred from these cost records.

### BASE-014: edit graph and actual Resolve render

The EVAL-04 evidence-to-edit report records graph construction from source evidence, zero graph constraint violations for completed visual graph runs, and four actual Resolve renders in Ren Qualification scratch projects through `resolve-axi` and Ren's render entry points. The two dialogue renders used 1080p24 review conform; two visual renders used native 29.97 cadence. `verify_render` passed every technical check except delivery loudness, which remained an unaltered source level. Runs without the required speech/action/object evidence abstained and emitted no cuts; the runner does not fabricate a graph to force a render. This confirms technical workflow execution, not editorial quality.

Re-ran both EVAL-04 briefs on the current commit against a manifest-only Project Model skeleton (zero observations; no media reads, model passes, or Resolve calls): both tasks reach evidence complete and plan complete, with edit_graph, render, and verify incomplete and the acceptance gate `valid=false` with unmeasured metrics null. The fail-closed mechanics reproduce on current main.

### BASE-015: coverage

The FI-01 corpus report provides source-artifact coverage for seven podcast sources: M0, M1, M2, M3, M3b, M6, and M7 are present on **7/7 (100%)**; M8 is **6/7 (85.7%)**; M4 is **1/7 (14.3%)**; M1b, M5, M3c, M3d, M3e, M3f, M9, and M10 are **0/7**. These are valid artifact-presence percentages, not percentages of seconds or frames analyzed.

For Apple 001, the historical SAM 2 run processed all 17 clips (100% clip-asset coverage) and 1,615 sampled frames; the selected subject track was valid on 1,544 samples (95.6%). Current project artifacts contain zero object segmentation outputs. A full time-covered percentage for every source/capability pair is not derivable from the committed records, so the JSON leaves it null rather than treating artifact presence as dense coverage. The only extra visual intelligence coverage in the current EVAL-04 evidence is sampled M2/M3 on IMG_1812 (144 frames) and IMG_1818 (89 frames), not all Apple 001 footage.

### BASE-016: cold versus warm

Cold and warm operations are separated where the logs permit: current visual search first query vs same-process follow-ups; one-source cold vs warm ingest; and historical MiniLM vs EmbeddingGemma indexing. Index-build times are not the same operation as resident inference and are labelled separately. The 85-minute EG2 build is an approximate resumed build record, not a warm query measurement.

## Existing metrics retained as historical references

| Capability | Historical result | Scope |
|---|---:|---|
| Speech retrieval | 0.85 macro recall | E2E utility benchmark |
| Visual retrieval | 0.15 macro recall | E2E utility benchmark |
| OWLv2 | 132/148 boxes | IoU >= 0.5, 40 frames |
| SAM 2.1 | 6,558.7 s / 807 s footage | 1,615 samples, 17 Apple 001 clips |
| MiniLM hybrid indexing | 5.2 s | 517 rows, Apple 001 |
| EmbeddingGemma unified indexing | approximately 85 min | Apple 001 full index, resumed build |

These scores have different fixtures and denominators and are not one combined comparison.

## Remaining baseline work

The machine-readable manifest records every reproduced metric, source report, fixture digest, and known gap. Existing reusable labels are recorded in the JSON manifest: Apple 001 action/object intervals (17 takes), podcast transcript text (25 short excerpts), and search labels (30 rows, but no exact matches to this frozen query set).

### Blind agent-label agreement for P1-P4

Three independent agent label sets were scored: OpenAI-family labeller A, OpenCode Muse-Spark labeller B, and Anthropic Claude Sonnet 5.5 tie-breaker C. The tie-break set contains 74 records: P1 16 query/source pairs, P2 12 stills, P3 40 frames, and P4 six audio windows. These are agent labels, not human annotations or gold truth. No Ren predictions were consulted while computing agreement.

Matching rules were fixed before scoring. P1 compares statuses exactly and positive sample-time sets exactly at the packet's 10-second sample points. For P2/P3 presence, aliases are table/round-table, plant/tree, man, woman, generic person, laptop, and hands; man, woman, and generic person remain separate presence classes. For box matching only, those three person labels are grouped as person. Within each source/time/class, boxes are paired one-to-one to maximize total IoU, with exactly the smaller class count paired (including zero-IoU pairs); box agreement means IoU >= 0.5. Visibility and confidence require exact equality within pairs. P4 lowercases words, strips punctuation, then uses sequence alignment; timing requires both boundaries within 100 ms, overlap and condition compare exactly, and speaker IDs are compared as same/different relations for adjacent aligned words.

| Packet / field | A-B | A-C | B-C |
|---|---:|---:|---:|
| P1 status | 14/16 (87.5%) | 14/16 (87.5%) | 16/16 (100%) |
| P1 exact positive sample-time sets | 12/16 (75.0%) | 13/16 (81.3%) | 12/16 (75.0%) |
| P2 class-presence sets | 0/12 | 0/12 | 0/12 |
| P2 matched boxes at IoU >= 0.5 | 12/55 (21.8%) | 6/48 (12.5%) | 30/54 (55.6%) |
| P2 visibility / confidence | 54/55 (98.2%) / 42/55 (76.4%) | 48/48 (100%) / 36/48 (75.0%) | 54/54 (100%) / 36/54 (66.7%) |
| P3 class-presence sets | 0/40 | 0/40 | 0/40 |
| P3 matched boxes at IoU >= 0.5 | 40/155 (25.8%) | 21/154 (13.6%) | 82/198 (41.4%) |
| P3 visibility / confidence | 150/155 (96.8%) / 140/155 (90.3%) | 140/154 (90.9%) / 101/154 (65.6%) | 160/198 (80.8%) / 137/198 (69.2%) |
| P4 aligned normalized words | 3,788 | 3,788 | 3,789 |
| P4 timing within 100 ms at both boundaries | 3,787/3,788 (99.97%) | 3,787/3,788 (99.97%) | 3,789/3,789 (100%) |
| P4 overlap / condition | 3,737/3,788 (98.7%) / 2,655/3,788 (70.1%) | 3,788/3,788 (100%) / 0/3,788 (0%) | 3,738/3,789 (98.7%) / 1,082/3,789 (28.6%) |
| P4 adjacent speaker relation | 3,480/3,782 (92.0%) | 3,532/3,782 (93.4%) | 3,305/3,783 (87.4%) |

The two status disagreements, Q01/LC4930 and Q03/LC4930, resolve to **present** by B+C majority. Across the 360 individual P1 sample points, 323 votes are unanimous and 37 differ; the pointwise majority marks 112 positive samples. Each annotator's sample agreement with that derived agent majority is A 94.4%, B 97.5%, and C 97.8%. This describes internal agent-label agreement only, not truth. P1 covers only LC4930 and LCATL0011, so it cannot establish full-corpus retrieval precision or recall.

P2/P3 class-presence sets are never identical across the three labelers; box agreement is limited, especially between A and C. C's P2 records have persistent per-source entity IDs but no explicit identity-link records, so no three-way cross-source identity-link agreement is claimed. P4 word text and timing agreement is high, while condition labels disagree sharply; the third labeler's overlap field also follows the no-overlap limitation in its report. These agent labels do not by themselves measure detector, tracker, transcription, diarization, natural-noise, or cross-recording identity accuracy. The complete pairwise metrics and adjudication provenance are in the [machine-readable manifest](2026-10-16-model-upgrade-baseline.json); the tie-breaker's method and limitations are in `/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data/vep-tiebreak-harness-baseline/report.md`.

Immutable publisher revisions, object preprocessing/serialization substage timing, Gemma frame/token counts, and full per-source temporal coverage also remain unmeasured.

Per-source coverage rows are in the JSON manifest. For each podcast source, the lane percentages are artifact presence (100% means a valid lane artifact exists for that source). For each Apple 001 source, the manifest reports selected-subject SAM sample coverage, sampled-frame denominator, per-clip wall time, and serialized mask size. These percentages do not claim dense evidence for every second of a source.
