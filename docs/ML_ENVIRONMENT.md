# The ML environment layer 1 needs, and how to rebuild it

`scripts/full_suite_gate.sh` is layer 1 of [the three-layer CI design](CI_LAYERS.md), and
part of what makes it layer 1 rather than a copy of layer 2 is the `heavy_ml` selection:
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

`uv` resolves this stack in seconds and can fetch 3.12 itself:

```sh
uv venv --python 3.12 ~/.local/share/vep/venv-py312
uv pip install --python ~/.local/share/vep/venv-py312/bin/python3 \
    -r requirements.txt
uv pip install --python ~/.local/share/vep/venv-py312/bin/python3 \
    pytest httpx2
```

Without `uv`, any real 3.12 interpreter works:

```sh
python3.12 -m venv <path>
source <path>/bin/activate
pip install -r requirements.txt
pip install pytest httpx2
```

`requirements.txt` is the only source of versions. **Do not pin anything here or on the
command line**: the manifest already declares the constraints, a second copy drifts from it,
and a local pin is by definition the one thing CI never sees.

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

# 2. whisperx imports AND satisfies the declared range
$VENV/bin/python3 -c "import whisperx; import importlib.metadata as m; \
    print('whisperx', m.version('whisperx'))"     # must be >=3.8,<4

# 3. parselmouth imports - prosody measures nothing without it, and
#    reports available:false rather than failing
$VENV/bin/python3 -c "import parselmouth; print('parselmouth ok')"

# 4. the compliance check the CLI runs, against this interpreter
$VENV/bin/python3 manage_project.py list >/dev/null && echo "CLI ok"
```

Then the real proof, which is the only one that distinguishes a *usable* stack from an
importable one:

```sh
$VENV/bin/python3 -m pytest tests/ -m heavy_ml -rs
# expected: 2 passed
```

Measured on the durable venv, 2026-09-05: `2 passed, 4407 deselected in 23.96s`, with
whisperx 3.8.6, faster-whisper 1.2.1, ctranslate2 4.8.2, torch 2.8.0, torchaudio 2.8.0,
pyannote.audio 4.0.7.

## Point the gate at it

`scripts/full_suite_gate.sh` runs `python3` unless told otherwise:

```sh
FULL_SUITE_GATE_PYTHON=~/.local/share/vep/venv-py312/bin/python3 \
    scripts/full_suite_gate.sh
```

**If this is not set, and the ambient `python3` lacks the ML stack, the heavy tier is not
measured.** The gate is fail-closed and says so in its verdict line rather than passing
quietly - `heavy_ml` is named as not measured - but a verdict that names an omission is still
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
- [`CI_LAYERS.md`](CI_LAYERS.md) - why the heavy tier is local rather than on GitHub.
