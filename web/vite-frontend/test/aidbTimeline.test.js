import assert from 'node:assert/strict';
import test from 'node:test';
import { parseHTML } from 'linkedom';
import { createAssistantMessage, applyAssistantMessagePart, completeAssistantMessage } from '../src/features/chat/timeline.js';
import { createActivityRenderer } from '../src/features/chat/activityRenderer.js';
import { createTimelineRenderer } from '../src/features/chat/timelineRenderer.js';
import { createDisclosureController } from '../src/features/ui/disclosureState.js';

for (const toolName of ['aidb_query', 'run_flash_step']) {
test(`normal chat preserves ${toolName} result details throughout streaming and completion`, async t => {
  const { document } = parseHTML('<html><body></body></html>');
  const requests = [];
  t.mock.method(globalThis, 'fetch', async url => {
    requests.push(url);
    const record = { record_id: 'si-1', formula_reduced: 'Si', calculation_type: 'static' };
    if (url === '/api/users/user/sessions/chat-a/aidb/results/query-1') {
      return { ok: true, json: async () => ({ records: [record] }) };
    }
    assert.equal(url, '/api/users/user/sessions/chat-a/aidb/records/si-1?binding_id=bound');
    return { ok: true, json: async () => ({ record }) };
  });
  const previousDocument = globalThis.document;
  globalThis.document = document;
  t.after(() => { globalThis.document = previousDocument; });
  const setMarkdownContent = (element, text) => { element.textContent = text; };
  const renderer = createTimelineRenderer({
    activityRenderer: createActivityRenderer({
      setMarkdownContent,
      getActiveRequest: () => null,
      getStepExecutionFeed: () => ({ bindRootHost() {} }),
    }),
    disclosureController: createDisclosureController(),
    setMarkdownContent,
    updatePreservingReadingPosition: update => update(),
    createAgentAvatarEl: () => document.createElement('span'),
    appendLiveTurnChild: (container, child) => container.appendChild(child),
    chatArea: document.body,
  });
  const message = createAssistantMessage({ id: 'chat-a' });
  applyAssistantMessagePart(message, {
    functionCall: { id: 'plan-1', name: 'validate_plan', args: {} },
  });
  const view = renderer.addAgentTimelineMessage(message);
  assert.match(view.element.textContent, /Agent activity/);
  assert.equal(view.element.querySelectorAll('.aidb-result').length, 0);

  const reference = { kind: 'aidb_result', user_id: 'user', session_id: 'chat-a',
    result_id: 'query-1', binding_id: 'bound' };
  applyAssistantMessagePart(message, {
    functionCall: { id: 'query-1', name: toolName, args: { conditions: { formula: 'Si' } } },
  });
  // The live renderer sees the invocation before its streamed response arrives.
  renderer.renderTimeline(view, message);
  assert.equal(view.element.querySelectorAll('.aidb-result').length, 0);
  applyAssistantMessagePart(message, {
    functionResponse: { id: 'query-1', name: toolName, response: { result: reference } },
  });
  renderer.renderTimeline(view, message);
  const panels = [...view.element.querySelectorAll('.aidb-result')];
  assert.equal(panels.length, 1);
  await panels[0].ready;
  assert.match(panels[0].textContent, /Si/);
  assert.equal(panels[0].querySelector('[data-action="detail"]').dataset.id, 'si-1');
  panels[0].querySelector('[data-action="detail"]').click();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.match(panels[0].textContent, /末帧结构与采样/);
  assert.equal(panels[0].querySelector('[data-action="detail"]').textContent, '收起');
  // Replayed responses revise the model but must retain the existing panel.
  applyAssistantMessagePart(message, {
    functionResponse: { id: 'query-1', name: toolName, response: { result: reference } },
  });
  renderer.renderTimeline(view, message);
  const assertRetainedDetails = () => {
    assert.equal(view.element.querySelectorAll('.aidb-result').length, 1);
    assert.ok(view.element.querySelector('.aidb-result') === panels[0], 'the loaded result panel must retain its identity');
    assert.equal(panels[0].querySelector('[data-action="detail"]').getAttribute('aria-expanded'), 'true');
    assert.match(panels[0].textContent, /末帧结构与采样/);
    assert.deepEqual(requests, [
      '/api/users/user/sessions/chat-a/aidb/results/query-1',
      '/api/users/user/sessions/chat-a/aidb/records/si-1?binding_id=bound',
    ]);
  };
  assertRetainedDetails();
  for (const part of [
    { thought: true, text: '正在核对' },
    { thought: true, text: '正在核对其他数据' },
    { functionCall: { id: 'plan-2', name: 'validate_plan', args: {} } },
    { functionResponse: { id: 'plan-2', name: 'validate_plan', response: { ok: true } } },
    { text: '已查询到' },
    { text: '已查询到所需记录。' },
  ]) {
    applyAssistantMessagePart(message, part);
    renderer.renderTimeline(view, message);
    assertRetainedDetails();
  }
  const exportReceipt = {
    kind: 'aidb_export', user_id: 'user', session_id: 'chat-a', binding_id: 'bound', export_id: 'download-1',
    record_id: 'si-1', files: {poscar:{filename:'POSCAR-si-1'}}, issues: {extxyz:'缺少逐原子力'},
  };
  applyAssistantMessagePart(message, {
    functionCall: {id:'export-call', name:'aidb_export', args:{record_id:'si-1'}},
  });
  applyAssistantMessagePart(message, {
    functionResponse: {id:'export-call', name:'aidb_export', response:{result:exportReceipt}},
  });
  renderer.renderTimeline(view, message);
  const link = view.element.querySelector('a[download]');
  assert.equal(link.getAttribute('href'), '/api/users/user/sessions/chat-a/aidb/exports/download-1/poscar');
  assert.match(view.element.textContent, /缺少逐原子力/);
  assert.deepEqual(requests, [
    '/api/users/user/sessions/chat-a/aidb/results/query-1',
    '/api/users/user/sessions/chat-a/aidb/records/si-1?binding_id=bound',
  ]);
  completeAssistantMessage(message);
  renderer.renderTimeline(view, message);
  assert.equal(view.element.querySelector('a[download]'), link);
});
}


