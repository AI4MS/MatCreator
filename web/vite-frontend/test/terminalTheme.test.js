import assert from "node:assert/strict";
import test from "node:test";

import { createWorkspaceTerminalTheme } from "../src/features/workspace/terminal.js";

test("workspace terminal theme reads semantic skin tokens from the body", () => {
  const body = {};
  const values = new Map([
    ["--terminal-bg", " #101820 "],
    ["--terminal-text", "#f4f7fb"],
    ["--accent-primary", "#42b8d5"],
    ["--selection", "rgba(66, 184, 213, 0.35)"],
  ]);
  const readComputedStyle = (element) => {
    assert.strictEqual(element, body);
    return { getPropertyValue: (name) => values.get(name) || "" };
  };

  assert.deepEqual(createWorkspaceTerminalTheme(body, readComputedStyle), {
    background: "#101820",
    foreground: "#f4f7fb",
    cursor: "#42b8d5",
    selectionBackground: "rgba(66, 184, 213, 0.35)",
  });
});

test("workspace terminal theme retains safe defaults for missing tokens", () => {
  const readComputedStyle = () => ({ getPropertyValue: () => "  " });

  assert.deepEqual(createWorkspaceTerminalTheme({}, readComputedStyle), {
    background: "#030712",
    foreground: "#d1fae5",
    cursor: "#7dd3fc",
    selectionBackground: "#1e40af88",
  });
});
