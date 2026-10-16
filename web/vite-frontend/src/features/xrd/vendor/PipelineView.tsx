import { RunHistory } from './RunHistory';
import { RunTimer, type Timing } from './RunTimer';
import { readFrameInfo } from './frameInfo';
import {ActivityLine,ActionIcon} from './WorkflowVisuals';
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { Settings2, type PluginViewProps } from '../sdk';
import type { Result } from './index';
import { MatchControls, Glyph, FocusLayer } from './MatchControls';
import { codCandidateId } from './CandidateStructure';
import './pipeline.css';
import { FrameTimeline, useCursor, selectedFrame, selectFrameCandidate, setParameterMode, type FrameSet } from './FrameCanvas';
import { InputDock } from './InputDock';
import { MultiphaseButton, SinglePhaseButton, MultiphaseGlow, MultiphasePanel, useMultiphase } from './Multiphase';
import { publishMultiphaseState, retainMultiphasePublisher, setMultiphaseSelection, useMultiphaseCanvas } from './MultiphaseCanvasState';

type Stage = 'search' | 'preopt' | 'fit';
type Progress = { percent: number; stage: string; indeterminate?: boolean; completed?: number; total?: number };
type Cif = { filename: string; source_base64: string; sha256?: string };
export type PipelineCandidate = {
  candidate_id: string; label: string; status: 'completed' | 'failed' | 'rejected'; error?: string;
  accepted?: boolean; converged?: boolean; metrics?: { rp_percent?: number; rwp_percent?: number };
  cell_before?: number[]; cell_after?: number[]; source_sha256?: string; starting_sha256?: string;
  output_cif?: Cif; report?: Record<string, unknown>; plot?: { observed: number[][]; calculated: number[][] };
};
export type PipelineResult = { mode: 'pipeline'; stage: Stage; match_run_id: string; preopt_run_id?: string; candidates: PipelineCandidate[]; interpretation?: string };
type StageRun = { run_id: string; status: string; result?: PipelineResult; previous_success?: { run_id: string; status: string; result: PipelineResult } };
type Workflow = { match_run_id?: string; active_stage?: Stage; preopt?: StageRun; fit?: StageRun };
type WorkflowProps = PluginViewProps & { renderResult(result: Result): ReactNode; renderParameters(result: Result): ReactNode };
const steps = ['参数设置', '匹配结果', '结构预优化', '全谱拟合'];
const labels: Record<Stage, string> = { search: '检索', preopt: '结构预优化', fit: '全谱拟合' };
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const display = (value: unknown, digits = 3) => finite(value) ? value.toFixed(digits) : '—';
const stageStep = (stage: Stage) => stage === 'fit' ? 3 : stage === 'preopt' ? 2 : 1;

function StageNumber({ name, label, fallback, min, max, integer, config, schema, disabled, save, invalid }: {
  name: string; label: string; fallback: number; min: number; max: number; integer?: boolean;
  config: Record<string, unknown>; schema: Record<string, unknown>; disabled: boolean;
  save(patch: Record<string, unknown>): void; invalid(name: string, error: string): void;
}) {
  const rule = ((schema.properties as Record<string, Record<string, unknown>> | undefined)?.[name]) ?? {};
  const actual = config[name] ?? rule.default ?? fallback;
  const [value, setValue] = useState(String(actual));
  useEffect(() => setValue(String(actual)), [actual]);
  const minimum = finite(rule.minimum) ? rule.minimum : min;
  const maximum = finite(rule.maximum) ? rule.maximum : max;
  const commit = () => {
    const number = Number(value);
    const error = !value.trim() || !finite(number) || number < minimum || number > maximum || (integer && !Number.isInteger(number))
      ? `${label}需为 ${minimum}–${maximum} 范围内的${integer ? '整数' : '数值'}` : '';
    invalid(name, error);
    if (!error && number !== Number(actual)) save({ [name]: number });
  };
  return <label className="xrd-number-field">{label}<input aria-label={label} type="number" min={minimum} max={maximum} step={integer ? '1' : 'any'} disabled={disabled} value={value} onChange={event => setValue(event.target.value)} onBlur={commit}/></label>;
}

