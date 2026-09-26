/**
 * Render many overlay cards against ONE bundle.
 *
 * The pipeline used to shell out to `npx remotion render` once per card.
 * Each invocation re-bundled the project and launched its own
 * chrome-headless-shell, so a 19-reel caption pass was 763 bundle-and-
 * launch cycles - and that startup, not the frame rendering, is what
 * took the captain's machine from load 4.3 to 17.4 in twenty seconds.
 * Rationing the pool did not help because the cost is per invocation.
 *
 * So: bundle once, open one browser, render every card through it.
 *
 * Reads a job file (argv[2]):
 *   { "composition": "SubtitleOverlay",
 *     "concurrency": 4,
 *     "cardConcurrency": 2,
 *     "jobs": [ { "props": {...}, "out": "/abs/path.mov" } ] }
 *
 * Writes one line of JSON per finished job to stdout, so the caller can
 * report progress and attribute a failure to the card that caused it
 * rather than to the batch.
 *
 * TWO MODES, ONE BUNDLE PATH.
 *
 *   render-batch.mjs <job-file.json>            batch: render the listed
 *                                               jobs, then exit.
 *   render-batch.mjs <job-file.json> --serve    server: bundle, print
 *                                               {"ready":true}, then read
 *                                               one job per line from
 *                                               stdin and answer one line
 *                                               each, until stdin closes.
 *
 * The second exists because the pipeline step renders card-by-card - its
 * seam is `renderer.render(props_path, out_path) -> (ok, error)`, decided
 * so the step keeps deciding WHICH cards render - and a per-card seam
 * cannot amortise a bundle unless something stays alive between calls.
 * Both modes bundle through the same lines below; only the source of the
 * jobs differs, so there is no second renderer to drift.
 *
 * Closing stdin is the shutdown signal, and it is the ONLY one needed:
 * when the parent dies for any reason its pipe closes, so the browser
 * this holds cannot outlive the run that made it.
 */
import { bundle } from "@remotion/bundler";
import {
  ensureBrowser,
  openBrowser,
  renderMedia,
  selectComposition,
} from "@remotion/renderer";
import { mapWithConcurrency, withBatchRenderer } from "./render-batch-core.mjs";
import { readFileSync } from "node:fs";
import readline from "node:readline";
import path from "node:path";

const jobFile = process.argv[2];
const serve = process.argv.includes("--serve");
if (!jobFile) {
  console.error("usage: render-batch.mjs <job-file.json>");
  process.exit(2);
}

const spec = JSON.parse(readFileSync(jobFile, "utf-8"));
const compositionId = spec.composition;
const jobs = spec.jobs ?? [];
const concurrency = spec.concurrency ?? null;
const cardConcurrency = Math.max(1, Math.floor(spec.cardConcurrency ?? 1));
// The ENCODER, not the browser, is what costs. Measured on the captain's
// 10-core machine mid-pass: remotion's bundled ffmpeg at 763% CPU - about
// 7.6 cores - encoding ProRes 4444, while `concurrency` (which bounds
// browser tabs) was already at its floor of 1. ffmpeg defaults to every
// core it can see, and `ffmpegOverride` is the supported hook for saying
// otherwise. The caller derives the number; this only injects it.
const encoderThreads = spec.encoderThreads ?? null;
const boundEncoder = ({ args }) =>
  encoderThreads ? ["-threads", String(encoderThreads), ...args] : args;

if (!compositionId) {
  console.error("job file names no composition");
  process.exit(2);
}

await ensureBrowser();

const serveUrl = await bundle({
  entryPoint: path.resolve(process.cwd(), "src/index.ts"),
  onProgress: () => {},
});

// ONE renderer, used by both modes, so the two cannot drift apart.
const withRenderer = (run) =>
  withBatchRenderer(
    {
      openBrowser,
      selectComposition,
      renderMedia,
      serveUrl,
      compositionId,
      concurrency,
      encoderThreads,
      boundEncoder,
    },
    run,
  );

if (serve) {
  // SERVER MODE. Bundle is paid; announce readiness and wait for work.
  //
  // The parent watches for this line before sending anything, so it can
  // distinguish "still bundling" from "wedged" without a guess.
  console.error("bundled once, serving");
  process.stdout.write(JSON.stringify({ ready: true }) + "\n");

  await withRenderer(async (renderOne) => {
    const rl = readline.createInterface({ input: process.stdin });
    const pending = new Set();
    for await (const line of rl) {
      const text = line.trim();
      if (!text) continue;
      let job;
      try {
        job = JSON.parse(text);
      } catch (err) {
        process.stdout.write(
          JSON.stringify({
            ok: false,
            out: null,
            error: `unparseable request: ${String(err && err.message)}`,
          }) + "\n",
        );
        continue;
      }
      while (pending.size >= cardConcurrency) await Promise.race(pending);
      let task;
      task = (async () => {
        try {
          await renderOne(job);
          process.stdout.write(
            JSON.stringify({
              requestId: job.requestId,
              ok: true,
              out: job.out,
            }) + "\n",
          );
        } catch (err) {
          // A card that fails is reported and the server STAYS UP: the
          // bundle is the expensive thing and one bad card must not cost it.
          process.stdout.write(
            JSON.stringify({
              requestId: job.requestId,
              ok: false,
              out: job.out,
              error: String(err && err.message ? err.message : err),
            }) + "\n",
          );
        }
      })().finally(() => pending.delete(task));
      pending.add(task);
    }
    await Promise.all(pending);
  });
  // stdin closed - the parent is done, or the parent is gone. Either way
  // this process must not outlive it.
  console.error("stdin closed, shutting down");
  process.exit(0);
}

console.error(`bundled once for ${jobs.length} card(s)`);

let failed = 0;
await withRenderer(async (renderOne) => {
  const results = await mapWithConcurrency(
    jobs,
    cardConcurrency,
    async (job) => {
      try {
        await renderOne(job);
        return { ok: true, out: job.out };
      } catch (err) {
        return {
          ok: false,
          out: job.out,
          error: String(err && err.message ? err.message : err),
        };
      }
    },
  );
  for (const result of results) {
    if (!result.ok) failed += 1;
    process.stdout.write(JSON.stringify(result) + "\n");
  }
});

console.error(`rendered ${jobs.length - failed}/${jobs.length}`);
process.exit(failed ? 1 : 0);
