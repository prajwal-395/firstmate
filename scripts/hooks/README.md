# Scripts a hook may run

This directory **is** the allow-list. `library/tools/hooks.py` reads it off disk on
every load, so a `run` action may only name a `.py` file that is already here, and a
declaration naming anything else is refused **before the run starts** - by name, with
this directory's contents listed.

A hook declaration **names** a script. It never supplies one, never carries a path, an
argument or a shell string, and a project's own `hooks.json` cannot add to this
directory. That is the point: a declaration is data, and data may not introduce code.

## The contract

- The script is executed as `sys.executable <script>`. Never a shell, so there is
  nothing for a shell to interpret even if a payload value contained a metacharacter.
  Only `.py` is allowed - a shebang plus an exec bit would put the choice of
  interpreter back in the file.
- **One JSON object arrives on stdin:**

  ```json
  {
    "condition": "qa_finding_raised",
    "hook": "the hook's declared name",
    "project_dir": "/absolute/path/to/the/project",
    "payload": { "...": "whatever the firing site observed" }
  }
  ```

- Exit `0` for success. A non-zero exit is **reported** in the run summary with the
  last line of stderr; it does not fail the run, because a hook is automation on top
  of the work and a run whose real work succeeded must not be failed by it.
- A script gets `SCRIPT_TIMEOUT_S` (60s) before it is killed and reported.
- `PIPELINE_HOOK_DEPTH=1` is set in the environment. Anything the script launches
  inherits it, and a pipeline run at non-zero depth fires no hooks at all - which is
  what stops a hook that starts a run that fires the hook. Do not unset it.

## Writing a real one

Start by pointing a hook at `record_payload.py` and reading what actually arrives.
The payload's shape belongs to the firing site, and guessing it is how a hook ends
up silently matching nothing.
