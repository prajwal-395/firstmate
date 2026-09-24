// ren-marker-hook for opencode. Disk-only session plugin - never probes Resolve.
// Install with: ren setup-hooks --app opencode --write
// That copies this file to ~/.config/opencode/plugins/ren-marker-hook.js.
module.exports = async function renMarkerHook(ctx) {
  const { execFile } = require("child_process");
  const run = (args) =>
    new Promise((resolve) => {
      execFile("ren-marker-hook", args, { timeout: 15000 }, (err, stdout) => {
        resolve(stdout || (err && err.message) || "");
      });
    });
  if (ctx && ctx.onSessionStart) {
    ctx.onSessionStart(async () => {
      const out = await run(["check"]);
      if (out && !/no pending timeline notes/.test(out)) return out;
      return "";
    });
  }
  return {};
};
