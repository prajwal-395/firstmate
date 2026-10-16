# Model research watchlist

`data/model_watch/candidates.json` is the candidate record store. `library.tools.model_watch` is a read-only reader and flagger. The seed includes all rows in section 15.2 of the 2026-10-15 model-upgrade punchlist, plus additional models and implementations named in its capability, license, and benchmark sections. It preserves rejected candidates and their reconsideration conditions.

## Read and triage

From the repository root:

```sh
python3 -m library.tools.model_watch
python3 -m library.tools.model_watch --json
python3 -m library.tools.model_watch --refresh-date 2026-11-15
```

The default report flags candidates only when task relevance, runnable code, weights, Apple Silicon compatibility, and a permissive or conditional license are all recorded. This is a queue for evaluation, not an adoption recommendation. `--refresh-date` finds records whose source review is at least 30 days old; use `--refresh-after-days` to change that interval. Unknown fields intentionally do not pass the evaluation gate.

## Refresh procedure

1. Review new and changed releases from Hugging Face, arXiv, GitHub, MLX and Core ML/ANE projects, native C++/Metal projects, Desert Ant Labs, Meta, NVIDIA, Qwen, ByteDance, Tencent, Ai2, and other relevant publishers. Search papers and repositories separately from checkpoint/model-card availability.
2. Start with the maintained source URLs in each record. Verify the publisher's release date, latest update/revision, runnable repository and exact checkpoint revision independently. Store the publisher's revision or commit identifier; never identify a checkpoint by inspecting or hashing Resolve's bundled files.
3. Update `last_updated` for every source review. Set `release_date` only when verified; otherwise keep `null`. Use `available`, `unavailable`, `unverified`, or `not-applicable` for code and weights. Describe Apple Silicon support as native, compatible, incompatible, or unknown, and identify whether it is a claim or a local measurement.
4. Record publisher performance under `performance.reported` with a scope and source. Put Ren runs under `performance.measured`, including fixture/split, precision, runtime, latency, peak memory, and measurement source. Never mix these categories. Keep model license distinct from code license and record license evidence, commercial status, and research status. Provisional punchlist summaries are leads, not final artifact clearance; the authoritative artifact decisions are maintained by the license lane.
5. Map the candidate to an existing capability id or a concrete missing capability. Prioritize candidates that could replace expensive work and small specialists with a relevant narrow task. Mark irrelevant candidates `relevance: "irrelevant"`, set `decision.status: "rejected"`, and state why. Do not delete rejected records.
6. Set a reconsideration trigger for rejected or quarantined candidates. When a faster runtime, better checkpoint, changed license, or new task changes the reason, reopen the same record and document the new evidence rather than creating a duplicate.
7. Add newly discovered candidates with a stable, lowercase identifier. Check for duplicates by normalized name and publisher. Keep source URLs and timestamps in the data; do not add a second registry or change canonical Ren schemas.
8. Run the reader against the updated file and validate its JSON. Add or update a focused fixture when changing the reader's contract. Keep model/checkpoint downloads and benchmarks in their owning qualification lane; this watchlist itself does not download or run candidates.

## Record meanings

- `reported` means a publisher or paper reported it. `measured` means Ren measured it using a named setup. An empty list means no value is recorded.
- `license.status` is an evidence state, while `commercial` and `research` describe use eligibility. A `conditional`, `research-only`, `unresolved`, or `unknown` record is not a commercial approval.
- A null release date means the exact release date is not established. `last_updated` means the watch record was last checked, not the model's release date.
- `decision.status` supports `watch`, `candidate`, `rejected`, and `adopted`. Changing a default or adopting a model requires the qualification scorecard against the incumbent, including quality, cold/warm latency, peak memory, and end-to-end cost.

## Seed limitations

This initial seed records the punchlist's candidate set and known baseline facts, with targeted primary-source checks for prominent Apple-native and retrieval candidates. Many rows intentionally retain `unverified` or `unknown`; the source review date does not mean every upstream artifact has been re-audited. Fill those fields during scheduled refreshes before evaluation or distribution decisions.
