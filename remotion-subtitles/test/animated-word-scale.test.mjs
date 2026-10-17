import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";
import { bundle } from "@remotion/bundler";
import { ensureBrowser, getCompositions, renderStill } from "@remotion/renderer";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const remotionDir = path.resolve(here, "..");

const props = {
  subtitles: [
    {
      text: "WIDE NEXT",
      startFrame: 0,
      endFrame: 40,
      words: [
        { word: "WIDE", startFrame: 10, endFrame: 22 },
        { word: "NEXT", startFrame: 22, endFrame: 34 },
      ],
    },
  ],
  fps: 30,
  width: 1080,
  height: 1920,
  durationInFrames: 40,
  style: {
    fontFamily: "Montserrat",
    fontSize: 100,
    fontWeight: 800,
    fontColor: "#FFFFFF",
    accentColor: "#FBF0B8",
    outlineColor: "#000000",
    outlineWidth: 4,
    position: "bottom",
    safeArea: { top: 100, right: 80, bottom: 200, left: 80 },
    captionMaxWidth: 920,
  },
};

function renderedWords(png, expectedWordCount = 2, gapThreshold = 10) {
  // Decode the real Remotion still through ffmpeg, then bound the bright
  // caption ink. This measures what Chromium painted, including transforms.
  const raw = execFileSync("ffmpeg", [
    "-v", "error", "-i", "pipe:0", "-frames:v", "1",
    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
  ], { input: png, maxBuffer: 16 * 1024 * 1024 });
  const width = 540;
  const height = 960;
  assert.equal(raw.length, width * height * 3);

  const columns = Array(width).fill(0);
  const columnTop = Array(width).fill(height);
  const columnBottom = Array(width).fill(-1);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 3;
      const [red, green, blue] = raw.subarray(offset, offset + 3);
      if (red > 155 && green > 145 && blue > 120) {
        columns[x] += 1;
        columnTop[x] = Math.min(columnTop[x], y);
        columnBottom[x] = Math.max(columnBottom[x], y);
      }
    }
  }

  const groups = [];
  let active = null;
  for (let x = 0; x < width; x += 1) {
    if (columns[x] > 0) {
      if (!active || x - active.right > gapThreshold) {
        active = { left: x, right: x, ink: 0, top: height, bottom: -1 };
        groups.push(active);
      }
      active.right = x;
      active.ink += columns[x];
      active.top = Math.min(active.top, columnTop[x]);
      active.bottom = Math.max(active.bottom, columnBottom[x]);
    }
  }
  const words = groups
    .filter((group) => group.ink > 40 && group.right - group.left > 8)
    .map((group) => ({
      ...group,
      width: group.right - group.left + 1,
      height: group.bottom - group.top + 1,
      center: (group.left + group.right) / 2,
    }));
  assert.equal(words.length, expectedWordCount,
    `fixture should render ${expectedWordCount} distinct word boxes: ${JSON.stringify(words)}`);
  return {
    words,
    gaps: words.slice(1).map((word, index) => word.left - words[index].right - 1),
  };
}

const box = ({ left, right, top, bottom, width, height, center }) =>
  ({ left, right, top, bottom, width, height, center });

const assertBoxWithinPixel = (actual, expected, message) => {
  for (const key of ["left", "right", "top", "bottom", "width", "height", "center"]) {
    assert.ok(Math.abs(actual[key] - expected[key]) <= 1,
      `${message}: ${key} differed by more than one pixel (${actual[key]} vs ${expected[key]})`);
  }
};

