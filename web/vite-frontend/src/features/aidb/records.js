// Presentation promoted from the verified 4989f1b record rows.
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json = value => esc(JSON.stringify(value, null, 2));
const facts = r => r.source_metadata?.vasp_facts ?? {};
const md = type => type === 'molecular_dynamics';
const sampleLabel = type => ({molecular_dynamics:'分子动力学',relaxation:'结构弛豫',static:'静态计算',matpes:'MatPES 静态标注'}[type] ?? type ?? '未知');
const unknown = '未知／未保存';
let detailCount = 0;
const compact = s => s == null ? unknown : String(s);
function temperature(type, start, end) {
  if (type && !md(type)) return '不适用';
  if (start == null && end == null) return unknown;
  return start === end ? start + ' K' : compact(start) + ' → ' + compact(end) + ' K';
}
function field(name, value) {
  if (value == null || typeof value !== 'object') return '<dt>' + esc(name) + '</dt><dd>' + esc(value ?? unknown) + '</dd>';
  const preview = structureJson(value) ? '<div class="json-structure" data-label="' + esc(name) +
    '" data-json-structure="' + esc(JSON.stringify(value)) + '"></div>' : '';
  return '<dt>' + esc(name) + '</dt><dd>' + preview + jsonTree(value) + '</dd>';
}
function fields(rows) {
  return '<dl class="fields">' + rows.map(([k,v]) => field(k,v)).join('') + '</dl>';
}
function section(name, rows, open = false) {
  return '<details ' + (open ? 'open' : '') + '><summary>' + esc(name) + '</summary>' + fields(rows) + '</details>';
}
function jsonTree(value) {
  if (value === null || typeof value !== 'object') return '<code>' + esc(JSON.stringify(value)) + '</code>';
  const array = Array.isArray(value);
  const entries = Object.entries(value);
  return '<ul class="json-tree">' + entries.map(([key, item]) => {
    const label = '<code>' + esc(array ? key : JSON.stringify(key)) + '</code>';
    if (item === null || typeof item !== 'object') return '<li>' + label + ': ' + jsonTree(item) + '</li>';
    const count = Array.isArray(item) ? '[' + item.length + ']' : '{' + Object.keys(item).length + '}';
    return '<li><details><summary>' + label + ' ' + count + '</summary>' + jsonTree(item) + '</details></li>';
  }).join('') + '</ul>';
}
function structureJson(value) {
  const vector = values => Array.isArray(values) && values.length === 3 && values.every(Number.isFinite);
  const matrix = value?.lattice?.matrix;
  return Array.isArray(matrix) && matrix.length === 3 && matrix.every(vector) &&
    Array.isArray(value.sites) && value.sites.length > 0 &&
    value.sites.every(site => vector(site.abc) && Array.isArray(site.species) && site.species.length > 0);
}
// This script runs in a sandboxed iframe, outside the parent module's scope.
// Keep it as source text: serializing a function after Vite transforms it can
// leak development import helpers (or build-local bindings) into the iframe.
const matterVizBootstrap = String.raw`async function mountMatterViz(structure) {
  const el = document.getElementById('viewer');
  try {
    const url = 'https://cdn.jsdelivr.net/npm/matterviz-anywidget@0.8.0/build/matterviz';
    const style = document.createElement('link');
    style.rel = 'stylesheet';
    style.href = url + '.css';
    document.head.append(style);
    const {default: widget} = await import(url + '.js');
    const data = {widget_type: 'structure', structure, style: 'width: 100%; height: 100%;'};
    const model = {get: key => data[key], set: (key, value) => {data[key] = value;},
      on() {}, off() {}, save_changes() {}, send() {}};
    el.replaceChildren();
    widget.render({el, model});
  } catch {
    el.textContent = 'MatterViz 加载失败。结构 JSON 仍可查看。';
  }
}`;
function attachStructureViews(root) {
  root.querySelectorAll('[data-json-structure]').forEach(panel => {
    const structure = JSON.parse(panel.dataset.jsonStructure);
    if (!structureJson(structure)) return;
    const frame = document.createElement('iframe');
    frame.title = 'MatterViz · ' + panel.dataset.label;
    frame.setAttribute('sandbox', 'allow-scripts');
    frame.loading = 'lazy';
    const data = JSON.stringify(structure).replaceAll('<', '\\u003c');
    frame.srcdoc = '<!doctype html><meta charset="utf-8"><style>html,body,#viewer{height:100%;margin:0}' +
      'body{background:#141817;color:#ebeeec;font:14px system-ui}</style><div id="viewer">正在加载 MatterViz…</div>' +
      '<script>(' + matterVizBootstrap + ')(' + data + ');</' + 'script>';
    panel.replaceChildren(frame);
  });
}

