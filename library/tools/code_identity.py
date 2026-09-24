"""The identity of the code that produced a cached preflight value.

The problem this exists to remove
---------------------------------
The preflight cache is invalidated by the FOOTAGE and never by the CODE
that wrote it.  A fix to a preflight step - a new field, a corrected
measurement, a changed prompt - is silently invisible on any project
that already has a cache: the system reports success while the work did
not happen.  Three commits of work read a path that never fired.

The identity check here is the companion to
``library/tools/footage_identity.py``.  That one watches the footage;
this one watches the code.  Together they make "preflight is skipped
once done" safe rather than merely fast.

What this hashes
----------------
Every ``.py`` and ``.json`` file in the step's own directory, sorted by
name, excluding ``__pycache__``.  This is the step's declared source: its
``step.py``, ``bridge.py``, ``manifest.json`` and ``handoff.md`` are all
there.  ``.md`` files are excluded because they are prose documentation,
not executable code - a typo fix in a handoff doc should not cost 69
minutes of vision analysis.

What this deliberately does NOT hash
-------------------------------------
*Not the model weights.*  A WhisperX upgrade is not a code change to step
1.04.  The operator can ``--rerun temporal_index`` for that.

*Not undeclared imports.*  Plumbing every step shares - the project
layout, stdout claiming, the brand registry's project declarations -
does not change a measured value and is listed in ``EXEMPT_IMPORTS``
rather than hashed, so touching it does not cost a 69-minute vision
re-run.  What IS hashed beyond the step directory is declared below in
``STEP_IMPLEMENTATION_DEPS``: the shared implementation files a step
executes as measurement code.  An undeclared shared import is a gap in
that declaration, and ``tests/test_step_ledger.py`` refuses it - it
scans every DAG-preflight step for the reference and fails until the
map covers it.

Why the map exists
------------------
Step 1.03's directory holds a launcher; the measurement lives in
``library/tools/analysis/vision_pipeline_v3.py``, which the step runs
as a subprocess.  Hashing the directory alone watches the launcher and
never the algorithm, so a fix to the algorithm - the usable-ranges
A-roll gate - was invisible to the cache on every existing project and
the run reported success while the work did not happen.  The map makes
that file part of step 1.03's identity, and the same shape everywhere
else it occurs: any preflight step whose cached values are computed by
shared code names that code here.

Adoption on first encounter
----------------------------
A ledger entry with no recorded ``code_hash`` is from before this check
existed.  It is ADOPTED - the current hash is recorded and the step is
not invalidated.  This matches the pattern ``apply_source_identity`` uses
for footage fingerprints (line 668-670 of run_pipeline.py), and avoids a
69-minute cold pass on the first run after the upgrade.

The trade-off, stated rather than hidden: an existing cached value
produced by buggy code survives one more run under adoption.  From the
second run onward, the hash is recorded and any code change invalidates
the cache.  An operator who wants to force the recompute can
``--rerun preflight`` once.
"""

import hashlib
import os
from pathlib import Path
from typing import Dict, Optional


# The extensions that count as executable source for a step.
# .md is deliberately excluded - a prose edit in handoff.md is not a code
# change and should not cost a 69-minute recomputation.
_SOURCE_EXTENSIONS = {".py", ".json"}

# Repo root, for resolving the implementation files below.  Overridable
# per call so tests can hash a scratch tree instead of this one.
REPO_ROOT = Path(__file__).resolve().parents[2]


