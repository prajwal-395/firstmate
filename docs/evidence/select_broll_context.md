# select_broll: scene prose inline, scene structure at a path

Test: `tests/unit/picture/test_select_broll_scene_prose_not_structure.py`.

Issue #679: "select_broll still reads the scene prose and the scene
structure". Written against the pre-#339 prompt, where BOTH travelled
inline in one context:

    semantic_analysis_documents   35,813 B   40.5%   (the structure:
                                     `scene[]`/`camera[]`/`objects[]` as
                                     fields, plus every assessment key)
    broll_candidates_toon           7,613 B    8.6%   (the prose: the
                                     `description` column is
                                     `vision_schema_adapter.scene_prose`)

#339 (11498a6) moved the structure to `footage_analysis.md`, reached by
the map in `footage_analysis_reference`, and withdrew every positive
`semantic_analysis_documents.*` path from the manifest. The prose column
stayed: it is the place axis `test_picture_view.py` pins to the
pre-bridge table, on a step whose handoff tells the model to match
content against it.

The test pins that end state on the assembled prompt, the shape
`tests/unit/context/test_broll_context_share.py` uses. Run it at 11498a6^ and it fails -
the bridge emits no reference, and the raw documents arrive inline carrying
every structure marker.