function boolLabel(value) { return value == null ? unknown : value ? '已确认' : '未确认'; }
function spacegroups(record) {
  const f = facts(record);
  const initial = f.initial_structure;
  const roles = [{label:'末帧空间群', symbol:f.spacegroup ?? f.symmetry?.symbol, evidence:f.symmetry}];
  if (md(record.calculation_type)) roles.unshift({label:'起始空间群', symbol:initial?.spacegroup ?? initial?.symmetry?.symbol,
    evidence:initial?.symmetry});
  return roles.map(role => ({...role, value:role.symbol ? role.symbol +
    (role.evidence?.number ? '（' + role.evidence.number + '）' : '') : '未核实'}));
}
function recordTable(records, excluded = [], canExport = true, candidate = false, exportCandidate = false) {
  const items = [...records.map(record => ({record, reasons: []})), ...excluded];
  if (!items.length) return '<div class="empty">没有匹配记录。</div>';
  return '<div class="scroll"><table class="table"><thead><tr><th>材料与构型</th><th>采样条件</th><th>标签方法</th><th>训练标签</th><th></th></tr></thead><tbody>' +
    items.map(({record:r,reasons}, index) => {
      const f=facts(r), s=f.sampling ?? {}, id=esc(r.record_id), type=r.calculation_type;
      const panel='record-detail-' + ++detailCount;
      const downloads='record-export-' + detailCount;
      const sample = f.source?.sample_name ?? f.source?.public_source?.split('/').at(-2) ?? r.record_id.slice(0,8);
      const symmetry = spacegroups(r).map(group => '<span class="spacegroup-role small">' + esc(group.label) +
        '：<span class="spacegroup-value' + (group.symbol ? '' : ' muted') + '">' + esc(group.value) + '</span></span>').join('');
      return '<tr><td>' + (candidate ? '<span class="small">第 ' + (index + 1) + ' 条</span> ' : '') + '<b>' + esc(r.formula_reduced) + '</b>' + symmetry +
        '<span class="sample-source small muted">' + esc(sample) + '</span>' +
        (reasons.length ? '<br><span class="warn small">未命中：' + esc(reasons.join('；')) + '</span>' : '') +
        '</td><td>' + esc(sampleLabel(type)) + '<br>系综：' + esc(md(type) ? (s.ensemble ?? unknown) : type ? '不适用' : unknown) +
        '<br>设定：' + esc(temperature(type,s.target_start_kelvin,s.target_end_kelvin)) +
        '</td><td>' + esc(r.functional ?? unknown) + '</td><td>' + ['energy','forces','virial'].map((k,i) =>
          '<span class="chip">' + ['能量','力','维里'][i] + ' ' + (r[k] == null ? '缺' : '有') + '</span>').join('') +
        '</td><td class="record-actions"><button data-action="detail" data-id="' + id +
        '" aria-expanded="false" aria-controls="' + panel + '">详情</button>' +
        (candidate && !exportCandidate ? '' : '<br><button data-action="export" data-id="' + id + '" aria-expanded="false" aria-controls="' +
          downloads + '" ' + (canExport ? '' : 'disabled title="导出尚未就绪"') + '>导出</button>') + '</td></tr>' +
        (candidate && !exportCandidate ? '' : '<tr class="record-inline" id="' + downloads + '" hidden><td colspan="5">' +
          '<div class="record-exports" aria-live="polite"></div></td></tr>') +
        '<tr class="record-inline" id="' + panel + '" hidden><td colspan="5"></td></tr>';
    }).join('') + '</tbody></table></div>';
}
function coverage(c) {
  if (!c) return '';
  return '<p><b>' + c.records + '</b> 条记录，<b>' + c.verified_frames + '</b> 个可核实末帧' +
    (c.unverified_records ? '，另有 ' + c.unverified_records + ' 条记录的帧身份无法核实' : '') +
    '。</p><div>同时有能量和力：' + c.energy_force_frames + ' 帧</div><div>其中也有维里张量：' +
    c.also_virial_frames + ' 帧</div>';
}
function detail(r) {
  const f=facts(r), s=f.sampling ?? {}, m=f.method ?? {}, l=f.labels ?? {}, source=f.source ?? {};
  const type=r.calculation_type, meta=r.source_metadata ?? {}, st=r.structure ?? {}, params=r.calculation_parameters ?? {};
  const initial = f.initial_structure;
  const roles = spacegroups(r);
  const samplingValue = key => s[key] ?? (s.unknown_reasons?.[key] ? unknown + ' · ' + s.unknown_reasons[key] : null);
  const temperatureReasons = [s.unknown_reasons?.target_start_kelvin, s.unknown_reasons?.target_end_kelvin].filter(Boolean);
  const configuredTemperature = [temperature(type,s.target_start_kelvin,s.target_end_kelvin), ...temperatureReasons].join(' · ');
  const inactiveSpin=m.spin?.ISPIN === 1 && m.spin?.LNONCOLLINEAR === false && m.spin?.LSORBIT === false;
  return '<p><b>' + esc(r.formula_reduced) + ' · ' + esc(sampleLabel(type)) + '</b></p><p class="small mono">' + esc(r.record_id) + '</p>' +
    section('末帧结构与采样', [['末帧晶相',f.phase],['末帧空间群',roles.at(-1).value],['末帧判定依据',f.symmetry],
      ['原子数',r.nsites],['晶格（Å）',st.lattice?.matrix],
      ['有序分数坐标',st.sites?.map(site => [site.label,...site.abc])],['末帧晶体结构',st],['采样类型',sampleLabel(type)],
      ['MD 系综',md(type) ? samplingValue('ensemble') : type ? '不适用' : null],['设定温度',configuredTemperature],
      [md(type) ? '实际温度（K）' : '实际温度',md(type) ? samplingValue('observed_temperature_kelvin') : type ? '不适用' : null],
      ['时间步长（fs）',md(type) ? samplingValue('step_fs') : type ? '不适用' : null],
      ['晶胞自由度',samplingValue('cell_mode')],['源 XML 离子步数',s.source_ionic_steps],
      ...(md(type) ? [['计划积分步数',s.planned_ionic_steps]] : []),['入库范围','末帧（1 帧）'],['帧索引（从 0 起）',r.frame_index]], true) +
    (md(type) ? section('MD 起始构型', [['起始空间群',roles[0].value],['空间群判定',initial?.symmetry],
      ['起始结构来源',initial?.source],['判定状态',initial?.unknown_reason ?? (initial?.spacegroup ? '已核实' : unknown)],
      ['晶格（Å）',initial?.structure?.lattice?.matrix],
      ['有序分数坐标',initial?.structure?.sites?.map(site => [site.label,...site.abc])],['起始晶体结构',initial?.structure]], true) : '') +
    section('计算方法与磁性', [['程序版本',m.version ?? meta.version],['泛函',r.functional],['判定依据',m.functional_evidence],
      ['泛函输入回显',m.functional_input],['泛函运行参数',m.functional_runtime],
      ['DFT+U 设置',params.LDAU == null ? null : params.LDAU ? '启用' : '未启用'],['U 参数',r.hubbard_u],
      ['赝势',m.potcar_symbols ?? meta.potcar_symbols],['k 点',m.kpoints ?? meta.kpoints],['输入截断能（eV）',m.input_encut_ev ?? params.ENCUT],
      ['自旋计算设置',m.spin],['材料磁性',m.magnetism ?? '未评估'],['逐原子投影磁矩',r.magmom ?? (inactiveSpin ? '不适用' : null)]]) +
    section('能量、力、应力和维里张量', [['电子总能',r.energy == null ? null : r.energy + ' eV/胞'],
      ['能量定义',l.energy_definition],['逐原子力（eV/Å）',r.forces],['应力',r.stress],['应力单位与符号',
        r.stress == null ? null : [r.stress_unit,r.stress_convention].filter(Boolean).join(' · ')],
      ['维里张量',r.virial],['维里张量单位',r.virial_unit],['末步电子收敛',boolLabel(l.electronic_converged)],
      ['弛豫离子收敛',type === 'relaxation' ? boolLabel(l.ionic_converged) : '不适用']]) +
    section('附加结果', [['密度（g/cm³）',f.density_g_cm3],['费米能（eV）',f.fermi_energy_ev],
      ['逐原子 PAW 投影电子布居（e/原子）',f.paw_population_e_per_atom]]) +
    section('来源与核对依据', [['来源数据集',r.source_dataset],['来源 ID',r.source_id],
      ['原始文件 SHA-256',source.file_sha256 ?? source.sha256 ?? (meta.sha256 ? {'vasprun.xml':meta.sha256} : null)],
      ['XML 与 OUTCAR 配对检查',source.xml_outcar_checks],['系综相关输入',s.input_evidence],
      ['系综判定',s.ensemble_evidence],...(md(type) ? [['实际温度证据',s.temperature_evidence]] : []),
      ['字段来源',source.field_evidence],['公开原始输出',source.public_source],
      ...(type === 'matpes' ? [['标注输入来源',source.labeling_input]] : []),
      ...(source.eos_member ? [['EOS 成员',source.eos_member]] : [])]) +
    '<details><summary>完整 JSON · 本次 skill 返回的记录</summary>' + jsonTree(r) + '</details>';
}