# Shared implementation files whose content belongs to a step's identity,
# keyed by step directory basename and repo-relative.  A file is listed
# here when a change to it can change the step's cached output: the step
# executes it as measurement code (a subprocess script or an imported
# measurement function).  Plumbing a step merely uses - the layout, a
# stdout claim, project declarations that never travel in a preflight
# cache - is NOT listed; it lives in EXEMPT_IMPORTS below.
#
# Keyed by directory, not DAG node id: node ids are the DAG's to rename,
# the directory is what is hashed.  A step with no row is hashed on its
# own directory alone (step 1.02's computation is inline in step.py and
# its only subprocess is the external ffprobe binary, which is not repo
# code and has no content to hash).
#
# step_1_06_object_segmentation has no row on purpose: it is unwired, so
# the runner never hashes it, and a declaration nothing reads is the
# failure mode output_contract exists to stop.  Whoever wires it into
# the DAG names library/tools/analysis/object_segmentation.py here, and
# the coverage test below fails until they do.
STEP_IMPLEMENTATION_DEPS = {
    "step_1_01_scan_project": (
        # enumerate_footage produces raw_footage_files, the step's output.
        "library/tools/footage_identity.py",
        # Its declaration parsing: which directory counts as the footage
        # root decides what the scan sees.  Scan is seconds, so a rare
        # schema edit re-scanning is the cheap, correct trade.
        "library/schemas/project_config.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies the scan's declaration
        # parsing validates against, so a fix here changes what the scan
        # accepts.
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # the scan's declaration parsing validates against (same chain
        # as the overlay_mode row above).
        "library/tools/graphics_renderer.py",
    ),
    "step_1_02_catalog_footage": (
        # Read by step.py for the source block's declared program stream
        # and measure flag: which audio stream counts as the program mix
        # decides what the catalog records, so a fix here changes the
        # cached rows.
        "library/tools/footage_identity.py",
        # Its declaration parsing: the source block is read through the
        # project config schema.
        "library/schemas/project_config.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies declaration parsing
        # validates against (same chain as the scan row above).
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # declaration parsing validates against (same chain as the
        # overlay_mode row above).
        "library/tools/graphics_renderer.py",
    ),
    "step_1_03_semantic_analysis": (
        # The executed measurement (D1: the usable-ranges gate lives here).
        "library/tools/analysis/vision_pipeline_v3.py",
        # Its measurement imports: soft-picture ranges and the steadiness
        # reading both land in the cached profile.
        "library/tools/analysis/picture_quality.py",
        "library/tools/camera_stability.py",
        # Imported by step.py: shapes the cached documents.
        "library/tools/vision_schema_adapter.py",
        # Imported by vision_schema_adapter (and vision_pipeline_v3):
        # renders the undescribed scene[] ranges into the prose every
        # consumer reads, so a fix here changes the cached documents.
        "library/tools/segment_coverage.py",
    ),
    "step_1_04_temporal_index": (
        # The project's declared transcription language: a fix here
        # changes which language every cached index is heard in.
        "library/tools/footage_identity.py",
        # Imported by footage_identity: the declaration parsing behind
        # the language and footage-root reads, so a schema fix changes
        # what the step accepts.
        "library/schemas/project_config.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies the declaration
        # parsing validates against (same chain as the scan entry).
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # the declaration parsing validates against (same chain as the
        # overlay_mode entry above).
        "library/tools/graphics_renderer.py",
        # Face measurement feeding the per-clip index.
        "library/tools/subject_framing.py",
        # Span extraction feeding region re-measurement.
        "library/tools/timeline_transcript.py",
        # Imported by timeline_transcript: project words bias the
        # transcription and surviving corrections are applied at the
        # transcript root, so a fix here changes the cached segments.
        "library/tools/transcript_corrections.py",
        # Imported by transcript_corrections: surviving corrections are
        # recorded as learnings, so a fix here changes what the cached
        # transcript carries. (Its own imports are exempt plumbing.)
        "library/tools/learned_context.py",
        # The confidence vocabulary carried in the cached segments
        # (avg_logprob): the transcription path keys its numbers here.
        "library/tools/transcript_confidence.py",
        # Imported by step.py beside its only use: normalizes the face
        # box that reaches the seeder, so a fix here changes the cached
        # face_boxes.
        "library/tools/analysis/object_segmentation.py",
        # Imported by timeline_transcript: the hybrid transcriber is the
        # primary arm that hears each clip, so a fix here changes the
        # cached segments.
        "library/tools/hybrid_transcription.py",
        # Imported by timeline_transcript beside the hybrid arm: forced
        # alignment where its environment is present, so a fix here
        # moves the cached word timings.
        "library/tools/mfa_align.py",
        # Imported by hybrid_transcription: the contained third-party
        # transcriber behind the hybrid arm, so a fix here changes what
        # the cached segments heard.
        "library/tools/heard_speech.py",
        # Imported by mfa_align: which interpreter carries the ML stack
        # decides what the alignment hears, so a fix here changes the
        # cached timings. (Its paths import is exempt plumbing.)
        "library/tools/shared_environment.py",
        # Imported by timeline_transcript: the one word-boundary clamp
        # both paths run, so a fix here moves the cached word ends.
        "library/tools/word_boundaries.py",
        # Imported by timeline_transcript: the row-fit predicate counts
        # the unfitted-text keys onto the cached document, so a fix here
        # changes them. (The per-reel rows_played half lives in
        # reel_hearing beside the Spans it reads, so this module reaches
        # no hearing machinery - pinned by
        # test_the_document_half_reaches_no_hearing_machinery.)
        "library/tools/transcript_fit.py",
    ),
    "step_1_05_prosody_analysis": (
        # The executed measurement.
        "library/tools/analysis/speech_advanced_pipeline.py",
        # Imported by step.py: classifies the cached profile.
        "library/tools/prosody_profile.py",
    ),
    "step_1_07_ocr_extraction": (
        # The whole measurement, imported by step.py.
        "library/tools/analysis/ocr_extractor.py",
    ),
}

