import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexUrl = new URL("../index.html", import.meta.url);
const baseCssUrl = new URL("../src/styles/base.css", import.meta.url);
const graphCssUrl = new URL("../src/styles/graphs.css", import.meta.url);
const motionCssUrl = new URL("../src/styles/motion.css", import.meta.url);
const skinsCssUrl = new URL("../src/styles/skins.css", import.meta.url);
const rackLabBrandSvgUrl = new URL("../public/rack-lab-brand.svg", import.meta.url);
const rackLabBrandMotionUrl = new URL("../src/features/brand/RackLabBrandMotion.js", import.meta.url);
const packageJsonUrl = new URL("../package.json", import.meta.url);

test("brand lockup exposes one labelled heading and no legacy subtitle", async () => {
  const html = await readFile(indexUrl, "utf8");

  assert.match(html, /class="brand-mark" aria-hidden="true"/);
  assert.match(html, /<clipPath id="brand-logo-clip"/);
  assert.match(html, /clip-path="url\(#brand-logo-clip\)"/);
  assert.match(html, /<feFuncA type="discrete" tableValues="0 0 1 1"><\/feFuncA>/);
  assert.match(html, /class="rack-lab-brand-logo"/);
  assert.match(html, /class="rack-lab-brand-base"/);
  assert.match(html, /class="brand-title brand-wordmark" aria-label="MatCreator"/);
  assert.match(html, /class="brand-wordmark-layer brand-wordmark-scan" aria-hidden="true"/);
  assert.match(html, /class="brand-wordmark-layer brand-wordmark-signal" aria-hidden="true"/);
  assert.doesNotMatch(html, /Plan · Execute · Review/);
});

test("brand motion is composited and has an explicit reduced-motion fallback", async () => {
  const [baseCss, motionCss] = await Promise.all([
    readFile(baseCssUrl, "utf8"),
    readFile(motionCssUrl, "utf8"),
  ]);

  assert.doesNotMatch(baseCss, /\.brand-mark::(?:before|after)/);
  assert.match(baseCss, /--brand-wordmark-motion:\s*#315f96/);
  assert.match(baseCss, /\.brand-wordmark-scan[\s\S]*background-clip:\s*text/);
  assert.doesNotMatch(motionCss, /@keyframes brand-mark-(?:ring|orbit)/);
  assert.match(motionCss, /@keyframes brand-mark-breathe/);
  assert.match(motionCss, /@keyframes brand-wordmark-scan/);
  assert.doesNotMatch(motionCss, /brand-liquid-flow/);
  assert.match(
    motionCss,
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.brand-wordmark-scan[\s\S]*animation:\s*none/,
  );
});

test("Rack Lab alone replaces the raster favicon with a clean local vector mark", async () => {
  const [html, baseCss, skinsCss, rackLabBrandSvg] = await Promise.all([
    readFile(indexUrl, "utf8"),
    readFile(baseCssUrl, "utf8"),
    readFile(skinsCssUrl, "utf8"),
    readFile(rackLabBrandSvgUrl, "utf8"),
  ]);

  assert.match(html, /class="rack-lab-brand-base"[\s\S]*href="\/rack-lab-brand\.svg"/);
  assert.match(baseCss, /\.rack-lab-brand-logo\s*\{[\s\S]*display:\s*none/);
  assert.match(skinsCss, /body\[data-style-recipe="rack-lab"\]\[data-style-recipe-version="1"\] \.brand-icon-original\s*\{[\s\S]*display:\s*none/);
  assert.match(skinsCss, /body\[data-style-recipe="rack-lab"\]\[data-style-recipe-version="1"\] \.rack-lab-brand-logo\s*\{[\s\S]*display:\s*block/);
  assert.match(
    skinsCss,
    /body\[data-style-recipe="rack-lab"\]\[data-style-recipe-version="1"\] \.brand-wordmark-scan,[\s\S]*\.brand-wordmark-signal\s*\{[\s\S]*animation:\s*none;[\s\S]*opacity:\s*0/,
  );
  assert.match(rackLabBrandSvg, /shape-rendering="geometricPrecision"/);
  assert.match(rackLabBrandSvg, /stroke-width="31"/);
  assert.doesNotMatch(rackLabBrandSvg, /<(?:image|filter)\b|feTurbulence|data:image|\.png/i);
});

test("Rack Lab bundles Anime.js and staggers locally split wordmark characters", async () => {
  const [source, skinsCss, packageJson] = await Promise.all([
    readFile(rackLabBrandMotionUrl, "utf8"),
    readFile(skinsCssUrl, "utf8"),
    readFile(packageJsonUrl, "utf8").then(JSON.parse),
  ]);

  assert.match(packageJson.dependencies.animejs, /^\^4\./);
  assert.match(source, /import \{ animate, splitText, stagger \} from "animejs"/);
  assert.match(source, /chars: \{ class: "rack-brand-char" \}/);
  assert.match(source, /delay: staggerImpl\(65\)/);
  assert.match(source, /y: \["0em", "-0\.22em", "0em"\]/);
  assert.match(source, /scale: \[1, 1\.025, 1\]/);
  assert.match(source, /prefers-reduced-motion: reduce/);
  assert.match(skinsCss, /\.rack-brand-char\s*\{[\s\S]*display:\s*inline-block[\s\S]*border-radius:\s*0\.22em/);
});

test("all five left controls share the neumorphic shell and semantic pressed states", async () => {
  const [html, graphCss] = await Promise.all([
    readFile(indexUrl, "utf8"),
    readFile(graphCssUrl, "utf8"),
  ]);
  const start = html.indexOf('<div class="left-action-row">');
  const end = html.indexOf('<div class="left-user-auth-actions"', start);
  const actionMarkup = html.slice(start, end);
  const controls = [
    "skill-graph-open",
    "workspace-cli-toggle",
    "app-mode-toggle",
    "theme-toggle",
    "settings-btn",
  ];

  assert.ok(start >= 0 && end > start);
  controls.forEach((id) => assert.match(actionMarkup, new RegExp(`id="${id}"`)));
  assert.equal((actionMarkup.match(/class="ghost icon-action-btn/g) || []).length, controls.length);
  assert.match(graphCss, /\.left-action-row \.icon-action-btn[\s\S]*box-shadow:/);
  assert.match(graphCss, /\[aria-pressed="true"\]/);
  assert.match(graphCss, /\[aria-expanded="true"\]/);
});
