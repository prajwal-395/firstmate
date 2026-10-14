# FI-24 evidence execution proof (2026-10-14)

The planner was exercised against FI-01's read-only LCATL0011 source memory
(`d19007c3…`) and footage. Producer output was merged into a temporary
Project Model under the system temp directory; the source footage and source
memory were not changed.

| Request | Plan selected | Work and cost | Answer |
|---|---|---|---|
| Find a laptop by boxes from 70-90 s; require named-object recall >= 0.85; budget 200 s | `object_detection:owlv2_named_queries` | 40 nominated M2 frames; predicted 145.553 s, actual producer wall 145.613 s; 46 measurements merged at Project Model revision 2 | OWLv2 returned 46 laptop candidates across the sampled frames. These are repeated frame detections, not 46 distinct laptops. |
| What does the speaker say about the CMO? | Existing source-memory M1 transcript | No producer; 0 s predicted; cache lookup only | The cached span expands CMO as chief marketing officer and explains the head-of-marketing context. Transcription stayed on its existing route. |
| What is visible between 10-20 s? (cached control) | Existing M4 scene evidence | No producer; 0 s predicted; cache lookup only | The cached scene describes a plant pot with flowers. |
| Find a named object from 70-90 s with a 1 s budget | OWLv2 measured route, refused at preflight | Predicted 145.553 s; producer not invoked | `budget_exceeded`; the stopping condition says not to invoke before explicit authorization. |

The 40-frame cost scope is the measured detector set (40 frames, 148 labeled
boxes). The requested 70-90 s interval contained exactly 40 stored M2 frames,
so the planner did not extrapolate the measurement. The run measured a 0.060 s
prediction error (145.613 s actual versus 145.553 s predicted). The 46 boxes
are frame-local candidates, not a count of distinct physical objects.

The cached M1/M4 cases were read and planned with the cache as sufficient
evidence, so execution returned without invoking a producer. The previous
cache smoke measured 0.001657 s for its combined read-and-plan pass. The
budget refusal was checked at plan time and by a unit test asserting the
executor does not call the producer for a non-ready plan.

The same graph exposes the fast-discovery, edit-preparation and
precision-execution policies. They retain the caller's quality bar; the
profiles set escalation behavior. The catalog includes the existing M4,
object detection, OCR, region, mask and speech-span acquisition surfaces.
Only object detection currently has compatible measured quality and cost
records plus a default executor. The other nodes remain ineligible until
their quality and cost are measured; no thresholds or costs are guessed.
Producer output from a selected node flows through the incremental
Project Model evidence merge.

## FI-01 utility benchmark

The `vep-utility-benchmark` lane landed during this task. Its read-only
benchmark was run against the analysis-only `geo-podcast` project and existing
source-memory root before and after the final validation pass. The inputs
remained read-only. Before, the FI-01 cached corpus read took 0.703 s and
reproduced 240/240 recorded metrics; retrieval took 5.261 s. After, the FI-01
read took 0.777 s and retrieval took 6.175 s. Speech macro recall stayed at
0.850 over 15 present queries (23/27 moments found), visual macro recall at
0.155 over 4 present queries, and speaker recall at P01 0.9933, P02 0.9967,
P03 0.4970 and P04 0.4217. The measured corpus and retrieval results were
unchanged; elapsed time varied between the two passes. The before and after
ledgers recorded code revisions `65f1d442ad1b339ba5e0561cbcebd83407d84e39`
and `d8dd3a297bbf43c1f49467a187bb1827e68856ad`, respectively. The benchmark
reports cold/warm ingest, edit preparation and final edit execution as
unmeasured; no ingest or Resolve work was run.
