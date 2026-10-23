import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import type { PluginViewProps, XrdMultiphaseOptions, XrdMultiphaseState, XrdMultiphaseTrial } from '../sdk';
import { Settings2 } from '../sdk';
import { FocusLayer } from './MatchControls';
import { ActionIcon } from './WorkflowVisuals';
import { resolveMultiphaseSelection, setMultiphaseSelection, useMultiphaseCanvas, type MultiphaseSelection } from './MultiphaseCanvasState';
import './multiphase.css';
import { useCanvasLoading } from './CanvasTransition';

const activeStatuses = new Set(['running', 'waiting', 'starting', 'stopping']);
export const MULTIPHASE_CANDIDATE_LIMIT = 30;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const number = (value: unknown, digits = 3) => finite(value) ? value.toFixed(digits) : '—';
const statusText = (status?: string) => ({ running: '运行中', waiting: '等待运行', pending: '等待运行', starting: '启动中', stopping: '正在停止', completed: '完成', cancelled: '已停止', failed: '失败', error: '失败', interrupted: '运行中断', disabled: '未启用', idle: '未运行' }[status ?? 'idle'] ?? status);
const stopText = (reason: string) => ({ evaluation_budget_reached: '已达到评估预算', search_space_exhausted: '已评估全部允许的组合', user_stopped: '用户已停止', controller_failed: '优化器失败，已停止', controller_cancelled: '优化器已取消，计算已停止', backend_restarted: '服务重启，运行已中断' }[reason] ?? reason);
const trialLabel = (trial: XrdMultiphaseTrial) => (trial.labels?.length ? trial.labels : trial.candidate_ids).join(' + ');

/** State lives above the panel, so changing the visible trial never restarts an agent. */
export function useMultiphase(host: PluginViewProps['host'], matchRunId?: string, previous?: { active: boolean; state?: XrdMultiphaseState }) {
  const [enabled, setEnabled] = useState(previous?.active ?? false);
  const [state, setState] = useState<XrdMultiphaseState>(previous?.state ?? { status: 'idle' });
  const [pending, setPending] = useState<'starting' | 'stopping'>();
  const [error, setError] = useState('');
  const [connectionError, setConnectionError] = useState('');
  const [options, setOptions] = useState<XrdMultiphaseOptions>({ budget: 24, max_phases: 3, evaluate_baseline: true });
  const [saveStatus, setSaveStatus] = useState('');
  const edited = useRef(false);
  const lock = useRef(false);
  const restored = useRef(false);
  const alive = useRef(true);
  const loadVersion = useRef(0);

  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!host.getMultiphase) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const version = loadVersion.current;
      let next: XrdMultiphaseState | undefined;
      try {
        next = await host.getMultiphase!();
        if (cancelled || version !== loadVersion.current || lock.current) return;
        setState(next);
        setConnectionError('');
        if (!restored.current) {
          if (!edited.current && next.next_options && Object.keys(next.next_options).length) setOptions(current => ({...current, ...next!.next_options, agent_node_id: next!.next_options?.agent_node_id || next!.controller?.agent_node_id}));
          else if (!edited.current && next.baseline?.status === 'disabled') setOptions(current => ({ ...current, evaluate_baseline: false }));
          // Recover a running agent after refresh. Completed results stay one click away.
          if (activeStatuses.has(next.status)) setEnabled(true);
          restored.current = true;
        }
      } catch (reason) { if (!cancelled && version === loadVersion.current) setConnectionError(String(reason)); }
      finally { if (!cancelled && (!next || activeStatuses.has(next.status) || activeStatuses.has(next.pywpem_review?.status ?? ''))) timer = setTimeout(load, next ? 2500 : 5000); }
    };
    void load();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [host, pending]);

  const changeOptions = (next: XrdMultiphaseOptions) => { edited.current = true; setOptions(next); setSaveStatus('未保存'); };
  const saveOptions = async () => {
    setSaveStatus('保存中…');
    try {
      if (!host.saveMultiphaseOptions) throw new Error('保存入口尚未就绪，请刷新页面');
      await host.saveMultiphaseOptions(options);
      setSaveStatus('已保存，下次搜索生效');
    } catch (reason) { setSaveStatus('保存失败：' + String(reason)); }
  };
  const start = async (candidateIds: string[], beforeStart: () => Promise<void>, restart = false) => {
    if (lock.current || activeStatuses.has(state.status)) return;
    setEnabled(true); setError('');
    if (!host.startMultiphase || !host.getMultiphase) { setError('多相 Agent 尚未接入，请刷新页面。'); return; }
    if (!matchRunId || candidateIds.length < 2) { setError('请先完成检索，获得至少两个候选物相。'); return; }
    if (candidateIds.length > MULTIPHASE_CANDIDATE_LIMIT) { setError(`多相模式最多支持 ${MULTIPHASE_CANDIDATE_LIMIT} 个候选；请调整检索候选数后重新检索。`); return; }
    lock.current = true; setPending('starting'); loadVersion.current++;
    try {
      await beforeStart();
      const agentNodeId = options.agent_node_id ?? (state.available_agents?.find(agent => agent.agent_node_id === state.controller?.agent_node_id) ?? state.available_agents?.[0])?.agent_node_id;
      await host.startMultiphase({ ...options, ...(restart ? { restart: true } : {}), ...(agentNodeId ? { agent_node_id: agentNodeId } : {}), max_phases: Math.min(options.max_phases ?? 3, candidateIds.length), source_match_run_id: matchRunId, candidate_ids: candidateIds });
      const next = await host.getMultiphase();
      if (alive.current) setState(next);
    } catch (reason) { if (alive.current) setError(String(reason)); }
    finally { lock.current = false; if (alive.current) setPending(undefined); }
  };
  const review = async (combinations: string[][]) => {
    if (lock.current || activeStatuses.has(state.status)) return;
    lock.current = true; setPending('starting'); setError(''); loadVersion.current++;
    try {
      if (!host.reviewMultiphase || !state.run_id) throw new Error('联合复核入口尚未就绪，请刷新页面。');
      await host.reviewMultiphase(state.run_id, combinations);
      const next = await host.getMultiphase?.();
      if (next && alive.current) setState(next);
    } catch (reason) { if (alive.current) setError(String(reason)); }
    finally { lock.current = false; if (alive.current) setPending(undefined); }
  };
  const stop = async (close = false) => {
    if (lock.current) return;
    lock.current = true; setError('');
    try {
      if (activeStatuses.has(state.status)) {
        if (!host.stopMultiphase) throw new Error('无法停止多相 Agent，请刷新页面。');
        setPending('stopping'); loadVersion.current++;
        await host.stopMultiphase();
        const next = await host.getMultiphase?.();
        if (next && alive.current) setState(next);
      }
      if (close && alive.current) setEnabled(false);
    } catch (reason) { if (alive.current) setError(String(reason)); }
    finally { lock.current = false; if (alive.current) setPending(undefined); }
  };
  // Choosing a branch only changes the view. Starting/stopping requires its own action.
  const open = () => setEnabled(value => !value);
  return { enabled, setEnabled, state, pending, error: error || connectionError, options, setOptions: changeOptions, saveOptions, saveStatus, start, review, stop, open, running: Boolean(pending) || activeStatuses.has(state.status) };
}