test('a retry turn enables downloads on the original completion row', async t => {
  const { document } = parseHTML('<html><body></body></html>');
  const previousDocument = globalThis.document;
  globalThis.document = document;
  t.after(() => { globalThis.document = previousDocument; });

  const base = '/api/users/user/sessions/chat-a/aidb';
  const completion = base + '/completions/job-1';
  const output = {kind: 'aidb_completion', user_id: 'user', session_id: 'chat-a', result_id: 'job-1'};
  const record = {record_id: 'si-1', formula_reduced: 'Si', calculation_type: 'relaxation', energy: -10.25};
  let verified = false;
  let failReadback = false;
  let detailReads = 0;
  let exportReads = 0;
  const downloads = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (url === completion) {
      return {ok: true, json: async () => ({
        kind: 'aidb_completion', result_id: 'job-1', compute_status: 'succeeded',
        archive_status: verified ? 'verified' : 'failed', reason: verified ? null : '写入暂时失败',
        outcome: verified ? 'added' : null, binding_id: 'bound',
        summary: {formula: 'Si', energy_ev_per_cell: -10.25, frame_index: 4},
        artifact_paths: ['outputs/OUTCAR'], records: verified ? [record] : [],
      })};
    }
    if (url === base + '/records/si-1?binding_id=bound') {
      detailReads += 1;
      if (failReadback) return {ok: false, json: async () => ({detail: {message: '数据库暂时不可用'}})};
      return {ok: true, json: async () => ({record})};
    }
    assert.equal(url, completion + '/records/si-1/export');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), {binding_id: 'bound'});
    exportReads += 1;
    return {ok: true, json: async () => ({
      kind: 'aidb_export', export_id: 'download-' + exportReads, record_id: 'si-1',
      files: {extxyz: {filename: 'si-1.extxyz'}},
    })};
  });
  document.body.addEventListener('click', event => {
    if (event.target.matches('a[download]')) downloads.push(event.target.getAttribute('href'));
  });
  const setMarkdownContent = (element, text) => { element.textContent = text; };
  const renderer = createTimelineRenderer({
    activityRenderer: createActivityRenderer({
      setMarkdownContent,
      getActiveRequest: () => null,
      getStepExecutionFeed: () => ({bindRootHost() {}}),
    }),
    disclosureController: createDisclosureController(),
    setMarkdownContent,
    updatePreservingReadingPosition: update => update(),
    createAgentAvatarEl: () => document.createElement('span'),
    appendLiveTurnChild: (container, child) => container.appendChild(child),
    chatArea: document.body,
  });
  const completionTurn = id => {
    const message = createAssistantMessage({id});
    applyAssistantMessagePart(message, {
      functionCall: {id, name: 'run_flash_step', args: {}},
    });
    applyAssistantMessagePart(message, {
      functionResponse: {id, name: 'run_flash_step', response: {result: {aidb_result: {...output}}}},
    });
    completeAssistantMessage(message);
    return renderer.addAgentTimelineMessage(message);
  };

  const first = completionTurn('first-collect');
  const original = first.element.querySelector('.aidb-result');
  await original.ready;
  assert.match(original.textContent, /计算已完成/);
  assert.match(original.textContent, /写入暂时失败/);
  assert.match(original.textContent, /Si.*-10.25 eV/);
  assert.equal(original.querySelector('[data-action="export"]').disabled, true);
  const artifact = original.querySelector('a[download]');
  assert.match(artifact.getAttribute('href'), /\/completions\/job-1\/artifact\?path=outputs%2FOUTCAR$/);

  verified = true;
  const retry = completionTurn('retry');
  await original.ready;
  assert.equal(first.element.querySelector('.aidb-result'), original);
  assert.match(original.textContent, /已归档.*新增/);
  assert.equal(original.querySelector('[data-action="export"]').disabled, false);
  assert.equal(retry.element.querySelector('.aidb-result'), null);
  const detail = original.querySelector('[data-action="detail"]');
  const tick = () => new Promise(resolve => setTimeout(resolve, 0));
  detail.click();
  await tick();
  assert.match(original.textContent, /末帧结构与采样/);
  assert.equal(detail.textContent, '收起');
  detail.click();
  assert.equal(detail.textContent, '详情');
  failReadback = true;
  detail.click();
  await tick();
  assert.match(original.textContent, /数据库暂时不可用/);
  assert.equal(detailReads, 2);
  assert.equal(original.querySelector('a[download]').href, artifact.href);
  const exportButton = original.querySelector('[data-action="export"]');
  exportButton.click();
  await tick();
  exportButton.click();
  await tick();
  assert.deepEqual(downloads, [base + '/exports/download-1/extxyz', base + '/exports/download-2/extxyz']);

  document.body.replaceChildren();
  const reloaded = completionTurn('history');
  const restored = reloaded.element.querySelector('.aidb-result');
  await restored.ready;
  assert.match(restored.textContent, /已归档/);
  assert.equal(restored.querySelector('[data-action="detail"]').getAttribute('aria-expanded'), 'false');
});
