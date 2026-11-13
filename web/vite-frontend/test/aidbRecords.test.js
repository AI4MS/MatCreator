import assert from 'node:assert/strict';
import test from 'node:test';
import { parseHTML } from 'linkedom';
import { createAIDBResult } from '../src/features/aidb/records.js';

test('record details reread on every opening and clear stale content on failure', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const record = {record_id:'si-1', formula_reduced:'Si', calculation_type:'relaxation', energy:-43.38227814};
  let reads = 0;
  const fetchImpl = async url => {
    assert.match(url, /sessions\/chat-a\/aidb\//);
    if (url.includes('/results/')) return {ok:true, json:async()=>({records:[record],binding_id:'bound'})};
    reads++;
    if (reads === 3) return {ok:false, status:409, json:async()=>({detail:{message:'记录读回失败'}})};
    return {ok:true,json:async()=>({record:{...record,energy:-43.38227814-reads}})};
  };
  const panel = createAIDBResult({user_id:'user',session_id:'chat-a',result_id:'q',binding_id:'bound'}, {fetchImpl, mountStructures:()=>{}});
  document.body.append(panel);
  await panel.ready;
  const button = panel.querySelector('[data-action="detail"]');
  const settle = () => new Promise(resolve=>setTimeout(resolve,0));
  button.click(); await settle();
  assert.equal(button.getAttribute('aria-expanded'),'true');
  assert.equal(button.textContent, '收起');
  assert.match(panel.textContent,/末帧结构与采样/);
  assert.match(panel.textContent,/-44.38227814/);
  const fieldNames = [...panel.querySelectorAll('dt')].map(node => node.textContent);
  assert.ok(fieldNames.includes('实际温度'));
  assert.ok(!fieldNames.includes('实际温度（K）'));
  assert.ok(!fieldNames.includes('实际温度证据'));
  button.click();
  assert.equal(button.getAttribute('aria-expanded'),'false');
  assert.equal(button.textContent, '详情');
  button.click(); await settle();
  assert.equal(reads,2);
  assert.match(panel.textContent,/-45.38227814/);
  button.click(); button.click(); await settle();
  assert.equal(button.textContent, '详情');
  assert.match(panel.textContent,/记录读回失败/);
  assert.doesNotMatch(panel.textContent,/-45.38227814/);
});