export function MultiphaseIcon() {
  return <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/><path d="m8 5.3 8 4.4M8 18.7v-9l8-4.4"/></svg>;
}

export function MultiphaseButton({ enabled, running, disabled, onClick }: { enabled: boolean; running: boolean; disabled: boolean; onClick(): void }) {
  const tooltip = useId();
  return <span className="xrd-multiphase-trigger"><button type="button" aria-label="多相模式" aria-describedby={tooltip} aria-pressed={enabled} disabled={disabled} onClick={onClick}><MultiphaseIcon/>{running && <i aria-hidden="true"/>}</button><span id={tooltip} role="tooltip">多相模式</span></span>;
}

export function SinglePhaseButton({ disabled, onClick }: { disabled: boolean; onClick(): void }) {
  const tooltip = useId();
  return <span className="xrd-multiphase-trigger"><button type="button" className="primary-button" aria-label="单相模式" aria-describedby={tooltip} disabled={disabled} onClick={onClick}><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.55" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9"/></svg></button><span id={tooltip} role="tooltip">单相模式 · 进入结构预优化</span></span>;
}

export function MultiphaseGlow({ running }: { running: boolean }) {
  return <span className="activity-glow xrd-multiphase-glow" data-phase={running ? 'running' : 'ready'} aria-hidden="true" style={{ '--activity-color': '#95b39a' } as CSSProperties}/>;
}

function MultiphaseNumber({ label, value, min, max, disabled, onChange }: { label: string; value: number; min: number; max: number; disabled: boolean; onChange(value: number): void }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const valid = (text: string) => text.trim() && Number.isInteger(Number(text)) && Number(text) >= min && Number(text) <= max;
  const adjust = (direction: number) => { const next = Math.max(min, Math.min(max, (valid(draft) ? Number(draft) : value) + direction)); setDraft(String(next)); onChange(next); };
  return <label className="xrd-number-field"><span>{label}</span><span className="xrd-number-control"><input aria-label={label} type="number" min={min} max={max} step={1} disabled={disabled} value={draft} onChange={event => { setDraft(event.target.value); if (valid(event.target.value)) onChange(Number(event.target.value)); }} onBlur={() => { if (!valid(draft)) setDraft(String(value)); }} onKeyDown={event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); adjust(event.key === 'ArrowUp' ? 1 : -1); } }}/><span className="xrd-number-steppers">{[1, -1].map(direction => <button key={direction} type="button" disabled={disabled} aria-label={`${direction === 1 ? '增加' : '减少'}${label}`} onPointerDown={event => event.preventDefault()} onClick={() => adjust(direction)}><svg viewBox="0 0 12 8" aria-hidden="true"><path d={direction === 1 ? 'M2 6 6 2 10 6' : 'M2 2 6 6 10 2'}/></svg></button>)}</span></span></label>;
}

