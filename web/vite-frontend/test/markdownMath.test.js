import assert from "node:assert/strict";
import test from "node:test";

import { marked } from "marked";
import markedKatex from "marked-katex-extension";

marked.use(markedKatex({ throwOnError: false, strict: "ignore", output: "html", nonStandard: true }));

test("renders inline and display TeX while preserving code literals", () => {
  const html = marked.parse("Mass: $E=mc^2$.\n\n$$\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}$$\n\n`$not_math$`");
  assert.match(html, /class="katex"/);
  assert.match(html, /class="katex-display"/);
  assert.match(html, /\$not_math\$/);
});
