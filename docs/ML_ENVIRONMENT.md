# The ML environment layer 1 needs, and how to rebuild it

`scripts/full_suite_gate.sh` is layer 1 of [the three-layer CI design](CI_LAYERS.md), and
part of what makes it layer 1 rather than a copy of layer 2 is the `real_model` selection:
those tests exercise the real Apple-Silicon dependencies, which no GitHub runner has. They
can only run against an interpreter that carries the ML stack **at the versions
`requirements.txt` declares**.

This page is the recreate procedure. It exists because a working environment on one machine
is not a guarantee, and the next machine - or the next person - has nothing without it.

## Why this is written down at all

Measured 2026-09-05. Every ML environment on the build machine was **Python 3.14.6 carrying
whisperx 3.2.0**, against a declared `whisperx>=3.8,<4` and a `requirements.txt` header
saying, in capitals, *"BUILD THIS ENVIRONMENT ON PYTHON 3.12. It is not a preference."*

whisperx 3.2.0 imports perfectly and then raises

```
TypeError: TranscriptionOptions.__init__() missing 2 required positional arguments:
'multilingual' and 'hotwords'
```

on every transcribe call, because on 3.14 there is no ctranslate2 wheel for what whisperx
3.2.0 pins, so faster-whisper 1.2.1 lands instead of the 1.0.0 it needs. `step_1_04` catches
that per clip and carries on, so project 001 measured **17 clips at "0 regions, 0.0s speech,
0 words"** - no transcript, no spine, no subtitles, the entire edit missing, reported as
success.

`manage_project.py` now REFUSES a non-compliant environment before a run starts, naming both
numbers:

```
ERROR: 'run' has ML dependencies this interpreter can import but requirements.txt
does not allow:
  whisperx: installed 3.2.0, requires <4,>=3.8
```

**A refusal whose remedy is undocumented is only half a fix.** This page is the other half.

## Build it

Python **3.12**, not `python3`. On the build machine `python3` is 3.14, and building with it
reproduces the broken environment exactly.

Install from the **lock**, `requirements/lock/macos-arm64-py312.txt`: the exact stack Ren
was measured on (macOS 14+ arm64, CPython 3.12, with hashes). `uv` can fetch 3.12 itself:

```sh
uv venv --python 3.12 ~/.local/share/vep/venv-py312
uv pip sync --python ~/.local/share/vep/venv-py312/bin/python3 \
    requirements/lock/macos-arm64-py312.txt
```

`sync` makes the venv EXACTLY the lock, removing anything undeclared - right for a new venv.
On an existing one that carries tools nothing declares (the build machine's does), use
`uv pip install -r` with the same file, which only adds and moves.

The lock already carries the test gate (`pytest`, `pytest-xdist`). Off Apple Silicon, or
without `uv`, install the policy instead - any real 3.12 interpreter works, and you get
whatever its floors resolve to today, not the measured stack:

```sh
python3.12 -m venv <path>
<path>/bin/pip install -r requirements.txt
```

### Policy, groups and the lock

`requirements.txt` is the **policy**: it `-r`-includes one file per dependency group under
`requirements/` (`core`, `graphics`, `analysis`, `identity`, `dev`), and each floor or
ceiling is stated once, with its measurement, in its group file. `pyproject.toml` exposes the
same groups as extras (`pip install -e '.[analysis]'`; `resolve`, `all` and `dev` compose
them). The map is `library/tools/dependency_groups.py`.

The lock is **generated, never edited**. After changing the venv on purpose (a floor raised,
a package added), regenerate it from that venv and commit it with the policy change:

```sh
scripts/lock_python_env.sh            # pins from the shared venv
```

It resolves `requirements.txt` for macOS arm64 / 3.12 with the venv's installed versions as
constraints, so every locked version is the measured one, and a venv that violates the policy
fails the resolve instead of being locked. Do not pin anything on the command line: a local
pin outside the policy and the lock is the one thing nobody else sees.

### Where to put it

**Outside the treehouse lane pool.** A venv inside a `.treehouse/*/video_editing_pilot`
worktree dies with that lane, and lanes are disposable. On the build machine the durable copy
is:

```
~/.local/share/vep/venv-py312
```

A per-checkout `.venv` is still fine for ordinary work - `.venv/` is gitignored and
`manage_project.py`'s advice names it - but the copy layer 1 depends on should not be
somewhere a teardown reaches.

## Verify it, before trusting it

Four checks. The first two are the ones that were silently false for weeks.

```sh
VENV=~/.local/share/vep/venv-py312

# 1. the interpreter is 3.12
$VENV/bin/python3 -V                       # Python 3.12.x

# 2. mlx_vlm imports AND satisfies the declared floor - vision is
#    broken on every real project below it, and it imports fine there
$VENV/bin/python3 -c "import mlx_vlm; import importlib.metadata as m; \
    print('mlx_vlm', m.version('mlx_vlm'))"     # must be >=0.7.2

# 3. parselmouth imports - prosody measures nothing without it, and
#    reports available:false rather than failing
$VENV/bin/python3 -c "import parselmouth; print('parselmouth ok')"

# 4. the compliance check the CLI runs, against this interpreter
$VENV/bin/python3 manage_project.py list >/dev/null && echo "CLI ok"
```

Then the real proof, which is the only one that distinguishes a *usable* stack from an
importable one:

```sh
$VENV/bin/python3 -m pytest tests/ -m real_model -rs
# expected: 2 passed
```

Measured on the durable venv, 2026-09-24: `2 passed` with mlx-vlm
0.7.2, mlx-lm 0.31.3, mlx 0.32.2 (latest), transformers 5.17.0,
huggingface-hub 1.33.0, torch 2.8.0, torchaudio 2.8.0 - and gemma4
12B loading the cached weights in 5.0s and answering on the
captain's footage in 4.9s through `vision_pipeline_v3`'s own load
path (see the PR body for the excerpt).

Transcription is NOT verified here: ingest transcribes through Voz
(`da voz`) and aligns through MFA, neither of which is a pip package.
`ren doctor` checks both are installed; step 1.04 refuses loudly on
audio neither can hear since the whisperx fallback arms left on
2026-09-24 (requirements.txt header has the chain).

## Point the gate at it

`scripts/full_suite_gate.sh` runs `python3` unless told otherwise:

```sh
FULL_SUITE_GATE_PYTHON=~/.local/share/vep/venv-py312/bin/python3 \
    scripts/full_suite_gate.sh
```

**If this is not set, and the ambient `python3` lacks the ML stack, the real_model category is not
measured.** The gate is fail-closed and says so in its verdict line rather than passing
quietly - `real_model` is named as not measured - but a verdict that names an omission is still
a verdict with an omission in it. Set the variable.

## A harmless warning you will see

`torchcodec` logs `dlopen` failures for `libavutil.5x.dylib` on macOS when the system ffmpeg
is newer than the versions it ships loaders for. It is a warning, the tests pass, and nothing
in this pipeline decodes through torchcodec - `library/tools/` shells out to `ffmpeg`
directly. Do not "fix" it by pinning ffmpeg.

## See also

- `requirements.txt` - its header carries the full 3.14 failure chain, and is the source of
  every version here.
- `manage_project.py` - `_noncompliant_ml_packages` is the check whose remedy this page is.
- [`CI_LAYERS.md`](CI_LAYERS.md) - why real-model qualification is local rather than on GitHub.