function TrialMetrics({ trial, objective }: { trial: XrdMultiphaseTrial; objective?: string }) {
  const converged = trial.fit?.converged ?? trial.converged;
  return <dl className="xrd-multiphase-metrics"><div><dt title={objective}>综合得分</dt><dd>{number(trial.score)}</dd></div><div><dt>留出 Rwp / %</dt><dd>{number(trial.validation?.metrics?.rwp_percent)}</dd></div><div><dt>全谱 Rwp / %</dt><dd>{number(trial.metrics?.rwp_percent)}</dd></div><div><dt>全谱 Rp / %</dt><dd>{number(trial.metrics?.rp_percent)}</dd></div><div><dt>{trial.fit?.screening_method === "fixed_profile_nnls_v1" ? "快速匹配" : "拟合收敛"}</dt><dd>{converged === true ? trial.fit?.screening_method === 'fixed_profile_nnls_v1' ? '线性求解完成' : '达到判据' : converged === false ? '未达到判据' : '未提供'}</dd></div></dl>;
}

function TrialRefinement({ trial }: { trial: XrdMultiphaseTrial }) {
  const fit = trial.fit;
  const range = (key: string) => fit?.parameter_bounds?.[key]?.map(value => number(value, 5)).join(' – ') ?? '未提供';
  const parameters: { key: 'zero_shift_deg' | 'fwhm_deg' | 'lorentz_fraction'; label: string }[] = [{ key: 'zero_shift_deg', label: '零点偏移 / °' }, { key: 'fwhm_deg', label: '峰宽 / °' }, { key: 'lorentz_fraction', label: '洛伦兹比例' }];
  return <>
    {Boolean(trial.phase_contributions?.length) && <div className="xrd-table"><table aria-label="多相晶胞精修结果"><thead><tr><th>物相</th><th title="拟合谱贡献的面积比例，不是质量分数">谱面积占比 / %</th><th>晶胞缩放</th><th>晶胞 a, b, c / Å</th></tr></thead><tbody>{trial.phase_contributions!.map(phase => <tr key={phase.candidate_id}><td>{phase.formula || phase.label || phase.candidate_id}</td><td>{finite(phase.profile_area_fraction) ? number(phase.profile_area_fraction * 100, 2) : '—'}</td><td>{number(phase.isotropic_cell_scale, 5)}</td><td><span>{phase.cell_fitted?.slice(0, 3).map(value => number(value, 5)).join(' · ') || '—'}</span>{phase.cell_input && <small className="xrd-multiphase-cell-input">输入：{phase.cell_input.slice(0, 3).map(value => number(value, 5)).join(' · ')}</small>}</td></tr>)}</tbody></table></div>}
    {fit && <details className="xrd-multiphase-bounds"><summary>拟合参数与约束范围</summary><div className="xrd-table"><table aria-label="多相拟合参数约束"><thead><tr><th>参数</th><th>拟合值</th><th>约束范围</th></tr></thead><tbody>{parameters.map(item => <tr key={item.key}><td>{item.label}</td><td>{number(fit[item.key], 5)}</td><td>{range(item.key)}</td></tr>)}{fit.parameter_bounds?.isotropic_cell_scale && <tr><td>各相晶胞缩放</td><td>见晶胞精修结果</td><td>{range('isotropic_cell_scale')}</td></tr>}</tbody></table></div>{fit.atomic_fractional_coordinates_fixed && <small>原子分数坐标固定。{fit.reflection_relative_intensities_fixed ? '反射相对强度固定。' : ''}</small>}</details>}
  </>;
}

type CombinationBar = {
  id: string; best?: boolean; label: string; phaseCount: number; iteration: number; status: string; score?: number;
  rwp?: number; rp?: number; holdout?: number; converged?: boolean; reason?: string; error?: string;
};

