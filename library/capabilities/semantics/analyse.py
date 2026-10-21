"""
Step 1.03 Bridge: Semantic Analysis
Runs the vision pipeline on new raw footage clips to generate semantic analysis documents.

**SILENCE IS NOT A WEDGE.** The child declares what it is doing and how
many units of work it has finished (`library/tools/vision_progress.py`),
and this step reads THAT. A child that is working is not killed however
long it is quiet; a child that stops moving is killed, and the reason
names the unit it was inside. A child that cannot become resident (the
model's memory is not available on this machine) refuses the WHOLE batch
with a reason, and every clip is reported at once - one bounded wait,
not one wait per clip.
[why](docs/RULE_EVIDENCE.md#the-vision-pass-was-killed-for-being-quiet)

Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**Never invoke `step_1_03_semantic_analysis/step.py` against a real project to test it.**
Exercise the collection half with an analysis dir of copied profiles and `raw_footage_files: []`. [why](docs/RULE_EVIDENCE.md#semantic-analysis-triggers-a-vision-run)
"""
import json, sys, subprocess, os, glob, time, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))
from library.tools.vision_schema_adapter import adapt_semantic_document, is_v3_profile
from library.tools.project_layout import Area, ProjectLayout
from library.tools import code_identity, footage_identity
from library.tools import vision_progress

# Per-clip ceiling for the vision analyser. It was 600s, which is under
# what a long clip needs: measured on this machine, a 3.6s clip costs 86s
# (five model calls, most of it fixed overhead) and the per-window passes
# scale with duration, so project 001's 188s clip is well past ten
# minutes. A clip that overruns is SKIPPED, not fatal, so a tight ceiling
# silently drops the longest - and therefore usually the most important -
# footage from the analysis. This one exists to break a wedge.
#
# It stays a PER-CLIP ceiling although one child now analyses the whole
# batch: the clock restarts every time the child lands a profile, so a
# wedge is broken after this long on ONE clip, never after N times it.
#
# It also restarts when the child FINISHES A UNIT of work (a frame, a
# window cut, a model call - `library/tools/vision_progress.py`), so the
# ceiling bounds a WEDGE and not a clip that is merely long: a 4941 s
# source needs thousands of units, and a ceiling that counted the whole
# clip would drop the longest - and usually the most important - footage
# from the analysis, which is what this ceiling was raised away from.
CLIP_ANALYSIS_TIMEOUT_S = int(os.environ.get("PIPELINE_CLIP_ANALYSIS_TIMEOUT_S", 3600))
_PROGRESS_POLL_S = 5.0
# Silence is not a wedge. A child that is working says so through the
# progress channel; this bound only catches a child that has stopped
# moving entirely (the rule that survives a child too old to write that
# channel). Measured 2026-10-20: the vision pass is silent through its
# long phases, and a 600 s silence bound killed a working child and
# reported the loss as a missing measurement.
VISION_STALL_TIMEOUT_S = int(os.environ.get(
    "PIPELINE_VISION_STALL_TIMEOUT_S", 600))
# How long ONE declared unit of work may run before it reads as a wedge
# inside that unit. A model call, a frame extraction, a window cut and
# the model load are each one unit; a unit that runs this long is not
# going to finish, and the machine should not keep holding the model's
# memory for it.
VISION_PHASE_TIMEOUT_S = int(os.environ.get(
    "PIPELINE_VISION_PHASE_TIMEOUT_S", 900))


class VisionStalled(RuntimeError):
    """The vision child stopped producing logs and profile progress."""


