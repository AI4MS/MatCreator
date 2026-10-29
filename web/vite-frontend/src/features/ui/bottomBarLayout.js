// Bottom-left action row: which quick-access buttons are visible, in what
// order. Device-local preference persisted in localStorage. The row holds at
// most five buttons; the control-panel button is hidden until added here.

const STORAGE_KEY = "mat_bottomBarLayout";

export const BOTTOM_BAR_MAX = 5;

export const BOTTOM_BAR_BUTTONS = [
  { id: "skill-graph-open", label: "Skill graph" },
  { id: "workspace-cli-toggle", label: "Workspace terminal" },
  { id: "app-mode-toggle", label: "App mode" },
  { id: "theme-toggle", label: "Theme" },
  { id: "settings-btn", label: "Settings" },
  { id: "dashboard-open", label: "Control panel" },
];

// The five buttons that shipped before the control panel existed — also the
// factory default layout.
export const BOTTOM_BAR_DEFAULT = [
  "skill-graph-open",
  "workspace-cli-toggle",
  "app-mode-toggle",
  "theme-toggle",
  "settings-btn",
];

const KNOWN_IDS = new Set(BOTTOM_BAR_BUTTONS.map((button) => button.id));

export function getBottomBarLayout() {
  let parsed;
  try {
    parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || "");
  } catch {
    parsed = null;
  }
  if (!Array.isArray(parsed)) return [...BOTTOM_BAR_DEFAULT];
  const ids = [...new Set(parsed.filter((id) => KNOWN_IDS.has(id)))];
  return ids.length ? ids.slice(0, BOTTOM_BAR_MAX) : [...BOTTOM_BAR_DEFAULT];
}

export function saveBottomBarLayout(ids) {
  const unique = [...new Set(ids.filter((id) => KNOWN_IDS.has(id)))].slice(0, BOTTOM_BAR_MAX);
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(unique));
  } catch {}
  return unique;
}

// Reorders visible buttons via flex `order` and hides the rest. Returns the
// applied layout.
export function applyBottomBarLayout(doc = document) {
  const ids = getBottomBarLayout();
  BOTTOM_BAR_BUTTONS.forEach(({ id }) => {
    const el = doc.getElementById(id);
    if (!el) return;
    const index = ids.indexOf(id);
    const visible = index >= 0;
    el.classList.toggle("mat-bb-hidden", !visible);
    if (visible) el.style.order = String(index);
  });
  return ids;
}