/** One bar is one evaluated combination, never an individual phase contribution. */
function CombinationTimeline({ items, selected, onSelect, follow, onFollow, review = false, title, source }: {
  items: CombinationBar[]; selected?: string; onSelect(id: string): void; follow: boolean; onFollow(): void; review?: boolean; title: string; source?: string;
}) {
  const loading = useCanvasLoading(source ?? '');
  const scroll = useRef<HTMLDivElement>(null);
  const tooltip = useId();
  const [hover, setHover] = useState<{ item: CombinationBar; left: number; top: number; below: boolean }>();
  const [playing, setPlaying] = useState(false);
  const index = Math.max(0, items.findIndex(item => item.id === selected));
  const reveal = (button: HTMLButtonElement, item: CombinationBar) => {
    const bounds = button.getBoundingClientRect();
    const below = bounds.top < 300;
    setHover({ item, left: Math.max(12, Math.min(bounds.left, window.innerWidth - Math.min(380, window.innerWidth - 24) - 12)), top: below ? Math.max(12, Math.min(bounds.bottom + 12, window.innerHeight - 280)) : bounds.top - 12, below });
  };
  useEffect(() => {
    const button = scroll.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]');
    const container = scroll.current;
    if (!button || !container) return;
    if (button.offsetLeft < container.scrollLeft) container.scrollLeft = button.offsetLeft;
    else if (button.offsetLeft + button.offsetWidth > container.scrollLeft + container.clientWidth) container.scrollLeft = button.offsetLeft + button.offsetWidth - container.clientWidth;
  }, [index, items.length]);
  useEffect(() => {
    if (!playing || items.length < 2) return;
    const timer = setTimeout(() => {
      if (index >= items.length - 1) setPlaying(false);
      else onSelect(items[index + 1].id);
    }, 850);
    return () => clearTimeout(timer);
  }, [playing, index, items, onSelect]);
  const select = (next: number) => { setPlaying(false); if (items[next]) onSelect(items[next].id); };
  return <section className="xrd-frame-timeline xrd-combination-timeline" aria-label={review ? 'OAW_XRDfit 复核柱状时间轴' : '多相组合柱状时间轴'}>
    <div className="xrd-frame-heading"><strong>{title}</strong><small title={review ? '柱高 = 100 / (1 + Rwp / 100)，仅用于复核结果之间比较；不计入搜索评分。' : '柱高为固定 0–100 搜索得分，综合留出残差与物相数量惩罚；不是物相正确概率。'}>{items.length ? `${index + 1} / ${items.length} · ${review ? '全谱复核' : '组合得分'}` : '等待组合评估'}</small></div>
    <div className="xrd-frame-controls">
      <button type="button" aria-label={playing ? '暂停组合回放' : '播放组合回放'} title={playing ? '暂停组合回放' : '播放组合回放'} disabled={items.length < 2} onClick={() => { if (!playing && index === items.length - 1) onSelect(items[0].id); setPlaying(!playing); }}><ActionIcon name={playing ? 'pause' : 'play'}/></button>
      <button type="button" aria-label="上一个组合" title="上一个组合" disabled={!items.length || index === 0} onClick={() => select(index - 1)}><ActionIcon name="back"/></button>
      <div ref={scroll} className="xrd-score-scroll nowheel" role="group" aria-label={review ? '按联合复核结果切换' : '按组合得分切换'} onScroll={() => setHover(undefined)}>
        {!items.length && <span className="xrd-score-empty">暂无评估记录</span>}
        {items.map((item, i) => {
          const score = review ? finite(item.rwp) ? 100 / (1 + item.rwp / 100) : undefined : item.score;
          const quality = finite(score) ? Math.max(0, Math.min(1, score / 100)) : undefined;
          const description = `${review ? '复核' : '第'} ${item.iteration}${review ? '' : ' 轮'} · ${item.label} · ${item.phaseCount} 相 · ${review ? `Rwp ${number(item.rwp)} %` : `得分 ${number(item.score)} / 100`} · ${statusText(item.status)}${item.best ? ' · 综合最佳组合' : ''}`;
          return <button key={item.id} type="button" className={`xrd-score-hit ${quality === undefined ? 'is-unrated' : quality === 0 ? 'is-zero' : ''} ${item.status === 'failed' ? 'is-failed' : ''} ${loading && selected === item.id ? 'is-loading' : ''}`} aria-busy={loading && selected === item.id} aria-label={description} aria-describedby={hover?.item.id === item.id ? tooltip : undefined} aria-pressed={selected === item.id} onClick={() => select(i)} onMouseEnter={event => reveal(event.currentTarget, item)} onMouseLeave={() => setHover(undefined)} onFocus={event => reveal(event.currentTarget, item)} onBlur={() => setHover(undefined)} onKeyDown={event => {
            const next = event.key === 'ArrowLeft' ? Math.max(0, i - 1) : event.key === 'ArrowRight' ? Math.min(items.length - 1, i + 1) : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : undefined;
            if (next !== undefined) { event.preventDefault(); select(next); (scroll.current?.children[next] as HTMLButtonElement)?.focus(); }
          }}><span className="xrd-score-phase">{item.phaseCount} 相组合</span><span className="xrd-score-well">{item.best && <svg className="xrd-combination-crown" style={{bottom: `${Math.max(2, quality === undefined ? 28 : quality * 116) + 5}px`}} viewBox="0 0 24 24" width="14" height="14" fill="currentColor" aria-label="最佳组合"><path d="m3 6 5 4 4-7 4 7 5-4-2 12H5L3 6Zm2 14h14v2H5Z"/></svg>}<span className="xrd-score-column" style={{ height: quality === undefined ? '28px' : `${quality * 116}px`, '--xrd-score-hue': quality === undefined ? undefined : quality * 120 } as CSSProperties}/></span><span className="xrd-score-number">{String(item.iteration).padStart(2, '0')}</span></button>;
        })}
      </div>
      <button type="button" aria-label="下一个组合" title="下一个组合" disabled={!items.length || index >= items.length - 1} onClick={() => select(index + 1)}><ActionIcon name="next"/></button>
      <button type="button" aria-label="跟随当前组合" title="跟随最新组合；完成后显示最佳结果" aria-pressed={follow} disabled={!items.length} onClick={() => { setPlaying(false); onFollow(); }}><ActionIcon name="follow"/></button>
    </div>
    {hover && createPortal(<div id={tooltip} role="tooltip" aria-label="组合评估信息" className="xrd-combination-tooltip" style={{ left: hover.left, top: hover.top, transform: hover.below ? 'none' : undefined }}><strong>{hover.item.label}</strong><span>{hover.item.phaseCount} 相 · {statusText(hover.item.status)}</span>{!review && <span>搜索得分：{number(hover.item.score)} / 100</span>}<span>全谱 Rwp：{number(hover.item.rwp)}% · Rp：{number(hover.item.rp)}%</span>{!review && <span>留出 Rwp：{number(hover.item.holdout)}%</span>}<span>收敛：{hover.item.converged === true ? '达到判据' : hover.item.converged === false ? '未达到判据' : '未提供'}</span>{hover.item.error && <span>{hover.item.error}</span>}{hover.item.reason && <span className="xrd-combination-reason">{hover.item.reason}</span>}</div>, document.body)}
  </section>;
}