def _run_clip_vision(cmd, progress=None, poll_s=_PROGRESS_POLL_S,
                     progress_file=None):
    """Run one vision child without letting it write our stdout.

    The step's contract with the runner is JSON on stdout, logs on
    stderr (`library/tools/step_stdout.py`), and the final `json.dump`
    below is parsed whole. The vision child prints progress chatter to
    its own stdout ("Model loaded/bound in ...", window lines); run
    uncaptured, that chatter inherits this process's stdout and is
    prepended to the result, so the runner rejects the whole step as
    "Step produced invalid JSON" AFTER every clip was analysed
    (measured 2026-09-24 on the rung-0a proof run: 11 profiles
    collected, step failed, retry burned the same wall twice). See
    below for where the captured streams go.

    `progress` is a zero-argument callable returning a count that grows
    as the child finishes clips (profiles on disk); the
    `CLIP_ANALYSIS_TIMEOUT_S` deadline restarts whenever it grows.

    `progress_file` is the child's progress channel
    (`library/tools/vision_progress.py`): the child records what it is
    doing and how many units of work it has finished, and this parent
    reads that instead of GUESSING from silence. A child that finishes
    units keeps the `CLIP_ANALYSIS_TIMEOUT_S` ceiling and the
    `VISION_STALL_TIMEOUT_S` silence bound from firing, however long it
    is quiet; a child that declares one unit and never finishes it is a
    wedge inside that unit and is killed after `VISION_PHASE_TIMEOUT_S`
    (env `PIPELINE_VISION_PHASE_TIMEOUT_S`, default 900). A child that
    reports nothing at all - too old to write the channel - is still
    caught by the silence bound after `VISION_STALL_TIMEOUT_S` (env
    `PIPELINE_VISION_STALL_TIMEOUT_S`, default 600) and raises
    `VisionStalled`, so a wedged model pass still releases its memory
    instead of sleeping forever. A nonzero exit raises
    `CalledProcessError`; an overrun kills the child and raises
    `TimeoutExpired`.

    Both of the child's streams are captured to a pipe and forwarded to
    this process's stderr, where the runner streams them as the step's
    log, live. Capturing (rather than inheriting the stderr descriptor)
    is what lets the stall bound watch for log output.
    """
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"
    # The child resolves its model through Hugging Face Hub; these flags
    # make every hub access in the child use the local cache and refuse
    # network access instead of blocking on or downloading hub metadata.
    # The loader itself sets the same flags around `load`
    # (`vision_pipeline_v3._huggingface_offline`); this is the outer
    # half, so no other hub touch in the child can wait on the network.
    child_env["HF_HUB_OFFLINE"] = "1"
    child_env["TRANSFORMERS_OFFLINE"] = "1"
    watcher = vision_progress.ProgressWatcher(progress_file)
    if progress_file:
        # A record left by an earlier child is not this child's progress.
        try:
            os.unlink(progress_file)
        except OSError:
            pass
    child = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
        env=child_env)
    last = progress() if progress else None
    deadline = time.monotonic() + CLIP_ANALYSIS_TIMEOUT_S
    stall_deadline = time.monotonic() + VISION_STALL_TIMEOUT_S
    returncode = None
    try:
        while True:
            # The wait is bounded by the poll interval and the clip
            # ceiling only. The silence bound is NOT part of it: a child
            # that declares a unit of work is inside that unit, and the
            # silence bound does not apply to it - a zero-length wait
            # would spin this loop on a child that is working.
            remaining = max(0.0, min(
                poll_s, deadline - time.monotonic()))
            readable, _, _ = select.select([child.stdout], [], [], remaining)
            if readable:
                try:
                    output = os.read(child.stdout.fileno(), 65536)
                except OSError:
                    output = b""
                if output:
                    sys.stderr.buffer.write(output)
                    sys.stderr.buffer.flush()
                    stall_deadline = time.monotonic() + VISION_STALL_TIMEOUT_S
            returncode = child.poll()
            if returncode is not None:
                break
            if progress:
                now = progress()
                if now != last:
                    last = now
                    deadline = time.monotonic() + CLIP_ANALYSIS_TIMEOUT_S
                    stall_deadline = time.monotonic() + VISION_STALL_TIMEOUT_S
            state = watcher.observe()
            now = time.monotonic()
            if state["advanced"]:
                # The child finished a unit of work: it is working, and
                # neither the ceiling nor the silence bound applies to it.
                deadline = now + CLIP_ANALYSIS_TIMEOUT_S
                stall_deadline = now + VISION_STALL_TIMEOUT_S
            if state["inflight"]:
                # A declared unit is running. It is held to the phase
                # bound below, and the silence bound does not apply to
                # it: a child that says what it is doing is not a child
                # that has stopped.
                if state["unit_age_s"] >= VISION_PHASE_TIMEOUT_S:
                    raise VisionStalled(
                        f"Vision analysis stalled inside "
                        f"{state['unit_label']!r}: no unit of work finished "
                        f"for {VISION_PHASE_TIMEOUT_S}s; stopped child to "
                        "release model memory")
            elif now >= stall_deadline:
                # Nothing in flight and nothing said for a long time: the
                # child is not answering at all.
                raise VisionStalled(
                    "Vision analysis stalled: no log or completed profile "
                    f"for {VISION_STALL_TIMEOUT_S}s; stopped child to release "
                    "model memory")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(cmd, CLIP_ANALYSIS_TIMEOUT_S)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
        try:
            _drain_child_output(child)
        finally:
            child.stdout.close()
    if returncode:
        raise subprocess.CalledProcessError(returncode, cmd)
    return returncode


