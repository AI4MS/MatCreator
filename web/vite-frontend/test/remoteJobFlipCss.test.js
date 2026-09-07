import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { REMOTE_JOBS_VISUAL_FIXTURE } from "../src/dev/remoteJobsVisualFixture.js";

const sessionsCssUrl = new URL("../src/styles/sessions.css", import.meta.url);
const skinsCssUrl = new URL("../src/styles/skins.css", import.meta.url);
const rackLabCssUrl = new URL("../src/styles/rackLabLiquidGlass.css", import.meta.url);
const controllerUrl = new URL("../src/features/remoteJobs/RemoteJobsController.js", import.meta.url);
const mainUrl = new URL("../src/main.js", import.meta.url);

test("remote job flip grows from the compact summary into an in-flow details card", async () => {
  const css = await readFile(sessionsCssUrl, "utf8");

  assert.match(css, /\.remote-job\s*\{[\s\S]*height:\s*94px[\s\S]*perspective:\s*720px/);
  assert.match(css, /\.remote-job\.is-flipped\s*\{[\s\S]*grid-column:\s*1 \/ -1[\s\S]*height:\s*clamp\(166px, 21vh, 188px\)[\s\S]*scale\(1\.012\)/);
  assert.match(css, /\.remote-job-list\s*\{[\s\S]*grid-auto-flow:\s*row;/);
  assert.doesNotMatch(css, /grid-auto-flow:\s*row dense/);
  assert.match(css, /\.remote-job-list\.has-expanded-card\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\)/);
  assert.match(css, /\.remote-job-list\.has-expanded-card > \.remote-job:not\(\.is-flipped\)\s*\{\s*display:\s*none/);
  assert.match(css, /\.remote-job-rotor\s*\{[\s\S]*transform-style:\s*preserve-3d[\s\S]*transition:\s*transform 340ms cubic-bezier\(0\.2, 0\.72, 0\.2, 1\)/);
  assert.match(css, /\.remote-job\.is-flipped \.remote-job-rotor\s*\{\s*transform:\s*rotateY\(180deg\)/);
  assert.match(css, /\.remote-job-face\s*\{[\s\S]*backface-visibility:\s*hidden/);
  assert.match(css, /\.remote-job-face\s*\{[\s\S]*color:\s*var\(--text-primary\)/);
  assert.match(css, /\.remote-job-face-details\s*\{[\s\S]*transform:\s*rotateY\(180deg\)/);
  assert.match(css, /\.remote-job-refresh-button\s*\{[\s\S]*top:\s*5px[\s\S]*right:\s*5px/);
  assert.match(css, /\.remote-job-refresh-button::after\s*\{[\s\S]*inset:\s*-7px/);
  assert.match(css, /\.remote-job-card-toggle\s*\{[\s\S]*clip-path:\s*inset\(50%\)[\s\S]*pointer-events:\s*none/);
  assert.match(css, /\.remote-job-face-details\s*\{[\s\S]*user-select:\s*text/);
  assert.match(css, /\.remote-job-details-back\s*\{[\s\S]*position:\s*relative[\s\S]*flex:\s*0 0 auto[\s\S]*border-radius:\s*50%/);
  assert.match(css, /\.remote-job-progress\[data-progress-mode="indeterminate"\] \.remote-job-progress-fill\s*\{[\s\S]*remote-job-progress-scan/);
  assert.match(css, /\.remote-job-detail-rows\s*\{[\s\S]*grid-template-columns:\s*repeat\(auto-fit, minmax\(112px, 1fr\)\)[\s\S]*overflow:\s*auto/);
  assert.match(css, /\.remote-job-detail-rows\s*\{[\s\S]*grid-auto-rows:\s*max-content[\s\S]*align-items:\s*stretch/);
  assert.match(css, /\.remote-job-detail-row\s*\{[\s\S]*box-sizing:\s*border-box[\s\S]*height:\s*100%/);
  assert.match(css, /\.remote-job-detail-row > code\s*\{[\s\S]*overflow-wrap:\s*anywhere[\s\S]*text-overflow:\s*clip[\s\S]*white-space:\s*normal[\s\S]*word-break:\s*break-word/);
  assert.match(css, /@media \(max-width: 520px\)[\s\S]*\.remote-jobs-pane\.is-expanded\s*\{\s*height:\s*min\(38vh, 320px\)/);
  assert.match(css, /@container remote-jobs \(max-width: 270px\)[\s\S]*\.remote-job-detail-rows\s*\{\s*grid-template-columns:\s*1fr/);
});

test("remote job cards port the library sheen to whichever face is visible", async () => {
  const css = await readFile(sessionsCssUrl, "utf8");

  assert.match(css, /\.remote-job-face::before\s*\{[\s\S]*linear-gradient\(116deg, transparent 24%, rgba\(255, 255, 255, 0\.34\) 46%, transparent 68%\)[\s\S]*translateX\(-70%\)[\s\S]*650ms cubic-bezier\(0\.2, 0\.8, 0\.2, 1\)/);
  assert.match(css, /\.remote-job:hover \.remote-job-face\[aria-hidden="false"\]::before,[\s\S]*\.remote-job:focus-within \.remote-job-face\[aria-hidden="false"\]::before\s*\{[\s\S]*translateX\(76%\)/);
  assert.match(css, /\.remote-job\.is-flipped:hover,[\s\S]*0 22px 44px rgba\(2, 8, 23, 0\.25\)/);
});

test("typed task stages use a clipped rolling viewport and reserve progress for execution", async () => {
  const css = await readFile(sessionsCssUrl, "utf8");

  assert.match(css, /\.remote-job-face-summary\s*\{[\s\S]*grid-template-rows:\s*minmax\(0, 1fr\) 18px auto/);
  assert.match(css, /\.remote-job-face-summary\.has-execution-progress\s*\{[\s\S]*grid-template-rows:\s*minmax\(0, 1fr\) 18px auto 6px/);
  assert.match(css, /\.remote-job-stage-viewport\s*\{[\s\S]*height:\s*18px[\s\S]*overflow:\s*hidden/);
  assert.match(css, /\.remote-job-stage-track\s*\{[\s\S]*transition:\s*transform 220ms cubic-bezier\(0\.2, 0\.72, 0\.2, 1\)/);
  assert.match(css, /\.remote-job-stage-item\s*\{[\s\S]*flex:\s*0 0 18px[\s\S]*height:\s*18px/);
  assert.doesNotMatch(css, /data-progress-mode="terminal"[\s\S]*width:\s*100%/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.remote-job-stage-track\s*\{\s*transition:\s*none/);
  assert.match(css, /@media \(forced-colors: active\)[\s\S]*\.remote-job-stage-viewport\s*\{[\s\S]*border-color:\s*CanvasText/);
});

test("Rack Lab keeps the task stage viewport inside the industrial skin contract", async () => {
  const css = await readFile(skinsCssUrl, "utf8");

  assert.match(css, /body\[data-style-recipe="rack-lab"\][\s\S]*\.remote-job-stage-viewport\s*\{[\s\S]*border:\s*2px solid var\(--skin-shell-border-color\)[\s\S]*inset 0 2px 4px/);
  assert.match(css, /body\[data-style-recipe="rack-lab"\][\s\S]*\.remote-job-stage-item\s*\{[\s\S]*font-family:\s*var\(--skin-label-font\)[\s\S]*font-weight:\s*800/);
  assert.match(css, /@media \(forced-colors: active\)[\s\S]*\.remote-job-stage-viewport\s*\{[\s\S]*background:\s*Canvas[\s\S]*box-shadow:\s*none/);
});

test("Rack Lab gives indeterminate MD execution an honest full-rail flow", async () => {
  const css = await readFile(rackLabCssUrl, "utf8");

  assert.match(css, /data-workload-kind="md"\]\[data-current-phase="execute"\][\s\S]*\.remote-job-progress\s*\{[\s\S]*height:\s*7px/);
  assert.match(css, /data-progress-mode="indeterminate"[\s\S]*\.remote-job-progress-fill\s*\{[\s\S]*width:\s*100%[\s\S]*rack-md-progress-flow/);
  assert.match(css, /@keyframes rack-md-progress-flow[\s\S]*background-position:\s*-72% 0[\s\S]*background-position:\s*172% 0/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*data-workload-kind="md"[\s\S]*animation:\s*none/);
});

test("remote job cards have static motion and high-contrast fallbacks", async () => {
  const css = await readFile(sessionsCssUrl, "utf8");

  assert.match(
    css,
    /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.remote-job-face::before,[\s\S]*transition:\s*none[\s\S]*\.remote-job-face\[aria-hidden="true"\][\s\S]*visibility:\s*hidden[\s\S]*\.remote-job-face::before\s*\{\s*display:\s*none/,
  );
  assert.match(
    css,
    /@media \(forced-colors: active\)[\s\S]*\.remote-job[\s\S]*border-left:\s*3px solid Highlight[\s\S]*\.remote-job-face::before,[\s\S]*display:\s*none[\s\S]*\.remote-job-refresh-button:focus-visible/,
  );
});

test("Remote Jobs fixture is development-only and the controller has no hover popover", async () => {
  const [controller, main] = await Promise.all([
    readFile(controllerUrl, "utf8"),
    readFile(mainUrl, "utf8"),
  ]);
  const statuses = new Set(REMOTE_JOBS_VISUAL_FIXTURE.map(({ status }) => status));

  assert.deepEqual(statuses, new Set(["running", "paused", "collected", "failed"]));
  assert.equal(REMOTE_JOBS_VISUAL_FIXTURE[0].view.phase_label, "Relaxation");
  assert.equal(REMOTE_JOBS_VISUAL_FIXTURE[0].view.workload_kind, "relaxation");
  assert.equal(REMOTE_JOBS_VISUAL_FIXTURE[0].view.show_progress, true);
  assert.equal(REMOTE_JOBS_VISUAL_FIXTURE[1].view.show_progress, false);
  assert.match(main, /import\.meta\.env\.DEV\s*&&\s*visualFixture === "remote-job-cards"/);
  assert.match(main, /remoteJobsController\.setPresentationJobs\(REMOTE_JOBS_VISUAL_FIXTURE\)/);
  assert.match(controller, /const flippedJobIds = new Set\(\)/);
  assert.match(controller, /setAttribute\("inert", ""\)/);
  assert.match(controller, /item\.addEventListener\("click"/);
  assert.match(controller, /className = "remote-job-refresh-button remote-job-action refresh-button"/);
  assert.doesNotMatch(controller, /remote-job-flip-toggle/);
  assert.match(controller, /export function remoteJobProgress/);
  assert.match(controller, /export function normalizeRemoteJobPresentation/);
  assert.match(controller, /const lastPhaseByJobId = new Map\(\)/);
  assert.match(controller, /export function remoteJobConfiguration/);
  assert.doesNotMatch(controller, /remote-job-detail-popover|mouseenter|mouseleave/);
});
