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

## MIT/ast-finetuned-audioset-10-10-0.4593 model/checkpoint
- Capability: audio.classify_event
- License: bsd-3-clause
- Approval: conditional
- Source: https://huggingface.co/MIT/ast-finetuned-audioset-10-10-0.4593
- Obligation: Apache-2.0: preserve copyright, license and NOTICE, mark modified files. CC BY 4.0: identify creator, license, link, and changes. NVIDIA custom: redistribute only under its accompanying terms.
- Obligation: Subject to exact upstream license; if no license declared, no redistribution clearance established.
- Obligation: This exact publisher revision was inspected via metadata and model card only; no weights fetched. Audit base model, adapters, tokenizer, training data and conversion separately. Publisher metadata is evidence only for declared license; review dependencies, data and conversion before packaging. Ren loader uses mutable repo ID and does not pass revision; audit SHA is not runtime pin.

## depth-anything/Depth-Anything-V2-Small-hf model/checkpoint
- Capability: visual.depth
- License: apache-2.0
- Approval: conditional
- Source: https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf
- Obligation: Apache-2.0: preserve copyright, license and NOTICE, mark modified files. CC BY 4.0: identify creator, license, link, and changes. NVIDIA custom: redistribute only under its accompanying terms.
- Obligation: Subject to exact upstream license; if no license declared, no redistribution clearance established.
- Obligation: This exact publisher revision was inspected via metadata and model card only; no weights fetched. Audit base model, adapters, tokenizer, training data and conversion separately. Publisher metadata is evidence only for declared license; review dependencies, data and conversion before packaging. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/depth.py:71.

## microsoft/Florence-2-base model/checkpoint
- Capability: visual.detect
- License: mit
- Approval: conditional
- Source: https://huggingface.co/microsoft/Florence-2-base
- Obligation: Apache-2.0: preserve copyright, license and NOTICE, mark modified files. CC BY 4.0: identify creator, license, link, and changes. NVIDIA custom: redistribute only under its accompanying terms.
- Obligation: Subject to exact upstream license; if no license declared, no redistribution clearance established.
- Obligation: This exact publisher revision was inspected via metadata and model card only; no weights fetched. Audit base model, adapters, tokenizer, training data and conversion separately. Publisher metadata is evidence only for declared license; review dependencies, data and conversion before packaging. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/analysis/open_vocabulary_detect.py:54.

## google/owlv2-base-patch16-ensemble model/checkpoint
- Capability: visual.detect
- License: apache-2.0
- Approval: conditional
- Source: https://huggingface.co/google/owlv2-base-patch16-ensemble
- Obligation: Apache-2.0: preserve copyright, license and NOTICE, mark modified files. CC BY 4.0: identify creator, license, link, and changes. NVIDIA custom: redistribute only under its accompanying terms.
- Obligation: Subject to exact upstream license; if no license declared, no redistribution clearance established.
- Obligation: This exact publisher revision was inspected via metadata and model card only; no weights fetched. Audit base model, adapters, tokenizer, training data and conversion separately. Publisher metadata is evidence only for declared license; review dependencies, data and conversion before packaging. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/analysis/open_vocabulary_detect.py:55.

## IDEA-Research/grounding-dino-tiny model/checkpoint
- Capability: visual.detect
- License: apache-2.0
- Approval: conditional
- Source: https://huggingface.co/IDEA-Research/grounding-dino-tiny
- Obligation: Apache-2.0: preserve copyright, license and NOTICE, mark modified files. CC BY 4.0: identify creator, license, link, and changes. NVIDIA custom: redistribute only under its accompanying terms.
- Obligation: Subject to exact upstream license; if no license declared, no redistribution clearance established.
- Obligation: This exact publisher revision was inspected via metadata and model card only; no weights fetched. Audit base model, adapters, tokenizer, training data and conversion separately. Publisher metadata is evidence only for declared license; review dependencies, data and conversion before packaging. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/analysis/open_vocabulary_detect.py:56.

## SAM 2.1 weights
- Capability: visual.segment
- License: apache-2.0
- Approval: conditional
- Source: https://huggingface.co/facebook/sam2.1-hiera-small
- Obligation: Include the upstream Apache-2.0 LICENSE, copyright notices, and NOTICE if present; mark modified files.
- Obligation: Apache-2.0 permits redistribution subject to license and notice obligations; do not bundle until Ren pins and verifies the exact loaded revision.
- Obligation: Publisher declares Apache-2.0 at the recorded Hub revision, but Ren loads the model by floating repo ID without a revision pin. The audit SHA is not the revision Ren actually loads. Pin runtime resolution before Commercial distribution; preserve LICENSE and check any bundled files. Current Ren loader has no immutable revision; exact_revision is the audit snapshot only, not a runtime pin. library/tools/analysis/object_segmentation.py:27.

Restricted or unresolved implementations are excluded from this notice and commercial manifest.