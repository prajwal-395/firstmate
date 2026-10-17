# Person identity and ReID work (AUD-043..051)

## What already exists

- `library/tools/person_entity.py` is the production identity baseline. It
  uses InsightFace `buffalo_l` ArcFace for within-source face matching and
  ECAPA speaker embeddings for cautious cross-source voice association.
  Face and voice decisions remain separate; a voice match does not merge two
  people.
- The incumbent face threshold and 1408 px input width are already measured
  in `person_entity.py`'s module documentation: a previous 508-frame combined
  study reported FAR=0 and FRR=0 at cosine 0.25 (closest different-person
  0.1452; weakest same-person 0.2647). This is historical incumbent evidence,
  not a result from the new calibration/held-out protocol.
- `library/tools/project_model.py` already validates identity links and
  persistent entity scopes. `library/tools/identity_reassociation.py`
  proposes evidence-backed cross-track links and only widens scope after a
  verified link. This lane adds no new canonical identity schema.
- Cross-source voice matching has a measured report and explicit limits in
  `docs/CROSS_SOURCE_VOICE_MEASUREMENT.md`.

## Added in this lane

- `library/tools/reid_study.py` stratifies face scores by condition and source,
  reports cross-file margins and names the closest different-person pairs.
  It uses the production face extraction path. Thresholds must be fixed on
  calibration data; held-out is report-only.
- `library/tools/cheetah_reid.py` is an explicitly acknowledged, research-only
  InsightFace 2.1 `PersonAnalysis` adapter. It reports face and body margins
  separately, includes face-absent detections, and can save scratch crops for
  box-matched appearance comparison. It is excluded from production imports by
  `tests/contracts/test_no_cheetah_in_production.py`.
- `library/tools/body_appearance.py` provides a no-weight HSV descriptor for
  face-absent crops. Similar clothing is not identity evidence on its own.
- `library/tools/identity_fusion.py` keeps face, voice, body, and co-occurrence
  evidence separate until a face plus an independent supporting signal passes.
  Uncalibrated body similarity cannot promote an identity; rejected evidence
  emits no link.
- `library/tools/identity_contradictions.py` reports same-frame co-presence,
  face/voice ownership disagreement, and multi-owner voice groups without
  resolving them.

## Measurement status and scorecard

The initial one-image, five-pass technical probe for `cheetah_s` and the
incumbent supplied timing and sampled RSS only. A later exact-crop evaluation
used the Round 6 blind agent labels after A relabelled B's identical crop and
pair set. Its measured scorecard is in
[`2026-10-17-person-identity-reid-labeled.json`](2026-10-17-person-identity-reid-labeled.json).
The original probe results and limitations remain in
[`2026-10-16-person-identity-reid-probe.json`](2026-10-16-person-identity-reid-probe.json).
The prior worker's status note says that the incumbent passed a held-out
profile case and that cheetah face embeddings were worse on profile images,
but the raw results have no documented blind-label provenance, so those
observations are not used as scorecard data. No held-out result may be tuned
or reported a second time.

**The following table is only the one-image technical probe; quality was not measured in those runs. The later exact-crop results follow below.**

| Candidate | License/use | Decision / status | Quality in probe | Cold/warm latency | Peak memory | Decode/infer/serialize | End-to-end cost |
|---|---|---|---|---|---|---|---|
| InsightFace `buffalo_l` incumbent | Research-only checkpoint; existing production path is preserved pending the edition registry's enforcement | retain baseline | not measured in this probe | 0.427 s init; 0.131 s median warm, one crop | 0.728 GiB sampled RSS | 0.0037 / 0.1271 mean / 0.00032 s | 1.067 s cold plus five inference calls; local CPU only |
| InsightFace 2.1 `cheetah_s` | Research-only; explicit acknowledgement required; never Commercial | research-only | not measured in this probe | 1.451 s init; 0.128 s median warm, one crop | 0.282 GiB sampled RSS | 0.0053 / 0.1347 mean / 0.00021 s | 2.130 s cold plus five inference calls; local CPU only |
| HSV body descriptor | No model weights; code uses existing NumPy/OpenCV dependencies | research-only until calibrated; no identity claim | not measured in this probe | not measured | not measured | not measured | not measured |

These rows record the one-crop technical probe only. The newer exact-crop
measurement below supersedes its pending-quality status. No adoption or
default change was made: the labeled sample contains only three known people,
and the `cheetah_s` weights are non-commercial. `buffalo_l` remains a research
comparator and is not eligible for Commercial distribution. The exact edition
license disposition remains owned by `vep-mu-license`.

