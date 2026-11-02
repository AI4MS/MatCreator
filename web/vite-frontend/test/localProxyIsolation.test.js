import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";
import { createServer } from "vite";
import config from "../vite.config.js";

function request(port, path, headers) {
  return new Promise((resolve, reject) => {
    const req = http.request({ hostname: "127.0.0.1", port, path, headers }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, body: JSON.parse(body) }));
    });
    req.on("error", reject);
    req.end();
  });
}

test("development proxies preserve the browser instance Host, Origin and cookie without granting a token", async () => {
  const upstream = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ host: req.headers.host, origin: req.headers.origin, cookie: req.headers.cookie, authorization: req.headers.authorization || null }));
  });
  await new Promise((resolve) => upstream.listen(0, "127.0.0.1", resolve));
  const target = `http://127.0.0.1:${upstream.address().port}`;
  const proxy = Object.fromEntries(Object.entries(config.server.proxy).map(([route, settings]) => [route, { ...settings, target }]));
  const vite = await createServer({
    configFile: false, plugins: [], logLevel: "silent",
    optimizeDeps: { noDiscovery: true, entries: [] },
    server: { ...config.server, host: "127.0.0.1", port: 0, hmr: false, proxy },
  });
  try {
    await vite.listen();
    const port = vite.httpServer.address().port;
    const host = `mc-${"a".repeat(32)}.localhost:${port}`;
    const origin = `http://${host}`;
    for (const path of ["/api/probe", "/apps/probe", "/run_sse", "/list-apps"]) {
      const result = await request(port, path, { Host: host, Origin: origin, Cookie: "synthetic_instance_session=example" });
      assert.equal(result.status, 200);
      assert.deepEqual(result.body, { host, origin, cookie: "synthetic_instance_session=example", authorization: null });
    }
  } finally {
    await vite.close();
    await new Promise((resolve) => upstream.close(resolve));
  }
});