def _drain_child_output(child):
    """Forward whatever the child wrote before it exited.

    The poll loop above forwards log lines while the child runs; without
    this, the lines it wrote between the last read and its exit would be
    lost. A closed pipe reads as empty, so this never blocks.
    """
    while True:
        try:
            output = os.read(child.stdout.fileno(), 65536)
        except OSError:
            return
        if not output:
            return
        sys.stderr.buffer.write(output)
        sys.stderr.buffer.flush()


def _batch_refusal(progress_file):
    """The reason the child refused EVERY clip, or None.

    A child that cannot become resident (the model's memory is not
    available on this machine) refuses the whole batch: every clip needs
    the same model. The refusal is recorded in the channel the child
    writes, so the parent reads the reason instead of inferring it from
    an exit code.
    """
    if not progress_file:
        return None
    record = vision_progress.read_progress(progress_file)
    if not record:
        return None
    refusal = record.get("refusal")
    if not isinstance(refusal, dict):
        return None
    if str(refusal.get("scope") or "") != "batch":
        return None
    return str(refusal.get("reason") or "the vision child refused the batch")


def _analyse_missing(missing_paths, base_cmd, analysis_dir, run=None,
                     on_profile=None, on_clip_failure=None,
                     progress_file=None):
    """Analyse `missing_paths` in as few vision children as possible.

    One child takes the whole batch, so Gemma is loaded once per run
    instead of once per clip (measured 2026-10-01: three 6s clips paid
    three model loads). The child writes each profile the moment its
    clip finishes, so an interrupted run still resumes per clip.

    A clip that kills the child (crash, nonzero exit, wedge) costs only
    itself, as when each clip had its own child: the child works the
    list IN ORDER, so the clip it died on is the first one still without
    a profile. That clip is reported and skipped, and a fresh child takes
    the rest. A clip the child declines without dying (no video stream)
    gets no profile and is not retried, as before.

    `progress_file` is the child's progress channel
    (`library/tools/vision_progress.py`): it is what lets the parent tell
    a working child from a wedged one, instead of reading silence.
    """
    run = run or _run_clip_vision
    pending = list(missing_paths)

    def _profiles_on_disk():
        if on_profile:
            on_profile()
        return len(_profile_stems(analysis_dir))

    while pending:
        print(f"  Analyzing {len(pending)} clip(s) in one vision run: "
              + ", ".join(os.path.basename(p) for p in pending),
              file=sys.stderr)
        try:
            run(base_cmd + ['--clip', *pending], progress=_profiles_on_disk,
                progress_file=progress_file)
            return
        except VisionStalled as exc:
            failure = str(exc)
        except subprocess.TimeoutExpired:
            failure = "Timeout"
        except subprocess.CalledProcessError as e:
            failure = f"Error ({e})"
        refusal = _batch_refusal(progress_file)
        if refusal:
            # The child refused for the WHOLE batch: every clip it was
            # asked about is unmeasured for this one reason, and a fresh
            # child would refuse the same way. Reporting it once is the
            # difference between one bounded wait and one wait per clip.
            for path in pending:
                if on_clip_failure:
                    on_clip_failure(path, refusal)
            print(f"  ⚠ {refusal}", file=sys.stderr)
            return
        done = _profile_stems(analysis_dir)
        remaining = [p for p in pending
                     if os.path.splitext(os.path.basename(p))[0] not in done]
        if not remaining:
            return
        failed_path = remaining[0]
        if on_clip_failure:
            on_clip_failure(failed_path, failure)
        print(f"  ⚠ {failure} on {os.path.basename(remaining[0])}, skipping",
              file=sys.stderr)
        pending = remaining[1:]


