# OCR and UI temporal tracks

Step 1.07 remains optional and deselected by default. `--with ocr_extraction`
produces `pipeline_output/steps/1_07_ocr_extraction/<clip_id>/ocr_result.json`
with schema version 2. The artifact keeps per-frame text polygons and boxes,
text tracks, script hints, alternative readings when a backend supplies them,
sample-derived appearance intervals, graphic/UI candidate tracks, and each
frame's graphic candidate geometry and median-color state signature.

The English EasyOCR reader is created with downloads disabled. Its configured
language is not treated as proof of the visible language: Latin text has a
null language hint, while script-specific Unicode ranges can carry a limited
script hint. EasyOCR's normal result has one reading, so alternatives are an
empty list unless the OCR backend returns explicit alternatives. OCR samples
are sparse (normally 1 fps plus scene boundaries); the recorded appearance
intervals are lower-bound estimates from positive samples, not frame-accurate
entry/exit times.

OpenCV contour geometry is recorded as candidate UI controls, cursors, or
graphic regions. Those labels are candidate classes only. `visual_state` is a
per-sample median-color signature, not a semantic claim such as enabled,
selected, or clicked. A downstream edit that depends on a cursor action or UI
state needs targeted verification before treating it as established.

When a schema-v2 artifact exists during Project Model evidence migration,
`library.tools.ocr_project_model` adds text and graphic entities, measured
observations, sparse geometry tracks, and polygon-mask references. The model
stores the artifact's relative path and SHA-256; `project_model_masks` resolves
the referenced track and refuses changed or missing bytes. Old OCR artifacts
remain readable for search, but need a fresh optional 1.07 run before they can
be migrated as temporal tracks.

`tests/fixtures/video_intelligence/screen_recording_proxy.json` and
`tests/unit/picture/test_ocr_temporal_tracking.py` provide synthetic labeled
screen-recording proxies for text changes, control appearance changes and
cursor movement. They test data flow and geometry only. There is no real
tutorial or screen-recording corpus in this checkout, so real-world OCR, UI,
and cursor accuracy claims remain unevaluated until such recordings are
available.
