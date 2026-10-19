# Resolve interaction assumptions reviewed for timeline addressing

This is the 3.2 audit for the addressing and delivery surface. Runtime item
and timeline addresses are live Resolve IDs; editorial identity remains
separate and is based on source media and source frame range. Exact names are
used only when there is no recorded Resolve identity, and ambiguity refuses.

| Category | Assumption | Treatment |
|---|---|---|
| Technical invariant | Resolve track indices are one-based and tracks are addressed with the API's `video` / `audio` media type. | Keep at the Resolve boundary. Timeline addressing enumerates the live count; `timeline_layout.resolve_live_track` maps a planned role and occupant back to the current index by the row name. |
| Confirmed Resolve limitation | `MediaPool.AppendToTimeline` writes to the project's current timeline, not the timeline handle passed by a caller. | Keep and enforce through the current-timeline lease/fence in `resolve_lock.py`. It is not a safe fallback destination. |
| Confirmed Resolve limitation | `TimelineItem.GetUniqueId()` addresses the current Resolve item but is not a durable editorial identity across rebuilds. | Keep it as a live handle only. `timeline_addressing.py` resolves source-range editorial identity separately and verifies timeline/item identity again before use. |
| App convention, state-derived | Reel track roles and row names describe the material in `timeline_layout.py`; physical indices follow the resulting live layout. | Use the `TrackPlan` plus live row names via `resolve_live_track`, rather than carrying an assumed index into a later operation. |
| App convention, state-derived | Reel picture rows used to mean V1/V2 in `reel_post_header.picture_runs`, even though row roles and counts vary. | Replaced with the named picture rows from the reel layout; captions, frame and other named layers are excluded. B-Roll remains a picture role. |
| Arbitrary interaction assumption | `reel_post_header.touch_spec` supplied a 1080x1920 canvas and a default graphics-disable choice when the caller did not provide them. | Removed the implicit canvas and graphics policy; the caller now supplies measured dimensions and its explicit choice. |
| Arbitrary/stale interaction assumption | `reel_deliver` treated a build's `timelines_built` name (or its suffix-stripped form) as the current delivery target. A stage name is neither a current target nor identity. | Removed. Delivery follows `staged_timelines` to the recorded Resolve timeline ID, resolves that ID in the exact project, and passes the same ID to the render path. Legacy records without identity can only resolve the exact plan name. |

The row order in `timeline_layout.py` is the reel product's declared layout
convention, not a Resolve API invariant. Qualification fixtures that assert
V1/A1 are intentionally describing their fixture shape. Product validators
that read named manifest tracks are likewise validating the manifest
contract, not operating the Resolve UI or guessing a live row.
