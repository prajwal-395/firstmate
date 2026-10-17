# Spatial grounding evaluation (OBJ-026..035)

## Current implementation

Ren has detector-produced normalized `xyxy` boxes and feeds named object seeds
into the existing SAM 2 segmentation path. `library/tools/analysis/object_segmentation.py`
validates and consumes those seeds. It also has helpers for resizing face boxes
through normalized source coordinates. `library/tools/cross_modal_grounding.py`
projects speech, face, and event evidence; it does not ground text to image
points or boxes. No text-to-region adapter, source/prepared-image transform
record, point-vs-coordinate comparison, or spatial grounding scorecard exists
on this baseline.

`library/tools/spatial_grounding.py` adds an isolated geometry boundary for
prepared-image points and boxes, a conversion to the existing SAM seed shape,
and a deterministic scorer. `library/tools/grounding_adapters.py` freezes a
Gemma textual-coordinate calibration arm and maps its 0..1000 coordinates
through prepared-image pixels into full-resolution source pixels. Its parser
accepts only a single JSON object, so surrounding prose and malformed output
remain visible as adapter failures. It does not add a Project Model schema or
register an unqualified checkpoint. Candidate outputs must identify the source
frame, transform, instance, and evidence row before they can become a tracking
seed. A point seed requires an explicit source-pixel radius selected on
calibration; the radius is not inferred from the point prediction.

## Evaluation design

Use only the seven GEO podcast originals and the 17 Apple 001 originals in the
frozen editorial-evaluation corpus. Keep synchronized podcast angles and each
Apple capture scene in one split. Choose thresholds and prompts on calibration;
score held-out exactly once after freezing predictions, labels, model revisions,
and preprocessing transforms. Do not describe these results as general footage
coverage.

Build paired examples for:

- text-to-point and text-to-box localization;
- referring expressions that distinguish multiple people or objects;
- small objects, using gold box area divided by original-frame area;
- explicit spatial relations such as left/right, beside, and in front of;
- object-instance identity, scored separately from geometric overlap;
- identical phrases under point output and textual coordinate generation.

Use at least two independent blind agent labellers from different model
families and a third tie-breaker. Labellers receive the frame and phrase without
model predictions. Preserve both initial labels, tie-break adjudication, and
agreement; describe the resulting reference as agent-labelled, never human or
gold-labelled. Exclude cases with unresolved visual ambiguity from accuracy
and report their count and agreement separately.

Report per-task point-in-box accuracy, box recall at IoU >= 0.5 and 0.75,
instance accuracy conditional on localization (geometrically associated predicted box to the annotated frame instance), small-object accuracy by object
area band, relationship accuracy, abstention, and malformed-output rate. Report
calibration and held-out separately. Compare point-producing models against a
textual-coordinate baseline on the same image, prompt, and label set.

Each candidate's frozen JSON scorecard must record model and published
revision, precision, license class and evidence, label-set revision, per-task
quality, cold and warm latency, peak memory, decode/infer/serialize time, and
end-to-end cost. Record an adoption decision from `adopt`, `retain baseline`,
`research-only`, `conditional fallback`, or `reject`. A research-only or
unresolved candidate stays outside both shipping manifests.

## Baseline and status

The current audit provides no text-grounding quality number. Existing OWLv2
box recall (132/148 at IoU >= 0.5) is an open-vocabulary detector measurement,
not a text-to-region or instance-grounding baseline, and its labels were not
created under this lane's blind agent protocol. The prior detector score must
not be relabelled as this evaluation's baseline. The existing SAM 2 path is a
tracker consumer, not a grounding predictor.

The incumbent Gemma textual-coordinate arm was run on 24 calibration queries
at `mlx-4bit`, revision
`73bcf09092aa277861d5a191b989b666f7f32e8f`. Its frozen predictions now carry
both prepared and source-frame geometry. Measured cost was 305.50 seconds end
to end, 43.32 seconds for the first inference, 9.37 seconds mean warm inference,
and 1177.66 MB peak RSS. Two queries abstained and no outputs were malformed.
The calibration labels now include two independent first-pass families (OpenAI/Codex and Anthropic Claude Sonnet 5.5) plus Muse Spark tie-breaks on nine disputed items. First-pass exact visibility and relation agreement was 20/24 each; 14 of 18 jointly visible boxes had IoU >= 0.5 (mean 0.763, median 0.920). Item_14 remains jointly ambiguous and is excluded. The 23 resolved queries score Gemma at 22/23 visibility accuracy, 12/22 point-in-box, 1/22 box recall at both IoU thresholds (0.5 and 0.75), and 0/1 on the adjudicated small-object item. Florence scores 17/23 visibility accuracy, 12/22 point-in-box, 10/22 box recall at IoU >= 0.5, 8/22 at IoU >= 0.75, and 0/1 on the same small-object item. Item_11 is a deliberately ambiguous query retained in the totals and separately reported. Neither adapter emits instance IDs or relation classes, so those metrics remain unscored. The held-out manifest is frozen at `docs/benchmarks/2026-10-17-spatial-grounding-heldout-manifest.json`; Florence and Gemma each have one frozen 12-query prediction pass. The two first-pass families agreed on visibility for 12/12, relation for 7/12, median box IoU 0.660, and reciprocal point cross-hits for 11/12. A third independent family adjudicated the nine disputed queries from full-resolution frames; all nine hashes matched the manifest. Final scoring uses the third-family boxes for those nine and the two-label consensus boxes for h01, h07, and h08. Florence scores 9/12 visibility, 7/12 point-in-box, 5/12 box IoU >= 0.5, 5/12 at >= 0.75, and 0/2 on small-object recall at IoU >= 0.5. Gemma scores 11/12 visibility, 4/12 point-in-box, 1/12 box IoU >= 0.5, 1/12 at >= 0.75, and 0/2 on small-object recall. Instance identity and spatial relationship accuracy remain unscored because the adapters do not output them. Final labels and the per-query comparison are in `docs/benchmarks/2026-10-17-spatial-grounding-heldout-tiebreak.jsonl` and `docs/benchmarks/2026-10-17-spatial-grounding-heldout-label-agreement.json`; candidate scorecards are updated.

