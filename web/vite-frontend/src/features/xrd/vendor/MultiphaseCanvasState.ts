import { useEffect, useSyncExternalStore } from 'react';
import type { PluginViewProps, XrdMultiphaseState, XrdMultiphaseTrial, XrdMultiphaseStructure } from '../sdk';

export type MultiphaseSelection = {
  lane: 'agent' | 'baseline' | 'pywpem';
  trialId?: string;
  reviewIndex?: number;
  follow: boolean;
  scope?: 'search' | 'review';
};
export type MultiphaseCanvasSnapshot = {
  active: boolean;
  state?: XrdMultiphaseState;
  selection: MultiphaseSelection;
  phaseId?: string;
  currentMatchRunId?: string;
  connectionError?: string;
};
export type MultiphaseDisplay = {
  lane: MultiphaseSelection['lane'];
  trialId?: string;
  reviewIndex?: number;
  iteration?: number;
  candidate_ids: string[];
  labels?: string[];
  status: string;
  error?: string;
  metrics?: XrdMultiphaseTrial['metrics'];
  score?: number;
  plot?: XrdMultiphaseTrial['plot'];
  structures?: XrdMultiphaseStructure[];
  caption: string;
};

const freshSelection = (): MultiphaseSelection => ({ lane: 'agent', follow: true });
const empty: MultiphaseCanvasSnapshot = { active: false, selection: freshSelection() };
const snapshots = new Map<string, MultiphaseCanvasSnapshot>();
const listeners = new Set<() => void>();
const publishers = new Map<string, number>();
const current = (source: string) => snapshots.get(source) ?? empty;
const update = (source: string, snapshot: MultiphaseCanvasSnapshot) => {
  snapshots.set(source, snapshot);
  listeners.forEach(listener => listener());
};

/** One run-scoped cursor for the analysis panel and both linked canvases. */
export function publishMultiphaseState(source: string, state: XrdMultiphaseState, active: boolean, currentMatchRunId?: string) {
  const previous = current(source);
  const changedRun = previous.state?.run_id !== state.run_id || previous.currentMatchRunId !== currentMatchRunId;
  if (!changedRun && previous.state === state && previous.active === active && !previous.connectionError) return;
  const snapshot: MultiphaseCanvasSnapshot = {
    active, state, currentMatchRunId,
    selection: changedRun ? freshSelection() : previous.selection,
    phaseId: changedRun || !active ? undefined : previous.phaseId,
  };
  const display = resolveMultiphaseSelection(state, snapshot.selection);
  if (snapshot.phaseId && !display?.candidate_ids.includes(snapshot.phaseId)) snapshot.phaseId = undefined;
  update(source, snapshot);
}

export function setMultiphaseSelection(source: string, selection: MultiphaseSelection) {
  const previous = current(source);
  const display = resolveMultiphaseSelection(previous.state, selection);
  update(source, { ...previous, selection,
    phaseId: previous.phaseId && display?.candidate_ids.includes(previous.phaseId) ? previous.phaseId : undefined });
}

export function selectMultiphasePhase(source: string, candidateId: string) {
  const previous = current(source);
  const display = resolveMultiphaseSelection(previous.state, previous.selection);
  if (candidateId !== 'all' && !display?.candidate_ids.includes(candidateId)) return;
  update(source, { ...previous, phaseId: candidateId === 'all' ? undefined : candidateId });
}

export function useMultiphaseCanvas(source: string) {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, () => current(source));
}

export function retainMultiphasePublisher(source: string) {
  publishers.set(source, (publishers.get(source) ?? 0) + 1);
  return () => { const count = (publishers.get(source) ?? 1) - 1; if (count) publishers.set(source, count); else publishers.delete(source); };
}

export function updateMultiphaseMatchRun(source: string, matchRunId?: string) {
  const previous = current(source);
  // Empty metadata during reconnect is not a new experiment.
  if (!matchRunId || previous.currentMatchRunId === matchRunId) return;
  publishMultiphaseState(source, previous.state ?? { status: 'idle' }, previous.active, matchRunId);
}