test("karaoke pulse grows the active word, resets it, and keeps layout fixed", {
  timeout: 120_000,
}, async () => {
  await ensureBrowser();
  const serveUrl = await bundle({
    entryPoint: path.join(remotionDir, "src/index.ts"),
    onProgress: () => {},
  });
  const compositions = await getCompositions(serveUrl, { inputProps: props });
  const composition = compositions.find((candidate) => candidate.id === "SubtitleOverlay");
  assert.ok(composition, "SubtitleOverlay composition is registered");

  const renderFrame = async (frame) => {
    const rendered = await renderStill({
      composition,
      serveUrl,
      frame,
      inputProps: props,
      scale: 0.5,
      imageFormat: "png",
    });
    return renderedWords(rendered.buffer);
  };

  const before = await renderFrame(9);
  const during = await renderFrame(16);
  const after = await renderFrame(22);
  const nextDuring = await renderFrame(27);

  assert.ok(during.words[0].width > before.words[0].width);
  assert.ok(during.words[0].height > before.words[0].height);
  assert.deepEqual(box(after.words[0]), box(before.words[0]),
    "the first word must have its original rendered size and position after its highlight");
  assertBoxWithinPixel(box(after.words[1]), box(before.words[1]),
    "the next word returns to its resting size without moving the line");
  assert.ok(Math.abs(during.words[1].center - before.words[1].center) <= 3,
    `scaling one word must not visibly reflow its neighbor (${before.words[1].center} to ${during.words[1].center})`);
  assert.ok(Math.abs(after.gaps[0] - before.gaps[0]) <= 1,
    "the inter-word gap returns to its original rendered width");
  assert.ok(during.gaps[0] > 0,
    "the active word must not expand beyond its measured layout slot");
  assert.deepEqual(
    { width: nextDuring.words[0].width, height: nextDuring.words[0].height },
    { width: before.words[0].width, height: before.words[0].height },
    "the previous word remains at base size when the next word is active",
  );
  assert.ok(Math.abs(nextDuring.words[0].center - before.words[0].center) <= 3,
    "the previous word's position remains stable when the next word is active");
  assert.ok(nextDuring.words[1].width > before.words[1].width,
    "the newly active word receives its own scale pulse");
  assert.ok(Math.abs(nextDuring.words[1].center - before.words[1].center) <= 3,
    "the active word scales in place without visibly moving its layout slot");
});

test("rendered word gaps stay at the delivered Reel 22 spacing through four seconds", {
  timeout: 300_000,
}, async () => {
  await ensureBrowser();
  const auditProps = {
    ...props,
    subtitles: [{
      text: "when it comes to",
      startFrame: 0,
      endFrame: 96,
      words: [
        { word: "when  ", startFrame: 0, endFrame: 18 },
        { word: "  it", startFrame: 18, endFrame: 36 },
        { word: "comes", startFrame: 36, endFrame: 60 },
        { word: "to", startFrame: 60, endFrame: 84 },
      ],
    }],
    fps: 24,
    durationInFrames: 96,
    style: { ...props.style, fontSize: 51 },
  };
  const serveUrl = await bundle({
    entryPoint: path.join(remotionDir, "src/index.ts"),
    onProgress: () => {},
  });
  const compositions = await getCompositions(serveUrl, { inputProps: auditProps });
  const composition = compositions.find((candidate) => candidate.id === "SubtitleOverlay");
  assert.ok(composition, "SubtitleOverlay composition is registered");

  const measurements = [];
  for (let frame = 0; frame < 96; frame += 1) {
    const rendered = await renderStill({
      composition,
      serveUrl,
      frame,
      inputProps: auditProps,
      scale: 0.5,
      imageFormat: "png",
    });
    const { gaps } = renderedWords(rendered.buffer, 4, 5);
    measurements.push(...gaps);
  }

  const targetGap = (20.667 / 58) * 51 * 0.5;
  assert.equal(measurements.length, 96 * 3,
    `all adjacent word pairs in all 96 frames must be measured, got ${measurements.length}`);
  const minGap = Math.min(...measurements);
  const maxGap = Math.max(...measurements);
  const meanGap = measurements.reduce((sum, gap) => sum + gap, 0) / measurements.length;
  assert.ok(minGap >= targetGap - 2,
    `minimum gap ${minGap}px is below the measured ${targetGap.toFixed(2)}px target`);
  assert.ok(maxGap <= targetGap + 2,
    `maximum gap ${maxGap}px is above the measured ${targetGap.toFixed(2)}px target`);
  assert.ok(Math.abs(meanGap - targetGap) <= 1,
    `mean gap ${meanGap.toFixed(2)}px differs from ${targetGap.toFixed(2)}px target`);
});
