# Frozen multimodal evaluation inputs

`2026-10-15-editorial-eval-corpus.json` remains the single 24-source dataset
manifest: it owns rights, source checksums, leakage groups, and training,
calibration, and held-out splits for the seven GEO podcast originals and
seventeen Apple 001 takes. The companion
[`2026-10-15-multimodal-eval-manifest.json`](2026-10-15-multimodal-eval-manifest.json)
freezes source-interval cases and checksummed task/executor inputs without
copying source metadata or label payloads.

Validate the suite with:

```sh
bin/vep -m library.tools.multimodal_eval_dataset \
  docs/benchmarks/2026-10-15-multimodal-eval-manifest.json
```

The validator checks the source-manifest checksum and rights/split rules,
fixture checksums, source split membership, interval bounds, and agent-label
protocol. It reads no media. Cases cover Apple 001 calibration and held-out
intervals plus calibration and held-out podcast speaker-association windows.

Existing blind agent annotations are reused from the Firstmate task data:
independent action/object labels cover all 17 Apple 001 takes; the separate
present/absent search set reports agreement and a third-agent resolution for
disagreements; reference edit sets cover the existing EVAL-04 briefs; and the
podcast transcript set has two independent submissions and a third-agent
resolution. These payloads remain outside the repository and are not treated
as human ground truth. Existing task proposals that still say
`gold_frozen: false` remain proposals, not labels.

The remaining label gap is stable cross-window person identity and
visible-to-audible speaker association. The blind request for those fields is
in the Firstmate task data at `vep-mu-harness/blind-packet.md`. It gives only
source paths, windows, questions, and a return format. Calibration
examples may select settings. Held-out predictions and labels stay sealed
from tuning and are used in one final report after calibration is frozen.

The explicit qualification record and gate are described in
[`model-adapter-contract.md`](model-adapter-contract.md). Heavy model passes
stay outside ordinary unit tests; adapter contracts, fixture integrity, and
the gate logic stay lightweight.
