# Installing and removing Ren

## Install

On an Apple Silicon Mac, run the installer from Terminal:

    curl -fsSL https://raw.githubusercontent.com/prajwal-395/video_editing_pilot/main/scripts/install_ren.sh | bash

The installer resolves the newest released `v*` tag, downloads the engine
artifact that `.github/workflows/release.yml` builds, verifies it against the
release's `SHA256SUMS.txt`, and installs the immutable engine under the
current Ren home. It then runs ren setup. Setup installs the Homebrew system
tools, Ren-managed Python 3.12 runtime, the hash-locked Python environment,
and the lockfile-keyed Node dependencies. It creates the configured projects,
SFX, and music folders, then runs ren doctor.

The terminal install route is unsigned: it needs no Apple Developer ID and
no notarization. The signed and notarized macOS app bundle
(`ren-engine-v*-macos.zip`) is a separate release-engineering milestone.

To install a specific release, or to fetch the artifact from a URL you
control (a mirror, or a local file for testing):

    curl -fsSL .../install_ren.sh | bash -- --tag v0.1.0
    curl -fsSL .../install_ren.sh | bash -- --url file:///path/to/ren-engine-v0.1.0.zip

`--url` reads the checksum from `<dir>/SHA256SUMS.txt` beside the artifact.

### Updating

Move an installed Ren to the newest release:

    ren update

`ren update` fetches the release artifact, verifies its checksum, and
activates it through the same verify-then-switch path as
`ren upgrade <engine-source>`: the candidate must pass ren doctor and the
project-format check before the active version moves. Undo it with
`ren rollback`. To update from a specific URL or tag:

    ren update --url file:///path/to/ren-engine-v0.1.0.zip
    ren update --tag v0.1.0

### Homebrew

A Homebrew tap formula lives in `homebrew-tap/Formula/ren.rb`. Once the tap
is published:

    brew install ren
    ren setup

The formula fetches the same release artifact and installs it into the Ren
home, so `ren update` and `ren rollback` work the same as the curl route.

When the engine is already installed, run ren setup to repair its runtime
or add a verified optional pack. Ren supports these opt-in packs:

| Command | Inventory entry | What it adds |
|---|---|---|
| ren setup --with panns | PANNs checkpoint | timed sound-event weights |
| ren setup --with mfa | micromamba and MFA models | forced-alignment runtime and pinned models |
| ren setup --with ecapa | ECAPA encoder | pinned single-track speaker weights |
| ren setup --with deepfilter | DeepFilterNet binary | pinned dialogue-cleanup runtime and weights |

Each pack calls its existing install script. The script asks
library/tools/shared_environment.py for the inventory's version and
integrity values, verifies the fetched content, and runs its install check.
Model packs are not installed by default. buffalo_l is intentionally absent:
the inventory identifies it as floating and non-commercial, so it does not
meet the pinned pack contract.

ren doctor returns success when Ren's universal Python baseline works.
It can still report MISS for Resolve, an agent host, or optional capability
packs. Install Resolve Studio separately to operate on timelines.

## What Ren owns

PIPELINE_VEP_HOME selects the Ren application home. If unset, Ren uses
$XDG_DATA_HOME/vep, or ~/.local/share/vep when XDG_DATA_HOME is unset.

| Location | Contents | ren uninstall |
|---|---|---|
| <Ren home>/versions/ and current | immutable engine builds and active pointer | removes |
| <Ren home>/python/ and venv-py312/ | Ren-managed CPython and locked Python packages | removes |
| <Ren home>/node/, micromamba/, bin/ | shared Node packages and installed command-line capability tools | removes Ren-owned files |
| <Ren home>/cache/ | uv and npm download/build caches | removes |
| <Ren home>/models/ | downloaded model weights and model caches, including Hugging Face, Torch, DeepFilterNet and MFA data | keeps by default; removes with --models |
| <Ren home>/install.json | installer paths needed for clean removal | removes |
| User shell profile | a marked PATH block for Ren and Homebrew commands | removes only Ren's marked block |
| Homebrew prefix | shared uv, Node.js, and ffmpeg packages | keeps; they may serve other software |
| ~/.config/ren/ | Ren machine settings | keeps |
| ~/Movies/Ren/assets/ | shared SFX, music and composition assets, unless configured elsewhere | keeps |
| ~/Movies/Ren/projects/ | customer projects, source footage and exports, unless configured elsewhere | always keeps |

The global PATH entry points to <Ren home>/bin/ren; it sets the same
PIPELINE_VEP_HOME, Python interpreter and model directories for every
command. The model and asset locations stay outside engine versions.

## Remove

Preview the exact paths first:

    ren uninstall --dry-run

Then remove the Ren runtime and cache:

    ren uninstall

The command asks before removing anything. Model downloads remain, so a
reinstall can reuse them. To remove those too, use ren uninstall --models.
For a non-interactive removal, add --yes after reviewing the dry run.

Ren does not remove Homebrew, its packages, user configuration, shared assets,
projects, source footage, or exports. It does not recursively remove the Ren
home: only the listed application-owned paths are considered, so unknown files
there remain in place.

## Developer and managed-machine options

From a checkout, use:

    scripts/install_ren.sh --source . --home "$HOME/.local/share/vep"

--home selects a disposable or alternate Ren home. --skip-system-deps
requires Homebrew, uv, Node.js/npm, ffmpeg and ffprobe to already be available.
The installer uses a GitHub source archive for normal installs and does not
run git clone, editable pip installation, or write a checkout path into the
engine tree.

For environment reconstruction details, see docs/ML_ENVIRONMENT.md and
docs/SHARED_ENVIRONMENT.md.
