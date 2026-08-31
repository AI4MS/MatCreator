import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const skinsCssUrl = new URL("../src/styles/skins.css", import.meta.url);
const mainJsUrl = new URL("../src/main.js", import.meta.url);

test("scopes structural Rack Lab CSS to its pre-reviewed recipe contract", async () => {
  const css = await readFile(skinsCssUrl, "utf8");

  assert.match(css, /body\[data-style-recipe="rack-lab"\]\[data-style-recipe-version="1"\]/);
  assert.doesNotMatch(css, /body\[data-skin="rack-lab"\]/);
  assert.doesNotMatch(css, /@import\s+url|url\s*\(\s*["']?https?:/i);
  assert.match(css, /\.center-tab::before[\s\S]*radial-gradient/);
  assert.doesNotMatch(css, /\.center-tab::after/);
  assert.doesNotMatch(css, /--metal-sheen-opacity|--metal-pointer-[xy]/);
  assert.match(css, /rotateX\(var\(--metal-tilt-x\)\) rotateY\(var\(--metal-tilt-y\)\)/);
  assert.match(css, /background-position:\s*0 0, var\(--metal-stripe-position\) 50%/);
  assert.match(css, /\.center-tab:focus-visible[\s\S]*outline-offset:\s*-3px/);
  assert.match(css, /\.composer-toolbar #file-upload-btn[\s\S]*box-shadow:/);
  assert.match(css, /#file-upload-btn:active:not\(:disabled\)[\s\S]*inset/);
  assert.match(css, /\.composer-toolbar #send-btn \{[\s\S]*linear-gradient[\s\S]*box-shadow:/);
  assert.match(css, /#send-btn:active:not\(:disabled\)[\s\S]*inset 3px 3px 7px/);
  assert.match(css, /#send-btn\.is-stopping[\s\S]*--rack-send-surface:/);
  assert.match(css, /#send-btn:disabled,[\s\S]*#send-btn\.is-finalizing/);
  assert.match(
    css,
    /\.remote-job:hover,[\s\S]*border-left-color:\s*var\(--remote-job-status-color\)[\s\S]*0 18px 36px rgba\(2, 8, 23, 0\.24\)/,
  );
  assert.match(
    css,
    /\.remote-job\.is-flipped:hover,[\s\S]*0 22px 44px rgba\(2, 8, 23, 0\.28\)/,
  );
  assert.doesNotMatch(css, /remote-job-flip-toggle/);
  assert.match(
    css,
    /\[data-theme="light"\][\s\S]*#file-upload-btn[\s\S]*color:\s*#1b2632/,
  );
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.center-tab[\s\S]*transform:\s*none/);
  assert.match(
    css,
    /@media \(forced-colors: active\)[\s\S]*\.center-tab\.active[\s\S]*background:\s*Highlight/,
  );
});

test("Rack Lab dog tags track pointer position through local CSS variables", async () => {
  const source = await readFile(mainJsUrl, "utf8");

  assert.match(source, /centerTabs\?\.addEventListener\("pointermove"/);
  assert.doesNotMatch(source, /--metal-pointer-[xy]/);
  assert.match(source, /--metal-tilt-x/);
  assert.match(source, /--metal-tilt-y/);
  assert.match(source, /--metal-stripe-position/);
  assert.match(source, /centerTabs\?\.addEventListener\("pointerout"/);
});