def _alias_duplicate_profile(analysis_dir, dup_path, dup_stem, orig_stem):
    """Give a duplicate clip the analyzed original's profile, by copy.

    The duplicate was skipped (same bytes as the original), so it has no
    profile of its own.  Copy the original's profile to the duplicate's
    stem, rewriting the path keys so the join maps it to the duplicate's
    clip_id - a lookup by either id then resolves to the same analysis.
    """
    src = os.path.join(analysis_dir, f"clip_profile_{orig_stem}_v3.json")
    dst = os.path.join(analysis_dir, f"clip_profile_{dup_stem}_v3.json")
    if not os.path.exists(src):
        return
    with open(src, encoding="utf-8") as handle:
        profile = json.load(handle)
    resolved = os.path.realpath(dup_path)
    for key in ("file_path", "path", "source_file"):
        if key in profile:
            profile[key] = resolved
    with open(dst, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, indent=2)


# Profiles are keyed by the media file's STEM, and that is deliberate.
#
# `vision_pipeline_v3` writes `clip_profile_<stem>_v3.json` and skips a
# clip whose file is already there, so the stem is the analyser's own
# cache key. `semantic_index.build_semantic_lookup` joins those documents
# to the catalog's synthetic `clip_XXX` ids by file path, so nothing
# downstream needs the file renamed.
#
# A `_rename_profile` helper used to rename each fresh profile from the
# stem to the catalog clip_id. It never once fired against v3 output: it
# looked for `clip_profile_<stem>.json` and `clip_profile_<stem>_video_only.json`
# while the analyser writes `clip_profile_<stem>_v3.json`. Meanwhile the
# "already analysed?" check below compared catalog clip_ids (`clip_001`)
# against the stems on disk (`IMG_1806_v3`) and therefore never matched,
# so EVERY clip was re-analysed on EVERY run - 45 to 90 minutes of vision
# on project 001, thrown away and redone each time. Had the rename ever
# worked it would have broken the analyser's own cache instead. Removed
# rather than repaired: the stem is the key, in both places.


# Collision-avoidance suffix appended by _unique() in project_migration.py.
# These are byte-identical duplicates and must be filtered out wherever the
# step globs for profiles, or the output lists phantom clips.
_COLLISION_SUFFIX_RE = __import__('re').compile(r'__\d+$')


def _is_collision_duplicate(filename):
    """True when *filename* was created by the collision-avoiding writer.

    The migration tool appends ``__2``, ``__3``, ... to avoid overwriting.
    These are byte-identical copies of the originals and should never be
    treated as distinct profiles.
    """
    stem = os.path.splitext(filename)[0]
    # strip the `clip_profile_` prefix and the optional `_v3` suffix
    name = stem
    if name.startswith('clip_profile_'):
        name = name[len('clip_profile_'):]
    if name.endswith('_v3'):
        name = name[:-len('_v3')]
    return bool(_COLLISION_SUFFIX_RE.search(name))


def _profile_stems(analysis_dir):
    """File stems that already have a profile in `analysis_dir`.

    Tolerates the two suffixes the analyser has used (`_v3`, and none) and
    ignores the partial `_video_only` documents and collision-avoidance
    duplicates (``__2``, ``__3``, ...).
    """
    stems = set()
    for path in glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json')):
        basename = os.path.basename(path)
        if _is_collision_duplicate(basename):
            continue
        name = basename[len('clip_profile_'):-len('.json')]
        if not name or name.endswith('_video_only'):
            continue
        if name.endswith('_v3'):
            name = name[:-len('_v3')]
        stems.add(name)
    return stems


