// Entry for dashboard.html — the standalone control-panel page (meant for a
// second monitor). Boots only the theme layer plus the dashboard controller;
// no chat/session stack. Follows the skin persisted by the main app.
import { createThemeManager } from "./theme/ThemeManager.js";
import { createThemeRegistry } from "./theme/ThemeRegistry.js";
import { BUILTIN_SKINS } from "./theme/builtinSkins.js";
import "./styles/index.css";
import { createDashboardController } from "./features/dashboard/DashboardController.js";

const themeRegistry = createThemeRegistry(BUILTIN_SKINS);
const themeManager = createThemeManager({
  registry: themeRegistry,
  target: document.body,
  eventTarget: window,
  storage: localStorage,
});
themeManager.apply({
  skinId: localStorage.getItem("mat_skin") || "matcreator-default",
  variant: localStorage.getItem("mat_skin_variant") || undefined,
});

const dashboardController = createDashboardController({});
dashboardController.renderStandalone(document.body);
