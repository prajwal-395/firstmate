import { continueRender, delayRender, staticFile } from "remotion";

/**
 * Fonts, loaded from disk and BLOCKING the render until they are ready.
 *
 * P3.3. `src/index.css` used to do
 *
 *     @import url('https://fonts.googleapis.com/css2?family=Montserrat...')
 *
 * with no `delayRender`, so whether the correct font was in place when
 * Remotion started rasterising was a race with the network. On a miss the
 * captions rendered in Chromium's fallback sans at a different width,
 * which changes line breaking as well as the letterforms - and nothing
 * downstream could tell, because the frames were still valid PNGs of the
 * right size.
 *
 * Montserrat now ships in `public/fonts/` as a single variable file
 * covering the whole 100-900 weight axis, which is every weight
 * `library/tools/subtitle_style.py` can ask for. No network at render
 * time, and `delayRender` holds the first frame until the face is
 * genuinely usable rather than merely requested.
 *
 * Licence: SIL Open Font License 1.1, redistributable including
 * commercially. The text ships beside the font as
 * `public/fonts/OFL-Montserrat.txt`; see AGENTS.md section 11.
 */

export const BUNDLED_FONT_FAMILY = "Montserrat";
export const BUNDLED_FONT_FILE = "fonts/Montserrat-Variable.ttf";

let loaded: Promise<void> | null = null;

/**
 * Load the bundled fonts once per render process.
 *
 * Safe to call from several compositions: the promise is memoised, so the
 * font is registered once and every later caller awaits the same load.
 */
export const loadBundledFonts = (): Promise<void> => {
  if (loaded) {
    return loaded;
  }

  const handle = delayRender(
    `Loading bundled font ${BUNDLED_FONT_FAMILY} from ${BUNDLED_FONT_FILE}`,
  );

  loaded = (async () => {
    const face = new FontFace(
      BUNDLED_FONT_FAMILY,
      `url(${staticFile(BUNDLED_FONT_FILE)}) format('truetype')`,
      // The variable axis, so a single file serves every weight a brand
      // template can name. Without this Chromium synthesises bold, which
      // is not the same shape as the real 900 weight.
      { weight: "100 900", style: "normal" },
    );
    await face.load();
    document.fonts.add(face);
    // `load()` resolves when the bytes are parsed; this waits until the
    // face is actually usable for layout, which is the thing that used to
    // race.
    await document.fonts.ready;
  })()
    .catch((err) => {
      // Fail loudly. A silent fallback to Chromium's default sans is the
      // exact failure this module exists to remove, and it is invisible
      // in the output.
      throw new Error(
        `Failed to load bundled font ${BUNDLED_FONT_FAMILY} from ` +
          `${BUNDLED_FONT_FILE}: ${err instanceof Error ? err.message : String(err)}`,
      );
    })
    .finally(() => {
      continueRender(handle);
    });

  return loaded;
};