def _require_keys(obj, keys, context):
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{context}: missing required keys: {missing}")

class VisionPipelineMissing(Exception):
    """The v3 analyser is not on disk, so nothing can be measured."""


def _read_semantic_profile(path):
    with open(path, encoding="utf-8") as fp:
        profile_data = json.load(fp)
    was_v3 = is_v3_profile(profile_data)
    profile_data = adapt_semantic_document(profile_data)
    profile_data["clip_id"] = os.path.basename(path).replace(
        "clip_profile_", "").replace(".json", "")
    return profile_data, was_v3


def analyse_semantics(raw_footage_files: list, project_folder: str = "",
                      clip_catalog: list | None = None,
                      stream_run_id: str = "") -> dict:
    """Run the v3 vision pass over any clip without a profile, then collect all.

    Returns `{"semantic_analysis_documents": [...], "total_clips_analyzed": n}`.
    Raises `VisionPipelineMissing` when the analyser is absent.

    A clip that times out or errors is reported on stderr and left
    without a profile rather than failing the others - the collection
    below reads whatever is on disk.
    """
    # Profiles are pipeline OUTPUT and land in the output tree.
    #
    # They used to be written to raw/analysis/, inside the captain's own
    # footage directory - so a step's product sat among the source
    # material it was derived from, and `raw/` was not read-only in
    # practice. The layout owner has no writable area under raw/, which
    # is what makes that unrepeatable rather than merely fixed.
    # See library/tools/project_layout.py.
    layout = ProjectLayout(project_folder or os.getcwd())
    analysis_dir = str(layout.write_dir(Area.VISION_ANALYSIS, step="semantic_analysis"))
    
    # Path to the vision pipeline tool (repo-relative)
    PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    VISION_PIPELINE = os.path.join(PILOT_ROOT, 'library', 'tools', 'analysis', 'vision_pipeline_v3.py')
    
    if not os.path.exists(VISION_PIPELINE):
        print(f"  ✗ Vision pipeline not found at {VISION_PIPELINE}", file=sys.stderr)
        print(f"    Expected: library/tools/analysis/vision_pipeline_v3.py", file=sys.stderr)
        raise VisionPipelineMissing(VISION_PIPELINE)
    
    # Which clips already have a profile, keyed by file stem - the same key
    # the analyser caches on. See the note above _profile_stems.
    #
    # Profiles are trusted only when they were written by THIS code. The
    # ledger forgets the step when its code changes
    # (`apply_code_identity`), but this step resumes from its on-disk
    # profiles - so a re-run after a method change printed
    # "invalidating" and then reused every stale profile (finding 8,
    # execution-frontier report 2026-09-24). The stamp beside the
    # profiles names the code that wrote them (`code_identity`); a
    # mismatch means the profiles describe what an older method saw, and
    # they are removed so the analyser below recomposes every clip.
    # No stamp reads as unknown code, never as a match: a cache from
    # before stamps existed is re-analyzed once, then stamped.
    #
    # Removing a profile no longer re-measures it whole: the analyser
    # composes each profile from measurement layers cached per source
    # (`library/tools/analysis/measurement_layers.py`), and only a
    # layer whose own method or inputs changed is measured again.
    step_code_hash = code_identity.current_code_hash(
        os.path.dirname(os.path.abspath(__file__)))
    # A None hash means there was nothing to hash: it matches nothing,
    # so the profiles below are re-measured rather than trusted.
    if (step_code_hash
            and step_code_hash == code_identity.read_code_stamp(
                analysis_dir)):
        existing_profiles = _profile_stems(analysis_dir)
    else:
        stale = sorted(glob.glob(
            os.path.join(analysis_dir, 'clip_profile_*.json')))
        if stale:
            print(f"Semantic Analysis: step code changed since "
                  f"{len(stale)} cached profile(s) were written - "
                  f"removing them and recomposing every clip "
                  f"(unchanged measurement layers are reused)",
                  file=sys.stderr)
            for path in stale:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    # Another writer already removed it; it is no
                    # longer available for the analyser to reuse.
                    continue
                except OSError as exc:
                    # The analyser independently skips an existing
                    # profile unless --force is passed. Continuing after
                    # a failed unlink would therefore let it return the
                    # stale profile and the stamp below would bless that
                    # file as if this method had written it.
                    raise RuntimeError(
                        f"Cannot invalidate stale semantic profile "
                        f"{path} ({exc}); refusing to run the analyser "
                        f"against a cache written by older code") from exc
        existing_profiles = set()

    stream_source_index = None
    published_stems = set()
    published_clip_ids = set()
    if clip_catalog is not None and stream_run_id:
        from library.tools import semantic_profile_stream
        stream_source_index = semantic_profile_stream.catalog_source_index(
            clip_catalog)

    def publish_completed_profiles():
        if stream_source_index is None:
            return
        from library.tools import semantic_profile_stream

        completed_stems = _profile_stems(analysis_dir)
        for profile_path in sorted(glob.glob(
                os.path.join(analysis_dir, "clip_profile_*.json"))):
            basename = os.path.basename(profile_path)
            if "_video_only" in basename or _is_collision_duplicate(basename):
                continue
            filename_stem = basename[len("clip_profile_"):-len(".json")]
            if filename_stem.endswith("_v3"):
                filename_stem = filename_stem[:-3]
            if filename_stem not in completed_stems:
                continue
            if filename_stem in published_stems:
                continue
            try:
                profile_data, _was_v3 = _read_semantic_profile(profile_path)
            except json.JSONDecodeError:
                # The child profile itself is atomic. A damaged legacy cache
                # is left for the next semantic run to invalidate or replace.
                print(f"  ⚠ Invalid JSON in {profile_path}, not publishing",
                      file=sys.stderr)
                published_stems.add(filename_stem)
                continue
            joined = semantic_profile_stream.clip_for_profile(
                profile_data, basename, stream_source_index)
            if joined is None:
                print(f"  ⚠ No catalog clip_id for semantic profile "
                      f"{basename}; it remains in the aggregate only",
                      file=sys.stderr)
                published_stems.add(filename_stem)
                continue
            clip_id, source_path = joined
            semantic_profile_stream.publish_record(
                project_folder,
                clip_id=clip_id,
                source_path=source_path,
                source_stem=filename_stem,
                profile=profile_data,
                run_id=stream_run_id,
                producer_code_hash=step_code_hash or "",
            )
            published_stems.add(filename_stem)
            published_clip_ids.add(clip_id)

    def mark_clip_missing(source_path, reason):
        if stream_source_index is None:
            return
        from library.tools import semantic_profile_stream

        clip_id = stream_source_index[0].get(os.path.realpath(source_path))
        if clip_id is None:
            return
        semantic_profile_stream.publish_missing_record(
            project_folder,
            clip_id=clip_id,
            source_path=source_path,
            source_stem=os.path.splitext(os.path.basename(source_path))[0],
            run_id=stream_run_id,
            producer_code_hash=step_code_hash or "",
            reason=reason,
        )

    # Find clips that need analysis
    all_clips = []
    missing_clips = []
    analyzed_digests: dict = {}
    duplicates: list = []
    for file_info in raw_footage_files:
        if isinstance(file_info, dict):
            fpath = file_info["path"]
            clip_id = file_info.get("clip_id", "")
        else:
            fpath = file_info
            clip_id = os.path.splitext(os.path.basename(fpath))[0]

        all_clips.append(fpath)
        stem = os.path.splitext(os.path.basename(fpath))[0]
        if stem in existing_profiles:
            continue
        digest = footage_identity.fingerprint(fpath)["content_digest"]
        if digest in analyzed_digests:
            duplicates.append(
                (fpath, clip_id, stem, analyzed_digests[digest]))
            continue
        analyzed_digests[digest] = stem
        missing_clips.append({"path": fpath, "clip_id": clip_id})

    print(f"Semantic Analysis: {len(all_clips)} total clips, {len(existing_profiles)} already analyzed, {len(missing_clips)} remaining", file=sys.stderr)

    # Cached profiles are durable too. Publish them before launching the
    # child so a consumer can use the first clip while later clips run.
    publish_completed_profiles()
    
    # Only run vision pipeline on missing clips
    if missing_clips:
        print(f"Running vision pipeline on {len(missing_clips)} new clips...", file=sys.stderr)
        
        # The harness travels via PIPELINE_HOST_HARNESS, set by the runner
        # from --full-auto. Codex answers stills through its subscription
        # CLI when available; otherwise the router uses gemma.
        #
        # The child reports its progress through the channel the parent
        # reads (`library/tools/vision_progress.py`), so a long clip that
        # is quiet while it works is not mistaken for a wedged one.
        progress_file = str(vision_progress.progress_path(analysis_dir))
        base_cmd = [sys.executable, VISION_PIPELINE,
                    '--output-dir', analysis_dir,
                    '--progress-file', progress_file]
        if project_folder:
            base_cmd += ['--project-folder', project_folder]
        _analyse_missing([clip["path"] for clip in missing_clips],
                         base_cmd, analysis_dir,
                         on_profile=publish_completed_profiles,
                         on_clip_failure=mark_clip_missing,
                         progress_file=progress_file)
        for dup_path, _dup_clip_id, dup_stem, orig_stem in duplicates:
            _alias_duplicate_profile(analysis_dir, dup_path, dup_stem, orig_stem)
        publish_completed_profiles()
    else:
        print("All clips already have vision profiles, skipping analysis.", file=sys.stderr)
    
    # Collect ALL clip profiles (existing + new).
    #
    # The v3 analyser emits scene[]/camera[]/actions[]/objects[]/assessment{}
    # while every consumer addresses the retired analysis.*/blocks shape.
    # Passing profiles through verbatim, as this step used to, meant the
    # richest observation of the footage reached nobody - and a re-run of
    # the analysis failed step 3.02 outright, because no clip yielded a
    # description. The adapter derives the consumer-facing view alongside
    # the v3 fields; legacy profiles pass through unchanged.
    profiles = []
    adapted_count = 0

    for f in sorted(glob.glob(os.path.join(analysis_dir, 'clip_profile_*.json'))):
        if '_video_only' in f:
            continue  # Skip partial profiles
        if _is_collision_duplicate(os.path.basename(f)):
            continue  # Skip __N collision-avoidance duplicates
        try:
            profile_data, was_v3 = _read_semantic_profile(f)
            if was_v3:
                adapted_count += 1
            profiles.append(profile_data)
        except json.JSONDecodeError:
            print(f"  ⚠ Invalid JSON in {f}, skipping", file=sys.stderr)

    print(f"Collected {len(profiles)} clip profiles "
          f"({adapted_count} adapted from the v3 vision schema)", file=sys.stderr)

    if stream_source_index is not None:
        for clip in clip_catalog:
            if clip["clip_id"] not in published_clip_ids:
                mark_clip_missing(
                    clip["path"],
                    "semantic analysis completed without a usable profile")

    # Stamp what this run's profiles were written under, so the next run
    # can tell its own method's output from an older one's. Written even
    # when nothing was analyzed: an empty collection under current code
    # is a fact the next run may trust, not a gap to re-probe.
    code_identity.write_code_stamp(analysis_dir, step_code_hash)

    return {
        'semantic_analysis_documents': profiles,
        'total_clips_analyzed': len(profiles),
    }


def main():
    data = json.loads(sys.stdin.read())

    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    try:
        from library.tools.semantic_profile_stream import current_run_context
        stream_context = current_run_context(data.get("project_folder", ""))
        result = analyse_semantics(
            raw_footage_files=data.get('raw_footage_files', []),
            project_folder=data.get('project_folder', ''),
            clip_catalog=data["clip_catalog"],
            stream_run_id=(stream_context["run_id"]
                           if stream_context["expected"] else ""),
        )
    except VisionPipelineMissing:
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == '__main__':
    main()