## MolmoPoint candidate attempt

After the captain lifted the model-run hold, the lane prepared an image-point
adapter for `mlx-community/MolmoPoint-8B-4bit` at published revision
`9bab196f867ceedecdb1eab071395bfedcd62538`. The publisher lists a 7.24 GB
[4-bit MLX checkpoint](https://huggingface.co/mlx-community/MolmoPoint-8B-4bit/tree/9bab196f867ceedecdb1eab071395bfedcd62538)
and Apache-2.0 license metadata; its model card also states that training
data includes sources restricted to academic and non-commercial research.
This is a research candidate pending the license lane's determination, not
an adoption or shipping-manifest entry.

The first scheduled run extracted and prepared its first calibration frame,
then the captain reinstated the model-run hold before the checkpoint finished
downloading or any model inference began. Its partial cache was removed.

After the hold was lifted, a one-query probe resumed from a partial cache with
an 8 GB scheduler reservation and a process-tree RSS guard. It downloaded the
checkpoint and began loading, but Ren measured a peak physical footprint of
8.68 GB and stopped the run for exceeding the reservation. Under a later 10 GB
authorization, Ren admitted a second one-query run on live macOS headroom; its
physical footprint reached 10.01 GB during loading and Ren stopped it before
inference. No prediction was written, so latency and quality remain
unmeasured. The orphaned adapter children were stopped by PID, and both
temporary checkpoint caches and prepared frames were deleted. MolmoPoint-8B-4bit is unmeasurable within the authorized memory ceiling.


## Florence-2-large calibration

The lane measured `microsoft/Florence-2-large` at published revision
`21a599d414c4d928c9032694c424fb94458e3594`. The publisher identifies the
checkpoint as MIT licensed and documents caption-to-phrase grounding. The
weights are about 1.55 GB on disk. The first full-fp32 MPS probe with 3 beams,
256 new tokens, and the default KV cache reached 13.77 GB physical footprint
and was stopped above the 10 GB ceiling.

Reducing generation to one beam and 128 tokens, disabling KV cache, and using
768-pixel square-padded input brought the run below budget. Debugging the first
24-query pass found that Transformers 5 did not honor the remote checkpoint's
legacy tied-weight declarations: it left the language output head random, so
that pass emitted junk and is excluded from results. The adapter now restores
the checkpoint's shared vocabulary weights before inference. It also uses
Florence's open-vocabulary detection task for direct referring expressions and
reads its task-specific box-label output.

The corrected 24-query calibration is frozen in
`2026-10-17-spatial-grounding-florence-calibration.json`. It completed in
89.51 seconds, with 6.19 seconds cold inference, 2.03 seconds mean warm
inference, 2.64 GB process RSS, and 6.472 GB peak process-tree physical
footprint under a 7.5 GB reservation. It returned boxes for 19 of 24 queries,
with five abstentions and no malformed outputs. These are calibration predictions only. On the 23 resolved calibration queries, Florence scores 17/23 visibility accuracy; for the 22 visible references it scores 12/22 point-in-box, 10/22 box recall at IoU >= 0.5, 8/22 at IoU >= 0.75, and 0/1 on the adjudicated <=1%-area small object. Item_11 is deliberately ambiguous; excluding it gives point-in-box 12/21, box recall 10/21 at IoU >= 0.5 and 8/21 at IoU >= 0.75. It abstained on five of 24 total queries and produced no malformed outputs. The candidate remains research-only and outside shipping manifests. The single held-out pass is scored against the two first-pass labelers plus a third-family adjudicator on nine disputes; Florence remains research-only. See `2026-10-17-spatial-grounding-label-agreement.json`, `2026-10-16-spatial-grounding-florence-scorecard.json`, and the frozen held-out manifest.

## Qwen2.5-VL 3B MLX 4-bit calibration

The requested Qwen arm used `mlx-community/Qwen2.5-VL-3B-Instruct-4bit` at
published revision `46d4cf06a06ffc1a766c214174f9cbed2f45bcab` through MLX-VLM
0.7.2, with the same 24 calibration images and frozen coordinate prompt. The
model card metadata identifies the Qwen Research License; review that license
before any product use.

The run completed in 142.10 seconds, with 59.88 seconds cold inference, 1.92
seconds mean warm inference, 3.22 GB process RSS, and 5.829 GB peak process-tree
physical footprint under a 7.5 GB reservation. All 24 answers failed the strict JSON parser: most had an extra closing
brace, with duplicate keys or trailing markup in some answers. The adapter did
not repair them, so accuracy is not scored and the malformed rate is 100%. The
candidate remains research-only and outside shipping manifests. Its frozen
predictions and scorecard are
`2026-10-17-spatial-grounding-qwen25vl-calibration.json` and
`2026-10-17-spatial-grounding-qwen25vl-scorecard.json`. No held-out split was
run.
