Ren model and checkpoint notices

Generated from registry.json.
Conditional and unresolved entries require review before distribution.

## SpeechBrain ECAPA speaker encoder
- Capability: audio.diarize
- License: Apache-2.0 recipe; CC-BY-4.0 VoxCeleb training data
- Approval: conditional
- Source: https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- Obligation: Attribute VoxCeleb dataset; verify rights for intended use.

## PANNs Cnn14 DecisionLevelMax
- Capability: audio.detect_event
- License: CC BY 4.0
- Approval: conditional
- Source: https://zenodo.org/records/3987831
- Obligation: Required for CC BY 4.0 and Apache-2.0 if distributed; exact notice details require review of the governing license. Otherwise not established.
- Obligation: Not affirmatively cleared for Ren packaging unless the exact license permits it; see conditions and evidence. For conditional/research-only/quarantine/unresolved records, do not bundle in Commercial.
- Obligation: Exact Zenodo record and file checksum are identified. CC BY 4.0 attribution and change-marking are required for redistributed adapted material.

## Montreal Forced Aligner English US ARPA models
- Capability: speech.align
- License: CC-BY-4.0 models; MIT aligner
- Approval: approved
- Source: https://github.com/MontrealCorpusTools/mfa-models
- Obligation: Provide attribution for model artifacts.

## Sentence Transformers MiniLM L6 v2
- Capability: visual.embed
- License: apache-2.0
- Approval: conditional
- Source: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
- Obligation: Include the upstream Apache-2.0 LICENSE, copyright notices, and NOTICE if present; mark modified files.
- Obligation: Apache-2.0 permits redistribution subject to license and notice obligations; do not bundle until Ren pins and verifies the exact loaded revision.
- Obligation: Publisher declares Apache-2.0 at the recorded Hub revision, but Ren loads the model by floating repo ID without a revision pin. The audit SHA is not the revision Ren actually loads. Pin runtime resolution before Commercial distribution; preserve LICENSE and check any bundled files. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/analysis/footage_query.py:97,352-381.

## LAION-CLAP HTSAT unfused
- Capability: audio.detect_event
- License: Apache-2.0
- Approval: approved
- Source: https://huggingface.co/laion/clap-htsat-unfused
