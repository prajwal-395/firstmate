/** Run cards through one Remotion browser and always release it afterward. */
export async function withBatchRenderer(
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
) {
  let puppeteerInstance;
  let browserPromise;

  const browser = async () => {
    if (!browserPromise) browserPromise = openBrowser("chrome");
    puppeteerInstance = await browserPromise;
    return puppeteerInstance;
  };

  const renderOne = async (job) => {
    const sharedBrowser = await browser();
    const composition = await selectComposition({
      serveUrl,
      id: compositionId,
      inputProps: job.props,
      puppeteerInstance: sharedBrowser,
    });
    await renderMedia({
      composition,
      serveUrl,
      codec: "prores",
      proResProfile: "4444",
      imageFormat: "png",
      pixelFormat: "yuva444p10le",
      outputLocation: job.out,
      inputProps: job.props,
      puppeteerInstance: sharedBrowser,
      ...(concurrency ? { concurrency } : {}),
      ...(encoderThreads ? { ffmpegOverride: boundEncoder } : {}),
    });
  };

  try {
    return await run(renderOne);
  } finally {
    if (puppeteerInstance) {
      const browser = puppeteerInstance;
      puppeteerInstance = undefined;
      await browser.close({ silent: true });
    }
  }
}

/** Run a finite job list with bounded parallel cards and input-order results. */
export async function mapWithConcurrency(items, concurrency, work) {
  const results = new Array(items.length);
  let next = 0;
  const workerCount = Math.max(1, Math.floor(concurrency || 1));
  const workers = await Promise.allSettled(
    Array.from({ length: Math.min(workerCount, items.length) }, async () => {
      while (next < items.length) {
        const index = next++;
        results[index] = await work(items[index], index);
      }
    }),
  );
  const failed = workers.find((worker) => worker.status === "rejected");
  if (failed) throw failed.reason;
  return results;
}
