# Ren end-to-end utility benchmark

> Status note, 2026-10-15 (CAP-01): the two operational failures recorded
> below - the `temporal.index` routing failure and the depth lane's missing
> checkpoint - were measured before
> [PR 2503](https://github.com/prajwal-395/video_editing_pilot/pull/2503)
> merged. The semantic-to-temporal route has since been re-qualified on a
> temporary project with every analysis capability built, and the pinned
> depth assets now resolve on this machine. The numbers below are the frozen
> pre-merge record and are kept as-is.

This FI-01 baseline combines the existing corpus metric and retrieval evaluators
with a separately measured one-source operational pass. The ledger always lists
all six phases. A phase can be `measured` even when an ingest is partial; its
`detail.completion` and failure fields describe what actually completed. Unknown
counts remain null rather than being reported as zero.

## Rerun

Run the registered FI-01 corpus and retrieval benchmark from the repository root.
The project and source-memory root are read-only; write the ledger outside the
checkout. The heavy-work lock covers the whole command:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-utility-benchmark -- \
  bin/vep -m library.tools.utility_benchmark \
    --project /path/to/geo-podcast-media \
    --memory-root /path/to/source_memory \
    --out /tmp/ren-utility-benchmark.json
```

The retrieval families default to speech, visual, and person. Use
`--families speech visual person verified_predicate` to include the VLM
verification arm. It acquires its own model resources. This command refreshes the
registered query and corpus measurements. To repeat the ingest/edit-preparation
measurements too, use a fresh scratch project and source-memory root, point the
analysis command at one short FI-01 source, run it once into a new collection,
then run it again on that collection with the same memory root. Finally run
`ren edit <scratch-project> --reels 1 --about 'why GEO is more than SEO 2.0'
--full-auto agent`. Wrap each heavy command in the same lock. Do not point these
commands at the captain's project or write to the registered FI-01 source-memory
root. The measured CLI sequence, with the scratch paths substituted, is:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-utility-benchmark -- \
  /usr/bin/time -l bin/vep -m ren.cli analyze /path/to/one-short-source \
    --into /tmp/utility/collection --slug fi01-short --memory-root /tmp/utility/memory
bin/vep -m library.tools.heavy_work_lock run --owner vep-utility-benchmark -- \
  /usr/bin/time -l bin/vep -m ren.cli analyze /tmp/utility/collection/fi01-short \
    --memory-root /tmp/utility/memory
bin/vep -m library.tools.heavy_work_lock run --owner vep-utility-benchmark -- \
  /usr/bin/time -l bin/vep -m ren.cli edit /tmp/utility/collection/fi01-short \
    --reels 1 --about 'why GEO is more than SEO 2.0' --full-auto agent --llm-timeout 300
```

These commands need the dependency environment described in `AGENTS.md`; make
the scratch directories first and keep the measurements alongside their output
logs. Final Resolve execution is intentionally excluded.

## Results

The retrieval harness ran on benchmark commit
`3e50aec97b87772ff92d4c368fd59f7eda1ff8c1`, with pipeline base
`f9e3a0842bd45cdab0661d4f5c3c8d433736f440`. Its cached FI-01 corpus metric
files carry their own measured revision,
`ad27452656d9445e5336f533f731d793704e7be1`.

The additional operational measurement used one 214.548-second FI-01 source,
`LCATL0011`, in a fresh scratch collection and source-memory root. The source
was read-only. Cold ingest took 835.58 s and peaked at 3078.6 MB RSS. It built
13 lanes, failed the depth lane because its checkpoint was unavailable, and the
run also exposed a `temporal.index` DAG routing failure (the step requires
`semantic_analysis_documents`). Warm re-ingest took 10.09 s and peaked at
2296.5 MB RSS. It reused 3 project analysis outputs and 9 source-memory lanes,
made no new Gemma calls, rebuilt four indexes/lanes, and retained the same two
failures. These are partial-ingest timings, not a claim that the full ingest
succeeded.

Edit preparation took 335 s. `select_reels` and `judge_reels` completed; one
agent-backend model call proposed zero reels because the transcript identified
one speaker and no exchange. The recorded RSS peak was 45.9 MB. Agent model
identity/cost and edit cache hits were not available. No timeline was built and
Resolve was not accessed. Final edit execution remains unmeasured because the
captain's Resolve project has unsaved edits and must not be touched.

| Phase / measurement | Result |
|---|---:|
| Cold ingest, one source | 835.58 s; peak RSS 3078.6 MB; 24 Gemma calls; partial |
| Warm ingest, same source | 10.09 s; peak RSS 2296.5 MB; 12 reuses; 0 Gemma calls; partial |
| First query (S01) | 4.728 s; recall 1.0; peak process RSS 1212.8 MB |
| Subsequent 30 queries | 2.217 s total; peak process RSS 1212.8 MB |
| Edit preparation | 335 s; peak RSS 45.9 MB; 1 agent-backend call; 0 reels proposed |
| Final edit execution | Unmeasured; Resolve prohibited |
| Speech macro recall / precision | 0.850 / 0.560 |
| Visual macro recall / precision | 0.155 / 0.3625 |

The hand-labelled retrieval set includes visual frames, gesture refusals, and
hand-to-face event spans. All 429 frames sampled at 0.5-second intervals across
the 214.548-second `LCATL0011` source were inspected for a drinking-from-cup
positive. None was visible, so the interaction query remains unmeasured; no
absent-event label or recall score is inferred. Per-query results and all phase
details are in the [machine-readable ledger](2026-10-14-end-to-end-utility-baseline.json).

The baseline is useful for comparing the measured paths, with two operational
limits clearly recorded: ingest completed only partially, and the small edit
preparation input yielded no reel. It does not estimate the time or memory for a
successful full ingest or a rendered edit.