function CandidateSelection({ result, selected, onChange, disabled }: { result: Result; selected: string[]; onChange(ids: string[]): void; disabled: boolean }) {
  return <section className="xrd-candidate-selection" aria-label="后续优化候选">
    <header><div><strong>候选核验</strong></div><span>{selected.length} / 10</span></header>
    <div className="xrd-candidate-grid">{result.candidates?.map((candidate, index) => {
      const checked = selected.includes(candidate.node_id);
      const available = Boolean(codCandidateId(candidate.metadata) || candidate.cifs?.length);
      return <label key={candidate.node_id} className={`xrd-candidate-choice ${checked ? 'is-selected' : ''}`}>
        <input type="checkbox" aria-label={`选择候选 ${candidate.filename}`} checked={checked} disabled={disabled || (!checked && selected.length >= 10)} onChange={() => onChange(checked ? selected.filter(id => id !== candidate.node_id) : [...selected, candidate.node_id])}/>
        <span className="xrd-candidate-rank">{String(index + 1).padStart(2, '0')}</span>
        <span><strong>{candidate.metadata.formula || candidate.filename}</strong><small>{candidate.filename} · {candidate.matches.length}/{candidate.reference_count} 峰匹配</small><small>{available ? (codCandidateId(candidate.metadata) ? '检索后自动缓存 COD CIF' : '已关联 CIF') : '尚未关联 CIF，运行时将保留失败原因'}</small></span>
      </label>;
    })}</div>
  </section>;
}