The `cheetah_s` pack used for this probe was already present in this lane's
personal scratch root at
`/Users/prajwal/.cache/vep-mu-reid/ifroot/models/cheetah_s`; its unpacked
package size was 29,264,445 bytes. The prior download date and transfer size
were not recorded, and no new download was made for the probe. The publisher identifies the
package as model `cheetah_s`, manifest version 2; the publisher manifest
publishes SHA-256 values for each ONNX file (recorded in the JSON report).
The pack was removed from scratch after the probe, as it is not adopted. The
publisher dates InsightFace 2.1's PersonAnalysis update to 2026-10-03 and
states that pretrained model weights are non-commercial research only:
[InsightFace release and license notes](https://github.com/deepinsight/insightface).

## Reproduction

Use only the authorized podcast and Apple 001 material. Ask the independent
labelers to build source-file-grouped calibration and held-out manifests;
blind each model family to the other's labels and adjudicate disagreements
with a third family. Record pairwise agreement and tie-breaks as agent labels.
Choose thresholds on calibration only, then evaluate held-out once.

For the incumbent (no model download):

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-reid \
  --profile semantics.analyse:inference -- \
  bin/vep -m library.tools.reid_study run <manifest.json> --out <report.json>
```

For InsightFace 2.1, first download only the approved `cheetah_s` research
pack into an external scratch model root, record its publisher revision,
download size and path, and delete it after the benchmark. Run it in an
InsightFace 2.1 scratch interpreter, not the shared InsightFace cache:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-reid \
  --profile semantics.analyse:inference -- \
  <scratch-python> -m library.tools.cheetah_reid run <manifest.json> \
  --research-ack --model-root <scratch-model-root> --out <report.json>
```

The numerical report and all scratch model/crop files stay outside the
repository. Record model revision, precision, bytes downloaded, timing
split, peak memory, license class, and the adoption decision in a scorecard
when the labelled measurements are available.

## Round 6 exact-crop evaluation (2026-10-17)

Labeller A blindly relabelled the exact 20 crops and 190 pair IDs supplied by
labeller B. The pair-label sets align 190/190 and agree semantically 190/190
after normalizing separator and unknown-label vocabulary; no third-family
tie-break was needed. These are agent labels, not human gold. There are 48
same-person pairs, 105 different-person pairs and 37 unknown-involved pairs. A and B
differ on whether the hand-only calibration crop counts as a person box; its
identity remains unknown and it does not create a pair-label disagreement.
The 37 unknown-involved pairs were excluded from identity metrics rather than
being forced into either class.

The threshold for each embedding was selected on the 45 known within-calibration
pairs by minimizing FAR + FRR, then minimizing FAR and preferring the higher
threshold on ties. It was frozen before a single evaluation of the 108 known
held-out-containing pairs (within-held-out plus calibration-heldout). Each
held-out set has 35 same-person and 73 different-person pairs. A zero false
accept result on 73 negatives is sample evidence, not a zero-risk guarantee.

| Feature | Calibration threshold | Held-out threshold | Held-out false matches | Held-out false rejects | Held-out AUC | Decision |
|---|---:|---:|---:|---:|---:|---|
| `buffalo_l` ArcFace face | 0.271332 (publisher default 0.25 also yielded 0/0 errors) | 0.271332 | 0/73 | 0/35 | 1.000 | retain baseline for personal/research comparison; not Commercial |
| `cheetah_s` face | 0.239327 (publisher default 0.45 yielded 0/73 false matches and 2/35 misses) | 0.239327 | 0/73 | 0/35 | 1.000 | research-only; no adoption |
| `cheetah_s` body ReID | 0.609646 (publisher default 0.85 yielded 0/73 false matches and 23/35 misses) | 0.609646 | 0/73 | 4/35 | 0.950 | research-only; never merge identity alone |
| HSV body descriptor on the supplied crop | 0.661122 | 0.661122 | 0/73 | 4/35 | 0.959 | research-only; non-decisive evidence only |

All four feature sets scored all 45 calibration and 108 held-out known pairs.
At the selected thresholds, buffalo and cheetah face features had no false
accepts or rejects in this small sample. Cheetah produced a body ReID vector
for 20/20 crops and a face vector for 18/20; the incumbent produced face
vectors for 18/20. The two crops without a face vector are the unknown crops,
so their face-absent identity quality is not measured. These are feature
coverage counts, not detection-recall measurements; the hand-only crop has a
person-box annotation difference between A and B. The hand-built HSV descriptor
also had four held-out false rejects. It has no learned weights, but it is not
a commercially eligible standalone identity system and its dependency
closure still needs `vep-mu-license` review. No AUD-050 commercial identity
alternative is established by this evaluation.

| Candidate | Cold initialization | Warm median per crop | 20-crop decode / infer / serialize | End to end, 20 crops | Peak RSS / declared | Precision and license |
|---|---:|---:|---:|---:|---:|---|
| `buffalo_l` incumbent | 1.358 s | 0.131 s (prior five-pass, one-crop probe) | 0.023 / 5.680 / 0.00104 s | 7.062 s | 0.999 / 1.5 GiB | unquantized publisher ONNX; research only |
| `cheetah_s` | 1.621 s | 0.128 s (prior five-pass, one-crop probe) | 0.020 / 1.437 / 0.00159 s | 3.080 s | 0.283 / 1.0 GiB | unquantized publisher ONNX; non-commercial research only |
| HSV descriptor | 0.111 s import | 0.00151 s per crop average | 0.034 / 0.030 / 0.00018 s | 0.175 s | 0.079 GiB | no learned weights; dependency closure review pending |

The `cheetah_s` pack was downloaded for this benchmark to
`/Users/prajwal/.cache/vep-mu-reid/ifroot/models/cheetah_s`; its unpacked size
was 29,264,445 bytes. The publisher manifest is version 2 and publishes the
per-file hashes recorded in the labeled scorecard. Its publisher license is
`cheetah_s-public-v1` with a non-commercial grant. The pack was removed after
the benchmark. `buffalo_l` was read from the existing personal cache; this
run did not download or hash its weights. Its active production loader
refused under the `commercial_client` edition, so the research-only baseline
measurement used direct InsightFace 2.1 loading in the isolated scratch
interpreter and did not alter production behavior. A first run with a 1.0
GiB declaration exited at 1,079,050,240 bytes before crop inference; the
successful retry declared 1.5 GiB and peaked at 1,072,693,248 bytes.

The sample contains only three known identities across 20 selected crops.
The held-out evidence covers cross-session and cross-file pairs, not a
confirmed independent-camera deployment. Most face-visible samples are
unoccluded, and unknown pairs cannot answer the face-absent identity question.
These results do not support adopting a model or asserting general false
match safety. Full per-crop model vectors remain outside the repository;
the tracked scorecard contains aggregate pair results, timing, memory,
publisher identity and license evidence.
