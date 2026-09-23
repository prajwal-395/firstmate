# Where a shared dependency lives, and how a checkout finds it

**One location per MACHINE, not one per checkout.**

This pipeline needs two things that are large, slow to build, and
identical for every checkout of the same commit:

| | what | size |
|---|---|---|
| **Node** | `node_modules` for the Remotion renderer, which draws every caption, motion graphic, timed-text card, bookend and full-frame element | 285 MB |
| **Python** | an interpreter carrying whisperx, mlx_vlm, torch and the rest of the ML stack | ~4 GB |

Both were assumed to live inside whichever checkout was running. Neither
does, on any machine this repository has been installed on. This page is
the design and the recreate procedure for both halves;
[`ML_ENVIRONMENT.md`](ML_ENVIRONMENT.md) remains the build procedure for
the Python venv itself, and nothing here moves it.

`library/tools/shared_environment.py` is the one module that answers
where either of them is.

## Why this is written down at all - the Node half

Measured 2026-09-14. `library/tools/paths.py` had

```python
REMOTION_DIR = PILOT_ROOT / "remotion-subtitles"
```

with no environment override - the only external path in that module
without one. `PIPELINE_PROJECTS_ROOT`, `PIPELINE_SFX_LIBRARY`,
`PIPELINE_MUSIC_LIBRARY`, `PIPELINE_REMOTION_COMPOSITIONS`,
`RESOLVE_SCRIPT_API` and the rest all read `os.environ.get(...)` with a
default. `remotion_batch.py` derived the same location a second time,
from `__file__`. So the renderer's dependencies could only ever be a
directory inside whichever checkout was running, and both consequences
followed from that:

- Exactly **one** working copy on the build machine carried
  `remotion-subtitles/node_modules`, at **585 MB**. No treehouse lane had
  it. **61 tests skipped in every lane**, so the local gate could never
  return an unqualified pass - and AGENTS.md 9 is explicit that a build
  which declines to measure something must say what.
- The remedy on offer - `cd remotion-subtitles && npm install` - fixes
  one lane by paying 585 MB again, per lane, forever. Six lanes is
  3.4 GB of the same bytes.

The captain ruled on it directly, 2026-09-14:

> *"we want this system to be agnostic and freestanding so as far as
> these dependencies, they should be installed in a singular location (so
> we don't have duplicate workspaces for the same thing) and set it up so
> that it is reliably accessible regardless of the system the video
> editing pipeline is being installed on"*

## The design, and what was rejected

The dependency tree lives ONCE per machine, in a store outside every
checkout, keyed by the lockfile that produced it. A checkout BINDS to it
with a symlink at `remotion-subtitles/node_modules`.

```
~/.local/share/vep/node/                       the store  (PIPELINE_NODE_STORE)
└── remotion-subtitles-537f2e073700/           one entry per package-lock.json
    ├── package.json                           what it was built from
    ├── package-lock.json
    └── node_modules/                          285 MB, installed once

<any checkout>/remotion-subtitles/
├── src/  public/  package.json  ...           TRACKED CODE - stays here
└── node_modules -> ~/.local/share/vep/node/remotion-subtitles-537f2e073700/node_modules
```

### The store never holds the application code

The open question was whether a shared store can serve tracked
application code that differs between checkouts without a staleness bug.
It can, because **it never serves the application code at all.**
`src/`, `package.json` and `public/fonts/` are tracked, they differ
between branches, and they stay where git put them. Only `node_modules/`
moves, and `node_modules/` is not checkout-specific in any degree -
measured on the 585 MB tree, 2026-09-14:

| measured | on the real tree |
|---|---|
| references to the checkout's own path inside `node_modules` | **0** |
| `.bin` entries that are absolute symlinks | **0** |

It is a pure function of `package-lock.json` plus the platform. That is
what makes a store safe here and would not make one safe for `src/`.

### Staleness is unreachable, not invalidated

The entry is named `<package.json name>-<first 12 hex of sha256(package-lock.json)>`.
Two checkouts whose locks agree share one tree by construction; two whose
locks differ can never see each other's. **There is no expiry step to
forget**, because a checkout whose dependency set changed asks for a
different directory.

### Why a symlink, and not a cleverer binding

The thing that resolves modules is Node, not this repository. Node,
Remotion's bundler and its headless browser all look beside `cwd`, and 58
tests in this suite independently ask
`os.path.isdir(REMOTION_DIR/"node_modules")`. A symlink is true for every
one of them, so the bytes moved out of the checkout with **no edit to any
test file and no edit to the renderer**. `node_modules/` is already in
`.gitignore`, so binding never dirties a checkout.

| rejected | the failure it carried |
|---|---|
| `npm install` per checkout (today) | 585 MB per lane; five of six lanes had none, and 61 tests skipped in all of them |
| A store of the whole `remotion-subtitles/` directory | serves TRACKED code that differs per branch - the staleness bug this design exists to avoid, and the render-cache fingerprints `src/` precisely because it changes |
| `NODE_PATH` / a resolver hook | deprecated, ignored by esbuild/rspack bundling, and invisible to the 58 tests' `isdir` probe; the renderer would still have to be taught about it |
| npm workspaces or pnpm's global store | changes what the renderer IS and how it is installed - out of scope, and it would still need a per-machine answer for where the store goes |
| Committing `node_modules` | 285 MB of platform-specific binaries in git; wrong on every axis |
| Containerising it | the wider standalone question (`vep-standalone-packaging`); a container is already known to be the wrong shape for the Resolve and Apple-Silicon halves |

### Nothing is baked to one home directory

`store_root()` reads `PIPELINE_NODE_STORE`, else `$XDG_DATA_HOME/vep/node`,
else `~/.local/share/vep/node` - derived from the running user, on any
machine. `tests/test_shared_environment.py` fails if an absolute home
directory appears in the module's code.

## The Python half: which interpreter, and where

Found the same day, by the captain, from the other end: a caller
resolved its interpreter as `<REPO_ROOT>/.venv/bin/python3`, falling
back to `/usr/bin/python3`. **No checkout on the build machine has a
`.venv`** - the working environment is the durable one at
`~/.local/share/vep/venv-py312`, which `ML_ENVIRONMENT.md` put outside
every checkout deliberately. So the caller always took the fallback,
and `/usr/bin/python3` carries none of whisperx, mlx_vlm or torch. The
call died inside a step's import, reporting a package nobody had
mentioned.

Two places carried the same assumption:

| | was | now |
|---|---|---|
| `scripts/full_suite_gate.sh` | `FULL_SUITE_GATE_PYTHON`, else ambient `python3` | the variable still wins; the default now asks the ladder |
| `manage_project.py`'s advice | "make a venv in this checkout" | build the per-machine one; the checkout's is named as the alternative |

### The ladder is QUERIED, because two callers cannot import it

`bin/vep` is bash and `scripts/full_suite_gate.sh` is bash. Neither can
import a Python module, so both ASK the ladder instead of carrying it:
they shell out to any `python3` to run
`python -m library.tools.shared_environment --resolve-interpreter`, and
the gate does the same through `python_interpreter()` directly. A rung
literal mirrored into a second language was tried first (PR 1141, a test
diffing the two) and removed: a diffed duplicate is still a duplicate,
stale the moment only one side is reinstalled. A caller that queries
cannot drift, because there is nothing on its side to drift.

Stamp-vs-query was decided for query, against the environment moving
after install: stamping the answered path at install time goes stale the
moment the durable venv is built later, a checkout `.venv` appears or
vanishes, or `PIPELINE_PYTHON` is set for one series - and a stamp is
never re-asked, so it answers wrongly for weeks. The query costs tens
of milliseconds of stdlib-only startup per call and is current on every
call by construction.

The bootstrap is neither a second ladder nor the old fallback: answering
evaluates only stdlib path predicates whose answer is identical whichever
interpreter asks (the module stays parseable by a stock 3.9), and the
bootstrap never executes pipeline code - the caller launches only the
answered path, and every other outcome refuses loudly where the message
is read.

```
INTERPRETER_CANDIDATES = (
    ("env",      "PIPELINE_PYTHON"),          # an explicit choice wins outright
    ("vep_home", "venv-py312/bin/python3"),   # the durable one, built once
    ("repo",     ".venv/bin/python3"),        # a checkout that has its own
)
```

**There is no stock-interpreter rung, and there must not be one.** A
stock Python does not fail at launch where the message would be read; it
fails forty seconds in, inside a step, with a traceback about a package
nobody mentioned. Refusing is the faster answer and the only honest one.

The durable rung outranks the checkout's because it is the one
`ML_ENVIRONMENT.md` builds and verifies; a checkout `.venv` may be a
half-built experiment. A refusal names **every rung it tried, by path**,
which is what the old message could not do.

### This does not re-home the ML venv

`~/.local/share/vep/venv-py312` is exactly where `ML_ENVIRONMENT.md`
already put it, for the reason that page gives. Nothing about building it
changes. What changes is that four callers now *find* it instead of
looking somewhere it has never been.

## The build half: what a reel build needs in its interpreter

Four instances, all on 2026-09-10/11, each costing a lane a failed build:
a cv2 without Haar cascades answering None from the face probe, a system
cv2 5.0 refusing the punch-in aim, and a purpose-built cv2 4.12 venv
missing `jsonschema` on the very next attempt. Each lane fixed it locally
with its own venv, each missing something different - nothing declared
the set, so every attempt rediscovered a different subset.

The declaration lives in the same module, because the value of the halves
above is one owner. `REEL_BUILD_LIBRARIES` (`jsonschema`, `yaml`) is the
library set a purpose-built venv is completed from; the Haar cascade is
the detector half, with the verdict owned by the loader the build really
calls (`subject_framing.load_face_cascade`) and the pin
(`opencv-python>=4.8,<5`) owned here. `require_reel_build_environment()`
is the one clear message before the build starts - every gap, each with
what supplies it. `env.face_detector` and `env.reel_build_libraries` in
`library/tools/requirements.py` carry the same two halves as the
`build_reels` pre-build refusal, so `build-reels` refuses before deriving
anything rather than three steps in. The verifier is deliberately not a
consumer: it grades placed timelines and aims nothing.

## Build it

```sh
scripts/install_node_deps.sh
```

That is the whole procedure, on any machine, in any checkout. It keys the
lockfile, `npm ci`s it into the store if no entry exists, binds this
checkout, and re-asks the resolution whether the dependencies are now
reachable before printing `NODE DEPS: PASS`. It is FAIL-CLOSED and does
not trust its own exit codes alone.

```sh
scripts/install_node_deps.sh --check        # report; installs nothing
scripts/install_node_deps.sh <remotion-dir> # bind a different checkout
```

The Python half has no install script here, because it already has one -
[`ML_ENVIRONMENT.md`](ML_ENVIRONMENT.md), unchanged. Ask where it landed:

```sh
python3 -c "from library.tools.shared_environment import python_interpreter; \
            print(python_interpreter('.'))"
```

Measured on the build machine, 2026-09-14: a fresh store entry from an
empty store root took **4.6s** and **286 MB** (warm npm cache; a cold
machine pays the download once). Binding a second checkout to an existing
entry is instant and costs nothing.

**A checkout that already carries a real `node_modules` directory is left
alone.** The script says so and stops rather than deleting somebody's
585 MB. To move it into the store, remove it yourself and re-run.

The only prerequisite is Node and npm on PATH (AGENTS.md 9). Nothing
here pins a Node version: `package-lock.json` is the only source of
versions, exactly as `requirements.txt` is on the Python side.

## Verify it, before trusting it

```sh
# 1. the resolution agrees with the disk
scripts/install_node_deps.sh --check            # NODE DEPS: PRESENT

# 2. the bind points OUT of the checkout
readlink remotion-subtitles/node_modules

# 3. the resolution itself, and the loud failure
python3 -m pytest tests/test_shared_environment.py -q
```

Then the real proof, which is the only one that distinguishes a *usable*
tree from a present one - these are the tests that skipped:

```sh
python3 -m pytest tests/test_staged_scene.py tests/test_fullframe_word_cues.py -q -rs
```

Measured on a bound checkout, 2026-09-14: **40 passed in 39.14s**, where
the same selection had reported `30 passed, 10 skipped`. Those are real
Remotion renders - bundle, headless browser, frames on disk - through a
`node_modules` that is not in the checkout.

## Absence is LOUD

`library/tools/shared_environment.py` is the one module that answers where
the renderer and its dependencies are. Nothing else may recompute it -
`paths.py`, `remotion_batch.py` and `requirements.py` all read it, and
`tests/test_shared_environment.py` fails if one of them spells the
directory itself again.

Absence does not skip and does not reach `node`, where it used to come
back as `ERR_MODULE_NOT_FOUND` for whichever package `render-batch.mjs`
imported first - a name no step chose and no remedy follows from.
`remotion_batch` refuses before launching anything, and the message names
the store entry, whether the tree is uninstalled or merely unbound, and
the one command:

```
The Remotion dependencies are not reachable at <checkout>/remotion-subtitles/node_modules.
No store entry exists for this checkout's package-lock.json. It belongs at
  /Users/<you>/.local/share/vep/node/remotion-subtitles-537f2e073700.
Install once per machine and bind this checkout:
    scripts/install_node_deps.sh <checkout>/remotion-subtitles
The store is shared by every checkout whose lockfile matches, so this is
paid once per dependency set, not once per checkout.
```

`env.remotion_installed` in `library/tools/requirements.py` carries the
same remedy, so a run refuses before a step starts rather than in the
middle of one.

## The environment variables

| variable | what it names | default |
|---|---|---|
| `PIPELINE_VEP_HOME` | the per-machine root both halves sit under | `$XDG_DATA_HOME/vep`, else `~/.local/share/vep` |
| `PIPELINE_NODE_STORE` | the Node dependency store | `<vep home>/node` |
| `PIPELINE_REMOTION_DIR` | where the renderer's SOURCE is | `<checkout>/remotion-subtitles` |
| `PIPELINE_NODE_MODULES` | the resolved tree, outright | `<remotion dir>/node_modules` |
| `PIPELINE_PYTHON` | the interpreter, outright | the ladder above |

`PIPELINE_NODE_MODULES` is the escape hatch for a machine whose layout
this design did not anticipate. It reports where Node will look; it does
not make Node look there, so it belongs with a checkout that is already
laid out that way.

## A harmless error you will see

`npm ci` prints a red `npm error 404 ... 'zod-check@*' is not in this
registry` block. That is `remotion-subtitles/package.json`'s own
`postinstall` script, which is written `npx zod-check || true` and
therefore succeeds. The install completes and reports `added 313
packages`. Do not "fix" it here - what the renderer does is its own
question.

## See also

- `library/tools/shared_environment.py` - the resolution, and the reasoning.
- `scripts/install_node_deps.sh` - the one way to fill the Node store.
- [`ML_ENVIRONMENT.md`](ML_ENVIRONMENT.md) - how the Python venv is built
  and verified. Unchanged by this; two callers now find what it builds.
- [`CI_LAYERS.md`](CI_LAYERS.md) - why layer 1 is local, and what those
  61 skips cost it.
