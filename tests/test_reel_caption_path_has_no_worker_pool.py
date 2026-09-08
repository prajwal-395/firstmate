"""No worker pool fans out renderer subprocesses on the caption path.

The defect this pins (issue #530)
---------------------------------
`library/tools/reel_build.py` rendered caption overlays through
`ThreadPoolExecutor(max_workers=8)`, where each worker ran a full
`npx remotion render` subprocess bringing its own headless browser with
its own internal concurrency. Measured on the captain's 10-core machine,
2026-09-05: 1-minute load 4.42 before, 18.12 twenty seconds in, 27.75
peak during teardown after the run was killed.

`8` was a literal encoding an assumption about a machine, never measured
against one (the pipeline holds no hardcoded values). The fix removed
the pool outright (890a61b: captions render sequentially through step
4.05's renderer seam) and then removed the deeper cost - one bundle and
browser launch per card - with the shared batch renderer whose
concurrency is DERIVED from the machine (`library/tools/remotion_batch.py`,
pinned by `tests/test_remotion_batch.py`).

What this guards, and what it does not
---------------------------------------
This guards the exact site the issue named and the sibling render loops
with the same shape: a pool literal reintroduced in any of them
recreates the load spike whatever the batch module does, because each
worker still carries a full renderer's fan-out. It does NOT guard
`remotion_batch.py` itself - that module deliberately manages
concurrency (frame and encoder bounds derived from free cores), owns no
pool literal, and is pinned by its own tests. A legitimate future need
for parallel rendering belongs there, behind a derived bound, not as a
pool literal at a call site.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

RENDER_PATH_FILES = (
    # The file the issue named (`reel_build.py:549`).
    "library/tools/reel_build.py",
    # Step 4.05 renders each card today, one subprocess at a time.
    "library/steps/step_4_05_render_subtitles/step.py",
    # Sibling per-card render loops with the same fan-out shape.
    "library/steps/step_4_06_render_motion_graphics/post_bridge.py",
    "library/tools/timed_text_render.py",
    "library/tools/full_frame_element.py",
    "library/tools/bookend_render.py",
)

FORBIDDEN = (
    "ThreadPoolExecutor",
    "ProcessPoolExecutor",
    "concurrent.futures",
    "multiprocessing.Pool",
    "max_workers",
)


def pool_literals_in(paths) -> dict:
    """Map each file holding a worker-pool literal to the literals found."""
    found = {}
    for rel in paths:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        hits = sorted(token for token in FORBIDDEN if token in text)
        if hits:
            found[rel] = hits
    return found


def test_no_worker_pool_fans_out_renderer_subprocesses():
    found = pool_literals_in(RENDER_PATH_FILES)
    assert not found, (
        f"a worker-pool literal is back on the render path: {found}. "
        f"Issue #530 measured 8 pooled `npx remotion render` workers "
        f"taking a 10-core box from load 4.42 to 18.12. Parallel "
        f"rendering belongs in `library/tools/remotion_batch.py` behind "
        f"a machine-derived bound, never as a pool literal at a call "
        f"site."
    )
