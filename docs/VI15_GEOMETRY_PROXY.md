# VI-15 geometry acceptance (C6 synthetic proxies)

VI-15 makes relative and metric depth distinct. M3g's Depth Anything V2
small output is relative and normalized; it does not have a physical
scale. Metric readings require a positive scale calibration and retain
its id and provenance. Camera-to-world transforms name both spaces,
carry provenance, and apply as finite 4x4 homogeneous matrices.

The `tests/unit/picture/test_depth.py` geometry cases are **C6 synthetic
proxies**: they use a known 0.25 metres-per-estimator-unit scale and a
known camera translation `(10, -2, 3)`. The metric conversion and point
transform match the fixture exactly (zero error against their constructed
values). These checks validate contract arithmetic, not a depth model's
accuracy. No licensed annotated RGB-D or multiview set is available on
this machine, so real-data accuracy is unmeasured.

The machine's configured Depth Anything V2 small checkpoint and source
checkout are also absent: `probe_estimator()` returns `no_checkpoint`,
and no `PIPELINE_DEPTH_CHECKPOINT` / `PIPELINE_DEPTH_REPO` variables are
set. The local Hugging Face cache records the model config as absent.
No model or dataset was downloaded, as required by this task. M3g
therefore remains an explicitly relative measurement and refuses to
claim metric depth without calibration.