# library.tools imports a step may reference WITHOUT declaring them
# above, each with the reason.  Anything else a step references must be
# declared: an undeclared measurement import is D1 again.
EXEMPT_IMPORTS = {
    # Directory plumbing: moving an area does not change a measured value.
    "library/tools/project_layout.py",
    # Centralized path configuration: every entry is a location or an
    # environment override, so a change here fails loud (a binary not
    # found) rather than as a silently stale cached value.  Declaring it
    # instead would re-run transcription over a moved directory - the
    # over-invalidation rot the coverage test exists to stop in the
    # other direction.
    "library/tools/paths.py",
    # Stdout claiming plumbing.
    "library/tools/step_stdout.py",
    # Project declarations.  step_ledger's rule is that they do NOT travel
    # in a preflight cache, so their code cannot stale one either.
    "library/tools/brand_registry.py",
    # Declaration VALIDATION.  The schema checks a declared delivery
    # format, framing intent or subtitle style when it reads project.yaml.
    # A change here can only move the refuse/accept line for a bad
    # declaration - a refusal surfaces loudly at run time, never as a
    # silently stale cached value for a valid project.  Declaring them
    # would drag brand_palette and safe_area into a footage scan's cache
    # key through their own imports, which is the over-invalidation rot
    # the coverage test exists to stop in the other direction.
    "library/tools/delivery_format.py",
    "library/tools/framing_intent.py",
    "library/tools/subtitle_style.py",
    # Deliver-preset validation vocabulary.  project_config.validate reads
    # only DELIVER_PRESET_KEYS, and only on the deliver_preset branch for
    # a project that declares one - the scan's own declaration block
    # (project_declared_config) carries no deliver keys at all.  A change
    # here can only move the refuse/accept line for a bad preset, which
    # every strict loader refuses loudly, never as a silently stale scan.
    # Declaring it instead would drag the whole reel-render machinery
    # (resolve_render, reel_build, reel_proposal, render_qa,
    # pool_stream_meta) into a footage scan's cache key through
    # reel_deliver's own imports - the over-invalidation rot the
    # coverage test exists to stop in the other direction.
    "library/tools/reel_deliver.py",
    # Live-Resolve ingest machinery.  The only preflight-reachable
    # reference is timeline_transcript.main, a command-line entry point
    # no run executes; preflight transcription never connects to
    # Resolve.  (It even imports a step file itself - orchestration, not
    # measurement.)  Re-examine the day a preflight step imports it as
    # measurement code.
    "library/tools/timeline_ingest.py",
}


def hash_asset_file(path: str) -> Optional[str]:
    """SHA-256 of a declared asset's bytes, or None when it is absent.

    The same shape as `plan_content_hash`: raw bytes, hex digest. None
    is a FIRST-CLASS answer - a declared file that is not on disk is a
    refusal elsewhere (`full_frame_element.measure_clip`,
    `placed_assets`), and a hash of nothing would match any other
    nothing. What calls this records the absence rather than hashing
    around it.

    Sized for media: the 16 MB logo card this was written for hashes in
    a fraction of a second; a caller hashing gigabyte-scale sources
    should say so rather than silently paying it every build.
    """
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def step_code_hash(step_dir: str, extra_files=()) -> Optional[str]:
    """A content hash of every source file in a step's directory.

    Returns None if the directory does not exist or contains no source
    files - callers treat that as "no identity to check".

    The hash is deterministic: files are sorted by name, each one
    contributes its relative name and its content, and the result is a
    single SHA-256 hex digest.

    ``extra_files`` are additional absolute paths folded in after the
    directory, each contributing its repo-relative name and its content.
    Missing files are skipped: a step cannot run without its
    implementation, so it fails loudly at run time, and the hash simply
    covers what is there.
    """
    step_path = Path(step_dir)
    if not step_path.is_dir():
        return None

    digest = hashlib.sha256()
    found = False
    for child in sorted(step_path.iterdir()):
        if child.is_dir():
            continue
        if child.suffix not in _SOURCE_EXTENSIONS:
            continue
        found = True
        # Include the filename so that renaming a file changes the hash.
        digest.update(child.name.encode("utf-8"))
        digest.update(child.read_bytes())

    for extra in sorted(set(extra_files)):
        try:
            data = Path(extra).read_bytes()
        except OSError:
            continue
        found = True
        digest.update(str(extra).encode("utf-8"))
        digest.update(data)

    return digest.hexdigest() if found else None


def implementation_deps(step_dir: str, repo_root=None) -> list:
    """Absolute paths of the declared shared files for one step directory.

    Looks the step up by directory basename in
    ``STEP_IMPLEMENTATION_DEPS`` and resolves each row against
    ``repo_root`` (default ``REPO_ROOT``).  Unknown steps have no
    declared implementation beyond their own directory.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    rels = STEP_IMPLEMENTATION_DEPS.get(Path(step_dir).name, ())
    return [str(root / rel) for rel in rels]


def code_hashes_for(step_dirs: Dict[str, str],
                    repo_root=None) -> Dict[str, str]:
    """{node_id: hash} for every step directory provided.

    A step whose directory is missing or empty is simply absent from the
    result.  Each step's hash folds in its declared
    ``STEP_IMPLEMENTATION_DEPS`` files, so a change to shared
    measurement code invalidates exactly the steps that execute it.
    """
    out: Dict[str, str] = {}
    for node_id, step_dir in step_dirs.items():
        h = step_code_hash(
            step_dir,
            extra_files=implementation_deps(step_dir, repo_root),
        )
        if h is not None:
            out[node_id] = h
    return out
