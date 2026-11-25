import assert from 'node:assert/strict';
import test from 'node:test';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build, createServer } from 'vite';
import { parseHTML } from 'linkedom';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const entry = '/src/features/aidb/records.js';
const widgetURL = 'https://cdn.jsdelivr.net/npm/matterviz-anywidget@0.8.0/build/matterviz.js';
const structure = {
  lattice: {matrix: [[5.43, 0, 0], [0, 5.43, 0], [0, 0, 5.43]]},
  sites: [{label: 'Si', abc: [0, 0, 0], species: [{element: 'Si', occu: 1}]}],
};
const moduleURL = code => 'data:text/javascript;base64,' + Buffer.from(code).toString('base64');

for (const mode of ['development', 'production']) {
  test(`${mode} record details mount MatterViz in the isolated structure frame`, async () => {
    const modules = new Map();
    let code;
    if (mode === 'development') {
      // Exercise Vite's browser transform without starting a listening server.
      const vite = await createServer({root: frontend, configFile: false,
        optimizeDeps: {noDiscovery: true, include: []},
        server: {middlewareMode: true, hmr: false}, appType: 'custom'});
      try {
        const collect = async path => {
          const result = await vite.transformRequest(path);
          modules.set(path, result.code);
          for (const match of result.code.matchAll(/from\s+["']([^"']+)["']/g)) {
            if (match[1].startsWith('/src/') && !modules.has(match[1])) await collect(match[1]);
          }
        };
        await collect(entry);
        code = modules.get(entry);
      } finally {
        await vite.close();
      }
    } else {
      const result = await build({root: frontend, configFile: false, logLevel: 'silent',
        build: {write: false, minify: 'esbuild', lib: {
          entry: fileURLToPath(new URL('../src/features/aidb/records.js', import.meta.url)),
          formats: ['es'],
        }}});
      code = result[0].output.find(item => item.type === 'chunk' && item.isEntry).code;
    }

    const hooks = registerHooks({
      resolve(specifier, context, nextResolve) {
        // The browser's development host and remote widget are external boundaries.
        if (specifier === '/@vite/client') return {url: moduleURL('export const injectQuery = url => url;'), shortCircuit: true};
        if (modules.has(specifier)) return {url: moduleURL(modules.get(specifier)), shortCircuit: true};
        if (specifier === widgetURL) return {url: moduleURL(`export default {
          render({el, model}) {
            const canvas = el.ownerDocument.createElement('canvas');
            canvas.dataset.structure = JSON.stringify(model.get('structure'));
            canvas.dataset.widgetType = model.get('widget_type');
            el.append(canvas);
          }
        };`), shortCircuit: true};
        return nextResolve(specifier, context);
      },
    });
    const previousDocument = globalThis.document;
    try {
      const {document} = parseHTML('<html><head></head><body></body></html>');
      globalThis.document = document;
      const {createAIDBResult} = await import(moduleURL(code));
      const record = {record_id: 'si-1', formula_reduced: 'Si', calculation_type: 'relaxation', structure};
      const fetchImpl = async url => ({ok: true, json: async () => url.includes('/results/')
        ? {records: [record], expanded_record_id: record.record_id} : {record}});
      const panel = createAIDBResult({user_id: 'u', session_id: 's', result_id: 'q', binding_id: 'b'}, {fetchImpl});
      document.body.append(panel);
      await panel.ready;
      const frame = panel.querySelector('iframe');
      assert.equal(frame.title, 'MatterViz · 末帧晶体结构');
      assert.equal(frame.getAttribute('sandbox'), 'allow-scripts');
      const frameDocument = parseHTML(frame.srcdoc).document;
      globalThis.document = frameDocument;
      // A fresh script scope matches the sandbox: parent-module helpers are unavailable.
      await new Function('return ' + frameDocument.querySelector('script').textContent)();
      const canvas = frameDocument.querySelector('#viewer canvas');
      assert.ok(canvas, frameDocument.querySelector('#viewer').textContent);
      assert.equal(canvas.dataset.widgetType, 'structure');
      assert.deepEqual(JSON.parse(canvas.dataset.structure), structure);
      assert.equal(frameDocument.querySelector('link').href,
        'https://cdn.jsdelivr.net/npm/matterviz-anywidget@0.8.0/build/matterviz.css');
    } finally {
      globalThis.document = previousDocument;
      hooks.deregister();
    }
  });
}
