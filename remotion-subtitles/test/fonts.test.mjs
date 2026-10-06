import { test } from "node:test";
import assert from "node:assert";
import { registerHooks } from "node:module";

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "remotion") {
      return { url: "mock:remotion", shortCircuit: true };
    }
    return nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    if (url === "mock:remotion") {
      return {
        format: "module",
        source: `
          export const delayRender = (label) => {
            const handle = { label, id: Math.random().toString(36).slice(2) };
            globalThis.__delayRenderHandles = globalThis.__delayRenderHandles || [];
            globalThis.__delayRenderHandles.push(handle);
            return handle;
          };
          export const continueRender = (handle) => {
            globalThis.__continueRenderCalls = globalThis.__continueRenderCalls || [];
            globalThis.__continueRenderCalls.push(handle);
          };
          export const staticFile = (path) => "/static/" + path;
        `,
        shortCircuit: true,
      };
    }
    return nextLoad(url, context);
  },
});

function setupMocks(options) {
  const continueCalls = [];
  globalThis.__continueRenderCalls = continueCalls;

  globalThis.document = {
    fonts: {
      ready: options.fontsReadyHangs
        ? new Promise(() => {})
        : Promise.resolve(),
      add: () => {},
      check: () => options.fontsCheckReturns ?? true,
    },
  };

  globalThis.FontFace = class {
    constructor(family, source, descriptors) {
      this.family = family;
      this.source = source;
      this.descriptors = descriptors;
      this.status = options.faceStatus ?? "loaded";
    }
    async load() {
      return this;
    }
  };

  return { continueCalls };
}

function mockSetTimeoutImmediate() {
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = (callback, _delay) => {
    originalSetTimeout(callback, 0);
    return 0;
  };
  return () => {
    globalThis.setTimeout = originalSetTimeout;
  };
}

test("clears delayRender handle when fonts.ready hangs and throws on unusable face", async () => {
  const { continueCalls } = setupMocks({ fontsReadyHangs: true });
  const restoreSetTimeout = mockSetTimeoutImmediate();

  try {
    const { loadBundledFonts, loadProjectFont } = await import("../src/fonts.ts");

    await loadBundledFonts();
    assert.ok(
      continueCalls.length > 0,
      "continueRender should have been called even when document.fonts.ready hangs",
    );

    setupMocks({ fontsReadyHangs: true, faceStatus: "error" });
    await assert.rejects(
      () => loadProjectFont("TestFont", "test-font.ttf"),
      /not usable/,
    );
  } finally {
    restoreSetTimeout();
  }
});
