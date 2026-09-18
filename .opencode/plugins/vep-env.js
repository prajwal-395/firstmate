// vep-env: the environment HOOK. Nothing has to remember to do this.
//
// Every lane rediscovers the same three variables and then re-types a
// ~200-character preamble before each command (239 preambles across six
// lanes, measured 2026-09-18 from 2,016 recorded lane tool calls in
// ~/.local/share/opencode/opencode.db). This plugin injects all three
// into EVERY shell opencode spawns, via the `shell.env` hook, so a lane
// that forgets still runs under the right environment:
//
//   RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB - exactly the values lanes
//       set (and `library/tools/paths.py`'s own defaults).
//   PIPELINE_PYTHON - rung 1 of `shared_environment`'s interpreter
//       ladder, resolved by QUERYING that module, never mirrored here.
//       A ladder duplicated in a second language drifts the first time
//       only one side changes (docs/SHARED_ENVIRONMENT.md); a caller
//       that queries cannot drift.
//
// This is the hook, not the wrapper. `bin/vep` is the script a lane calls
// DELIBERATELY to run under the resolved interpreter - the hook cannot
// choose which binary a command invokes, and a bare `python3` on this
// machine is 3.14 with none of the ML stack. Do not collapse the two:
// extending the wrapper never replaces this injection, and vice versa.
//
// Staleness: the answer is cached while the cached interpreter still
// exists and is executable, and re-queried the moment it does not - a
// venv built mid-session is picked up by the next shell, and a deleted
// one stops being served. The query vehicle may be ANY python3;
// answering evaluates only stdlib path predicates.
//
// Failure is open, never a fallback: when nothing resolves, PIPELINE_PYTHON
// is left UNSET so the consumer refuses with the ladder's own message
// rather than running under a stock interpreter and dying forty seconds
// inside a step. The Resolve variables are constants and are always set.
import { execFileSync } from "node:child_process";
import { accessSync, constants } from "node:fs";

// Exactly what lanes export, character for character - and what
// `library/tools/paths.py` defaults to. Constants, per machine.
const RESOLVE_SCRIPT_API =
  "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting";
const RESOLVE_SCRIPT_LIB =
  "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so";

const isExecutable = (path) => {
  if (!path) return false;
  try {
    accessSync(path, constants.X_OK);
    return true;
  } catch {
    return false;
  }
};

// Ask the ladder. Returns the interpreter path, or "" when the ladder
// finds nothing. Never throws: an unanswered query is served as "unset",
// and the refusal happens where the message will be read.
const queryLadder = (repoRoot) => {
  try {
    const out = execFileSync(
      "python3",
      [
        "-m",
        "library.tools.shared_environment",
        "--resolve-interpreter",
        "--repo-root",
        repoRoot,
      ],
      // Answered from the checkout itself, so the module resolves
      // whatever the server's own working directory is.
      { encoding: "utf-8", timeout: 60000, cwd: repoRoot }
    );
    const report = JSON.parse(out);
    return typeof report.python === "string" ? report.python : "";
  } catch {
    return "";
  }
};

export const VepEnv = async ({ directory }) => {
  // Rung 1 is an explicit choice and wins outright - when the server
  // environment already names something executable, that is the answer
  // and there is nothing to ask.
  let cached = isExecutable(process.env.PIPELINE_PYTHON)
    ? process.env.PIPELINE_PYTHON
    : "";

  return {
    "shell.env": async (input, output) => {
      const repoRoot = (input && input.cwd) || directory || process.cwd();
      if (!isExecutable(cached)) {
        cached = queryLadder(repoRoot);
      }
      output.env.RESOLVE_SCRIPT_API = RESOLVE_SCRIPT_API;
      output.env.RESOLVE_SCRIPT_LIB = RESOLVE_SCRIPT_LIB;
      // Unset when unanswered: a stock interpreter is not a fallback, it
      // dies inside a step with a traceback about a package nobody
      // mentioned (the trap a previous investigation fell into).
      if (isExecutable(cached)) {
        output.env.PIPELINE_PYTHON = cached;
      }
    },
  };
};
