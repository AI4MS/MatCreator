import assert from "node:assert/strict";
import test from "node:test";

import { normalizeFontScale } from "../src/features/ui/appearance.js";

test("normalizeFontScale accepts presets and clamps oversized legacy values", () => {
  assert.equal(normalizeFontScale(90), 90);
  assert.equal(normalizeFontScale("125"), 125);
  assert.equal(normalizeFontScale(200), 150);
  assert.equal(normalizeFontScale(115), 100);
  assert.equal(normalizeFontScale(null), 100);
});