export function MultiphasePanel({ state, pending, error, options, onOptions, onSaveOptions, saveStatus, onStart, onRestart, onAdvance, onBack, onReview, onStop, candidateCount, canStart, currentMatchRunId, source, actionContainer, view = 'all' }: {
  state: XrdMultiphaseState; pending?: 'starting' | 'stopping'; error: string; options: XrdMultiphaseOptions;
  onSaveOptions?(): void; saveStatus?: string;
  onOptions(options: XrdMultiphaseOptions): void; onStart(): void; onRestart?(): void; onAdvance?(): void; onBack?(): void; onReview?(combinations: string[][]): void; onStop(): void; candidateCount: number; canStart: boolean; currentMatchRunId?: string; source?: string; actionContainer?: HTMLElement | null; view?: 'search' | 'review' | 'all';
}) {
  const [settingsAnchor, setSettingsAnchor] = useState<HTMLButtonElement | null>(null);
  const settingsOpen = Boolean(settingsAnchor);
  const settingsId = useId();
  const boTooltip = useId();
  const localSource = useId();
  const cursorSource = source ?? localSource;
  const snapshot = useMultiphaseCanvas(cursorSource);
  const cursor = snapshot.selection;
  const display = resolveMultiphaseSelection(state, cursor);
  const select = (selection: MultiphaseSelection) => setMultiphaseSelection(cursorSource, { scope: view === 'all' ? undefined : view, ...selection });
  useEffect(() => {
    if (view !== 'all' && cursor.scope !== view) setMultiphaseSelection(cursorSource, { lane: view === 'review' ? 'pywpem' : 'agent', scope: view, follow: true });
  }, [cursorSource, cursor.scope, view]);
  const running = Boolean(pending) || activeStatuses.has(state.status);
  const stale = Boolean(state.run_id && currentMatchRunId && state.source_match_run_id !== currentMatchRunId);
  const poolError = candidateCount > MULTIPHASE_CANDIDATE_LIMIT ? `当前检索有 ${candidateCount} 个候选，多相模式最多支持 ${MULTIPHASE_CANDIDATE_LIMIT} 个。请在参数设置中将候选数调整至 ${MULTIPHASE_CANDIDATE_LIMIT} 或以下，再重新检索。` : candidateCount < 2 ? '当前候选不足两个，请重新检索以比较单相与多相组合。' : '';
  const actionTooltip = useId();
  const isReview = view === 'review';
  const comboKey = (ids: string[]) => JSON.stringify([...ids].sort());
  const reviewCandidates = [...new Map([...(state.trials ?? []), ...(state.baseline?.trials ?? [])].filter(t => t.status === 'completed').map(t => [comboKey(t.candidate_ids), t])).values()].sort((a, b) => (finite(b.score) ? b.score : -Infinity) - (finite(a.score) ? a.score : -Infinity));
  const recommended = state.review_recommendations?.combinations ?? [];
  const [reviewSelection, setReviewSelection] = useState<{run?: string; keys: string[]} | null>(null);
  const selectedKeys = reviewSelection && reviewSelection.run === state.run_id ? reviewSelection.keys : recommended.map(comboKey);
  const selectedCombinations = reviewCandidates.filter(t => selectedKeys.includes(comboKey(t.candidate_ids))).map(t => t.candidate_ids);

  const completed = isReview ? state.pywpem_review?.status === 'completed' : state.llm_status === 'completed' && state.baseline?.status !== 'running' || state.status === 'completed';
  const actionLabel = running ? isReview ? '停止联合复核' : '停止筛选' : isReview ? completed ? '重新联合复核' : '开始联合复核' : completed ? '重新搜索' : state.resumable && !stale ? '继续筛选' : '开始多相优化';
  const launch = isReview ? () => onReview?.(selectedCombinations) : completed || stale ? onRestart ?? onStart : onStart;
  const runAction = <>
    <span className="xrd-multiphase-trigger"><button type="button" aria-label="下次运行设置" aria-expanded={settingsOpen} aria-controls={settingsId} aria-pressed={settingsOpen} onClick={event => setSettingsAnchor(settingsAnchor ? null : event.currentTarget)}><Settings2 size={18} strokeWidth={1.7} aria-hidden="true"/></button><span role="tooltip">下次运行设置</span></span>
    {onBack && <span className="xrd-multiphase-trigger"><button type="button" aria-label={isReview ? '返回组合筛选' : '返回匹配结果'} title={isReview ? '返回组合筛选' : '返回匹配结果'} disabled={running} onClick={onBack}><ActionIcon name="back"/></button></span>}
    <span className="xrd-multiphase-trigger"><button type="button" className="primary-button" aria-label={actionLabel} aria-describedby={actionTooltip} disabled={running ? Boolean(pending) : isReview ? stale || !selectedCombinations.length || !onReview : !canStart || Boolean(poolError)} onClick={running ? onStop : launch}><ActionIcon name={running ? 'stop' : completed ? 'restart' : 'play'}/>{!actionContainer && actionLabel}</button><span id={actionTooltip} role="tooltip">{actionLabel}</span></span>
    {onAdvance && !isReview && <span className="xrd-multiphase-trigger"><button type="button" aria-label="下一步：联合复核" title="下一步：联合复核" disabled={running || stale || !state.incumbent} onClick={onAdvance}><ActionIcon name="next"/></button></span>}
  </>;
  const sequentialJev = state.protocol_version === 'jev_autoregressive_v2';
  const optimizerLabel = state.optimizer_label || (sequentialJev || state.status === 'idle' && !state.run_id ? 'Jev' : 'LLM Agent');
  const optimizerAgents = state.available_agents ?? [];
  const selectedAgent = options.agent_node_id ?? optimizerAgents.find(agent => agent.agent_node_id === state.controller?.agent_node_id)?.agent_node_id ?? optimizerAgents[0]?.agent_node_id ?? '';
  const showBaseline = options.evaluate_baseline !== false && state.baseline?.status !== 'disabled';
  useEffect(() => {
    if (!showBaseline && cursor.lane === 'baseline') select({ lane: 'agent', follow: true });
  }, [showBaseline, cursor.lane]);
  const overview = [
    { name: optimizerLabel, lane: 'agent' as const, result: state.incumbent, count: state.trials?.length ?? 0, status: pending ?? state.llm_status ?? (state.baseline?.status === 'running' && state.incumbent ? 'completed' : state.status) },
    { name: 'BO_baseline', lane: 'baseline' as const, result: state.baseline?.incumbent, count: state.baseline?.evaluations ?? state.baseline?.trials?.length ?? 0, status: state.baseline?.status ?? 'idle' },
  ].filter(item => item.lane !== 'baseline' || showBaseline);
  const successful = overview.filter(item => item.result?.status === 'completed' && finite(item.result.score));
  const winner = successful.reduce<(typeof overview)[number] | undefined>((best, item) => !best || item.result!.score! > best.result!.score! ? item : best, undefined);
  const lane = !showBaseline ? 'agent' : display?.lane === 'baseline' ? 'baseline' : display?.lane === 'agent' ? 'agent' : cursor.lane === 'baseline' ? 'baseline' : winner?.lane ?? 'agent';
  const trials = lane === 'agent' ? state.trials ?? [] : state.baseline?.trials ?? [];
  const incumbent = lane === 'agent' ? state.incumbent : state.baseline?.incumbent;
  const chosen = trials.find(trial => trial.trial_id === display?.trialId) ?? (display?.lane === 'pywpem' ? undefined : incumbent);
  const chooseTrial = (trialId: string) => select({ lane, trialId, follow: false });
  const chooseLane = (next: 'agent' | 'baseline') => {
    const result = next === 'agent' ? state.incumbent ?? state.trials?.at(-1) : state.baseline?.incumbent ?? state.baseline?.trials?.at(-1);
    select({ lane: next, trialId: result?.trial_id, follow: false });
  };
  const reviewItems = state.pywpem_review?.reviews ?? [];
  const reviewIndex = display?.lane === 'pywpem' ? display.reviewIndex : undefined;
  const chosenReview = reviewIndex === undefined ? undefined : reviewItems[reviewIndex];
  const liveReview = state.pywpem_review?.status === 'running' ? state.pywpem_review.live : undefined;
  const reviewBars: CombinationBar[] = reviewItems.map((review, index) => ({ id: String(index), label: (review.full.labels ?? review.full.candidate_ids).join(' + '), phaseCount: review.full.candidate_ids.length, iteration: index + 1, status: review.full.status, rwp: review.full.metrics?.rwp_percent, rp: review.full.metrics?.rp_percent, converged: review.full.converged, error: review.full.error }));
  if (liveReview) reviewBars.push({ id: 'live', label: `实时迭代 ${liveReview.iteration} · ${(liveReview.labels ?? liveReview.candidate_ids).join(' + ')}`, phaseCount: liveReview.candidate_ids.length, iteration: liveReview.iteration, status: liveReview.status, rwp: liveReview.metrics?.rwp_percent, rp: liveReview.metrics?.rp_percent });
  const phaseLimit = Math.max(2, Math.min(4, candidateCount));
  return <section className="xrd-multiphase xrd-match-ui" aria-label="多相自动筛选与联合拟合">
    {stale && <p className="xrd-multiphase-stale" role="note">以下结果来自先前检索，不能作为当前实验谱的优化结果。{running ? '先前运行仍在进行；停止后可使用当前检索重新运行。' : '重新运行将使用当前检索的候选池。'}</p>}
    {poolError && <p role="alert">{poolError}</p>}
    {settingsAnchor && <FocusLayer anchor={settingsAnchor} kind="settings" title="下次运行设置" onClose={() => setSettingsAnchor(null)}><section id={settingsId} aria-label="下次运行设置" className="xrd-multiphase-settings"><div>{optimizerAgents.length > 0 && <label className="xrd-multiphase-agent-select"><span>优化 Agent</span><select value={selectedAgent} disabled={running} onChange={event => onOptions({ ...options, agent_node_id: event.target.value })}>{!optimizerAgents.some(agent => agent.agent_node_id === selectedAgent) && <option value={selectedAgent}>选择 Jev Agent</option>}{optimizerAgents.map(agent => <option key={agent.agent_node_id} value={agent.agent_node_id}>{agent.name}</option>)}</select></label>}<MultiphaseNumber label="每个算法的评估预算" min={6} max={100} disabled={running} value={options.budget ?? 24} onChange={value => onOptions({ ...options, budget: value })}/><MultiphaseNumber label="每个组合最多物相" min={2} max={phaseLimit} disabled={running} value={Math.min(options.max_phases ?? 3, phaseLimit)} onChange={value => onOptions({ ...options, max_phases: value })}/><label className="xrd-multiphase-baseline-toggle xrd-multiphase-trigger"><input type="checkbox" checked={options.evaluate_baseline !== false} aria-label="同拟合预算评测 BO_baseline" aria-describedby={boTooltip} onChange={event => onOptions({ ...options, evaluate_baseline: event.target.checked })}/>同拟合预算评测 BO_baseline<span id={boTooltip} role="tooltip">可选；关闭后只展示 Jev 记录。运行期间修改将在下次搜索生效，不会停止本轮 BO。</span></label>{onSaveOptions && <div className="xrd-multiphase-save"><small role="status">{saveStatus}</small><span className="xrd-multiphase-trigger"><button type="button" className="xrd-settings-save" aria-label="保存设置" disabled={saveStatus === '保存中…'} onClick={onSaveOptions}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 3h12l4 4v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M7 3v6h10V3M7 21v-8h10v8M14 5v2"/></svg></button><span role="tooltip">保存设置</span></span></div>}</div></section></FocusLayer>}
    {view !== 'review' && <>
    <div className="xrd-multiphase-trials"><header><strong>{stale ? '历史组合评估记录' : '组合评估记录'}</strong><div role="group" aria-label="查看优化算法"><button type="button" aria-pressed={lane === 'agent'} onClick={() => chooseLane('agent')}>{optimizerLabel}</button>{showBaseline && <button type="button" aria-pressed={lane === 'baseline'} onClick={() => chooseLane('baseline')}>BO_baseline</button>}</div></header>
      <CombinationTimeline source={cursorSource} title={lane === 'agent' ? `${optimizerLabel} · 组合筛选` : 'BO_baseline · 组合筛选'} items={trials.map(trial => ({ id: trial.trial_id, best: trial.status === 'completed' && finite(trial.score) && trial.score === winner?.result?.score, label: trialLabel(trial), phaseCount: trial.candidate_ids.length, iteration: trial.iteration, status: trial.status, score: trial.score, rwp: trial.metrics?.rwp_percent, rp: trial.metrics?.rp_percent, holdout: trial.validation?.metrics?.rwp_percent, converged: trial.fit?.converged ?? trial.converged, reason: trial.reason, error: trial.error }))} selected={chosen?.trial_id} onSelect={chooseTrial} follow={cursor.follow} onFollow={() => select({ lane, follow: true })}/>
    </div>
    {trials.length > 0 && <details className="xrd-multiphase-trials"><summary>查看组合评估表</summary><div className="xrd-table"><table aria-label="多相组合评估记录"><thead><tr><th>轮次</th><th>物相组合</th><th>得分</th><th>全谱 Rwp / %</th><th>状态</th></tr></thead><tbody>{trials.map(trial => <tr key={trial.trial_id} aria-selected={chosen?.trial_id === trial.trial_id}><td>{trial.iteration}</td><td><button type="button" onClick={() => chooseTrial(trial.trial_id)}>{trialLabel(trial)}</button>{trial.trial_id === incumbent?.trial_id && <small className="xrd-multiphase-best-label">本算法最优</small>}</td><td>{number(trial.score)}</td><td>{number(trial.metrics?.rwp_percent)}</td><td>{statusText(trial.status)}</td></tr>)}</tbody></table></div></details>}
    {chosen && <section className="xrd-multiphase-detail" aria-label="选中组合详情"><header><strong>{trialLabel(chosen)}</strong><span>{lane === 'agent' ? optimizerLabel : 'BO_baseline'} · 第 {chosen.iteration} 轮 · {chosen.candidate_ids.length} 相</span></header><TrialMetrics trial={chosen} objective={state.objective_description}/><TrialRefinement trial={chosen}/>{chosen.error && <p role="alert">{chosen.error}</p>}</section>}
    </>}
    {view !== 'search' && <section className="xrd-multiphase-detail" aria-label="OAW_XRDfit 联合复核">
      {!state.pywpem_review?.selected_combinations?.length && <fieldset disabled={running} className="xrd-review-selection"><legend>待复核组合 · 已选 {selectedCombinations.length} 组</legend>
        {state.review_recommendations?.error && <p role="alert">Jev 推荐未完成：{state.review_recommendations.error}；可手动选择。</p>}
        {!state.review_recommendations && <small>本次历史运行暂无 Jev 推荐，可手动选择需要复核的组合。</small>}
        <div className="xrd-review-card-grid">{reviewCandidates.map((trial, index) => {
          const key = comboKey(trial.candidate_ids), checked = selectedKeys.includes(key);
          const isRecommended = recommended.some(ids => comboKey(ids) === key);
          return <label key={key} className={`xrd-review-card ${checked ? 'is-selected' : ''}`}>
            <input type="checkbox" checked={checked} onChange={event => setReviewSelection({run: state.run_id, keys: event.target.checked ? [...selectedKeys, key] : selectedKeys.filter(k => k !== key)})}/>
            <span className="xrd-review-card-body"><span className="xrd-review-card-heading"><span>#{String(index + 1).padStart(2, '0')} · {trial.candidate_ids.length} 相组合</span>{isRecommended && <span className="xrd-review-recommended">Jev 推荐</span>}</span><span className="xrd-review-card-phases">{trialLabel(trial)}</span><span className="xrd-review-card-score">筛选得分 <strong>{number(trial.score)}</strong></span></span>
          </label>;
        })}</div>
      </fieldset>}
      <CombinationTimeline source={cursorSource} review title="联合全谱复核" items={reviewBars} selected={reviewIndex === undefined ? liveReview && cursor.follow ? 'live' : undefined : String(reviewIndex)} onSelect={id => select({ lane: 'pywpem', reviewIndex: id === 'live' ? undefined : Number(id), follow: id === 'live' })} follow={cursor.follow} onFollow={() => select({ lane: 'pywpem', follow: true })}/>
      {state.pywpem_review?.error && <p role="alert">{state.pywpem_review.error}</p>}
      {chosenReview?.full.error && <p role="alert">{chosenReview.full.error}</p>}
    </section>}
    {state.stop_reason && <p className="xrd-multiphase-stop-reason">{stopText(state.stop_reason)}</p>}
    {(error || state.error || state.baseline?.error) && <p role="alert">{error || state.error || state.baseline?.error}</p>}
    {view !== 'review' && Boolean(state.excluded_candidates?.length) && <details><summary>未进入组合评估的候选（{state.excluded_candidates!.length}）</summary>{state.excluded_candidates!.map(candidate => <p key={candidate.candidate_id}>{candidate.candidate_id} · {candidate.input_error || '未提供原因'}</p>)}</details>}
    <footer>{actionContainer ? createPortal(runAction, actionContainer) : runAction}</footer>
  </section>;
}
