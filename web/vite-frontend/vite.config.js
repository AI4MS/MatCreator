import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Configurable port variables
const webPort = process.env.MATCREATOR_WEB_PORT || "8001";
const webTarget =
  process.env.MATCREATOR_WEB_TARGET || `http://localhost:${webPort}`;
const frontendPort = Number(process.env.MATCREATOR_FRONTEND_PORT || "5173");

// Preserve the browser's Host, Origin and credentials. Authentication and origin
// validation belong to the Web backend; injecting a launcher token here would
// grant every browser request access to the private instance.

export default defineConfig({
  plugins: [svelte()],
  esbuild: { jsx: "automatic" },
  server: {
    port: frontendPort,
    strictPort: true,
    proxy: {
      "/api": {
        target: webTarget,
        ws: true,
      },
      "/run_sse": {
        target: webTarget,
      },
      "/apps": {
        target: webTarget,
      },
      "/list-apps": {
        target: webTarget,
      },
    },
  },
});