function FitPlot({ plot }: { plot: NonNullable<PipelineCandidate['plot']> }) {
  const observed = plot.observed.filter(point => point.length >= 2 && point.every(finite));
  const calculated = plot.calculated.filter(point => point.length >= 2 && point.every(finite));
  if (!observed.length || !calculated.length) return null;
  const bounds = [...observed, ...calculated].reduce((range, point) => [Math.min(range[0], point[0]), Math.max(range[1], point[0]), Math.min(range[2], point[1]), Math.max(range[3], point[1])], [Infinity, -Infinity, 0, -Infinity]);
  const [lo, hi, low, high] = bounds;
  if (hi <= lo || high <= low) return null;
  const x = (value: number) => 40 + (value - lo) / (hi - lo) * 600;
  const y = (value: number) => 188 - (value - low) / (high - low) * 165;
  const line = (points: number[][]) => points.map(p => `${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join(' ');
  return <div className="xrd-fit-plot"><svg viewBox="0 0 670 230" role="img" aria-label="实验谱与全谱拟合结果"><line x1="40" x2="640" y1="188" y2="188" stroke="currentColor" opacity=".4"/><polyline points={line(observed)} fill="none" stroke="var(--xrd-observed, #b9d8dd)" strokeWidth="1.2"/><polyline points={line(calculated)} fill="none" stroke="var(--xrd-calculated, #e99b71)" strokeWidth="1.2"/>{Array.from({ length: 6 }, (_, i) => lo + (hi - lo) * i / 5).map(value => <text key={value} x={x(value)} y="210" fill="currentColor" fontSize="11" textAnchor="middle">{value.toFixed(1)}</text>)}<text x="335" y="228" fill="currentColor" fontSize="11" textAnchor="middle">2θ / ° · 实验与计算使用同一强度尺度</text></svg><small>蓝：实验谱　铜：计算谱</small></div>;
}

function CifActions({ candidate, host }: { candidate: PipelineCandidate; host: PluginViewProps['host'] }) {
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const cif = candidate.output_cif;
  if (!cif) return null;
  const open = async () => {
    setBusy(true); setError('');
    try {
      if (host.openLinkedCanvas) {
        await host.openLinkedCanvas('xrd.structure-canvas', 'XRD · 结构画布');
        return;
      }
      if (!host.openInputNode) throw new Error('请刷新页面以启用独立结构画布');
      await host.openInputNode('xrd.cif', `${candidate.label} · ${candidate.accepted === false ? '原始候选' : '结构输出'}`, { filename: cif.filename, source_base64: cif.source_base64 }, 'xrd.input');
    } catch (reason) { setError(String(reason)); } finally { setBusy(false); }
  };
  return <><div className="xrd-pipeline-cif"><a href={`data:chemical/x-cif;base64,${cif.source_base64}`} download={cif.filename} aria-label={`下载 ${candidate.label} CIF`} title="下载 CIF"><CifIcon name="download"/></a><button type="button" disabled={busy} aria-busy={busy} aria-label="打开结构画布" title={busy ? '正在打开…' : '打开结构画布并连接'} onClick={() => void open()}><CifIcon name="canvas"/></button></div>{error && <p role="alert">{error}</p>}</>;
}

function CifIcon({ name }: { name: 'download' | 'canvas' }) {
  return <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{name === 'download' ? <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/> : <><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m7 15 4-7 6 8-10-1Z"/><circle cx="11" cy="8" r="1"/></>}</svg>;
}

function PipelineOutput({ run, stage, host, source }: { run?: StageRun; stage: 'preopt' | 'fit'; host: PluginViewProps['host']; source:string }) {
  useCursor(source);
  const result = run?.result ?? run?.previous_success?.result;
  const [chosen, setChosen] = useState('');
  if (!result) return <div className="xrd-pipeline-empty"><strong>{stage === 'preopt' ? '从候选结构出发，约束晶胞与原子坐标' : '分别拟合，比较所有候选的全谱残差'}</strong><small>{stage === 'preopt' ? '原始 CIF 保留不变；被拒绝的预优化会回退到原始结构，失败候选不会进入全谱拟合。' : '全谱拟合使用当前预优化批次的结构与实验谱。迭代完成与达到收敛判据分别显示。'}</small></div>;
  const candidate = result.candidates.find(item => item.candidate_id === (selectedFrame(source)?.candidate_id ?? chosen)) ?? result.candidates[0];
  return <section className="xrd-pipeline-output" aria-label={stage === 'preopt' ? '结构预优化结果' : '全谱拟合比较'}>
    {!run?.result && <p className="xrd-note">以下为上次成功运行的结果。本次未生成新结果，后续流程需重新运行当前步骤。</p>}
    <div className="xrd-table"><table><thead><tr><th>候选结构</th><th>运行状态</th>{stage === 'preopt' ? <th>结构采用</th> : <><th title="在原始实验采样点上独立计算；w = 1 / max(Iobs, 1)，Rwp = 100 × √[Σw(Iobs−Icalc)² / ΣwIobs²]">Rwp / %</th><th title="在原始实验采样点上独立计算；Rp = 100 × Σ|Iobs−Icalc| / Σ|Iobs|">Rp / %</th><th>收敛</th></>}</tr></thead><tbody>{result.candidates.map(item => <tr key={item.candidate_id} aria-selected={candidate?.candidate_id === item.candidate_id}><td><button type="button" onClick={() => {setChosen(item.candidate_id);selectFrameCandidate(source,item.candidate_id);}}>{item.label}</button></td><td>{item.status === 'failed' ? '失败' : item.status === 'rejected' ? '预优化未接受' : '完成'}</td>{stage === 'preopt' ? <td>{item.status === 'failed' ? '不参与后续拟合' : item.accepted === false || item.status === 'rejected' ? '保留原始 CIF' : '采用优化 CIF'}</td> : <><td>{display(item.metrics?.rwp_percent)}</td><td>{display(item.metrics?.rp_percent)}</td><td>{item.status === 'failed' ? '—' : item.converged === true ? '达到判据' : item.converged === false ? '未达到判据' : '未提供'}</td></>}</tr>)}</tbody></table></div>

    {candidate && <div className="xrd-pipeline-detail" tabIndex={0} title={stage === 'fit' ? 'Rwp、Rp 在相同原始实验采样点上独立计算；Rwp 权重为 1 / max(Iobs, 1)。算法原始报告保留在下方运行依据中。' : undefined}><header><strong>{candidate.label}</strong></header>
      {candidate.error && <p role="alert">{candidate.error}</p>}
      {(candidate.cell_before?.length || candidate.cell_after?.length) ? <div className="xrd-table"><table aria-label="晶胞参数变化"><thead><tr><th>晶胞参数</th>{['a / Å', 'b / Å', 'c / Å', 'α / °', 'β / °', 'γ / °'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody><tr><td>输入</td>{Array.from({ length: 6 }, (_, i) => <td key={i}>{display(candidate.cell_before?.[i], 5)}</td>)}</tr><tr><td>输出</td>{Array.from({ length: 6 }, (_, i) => <td key={i}>{display(candidate.cell_after?.[i], 5)}</td>)}</tr></tbody></table></div> : null}
      {candidate.plot && <FitPlot plot={candidate.plot}/>}
      <details><summary>运行依据与结构来源</summary><dl className="xrd-pipeline-provenance"><dt>原始 CIF SHA256</dt><dd>{candidate.source_sha256 || '未提供'}</dd><dt>本步输入 SHA256</dt><dd>{candidate.starting_sha256 || '未提供'}</dd><dt>输出 CIF SHA256</dt><dd>{candidate.output_cif?.sha256 || '未提供'}</dd></dl>{candidate.report && <pre>{JSON.stringify(candidate.report, null, 2)}</pre>}</details>
    </div>}
  </section>;
}

export function PipelineWorkflow({ card, host, definition, renderResult, renderParameters }: WorkflowProps) {
  const [stageTiming, setStageTiming] = useState<Record<string, Timing>>({});
  const [settingsAnchor,setSettingsAnchor]=useState<HTMLButtonElement>();
  const [multiphaseActionContainer, setMultiphaseActionContainer] = useState<HTMLSpanElement | null>(null);
  const [frameSets, setFrameSets] = useState<Record<string,FrameSet>>({});
  const [step, setStep] = useState(0); const [result, setResult] = useState<Result>(); const [workflow, setWorkflow] = useState<Workflow>({});
  const sharedMultiphase = useMultiphaseCanvas(card.id);
  const multiphase = useMultiphase(host, workflow.match_run_id, sharedMultiphase);
  const [multiphaseStage, setMultiphaseStage] = useState<'search' | 'review'>(() => sharedMultiphase.selection.scope === 'review' || sharedMultiphase.selection.lane === 'pywpem' ? 'review' : 'search');
  useEffect(() => retainMultiphasePublisher(card.id), [card.id]);
  useEffect(() => { if (initialized.current) publishMultiphaseState(card.id, multiphase.state, multiphase.enabled, workflow.match_run_id); }, [card.id, multiphase.state, multiphase.enabled, workflow.match_run_id]);
  useEffect(()=>{setParameterMode(card.id,!multiphase.enabled && step===0);},[card.id,step,multiphase.enabled]);
  const [progress, setProgress] = useState<Progress>(); const [lastStatus, setLastStatus] = useState(''); const [activeStage, setActiveStage] = useState<Stage>('search');
  const [archive, setArchive] = useState(''); const [selected, setSelected] = useState<string[]>([]);
  const [starting, setStarting] = useState(false); const [error, setError] = useState('');
  const [discussionPending, setDiscussionPending] = useState(false);
  const [discussionError, setDiscussionError] = useState('');
  const [invalid, setInvalid] = useState<Record<string, string>>({}); const invalidRef = useRef<Record<string, string>>({});
  const [inputs, setInputs] = useState<Awaited<ReturnType<NonNullable<PluginViewProps['host']['getInputs']>>>>(); const [inputError, setInputError] = useState('');
  const queue = useRef(Promise.resolve()); const saveErrors = useRef<Record<string, string>>({});
  const pendingConfig = useRef<Record<string, unknown>>({});
  const selectedMatch = useRef(''); const initialized = useRef(false); const lastStamp = useRef(''); const launchLock = useRef(false);
  const stepId = useId(); const running = card.status === 'running' || card.status === 'waiting'; const busy = running || starting || multiphase.running;
  const configure = (patch: Record<string, unknown>) => {
    Object.assign(pendingConfig.current, patch);
    const key = Object.keys(patch).join(',');
    const task = queue.current.then(() => host.updateConfig(patch)).then(() => { delete saveErrors.current[key]; setError(Object.values(saveErrors.current).join('；')); }).catch(reason => { saveErrors.current[key] = String(reason); setError(String(reason)); });
    queue.current = task;
  };
  const reportInvalid = (name: string, message: string) => { invalidRef.current = { ...invalidRef.current, [name]: message }; setInvalid(invalidRef.current); };
  useEffect(() => {
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const info = await readFrameInfo(card.id,()=>host.getAgentInfo()); if (!alive) return;
        const details = info.details ?? {};
        setStageTiming((details.stage_timing ?? {}) as Record<string, Timing>);
        setFrameSets((details.frame_sets ?? {}) as Record<string,FrameSet>);
        const next = details.result as Result | undefined;
        const state = (details.workflow ?? {}) as Workflow;
        const last = details.last_run as { run_id?: string; id?: string; status?: string; workflow_stage?: Stage } | undefined;
        const active = state.active_stage ?? last?.workflow_stage ?? (card.config.workflow_stage as Stage) ?? 'search';
        const stamp = JSON.stringify([last?.run_id ?? last?.id, last?.status, state.match_run_id, state.preopt?.run_id, state.preopt?.status, state.fit?.run_id, state.fit?.status, Boolean(next)]);
        setWorkflow(state); setLastStatus(last?.status ?? ''); setActiveStage(active); setProgress(details.progress as Progress | undefined); setArchive(String(details.archive_url ?? details.archive ?? ''));
        if (next) {
          setResult(next);
          const matchKey = state.match_run_id || JSON.stringify(next.candidates?.map(item => item.node_id));
          if (selectedMatch.current !== matchKey) {
            const saved = card.config.workflow_match_run_id === state.match_run_id && Array.isArray(card.config.selected_candidate_ids) ? card.config.selected_candidate_ids as string[] : undefined;
            const ids = new Set(next.candidates?.map(candidate => candidate.node_id));
            setSelected((saved ?? next.candidates?.slice(0, 3).map(candidate => candidate.node_id) ?? []).filter(id => ids.has(id)).slice(0, 10)); selectedMatch.current = matchKey;
          }
        }
        if (!initialized.current) { setStep(state.fit ? 3 : state.preopt ? 2 : next ? 1 : 0); initialized.current = true; }
        else if (stamp !== lastStamp.current && last?.status && !['running', 'waiting'].includes(last.status)) setStep(stageStep(active));
        lastStamp.current = stamp;
      } catch (reason) { if (alive) setError(String(reason)); }
      finally { if (alive && (running || starting)) timer = setTimeout(load, 1000); }
    };
    void load(); return () => { alive = false; clearTimeout(timer); };
  }, [host, card.status, running, starting]);
  useEffect(() => {
    if (!host.getInputs) return;
    let alive = true; let timer: ReturnType<typeof setTimeout>;
    const load = async () => { try { const value = await host.getInputs!(); if (alive) { setInputs(value); setInputError(''); } } catch (reason) { if (alive) setInputError(String(reason)); } finally { if (alive) timer = setTimeout(load, 4000); } };
    void load(); return () => { alive = false; clearTimeout(timer); };
  }, [host]);
  const missing = inputs ? inputs.filter(item => item.kind === 'pattern').length !== 1 ? '请连接一个实验谱' : !inputs.some(item => item.ready && item.kind === 'pattern') ? '实验谱尚未导入' : !inputs.some(item => item.ready && ['reference', 'library'].includes(item.kind)) ? '请连接已导入的标准卡片或谱库' : inputs.some(item => !item.ready && item.kind === 'reference') ? '有标准卡片尚未导入峰表' : '' : host.getInputs ? '正在检查输入' : '';
  const matchId = workflow.match_run_id;
  const eligible = workflow.preopt?.result?.candidates.filter(candidate => candidate.status !== 'failed' && selected.includes(candidate.candidate_id)) ?? [];
  const preoptSelection = workflow.preopt?.result?.candidates.map(candidate => candidate.candidate_id) ?? [];
  const selectionChanged = Boolean(!busy && !['running', 'waiting'].includes(workflow.preopt?.status ?? '') && workflow.preopt?.result && selected.some(id => !preoptSelection.includes(id)));
  const invalidMessage = Object.values(invalid).filter(Boolean).join('；');
  const choose = (ids: string[]) => { setSelected(ids); configure({ selected_candidate_ids: ids, workflow_match_run_id: matchId }); };
  const start = async (stage: Stage) => {
    if (launchLock.current || running) return;
    launchLock.current = true; setStarting(true); setError(''); setActiveStage(stage); setProgress(undefined);
    try {
      await queue.current;
      if (Object.values(saveErrors.current).some(Boolean) || Object.values(invalidRef.current).some(Boolean)) throw new Error('参数有误，请修正后重试');
      if (stage !== 'search' && (!matchId || !selected.length)) throw new Error('请先完成检索并选择候选结构');
      if (stage === 'fit' && (!workflow.preopt?.result || !eligible.length || selectionChanged)) throw new Error('请先为当前候选完成结构预优化');
      const values = { ...card.config, ...pendingConfig.current };
      if (stage === 'fit' && Number(values.low_angle ?? 20) >= Number(values.high_angle ?? 70)) throw new Error('晶胞更新峰区间下限必须小于上限');
      await host.updateConfig({ workflow_stage: stage, ...(stage === 'search' ? {} : { workflow_match_run_id: matchId, selected_candidate_ids: stage === 'fit' ? eligible.map(candidate => candidate.candidate_id) : selected }), ...(stage === 'fit' ? { workflow_preopt_run_id: workflow.preopt!.run_id } : {}) });
      if (!host.runAnalysis) throw new Error('请刷新页面');
      await host.runAnalysis();
    } catch (reason) { setError(String(reason)); } finally { launchLock.current = false; setStarting(false); }
  };
  const fields = (items: { name: string; label: string; fallback: number; min: number; max: number; integer?: boolean }[]) => items.map(field => <StageNumber key={field.name} {...field} config={card.config} schema={definition.config_schema ?? {}} disabled={busy} save={configure} invalid={reportInvalid}/>);
  const done = [Boolean(result), Boolean(result), Boolean(workflow.preopt?.result), Boolean(workflow.fit?.result)];
  const searchCandidates = result?.candidates?.map(candidate => candidate.node_id) ?? [];
  const verifiedPool = multiphase.state.source_match_run_id === matchId
    ? multiphase.state.pool?.map(candidate => candidate.candidate_id).filter(id => searchCandidates.includes(id))
    : undefined;
  const multiphaseCandidates = verifiedPool && verifiedPool.length >= 2 ? verifiedPool : searchCandidates;
  const viewSingle = (next: number) => { multiphase.setEnabled(false); setStep(next); };
  const viewMultiphase = (stage: 'search' | 'review') => {
    multiphase.setEnabled(true); setMultiphaseStage(stage); setStep(1);
    setMultiphaseSelection(card.id, { lane: stage === 'review' ? 'pywpem' : 'agent', scope: stage, follow: true });
  };
  const reviewStatus = multiphase.state.pywpem_review?.status ?? 'idle';
  const reviewRunning = reviewStatus === 'running';
  const finalDiscussion = Boolean(multiphase.enabled && multiphaseStage === 'review' && reviewStatus === 'completed' && result && host.openResultsConversation);
  const searchDone = Boolean(multiphase.state.incumbent) && !multiphase.running;
  const beforeMultiphase = async () => {
    await queue.current;
    if (Object.values(saveErrors.current).some(Boolean) || Object.values(invalidRef.current).some(Boolean)) throw new Error('参数有误，请修正后重试');
  };
  const openDiscussion = async () => {
    if (discussionPending) return;
    setDiscussionPending(true); setDiscussionError('');
    try { await host.openResultsConversation?.(); }
    catch (reason) { setDiscussionError(String(reason)); }
    finally { setDiscussionPending(false); }
  };
  return <section className={`xrd-panel xrd-match-ui xrd-pipeline nodrag nopan${multiphase.enabled ? ' is-multiphase' : ''}`} aria-busy={busy}>
    {multiphase.enabled && multiphase.running && <MultiphaseGlow running/>}
    <nav className={`xrd-workflow xrd-workflow-branches ${result ? 'has-result' : ''}`} aria-label="检索步骤">
      <div className="xrd-workflow-shared">
        <button type="button" aria-current={!multiphase.enabled && step === 0 ? 'step' : undefined} aria-controls={stepId} disabled={busy} onClick={() => viewSingle(0)}><span className="xrd-step-number">01</span><span>参数设置</span></button>
        <ActivityLine active={(running || starting) && activeStage === 'search'} label="检索" status={done[1] ? 'completed' : activeStage === 'search' ? lastStatus : 'idle'}/>
        <button type="button" aria-current={!multiphase.enabled && step === 1 ? 'step' : undefined} aria-controls={stepId} disabled={busy || !result} onClick={() => viewSingle(1)}><span className="xrd-step-number">02</span><span>匹配结果</span></button>
      </div>
      <div className="xrd-workflow-fork">
        <div className={`xrd-workflow-branch is-single ${!multiphase.enabled && step >= 2 ? 'is-current' : ''}`} aria-label="单相流程">
          <span className="xrd-branch-label">单相</span><ActivityLine active={(running || starting) && activeStage === 'preopt'} label="结构预优化" status={done[2] ? 'completed' : activeStage === 'preopt' ? lastStatus : 'idle'}/>
          <button type="button" aria-current={!multiphase.enabled && step === 2 ? 'step' : undefined} disabled={busy || !result || !matchId} onClick={() => viewSingle(2)}><span className="xrd-step-number">03</span><span>结构预优化</span></button>
          <ActivityLine active={(running || starting) && activeStage === 'fit'} label="全谱拟合" status={done[3] ? 'completed' : activeStage === 'fit' ? lastStatus : 'idle'}/>
          <button type="button" aria-current={!multiphase.enabled && step === 3 ? 'step' : undefined} disabled={busy || !workflow.preopt} onClick={() => viewSingle(3)}><span className="xrd-step-number">04</span><span>全谱拟合</span></button>
        </div>
        <div className={`xrd-workflow-branch is-multi ${multiphase.enabled ? 'is-current' : ''}`} aria-label="多相流程">
          <span className="xrd-branch-label">多相</span><ActivityLine active={multiphase.running && !reviewRunning} label="组合筛选" status={searchDone || reviewStatus !== 'idle' ? 'completed' : multiphase.state.status}/>
          <button type="button" aria-current={multiphase.enabled && multiphaseStage === 'search' ? 'step' : undefined} disabled={running || starting || !matchId || multiphaseCandidates.length < 2} onClick={() => viewMultiphase('search')}><span className="xrd-step-number">03</span><span>组合筛选</span></button>
          <ActivityLine active={reviewRunning} label="联合复核" status={reviewStatus}/>
          <button type="button" aria-current={multiphase.enabled && multiphaseStage === 'review' ? 'step' : undefined} disabled={running || starting || !matchId || !multiphase.state.run_id} onClick={() => viewMultiphase('review')}><span className="xrd-step-number">04</span><span>联合复核</span></button>
        </div>
      </div>
    </nav>
    <div className="action-row xrd-workflow-actions">
      {!finalDiscussion && <RunHistory host={host}/>}
      {!finalDiscussion && result && host.openResultsConversation && <button type="button" className="xrd-results-discussion" aria-label="结果讨论" title={discussionPending ? '正在打开结果讨论…' : '结果讨论'} disabled={discussionPending} aria-busy={discussionPending} onClick={() => void openDiscussion()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 1-8 8H5l-3 3v-7a8 8 0 0 1 8-12h2a8 8 0 0 1 8 8Z"/><path d="M7 10h8M7 14h5"/></svg></button>}
      <RunTimer stage={multiphase.enabled && multiphaseStage === "review" && multiphase.state.pywpem_review?.status === "running" ? multiphase.state.pywpem_review.progress?.stage ?? "联合复核" : undefined} timing={multiphase.enabled ? multiphase.state.timing?.[multiphaseStage] : stageTiming[step <= 1 ? "search" : step === 2 ? "preopt" : "fit"]}/>
      {step === 1 && !multiphase.enabled && <span className="xrd-mode-hint">{multiphaseCandidates.length<2?'多相需要至少 2 个检索候选（当前 '+multiphaseCandidates.length+' 个）':'选择后续分支'}</span>}
      <span className="mc-xrd-action-spacer"/>
      {!multiphase.enabled && step > 0 && <button type="button" disabled={busy} onClick={() => setStep(step - 1)} aria-label={`返回${steps[step - 1]}`} title={`返回${steps[step - 1]}`}><ActionIcon name="back"/></button>}
      {multiphase.enabled && <span className="xrd-multiphase-actions" ref={setMultiphaseActionContainer}/> }
      {finalDiscussion && <RunHistory host={host}/>}
      {finalDiscussion && <button type="button" className="xrd-results-discussion xrd-results-discussion-final" aria-label="结果讨论" title={discussionPending ? '正在打开结果讨论…' : '结果讨论'} disabled={discussionPending} aria-busy={discussionPending} onClick={() => void openDiscussion()}><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 0 1-8 8H5l-3 3v-7a8 8 0 0 1 8-12h2a8 8 0 0 1 8 8Z"/><path d="M7 10h8M7 14h5"/></svg></button>}
      {step === 1 && !multiphase.enabled && <MultiphaseButton enabled={multiphase.enabled} running={multiphase.running} disabled={busy || !matchId || multiphaseCandidates.length < 2} onClick={() => multiphase.enabled ? viewSingle(1) : viewMultiphase('search')}/>}
      {!multiphase.enabled && <>
      {running ? <button type="button" className="primary-button" aria-label={`停止${labels[activeStage]}`} title={`停止${labels[activeStage]}`} onClick={() => void host.stopAnalysis?.().catch(reason => setError(String(reason)))}><ActionIcon name="stop"/></button> : <>
      {step === 0 && <button type="button" className="primary-button" disabled={busy || Boolean(missing || inputError || invalidMessage)} aria-label={result ? '重新检索' : '开始检索'} title={result ? '重新检索' : '开始检索'} onClick={() => void start('search')}><ActionIcon name={result ? 'restart' : 'play'}/></button>}
      {step === 1 && <SinglePhaseButton disabled={busy || !selected.length || !matchId} onClick={() => setStep(2)}/>}
      {step === 2 && <button type="button" className="primary-button" disabled={busy || !selected.length || !matchId || Boolean(invalidMessage)} aria-label={workflow.preopt?.status==='completed'?'重新预优化':'开始预优化'} title={workflow.preopt?.status==='completed'?'重新预优化':'开始预优化'} onClick={() => void start('preopt')}><ActionIcon name={workflow.preopt?.status==='completed'?'restart':'play'}/></button>}
      {step === 3 && <button type="button" className="primary-button" disabled={busy || !eligible.length || selectionChanged || Boolean(invalidMessage)} aria-label={workflow.fit?.status==='completed'?'重新全谱拟合':'开始全谱拟合'} title={workflow.fit?.status==='completed'?'重新全谱拟合':'开始全谱拟合'} onClick={() => void start('fit')}><ActionIcon name={workflow.fit?.status==='completed'?'restart':'play'}/></button>}
      </>}
      {step === 0 && <button type="button" disabled={busy || !result} aria-label="进入匹配结果" title="进入匹配结果" onClick={() => setStep(1)}><ActionIcon name="next"/></button>}
      {step >= 2 && <button type="button" disabled={busy || step === 3 || !eligible.length || selectionChanged} aria-label={step === 3 ? '已到流程末步' : '进入全谱拟合'} title={step === 3 ? '已到流程末步' : '进入全谱拟合'} onClick={() => setStep(3)}><ActionIcon name="next"/></button>}
      </>}
    </div>
    {discussionError && <p role="alert">{discussionError}</p>}
    {(running || starting) && <small role="status">{progress?.stage ?? `准备${labels[activeStage]}`}{progress?.total ? ` · ${progress.completed ?? 0} / ${progress.total}` : ''}</small>}
    {multiphase.enabled && <MultiphasePanel actionContainer={multiphaseActionContainer} source={card.id} view={multiphaseStage} state={multiphase.state} pending={multiphase.pending} error={multiphase.error} options={multiphase.options} onOptions={multiphase.setOptions} onSaveOptions={() => void multiphase.saveOptions()} saveStatus={multiphase.saveStatus} onBack={() => multiphaseStage === 'review' ? viewMultiphase('search') : viewSingle(1)} onReview={combinations => void multiphase.review(combinations)} onAdvance={() => viewMultiphase('review')} onRestart={() => void multiphase.start(multiphaseCandidates, beforeMultiphase, true)} onStart={() => void multiphase.start(multiphaseCandidates, beforeMultiphase)} onStop={() => void multiphase.stop()} candidateCount={multiphaseCandidates.length} currentMatchRunId={matchId} canStart={!running && !starting && Boolean(matchId) && multiphaseCandidates.length >= 2}/>}
    {!multiphase.enabled && <div id={stepId} className="xrd-step-content">
      {step > 0 && <FrameTimeline phases={Object.fromEntries((result?.candidates??[]).map(c=>[c.node_id,c.metadata.formula||'未提供物相']))} toolbar={step>=2?<button type="button" className="xrd-stage-settings" title={step===2?"结构预优化设置":"全谱拟合设置"} aria-label={step===2?"结构预优化设置":"全谱拟合设置"} aria-haspopup="dialog" onClick={e=>setSettingsAnchor(e.currentTarget)}><Settings2 size={18}/></button>:undefined} source={card.id} data={frameSets[step===1?'search':step===2?'preopt':'fit'] ?? {run_id:`pending-${step}`, stage:step===1?'search':step===2?'preopt':'fit', observed:result?.pattern?.points??[],frames:[]}} host={host}/>}
      {step === 0 && <MatchControls config={card.config} schema={definition.config_schema ?? {}} save={configure} disabled={busy} onInvalid={reportInvalid} advanced={result ? renderParameters(result) : <small>尚无上次运行参数</small>}/>}
      {step === 1 && result && <><CandidateSelection result={result} selected={selected} onChange={choose} disabled={busy}/>{!matchId && <p className="xrd-note">当前没有可引用的检索快照。请返回参数设置重新检索，再继续优化。</p>}{renderResult(result)}</>}
      {step === 2 && <>{settingsAnchor&&<FocusLayer anchor={settingsAnchor} kind="settings" title="结构预优化设置" onClose={()=>setSettingsAnchor(undefined)}><section className="xrd-zone"><h3><span>03</span>结构预优化</h3><div className="xrd-zone-body"><small>已选择 {selected.length} 个候选。按调整窗口约束晶胞长度与原子分数坐标；原始结构与接受判据保留在各自运行记录中。</small><div className="xrd-stage-fields">{fields([{ name: 'preopt_max_nfev', label: '预优化最大函数评估次数', fallback: 120, min: 1, max: 5000, integer: true }, { name: 'preopt_coordinate_window', label: '原子分数坐标调整窗口', fallback: .01, min: .0001, max: .5 }, { name: 'preopt_cell_window', label: '晶胞长度相对调整窗口', fallback: .35, min: .0001, max: 2 }])}</div><small>分数坐标窗口 0.01 表示 ±0.01；晶胞长度窗口 0.35 表示 ±35%。</small></div></section></FocusLayer>}{selectionChanged && <p className="xrd-note">候选选择已改变，请重新运行预优化以包含新增候选。</p>}<PipelineOutput run={workflow.preopt} stage="preopt" host={host} source={card.id}/></>}
      {step === 3 && <>{settingsAnchor&&<FocusLayer anchor={settingsAnchor} kind="settings" onClose={()=>setSettingsAnchor(undefined)}><section className="xrd-zone"><h3><span>04</span>全谱拟合与比较</h3><div className="xrd-zone-body"><small>本次可拟合 {eligible.length} 个候选。预优化未接受时使用原始 CIF；失败候选排除。</small><div className="xrd-stage-fields">{fields([{ name: 'iterations', label: '全谱拟合最大迭代', fallback: 5, min: 1, max: 1000, integer: true }, { name: 'low_angle', label: '晶胞更新峰区间下限 / °', fallback: 20, min: 0, max: 180 }, { name: 'high_angle', label: '晶胞更新峰区间上限 / °', fallback: 70, min: 0, max: 180 }])}</div><small>波长使用本次匹配结果的实验参数。晶胞更新峰区间不是全谱裁剪范围。</small></div></section></FocusLayer>}<PipelineOutput run={workflow.fit} stage="fit" host={host} source={card.id}/></>}
      {invalidMessage && <p role="alert">{invalidMessage}</p>}{error && <p role="alert">{error}</p>}
      {!busy && lastStatus === 'failed' && <p role="alert">上次{labels[activeStage]}失败。已有结果保留，请检查参数及输入后重试；详情保留在 OAW 运行记录中。</p>}
      {!busy && lastStatus === 'cancelled' && <small role="status">上次{labels[activeStage]}已取消，参数与已有结果保留。</small>}
    </div>}
    {step === 0 && !multiphase.enabled && <section className="xrd-inputs"><h3>输入配置</h3><InputDock props={{card,host,definition,level:'workspace'}} inputs={inputs} disabled={busy}/>{inputError && <small role="alert">{inputError}</small>}</section>}

  </section>;
}
