import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssUrl = new URL("../src/styles/rackLabLiquidGlass.css", import.meta.url);
const indexCssUrl = new URL("../src/styles/index.css", import.meta.url);

test("Rack Lab imports an isolated native liquid-glass material recipe", async () => {
  const [css, indexCss] = await Promise.all([
    readFile(cssUrl, "utf8"),
    readFile(indexCssUrl, "utf8"),
  ]);

  assert.match(indexCss, /@import "\.\/rackLabLiquidGlass\.css";/);
  assert.match(css, /body\[data-skin="rack-lab"\] \.remote-job-glass-warp/);
  assert.match(css, /filter:\s*url\("#rack-remote-job-liquid-glass-filter"\)/);
  assert.match(css, /backdrop-filter:\s*blur\(var\(--rack-glass-blur\)\) saturate\(132%\) contrast\(104%\)/);
  assert.match(css, /body\[data-skin="rack-lab"\] \.remote-job-glass-edge\s*\{[\s\S]*mask-composite:\s*exclude[\s\S]*mix-blend-mode:\s*screen/);
  assert.match(css, /\.remote-job-face > :not\(\.remote-job-glass-warp\):not\(\.remote-job-glass-edge\)\s*\{[\s\S]*z-index:\s*2/);
  assert.match(css, /\.remote-job-face\[aria-hidden="false"\]\s*\{[\s\S]*backdrop-filter:\s*blur\(var\(--rack-glass-blur\)\)/);
  assert.match(css, /\.remote-job-face\[aria-hidden="true"\]\s*\{[\s\S]*backdrop-filter:\s*none/);
  const baseCardRule = css.match(/body\[data-skin="rack-lab"\] \.remote-job\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.doesNotMatch(baseCardRule, /will-change:\s*transform/);
  assert.match(css, /liquid-glass-react \(MIT, Max Rovensky/);
  assert.doesNotMatch(css, /--rack-glass-[xy]/);
  assert.doesNotMatch(css, /radial-gradient\([^)]*var\(--rack-glass-/);
  assert.doesNotMatch(css, /body\[data-style-recipe="rack-lab"\]/);
  assert.doesNotMatch(css, /transition:\s*all\b/);
});

test("Rack liquid glass preserves readable progressive fallbacks", async () => {
  const css = await readFile(cssUrl, "utf8");

  assert.match(css, /@supports not \(\(-webkit-backdrop-filter:[\s\S]*\.remote-job-glass-warp\s*\{\s*display:\s*none/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*filter:\s*none !important[\s\S]*transition:\s*none/);
  assert.match(css, /@media \(prefers-reduced-transparency: reduce\)[\s\S]*background:\s*var\(--skin-shell-background\)[\s\S]*\.remote-job-glass-warp, \.remote-job-glass-edge/);
  assert.match(css, /@media \(forced-colors: active\)[\s\S]*background:\s*Canvas[\s\S]*color:\s*CanvasText[\s\S]*text-shadow:\s*none/);
  assert.match(css, /@container remote-jobs \(max-width: 270px\)[\s\S]*filter:\s*none !important/);
});
