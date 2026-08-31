import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const baseCssUrl = new URL("../src/styles/base.css", import.meta.url);
const overlaysCssUrl = new URL("../src/styles/overlays.css", import.meta.url);
const chatCssUrl = new URL("../src/styles/chat.css", import.meta.url);
const skinsCssUrl = new URL("../src/styles/skins.css", import.meta.url);
const resizersUrl = new URL("../src/features/layout/resizers.js", import.meta.url);

test("narrow layouts stack panels and disable persisted rail widths at one breakpoint", async () => {
  const [baseCss, overlaysCss, skinsCss, resizersSource] = await Promise.all([
    readFile(baseCssUrl, "utf8"),
    readFile(overlaysCssUrl, "utf8"),
    readFile(skinsCssUrl, "utf8"),
    readFile(resizersUrl, "utf8"),
  ]);

  assert.match(resizersSource, /matchMedia\("\(max-width: 1000px\)"\)/);
  assert.match(baseCss, /@media \(max-width: 1000px\)[\s\S]*overflow-x:\s*hidden[\s\S]*\.page,[\s\S]*\.app-grid\s*\{[\s\S]*height:\s*auto/);
  assert.match(
    overlaysCss,
    /@media \(max-width: 1000px\)[\s\S]*\.app-grid\s*\{[\s\S]*flex-direction:\s*column[\s\S]*\.graph-rail,\s*\.side-panel,\s*\.file-explorer-col\s*\{[\s\S]*width:\s*100%\s*!important[\s\S]*max-width:\s*none[\s\S]*\.panel-resizer,\s*\.col-resizer\s*\{\s*display:\s*none\s*!important/,
  );
  assert.match(skinsCss, /@media \(max-width: 1000px\)[\s\S]*data-style-recipe="rack-lab"[\s\S]*\.app-grid/);
});

test("compact desktop rail caps are declared after feature-owned widths", async () => {
  const [baseCss, overlaysCss] = await Promise.all([
    readFile(baseCssUrl, "utf8"),
    readFile(overlaysCssUrl, "utf8"),
  ]);

  assert.match(baseCss, /@media \(max-width: 1180px\) and \(min-width: 1001px\)/);
  assert.match(
    overlaysCss,
    /@media \(max-width: 1180px\) and \(min-width: 1001px\)[\s\S]*\.graph-rail,\s*\.side-panel\s*\{\s*max-width:\s*280px/,
  );
});

test("wide chat content and composer follow the available center column", async () => {
  const [chatCss, overlaysCss] = await Promise.all([
    readFile(chatCssUrl, "utf8"),
    readFile(overlaysCssUrl, "utf8"),
  ]);

  assert.match(chatCss, /\.message\s*\{[\s\S]*?width:\s*100%/);
  assert.match(chatCss, /\.agent-message \.message-bubble\s*\{[\s\S]*?max-width:\s*none/);
  assert.match(
    chatCss,
    /\.input-area\s*\{[\s\S]*?inset-inline:\s*16px[\s\S]*?width:\s*auto[\s\S]*?transform:\s*none/,
  );
  assert.doesNotMatch(chatCss, /width:\s*min\(100%,\s*968px\)/);
  assert.doesNotMatch(chatCss, /width:\s*min\(calc\(100% - 32px\),\s*980px\)/);
  assert.match(
    overlaysCss,
    /@media \(max-width: 640px\)[\s\S]*?\.input-area\s*\{[\s\S]*?inset-inline:\s*10px[\s\S]*?width:\s*auto/,
  );
});