export function aidbResults(payload) {
  const found = new Map();
  const visit = value => {
    if (!value || typeof value !== 'object') return;
    const id = ['aidb_result', 'aidb_completion'].includes(value.kind) ? value.result_id : value.kind === 'aidb_export' ? value.export_id : null;
    if (id && value.session_id) {
      found.set(value.kind + ':' + value.session_id + ':' + id, value);
      return;
    }
    Object.values(value).forEach(visit);
  };
  visit(payload);
  return [...found.values()];
}

const downloadLabels = {
  extxyz: '下载微调数据（EXTXYZ）',
  poscar: '下载 POSCAR',
  html: '下载完整详情（HTML）',
  temperature: '下载 AIMD 温度曲线（HTML）',
  'bands.json': '下载能带数据（JSON）',
  'bands.svg': '下载能带图（SVG）',
};

function sessionEndpoint(reference) {
  return '/api/users/' + encodeURIComponent(reference.user_id) + '/sessions/' +
    encodeURIComponent(reference.session_id) + '/aidb';
}

function showDownloads(container, base, receipt) {
  container.replaceChildren();
  const links = {};
  for (const [fmt, file] of Object.entries(receipt.files || {})) {
    const label = downloadLabels[fmt] || file.filename;
    const link = document.createElement('a');
    link.href = base + '/exports/' + encodeURIComponent(receipt.export_id) + '/' + fmt;
    link.download = file.filename;
    link.textContent = label;
    container.append(link);
    links[fmt] = link;
  }
  for (const issue of Object.values(receipt.issues || {})) {
    const message = document.createElement('p');
    message.textContent = issue;
    container.append(message);
  }
  return links;
}

