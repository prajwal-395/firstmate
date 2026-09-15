// Which interpreter runs the bridge: ASKED, never derived.
//
// The ladder lives in exactly one place -
// `library/tools/shared_environment.py` (`INTERPRETER_CANDIDATES`,
// `interpreter_candidates()`, `python_interpreter()`) - and this module
// is how the plugin obeys it without importing it: it shells out to ANY
// `python3` on PATH to run
//
//   python3 -m library.tools.shared_environment --resolve-interpreter \
//       --repo-root <REPO_ROOT>
//
// and launches the bridge with the answered path.
//
// Why the bootstrap is neither a second ladder nor the old fallback:
// answering the query evaluates only stdlib path predicates (`is_file`,
// the executable bit) whose answer is identical whichever interpreter
// asks - the suite pins the module parseable by a stock 3.9, which could
// never run the pipeline and can still ask correctly. The bootstrap
// NEVER executes pipeline code: the bridge is launched only with the
// ladder's answered path, and anything else - no bootstrap on PATH, a
// failed query, an empty answer, an answer that moved - refuses loudly
// HERE, where the message is read, instead of dying inside a step forty
// seconds later with a traceback about a package nobody mentioned.
//
// Two things this module holds that are NOT ladder logic:
//   - the PIPELINE_PYTHON fast path. Rung 1 IS the explicit override, so
//     an override naming an existing executable answers without asking.
//     It applies the same predicate the ladder applies (exists,
//     executable), so it cannot disagree with the query - it only lets a
//     machine with no bootstrap at all still resolve. A set-but-absent
//     override falls through to the query, whose answer governs.
//   - no cache. The query runs on every bridge call - tens of
//     milliseconds of stdlib-only startup - so an environment that moves
//     after install is picked up on the next call with no reinstall and
//     no invalidation step to forget. That is the whole of the
//     stamp-vs-query argument, decided in favour of query.

const { execFile } = require('child_process');
const fs = require('fs');

const QUERY_MODULE = 'library.tools.shared_environment';
const QUERY_FLAG = '--resolve-interpreter';
const QUERY_TIMEOUT_MS = 30000;

function executableExists(p) {
    if (!p) return false;
    try {
        fs.accessSync(p, fs.constants.X_OK);
    } catch (err) {
        return false;
    }
    try {
        return fs.statSync(p).isFile();
    } catch (err) {
        return false;
    }
}

function noBootstrapMessage() {
    return 'VEP: no `python3` on PATH to ASK the interpreter ladder, so the '
        + 'bridge was not launched. The plugin never runs the pipeline on a '
        + 'stock interpreter. Either set PIPELINE_PYTHON to an interpreter '
        + 'carrying the ML stack (the durable one built by '
        + 'docs/ML_ENVIRONMENT.md), or put a python3 on PATH so the ladder '
        + 'can be asked.';
}

function queryFailureMessage(err, stderr) {
    const detail = String((err && err.message) || err);
    const tail = String(stderr || '').slice(0, 2000);
    return 'VEP: the interpreter query failed before the ladder could '
        + 'answer, so the bridge was not launched: ' + detail
        + (tail ? '\nquery stderr:\n' + tail : '')
        + '\nSet PIPELINE_PYTHON to an interpreter carrying the ML stack '
        + '(the durable one built by docs/ML_ENVIRONMENT.md) to skip the '
        + 'query outright.';
}

function checkoutGoneMessage(repoRoot) {
    return 'VEP: the stamped checkout is not there anymore: ' + repoRoot
        + ' - the checkout moved or was deleted after the plugin was '
        + 'installed. Re-run scripts/install_workflow_integration.sh from '
        + 'the checkout so the plugin points at it again; the bridge was '
        + 'not launched.';
}

function checkoutPresent(repoRoot) {
    try {
        return !!repoRoot && fs.statSync(repoRoot).isDirectory();
    } catch (err) {
        return false;
    }
}

function queryLadder(repoRoot) {
    return new Promise((resolve) => {
        const child = execFile(
            'python3',
            [ '-m', QUERY_MODULE, QUERY_FLAG, '--repo-root', repoRoot ],
            { cwd: repoRoot, timeout: QUERY_TIMEOUT_MS,
              maxBuffer: 1024 * 1024, encoding: 'utf-8' },
            (err, stdout, stderr) => {
                if (err) {
                    // ENOENT included: no bootstrap is a refusal, never a
                    // reason to reach for a stock interpreter instead.
                    const message = (err.code === 'ENOENT')
                        ? noBootstrapMessage()
                        : queryFailureMessage(err, stderr);
                    resolve({ ok: false, error: message });
                    return;
                }
                let report = null;
                try {
                    report = JSON.parse(stdout);
                } catch (parseErr) {
                    resolve({ ok: false,
                              error: queryFailureMessage(parseErr, stderr) });
                    return;
                }
                if (!report || !report.python) {
                    // The ladder found nothing. Its own wording names every
                    // rung it tried and the procedure that fixes it, so it
                    // passes through VERBATIM - a second wording here would
                    // be a second ladder's worth of drift.
                    const error = (report && report.error)
                        ? String(report.error)
                        : 'VEP: the interpreter ladder answered with no '
                        + 'interpreter and no explanation.';
                    resolve({ ok: false, error });
                    return;
                }
                if (!executableExists(report.python)) {
                    resolve({ ok: false,
                              error: 'VEP: the ladder answered '
                              + report.python + ' but it is no longer '
                              + 'executable - the environment moved between '
                              + 'the answer and the launch. Retry the '
                              + 'request; the next call asks the ladder '
                              + 'again and picks up the move.' });
                    return;
                }
                resolve({ ok: true, python: report.python });
            });
        void child;
    });
}

async function resolvePython(repoRoot) {
    // Checked first: a missing command and a missing cwd fail `execFile`
    // with the same ENOENT, so the checkout's presence is established
    // here and the no-bootstrap message below can only mean the
    // bootstrap.
    if (!checkoutPresent(repoRoot)) {
        return { ok: false, error: checkoutGoneMessage(repoRoot) };
    }
    const override = process.env.PIPELINE_PYTHON;
    if (override && executableExists(override)) {
        return { ok: true, python: override };
    }
    return queryLadder(repoRoot);
}

module.exports = { resolvePython };