test('clicking export uses that result row, downloads immediately, and clears links if a repeat fails', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const base = '/api/users/user/sessions/chat-a/aidb';
  const downloads = [];
  const records = [{record_id:'si-1', formula_reduced:'Si'}, {record_id:'si-2', formula_reduced:'Si'}];
  let attempts = 0;
  const fetchImpl = async (url, options) => {
    if (url === base + '/results/query') return {ok:true, json:async()=>({records})};
    if (url === base + '/records/si-2?binding_id=bound') {
      return {ok:true, json:async()=>({record:records[1]})};
    }
    assert.equal(url, base + '/results/query/records/si-2/export');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), {binding_id:'bound'});
    attempts++;
    if (attempts === 2) {
      return {ok:false, status:409, json:async()=>({detail:{message:'本次记录读回失败'}})};
    }
    if (attempts === 3) {
      return {ok:true, json:async()=>({kind:'aidb_export', export_id:'partial', record_id:'si-2',
        files:{poscar:{filename:'POSCAR-si-2'}}, issues:{extxyz:'缺少逐原子力'}})};
    }
    return {ok:true, json:async()=>({kind:'aidb_export', export_id:'first', record_id:'si-2',
      files:{extxyz:{filename:'aidb-si-2.extxyz'}}, issues:{}})};
  };
  const panel = createAIDBResult({user_id:'user',session_id:'chat-a',result_id:'query',binding_id:'bound'}, {fetchImpl});
  panel.addEventListener('click', event => {
    if (event.target.matches('a[download]')) downloads.push(event.target.getAttribute('href'));
  });
  document.body.append(panel);
  await panel.ready;
  const button = panel.querySelector('[data-action="export"][data-id="si-2"]');
  const otherButton = panel.querySelector('[data-action="export"][data-id="si-1"]');
  const detailButton = panel.querySelector('[data-action="detail"][data-id="si-2"]');
  const controlledRow = control => document.getElementById(control.getAttribute('aria-controls'));
  const settle = () => new Promise(resolve=>setTimeout(resolve,0));

  detailButton.click(); await settle();
  const detailRow = controlledRow(detailButton);
  const exportRow = controlledRow(button);
  assert.equal(button.textContent, '导出');
  assert.equal(button.getAttribute('aria-expanded'), 'false');
  assert.ok(exportRow?.hidden);
  const summaryRow = button.closest('tr');
  assert.equal(summaryRow.nextElementSibling, exportRow);
  assert.equal(exportRow.firstElementChild.getAttribute('colspan'), String(summaryRow.children.length));

  button.click();
  assert.equal(button.getAttribute('aria-expanded'), 'true');
  assert.equal(exportRow.hidden, false);
  assert.match(exportRow.textContent, /准备下载/);
  assert.doesNotMatch(summaryRow.textContent, /准备下载/);
  await settle();
  assert.deepEqual(downloads, [base + '/exports/first/extxyz']);
  assert.match(exportRow.textContent, /下载微调数据（EXTXYZ）/);
  assert.equal(summaryRow.querySelector('a[download]'), null);
  assert.ok(controlledRow(otherButton).hidden);
  button.click(); await settle();
  assert.match(exportRow.querySelector('[role="alert"]').textContent, /本次记录读回失败/);
  assert.equal(panel.querySelector('a[download]'), null);
  button.click(); await settle();
  assert.match(exportRow.textContent, /缺少逐原子力/);
  assert.equal(exportRow.querySelector('a[download]').getAttribute('href'), base + '/exports/partial/poscar');
  assert.equal(detailButton.getAttribute('aria-expanded'), 'true');
  assert.equal(detailRow.hidden, false);
  assert.match(detailRow.textContent, /末帧结构与采样/);
  assert.equal(downloads.length, 1);
  assert.equal(attempts, 3);
});

test('a relaxation choice shows reply ordinals and only offers details', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const records = [{record_id:'run-b', formula_reduced:'Si'}, {record_id:'run-a', formula_reduced:'Si'}];
  const panel = createAIDBResult({user_id:'user', session_id:'chat-a', result_id:'choice', binding_id:'bound'},
    {fetchImpl: async () => ({ok:true, json:async()=>({operation:'relaxation_candidates', records})})});
  await panel.ready;

  assert.match(panel.textContent, /2 条完全匹配/);
  const buttons = [...panel.querySelectorAll('[data-action="detail"]')];
  assert.deepEqual(buttons.map(button => button.dataset.id), ['run-b', 'run-a']);
  assert.match(buttons[0].closest('tr').textContent, /第 1 条/);
  assert.match(buttons[1].closest('tr').textContent, /第 2 条/);
  assert.equal(panel.querySelector('[data-action="export"]'), null);
});

test('MD detail preserves planned steps, Kelvin units and the reason an observation is unavailable', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const record = {record_id:'md-1', formula_reduced:'Si', calculation_type:'molecular_dynamics',
    source_metadata:{vasp_facts:{sampling:{ensemble:'NVT', target_start_kelvin:300, target_end_kelvin:600,
      observed_temperature_kelvin:null, step_fs:1, source_ionic_steps:3, planned_ionic_steps:3,
      unknown_reasons:{observed_temperature_kelvin:'OUTCAR 末步缺少唯一可核实的瞬时温度'}}}}};
  const fetchImpl = async url => ({ok:true, json:async()=>url.includes('/results/')
    ? {records:[record]} : {record}});
  const panel = createAIDBResult({user_id:'user',session_id:'chat-a',result_id:'q',binding_id:'bound'},
    {fetchImpl, mountStructures:()=>{}});
  await panel.ready;
  panel.querySelector('[data-action="detail"]').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  const field = name => [...panel.querySelectorAll('dt')].find(node=>node.textContent===name)?.nextElementSibling.textContent;
  assert.equal(field('计划积分步数'), '3');
  assert.equal(field('设定温度'), '300 → 600 K');
  assert.equal(field('实际温度（K）'), '未知／未保存 · OUTCAR 末步缺少唯一可核实的瞬时温度');
});

