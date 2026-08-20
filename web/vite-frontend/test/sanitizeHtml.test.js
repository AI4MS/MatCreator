import assert from "node:assert/strict";
import test from "node:test";

import { isSafeRenderedUrl } from "../src/shared/rendering/sanitizeHtml.js";

test("allows ordinary link and image URLs", () => {
  assert.equal(isSafeRenderedUrl("https://example.com/docs"), true);
  assert.equal(isSafeRenderedUrl("/api/session/file?id=1"), true);
  assert.equal(isSafeRenderedUrl("#result"), true);
  assert.equal(isSafeRenderedUrl("blob:https://example.com/id", { image: true }), true);
});

test("rejects executable and inline-data URL schemes", () => {
  assert.equal(isSafeRenderedUrl("javascript:alert(1)"), false);
  assert.equal(isSafeRenderedUrl("java\nscript:alert(1)"), false);
  assert.equal(isSafeRenderedUrl("data:text/html,<script>alert(1)</script>"), false);
  assert.equal(isSafeRenderedUrl("data:image/svg+xml,<svg onload=alert(1)>", { image: true }), false);
});

test("uses different protocol allowlists for links and images", () => {
  assert.equal(isSafeRenderedUrl("mailto:team@example.com"), true);
  assert.equal(isSafeRenderedUrl("mailto:team@example.com", { image: true }), false);
  assert.equal(isSafeRenderedUrl("blob:https://example.com/id"), false);
});