export function createAIDBResult(reference, {fetchImpl = globalThis.fetch, mountStructures = attachStructureViews} = {}) {
  const root = document.createElement('section');
  root.className = 'aidb-result';
  const base = sessionEndpoint(reference);
  const completionPath = reference.kind === 'aidb_completion'
    ? '/completions/' + encodeURIComponent(reference.result_id) : null;
  let bindingId = reference.binding_id;
  if (reference.kind === 'aidb_export') {
    root.setAttribute('aria-label', 'AIDB 记录导出');
    const title = document.createElement('p');
    title.textContent = 'AIDB 记录导出 · ' + reference.record_id;
    const downloads = document.createElement('div');
    downloads.className = 'record-exports';
    showDownloads(downloads, base, reference);
    root.append(title, downloads);
    return root;
  }
  root.setAttribute('aria-label', 'AIDB 查询结果');
  root.textContent = '正在读取 AIDB 查询结果…';
  const request = async (path, options) => {
    const response = await fetchImpl(base + path, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail?.message || payload.detail || 'AIDB 读取失败（' + response.status + '）');
    return payload;
  };
  const exportRow = async button => {
    const row = root.querySelector('#' + button.getAttribute('aria-controls'));
    const container = row.querySelector('.record-exports');
    button.disabled = true;
    button.setAttribute('aria-expanded', 'true');
    row.hidden = false;
    container.textContent = '正在按记录 ID 重新读取并准备下载…';
    try {
      const receipt = await request((completionPath ?? '/results/' + encodeURIComponent(reference.result_id)) +
        '/records/' + encodeURIComponent(button.dataset.id) + '/export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({binding_id: bindingId}),
      });
      if (receipt.kind !== 'aidb_export' || receipt.record_id !== button.dataset.id || !receipt.export_id) {
        throw new Error('导出回执与所选记录不一致');
      }
      const links = showDownloads(container, base, receipt);
      links.extxyz?.click();
    } catch (error) {
      const failure = document.createElement('p');
      failure.setAttribute('role', 'alert');
      failure.textContent = '导出尚未就绪：' + error.message;
      container.replaceChildren(failure);
    } finally {
      button.disabled = false;
    }
  };
  const open = async button => {
    const row = root.querySelector('#' + button.getAttribute('aria-controls'));
    const content = row.firstElementChild;
    if (button.getAttribute('aria-expanded') === 'true') {
      button.setAttribute('aria-expanded', 'false');
      button.textContent = '详情';
      row.hidden = true;
      content.replaceChildren();
      return;
    }
    button.disabled = true;
    content.textContent = '正在按记录 ID 重新读取…';
    row.hidden = false;
    try {
      const result = await request('/records/' + encodeURIComponent(button.dataset.id) + '?binding_id=' + encodeURIComponent(bindingId));
      content.innerHTML = detail(result.record);
      mountStructures(content);
      button.setAttribute('aria-expanded', 'true');
      button.textContent = '收起';
    } catch (error) {
      content.replaceChildren();
      const failure = document.createElement('p');
      failure.setAttribute('role', 'alert');
      failure.textContent = '详情未能读回：' + error.message;
      content.append(failure);
      button.setAttribute('aria-expanded', 'false');
      button.textContent = '详情';
    } finally {
      button.disabled = false;
    }
  };
  root.addEventListener('click', event => {
    const button = event.target.closest('button[data-action]');
    if (!button || button.disabled) return;
    if (button.dataset.action === 'detail') void open(button);
    if (button.dataset.action === 'export') void exportRow(button);
  });

  if (completionPath) {
    root.setAttribute('aria-label', 'VASP 计算结果');
    root.textContent = '正在读取计算结果…';
    let revision = 0;
    let refreshable = true;
    const seen = new WeakSet([reference]);
    const refresh = () => {
      const current = ++revision;
      root.ready = request(completionPath).then(result => {
        if (current !== revision) return;
        if (result.kind !== 'aidb_completion' || result.result_id !== reference.result_id) {
          throw new Error('完成回执与本次作业不一致');
        }
        bindingId = result.binding_id;
        refreshable = result.archive_status !== 'verified' || Boolean(result.readback_issue);
        const compute = result.compute_status === 'succeeded' ? '计算已完成' : '计算产物已回收，结果未核实';
        const archive = {
          pending: '等待归档核实',
          failed: '归档未核实',
          verified: '已归档' + (result.outcome === 'added' ? ' · 新增记录' :
            result.outcome === 'existing' ? ' · 已有记录' : ''),
        }[result.archive_status];
        const summary = [
          result.summary?.formula,
          result.summary?.energy_ev_per_cell != null ? result.summary.energy_ev_per_cell + ' eV/胞' : '',
          result.eos_member ? (result.eos_member.member_id === 'relaxation' ? 'EOS 初始弛豫' : 'EOS 体积点 ' + result.eos_member.member_id) : '',
          result.labeling_input?.frame_index != null ? '源帧 ' + result.labeling_input.frame_index : '',
          result.summary?.frame_index != null ? '帧索引 ' + result.summary.frame_index : '',
        ].filter(Boolean).join(' · ');
        const artifacts = (result.artifact_paths || []).map(path =>
          '<a download href="' + esc(base + completionPath + '/artifact?' + new URLSearchParams({path})) +
            '">' + esc(downloadLabels[path.split('/').at(-1)] || path.split('/').at(-1)) + '</a>').join('');
        root.innerHTML = '<p><b>' + esc(compute) + '</b> · ' + esc(archive) + '</p>' +
          (summary ? '<p class="small">' + esc(summary) + '</p>' : '') +
          (result.reason ? '<p class="warn">' + esc(result.reason) + '</p>' : '') +
          (result.artifact_delivery_issue ? '<p class="warn">计算产物：' + esc(result.artifact_delivery_issue) + '</p>' : '') +
          (result.readback_issue ? '<p class="warn" role="alert">详情未能读回：' + esc(result.readback_issue) + '</p>' : '') +
          (artifacts ? '<div class="record-exports" aria-label="计算产物">' + artifacts + '</div>' : '') +
          (result.records.length ? recordTable(result.records) :
            '<button type="button" disabled>详情</button> <button type="button" data-action="export" disabled>导出</button>') +
          (refreshable ? ' <button type="button" data-action="refresh-completion">刷新归档状态</button>' : '');
      }).catch(error => {
        if (current !== revision) return;
        root.querySelector('[data-completion-error]')?.remove();
        const failure = document.createElement('p');
        failure.setAttribute('role', 'alert');
        failure.dataset.completionError = '';
        failure.textContent = '计算结果未能读取：' + error.message;
        root.append(failure);
        if (!root.querySelector('[data-action="refresh-completion"]')) {
          const retry = document.createElement('button');
          retry.type = 'button';
          retry.dataset.action = 'refresh-completion';
          retry.textContent = '刷新归档状态';
          root.append(retry);
        }
      });
    };
    root.observeCompletion = update => {
      if (seen.has(update)) return;
      seen.add(update);
      if (refreshable) refresh();
    };
    root.addEventListener('click', event => {
      if (event.target.closest('[data-action="refresh-completion"]')) refresh();
    });
    refresh();
    return root;
  }
  root.ready = request('/results/' + encodeURIComponent(reference.result_id)).then(result => {
    if (result.operation === 'matpes_candidates') {
      root.setAttribute('aria-label', 'MatPES 完全匹配候选');
      root.innerHTML = '<p><b>源帧 ' + esc(result.frame_index) + ' · ' +
        result.records.length + ' 条完全匹配</b></p>' + recordTable(result.records, [], true, true, true);
      return;
    }
    if (result.operation === 'eos_candidates') {
      root.setAttribute('aria-label', 'EOS 完全匹配候选');
      const member = result.eos_member === 'relaxation' ? '初始弛豫' : '体积点 ' + esc(result.eos_member);
      root.innerHTML = '<p><b>EOS ' + member + ' · ' +
        result.records.length + ' 条完全匹配</b></p>' + recordTable(result.records, [], true, true, true);
      return;
    }
    if (result.operation === 'relaxation_candidates') {
      root.setAttribute('aria-label', 'VASP 完全匹配候选');
      root.innerHTML = '<p><b>' + result.records.length + ' 条完全匹配</b></p>' +
        recordTable(result.records, [], false, true);
      return;
    }
    const conditions = result.conditions || result.report?.conditions || {};
    root.innerHTML = '<p class="small muted">实际查询条件：<code>' + esc(JSON.stringify(conditions)) + '</code> · AIDB 返回 ' +
      esc(result.report?.match_count ?? result.records.length) + ' 条' +
      (Object.keys(result.host_filter || {}).length ? ' · 按结构角色与设定温度核对后 ' + result.records.length + ' 条' : '') + '</p>' +
      (result.duplicates ? '<p class="small muted">同一来源的末帧合并显示，折叠 ' + result.duplicates + ' 条重复记录。</p>' : '') +
      coverage(result.coverage) + recordTable(result.records);
    if (result.expanded_record_id) {
      const button = [...root.querySelectorAll('[data-action="detail"]')].find(b => b.dataset.id === result.expanded_record_id);
      if (button) return open(button);
    }
  }).catch(error => {
    root.replaceChildren();
    const failure = document.createElement('p');
    failure.setAttribute('role', 'alert');
    failure.textContent = '查询结果未能读取：' + error.message;
    root.append(failure);
  });
  return root;
}

export { detail as recordDetailHTML, recordTable as recordTableHTML };