test('MatPES candidate rows show source index and keep detail and export read-only', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const base = '/api/users/user/sessions/chat-a/aidb';
  const record = {record_id:'nacl-2', formula_reduced:'ClNa', calculation_type:'matpes'};
  const requests = [];
  const downloads = [];
  const fetchImpl = async (url, options) => {
    requests.push([url, options?.method || 'GET']);
    if (url === base + '/results/q') return {ok:true,json:async()=>({
      operation:'matpes_candidates', frame_index:2, records:[record], binding_id:'bound',
    })};
    if (url === base + '/records/nacl-2?binding_id=bound') return {ok:true,json:async()=>({record})};
    assert.equal(url, base + '/results/q/records/nacl-2/export');
    assert.equal(options.method, 'POST');
    return {ok:true,json:async()=>({
      kind:'aidb_export', record_id:'nacl-2', export_id:'data', files:{extxyz:{filename:'nacl.extxyz'}},
    })};
  };
  const panel = createAIDBResult({user_id:'user',session_id:'chat-a',result_id:'q',binding_id:'bound'},
    {fetchImpl, mountStructures:()=>{}});
  panel.addEventListener('click', event => {
    if (event.target.matches('a[download]')) downloads.push(event.target.getAttribute('href'));
  });
  document.body.append(panel);
  await panel.ready;
  const settle = () => new Promise(resolve=>setTimeout(resolve,0));
  assert.match(panel.textContent, /源帧 2/);
  const detail = panel.querySelector('[data-action="detail"]');
  detail.click(); await settle();
  assert.match(panel.textContent, /末帧结构与采样/);
  detail.click();
  assert.equal(detail.getAttribute('aria-expanded'), 'false');
  detail.click(); await settle();
  panel.querySelector('[data-action="export"]').click(); await settle();
  assert.deepEqual(requests.map(item=>item[1]), ['GET', 'GET', 'GET', 'POST']);
  assert.deepEqual(downloads, [base + '/exports/data/extxyz']);
});

test('EOS candidate details and downloads use the selected volume row', async () => {
  const { document } = parseHTML('<html><body></body></html>');
  globalThis.document = document;
  const base = '/api/users/user/sessions/chat-a/aidb';
  const record = {record_id:'si-v6', formula_reduced:'Si', calculation_type:'static'};
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push([url, options?.method || 'GET']);
    if (url === base + '/results/q') return {ok:true,json:async()=>({
      operation:'eos_candidates', eos_member:'6', records:[record], binding_id:'bound',
    })};
    if (url === base + '/records/si-v6?binding_id=bound') return {ok:true,json:async()=>({record})};
    assert.equal(url, base + '/results/q/records/si-v6/export');
    return {ok:true,json:async()=>({
      kind:'aidb_export', record_id:'si-v6', export_id:'download', files:{extxyz:{filename:'si-v6.extxyz'}},
    })};
  };
  const panel = createAIDBResult({user_id:'user',session_id:'chat-a',result_id:'q',binding_id:'bound'},
    {fetchImpl, mountStructures:()=>{}});
  const downloads = [];
  panel.addEventListener('click', event => {
    if (event.target.matches('a[download]')) downloads.push(event.target.getAttribute('href'));
  });
  document.body.append(panel);
  await panel.ready;
  assert.match(panel.textContent, /EOS 体积点 6/);
  panel.querySelector('[data-action="detail"]').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(panel.textContent, /末帧结构与采样/);
  panel.querySelector('[data-action="export"]').click();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.deepEqual(calls.map(item=>item[1]), ['GET', 'GET', 'POST']);
  assert.deepEqual(downloads, [base + '/exports/download/extxyz']);
});
