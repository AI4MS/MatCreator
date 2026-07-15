import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Configurable port variables
const webPort = process.env.MATCREATOR_WEB_PORT || "8001";
const webTarget =
  process.env.MATCREATOR_WEB_TARGET || `http://localhost:${webPort}`;
const frontendPort = Number(process.env.MATCREATOR_FRONTEND_PORT || "5173");

// ADK's _OriginCheckMiddleware blocks non-safe HTTP methods (POST/DELETE/…)
// when the Origin header doesn't match the server's own host. Vite forwards
// the browser's Origin (e.g. http://localhost:5173) unchanged, which ADK
// rejects. Stripping the header makes the proxy behave like Python requests
// (no Origin → ADK skips the check entirely).
function stripOrigin(proxy) {
  proxy.on("proxyReq", (proxyReq) => {
    proxyReq.removeHeader("origin");
  });
}

export default defineConfig({
  plugins: [svelte()],
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
        changeOrigin: true,
        configure: stripOrigin,
      },
      "/apps": {
        target: webTarget,
        changeOrigin: true,
        configure: stripOrigin,
      },
      "/list-apps": {
        target: webTarget,
        changeOrigin: true,
        configure: stripOrigin,
      },
    },
  },
});
