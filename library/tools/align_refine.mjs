#!/usr/bin/env node
// Driver for Desert Ant Align's Node SDK (@desert-ant-labs/align/native).
//
// Why a subprocess: the refiner is Node-only (two-graph cascade, one model
// per WebAssembly module), while the pipeline is Python. This script is the
// whole seam - `library/tools/desert_ant_align.py` never speaks the SDK's
// protocol inline, and the SDK is never a pipeline dependency.
//
// Usage:
//   node align_refine.mjs languages --package-dir <dir>
//   node align_refine.mjs refine --package-dir <dir> --pcm <raw-f32-mono>
//       --rate <hz> --words <words.json> --out <refined.json>
//       --language <code> [--model-dir <dir>]
//
// PCM is raw little-endian float32 mono; the rate travels alongside so no
// WAV parsing is needed here. Words JSON is an array of objects with at
// least {text, start, end}; every extra key round-trips untouched by the
// SDK and is passed through here verbatim. Language support is NEVER
// assumed: `languages` reports the SDK's own list, and `refine` reports
// whether the requested language was supported alongside the words, so a
// caller can tell a passthrough from a refinement.
import { readFileSync, writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith("--")) {
      out[argv[i].slice(2)] = argv[i + 1] ?? "";
      i++;
    }
  }
  return out;
}

async function loadAlign(packageDir, modelDir) {
  const entry = await import(pathToFileURL(join(packageDir, "node.js")).href);
  const options = modelDir ? { directory: modelDir } : undefined;
  return await entry.Align.load(options);
}

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  const opts = args(rest);
  if (command === "languages") {
    const { Align } = await import(
      pathToFileURL(join(opts["package-dir"], "node.js")).href
    );
    process.stdout.write(
      JSON.stringify({ languages: Align.languages, sdkVersion: Align.sdkVersion })
    );
    return;
  }
  if (command !== "refine") {
    throw new Error(`unknown command ${command}`);
  }
  const { Align } = await import(
    pathToFileURL(join(opts["package-dir"], "node.js")).href
  );
  const language = opts["language"] ?? "";
  const supported = Align.isSupported(language);
  const align = await loadAlign(
    opts["package-dir"], opts["model-dir"] || undefined
  );
  const raw = readFileSync(opts["pcm"]);
  const samples = new Float32Array(
    raw.buffer, raw.byteOffset, Math.floor(raw.length / 4)
  );
  const words = JSON.parse(readFileSync(opts["words"], "utf-8"));
  const refined = await align.refine(samples, Number(opts["rate"]), words, {
    language,
  });
  const result = {
    language,
    supported,
    sdkVersion: align.sdkVersion,
    downloaded: align.isDownloaded(),
    words: refined,
  };
  writeFileSync(opts["out"], JSON.stringify(result));
  align.dispose();
}

main().catch((error) => {
  process.stderr.write(`align_refine: ${error?.message ?? error}\n`);
  process.exit(1);
});