type Poller = { clients: Map<symbol, NonNullable<PluginViewProps['host']['getMultiphase']>>; timer?: ReturnType<typeof setTimeout>; stopped: boolean };
const pollers = new Map<string, Poller>();
/** Keep the linked canvases live while the user reads an Agent or Conversation tab. */
export function useMultiphaseCanvasPolling(source: string, read?: PluginViewProps['host']['getMultiphase']) {
  useEffect(() => {
    if (!source || !read) return;
    let poller = pollers.get(source);
    const key = Symbol(source);
    if (!poller) {
      poller = { clients: new Map(), stopped: false };
      pollers.set(source, poller);
      const currentPoller = poller;
      const poll = async () => {
        const before = current(source);
        if (!currentPoller.stopped && before.active && (!before.state || activeStatuses.has(before.state.status) || activeStatuses.has(before.state.pywpem_review?.status ?? '')) && !publishers.has(source)) {
          const fetch = currentPoller.clients.values().next().value;
          try {
            const state = await fetch?.();
            const after = current(source);
            // A tab switch or a newer Pipeline publication owns its state.
            if (state && !currentPoller.stopped && after.active && !publishers.has(source) && after.state === before.state) {
              publishMultiphaseState(source, state, after.active, after.currentMatchRunId);
            }
          } catch (reason) {
            const after = current(source);
            if (!currentPoller.stopped && after.active && !publishers.has(source)) update(source, { ...after, connectionError: `实时结果同步暂时中断：${String(reason)}` });
          }
        }
        if (!currentPoller.stopped) currentPoller.timer = setTimeout(poll, 2500);
      };
      currentPoller.timer = setTimeout(poll, 2500);
    }
    poller.clients.set(key, read);
    const subscription = poller;
    return () => {
      subscription.clients.delete(key);
      if (!subscription.clients.size) { subscription.stopped = true; clearTimeout(subscription.timer); pollers.delete(source); }
    };
  }, [source, read]);
}

const activeStatuses = new Set(['running', 'waiting', 'starting', 'stopping']);
const isSuccessful = (trial: { status: string; error?: string }) => !trial.error && ['completed', 'succeeded', 'success'].includes(trial.status);
const best = (trials: XrdMultiphaseTrial[]) => trials.filter(isSuccessful).reduce<XrdMultiphaseTrial | undefined>((chosen, trial) =>
  !chosen || (trial.score ?? -Infinity) > (chosen.score ?? -Infinity) ? trial : chosen, undefined);

/** Resolves actual transported data only; never substitutes a different candidate or failed fit. */
export function resolveMultiphaseSelection(state?: XrdMultiphaseState, selection: MultiphaseSelection = freshSelection()): MultiphaseDisplay | undefined {
  if (!state) return;
  const reviews = state.pywpem_review?.reviews ?? [];
  if (selection.scope !== 'search' && selection.follow && state.pywpem_review?.live && activeStatuses.has(state.pywpem_review.status)) {
    const live = state.pywpem_review.live;
    return { ...live, lane: 'pywpem', trialId: live.trial_id, caption: `OAW_XRDfit · 实时迭代 ${live.iteration}` };
  }
  const successfulReviews = reviews.map((review, index) => ({ full: review.full, index })).filter(review => isSuccessful(review.full));
  const autoReview = successfulReviews.reduce<typeof successfulReviews[number] | undefined>((chosen, review) =>
    !chosen || (review.full.metrics?.rwp_percent ?? Infinity) < (chosen.full.metrics?.rwp_percent ?? Infinity) ? review : chosen, undefined);
  const failedReviewIndex = selection.scope === 'review' && selection.follow && state.pywpem_review?.status === 'failed' && reviews.length ? reviews.length - 1 : undefined;
  const reviewIndex = selection.lane === 'pywpem' && !selection.follow ? selection.reviewIndex : selection.scope !== 'search' && selection.follow && autoReview ? autoReview.index : failedReviewIndex;
  if (reviewIndex !== undefined) {
    const full = reviews[reviewIndex]?.full;
    if (!full) return;
    return { ...full, lane: 'pywpem', reviewIndex, caption: `OAW_XRDfit · 组合 ${reviewIndex + 1}` };
  }
  if (selection.lane === 'pywpem' && !selection.follow) return;
  if (selection.scope === 'review') return;
  let lane = selection.lane === 'baseline' ? 'baseline' as const : 'agent' as const;
  const running = activeStatuses.has(state.status);
  if (selection.follow && running && activeStatuses.has(state.baseline?.status ?? '')) lane = 'baseline';
  const trials = lane === 'baseline' ? state.baseline?.trials ?? [] : state.trials ?? [];
  let trial: XrdMultiphaseTrial | undefined;
  if (!selection.follow) trial = trials.find(item => item.trial_id === selection.trialId);
  else if (running) trial = trials.at(-1);
  else {
    const agentBest = state.incumbent ?? best(state.trials ?? []);
    const baselineBest = state.baseline?.incumbent ?? best(state.baseline?.trials ?? []);
    trial = best([agentBest, baselineBest].filter((value): value is XrdMultiphaseTrial => Boolean(value)));
    lane = trial === baselineBest && trial !== agentBest ? 'baseline' : 'agent';
  }
  if (!trial) return;
  return { ...trial, lane, trialId: trial.trial_id, caption: `${lane === 'agent' ? state.optimizer_label || 'LLM Agent' : 'BO_baseline'} · 第 ${trial.iteration} 轮` };
}
