import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { clamp, diffusionMask, READER_TRANSITION } from './readerTransition';

const pending = new Map<string, Set<string>>();
const listeners = new Set<() => void>();
export function useCanvasLoading(source: string) {
  return useSyncExternalStore(fn => { listeners.add(fn); return () => listeners.delete(fn); }, () => Boolean(pending.get(source)?.size));
}

/** Readiness belongs to this selection. A late render must not uncover a newer one. */
export function CanvasTransition({ source, identity, children }: { source: string; identity: string; children(ready: () => void): ReactNode }) {
  const owner = useId();
  const [settled, setSettled] = useState<string>();
  const [covered, setCovered] = useState<string>();
  const busy = settled !== identity || covered !== identity;
  useLayoutEffect(() => {
    setSettled(undefined);
    setCovered(undefined);
    const timer = window.setTimeout(() => setCovered(identity), window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 0 : READER_TRANSITION.spreadMs);
    return () => window.clearTimeout(timer);
  }, [identity]);
  const latest = useRef(identity); latest.current = identity;
  const ready = useCallback(() => { if (latest.current === identity) setSettled(identity); }, [identity]);
  const glass = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const entries = pending.get(source) ?? new Set<string>();
    pending.set(source, entries);
    if (busy) entries.add(owner); else entries.delete(owner);
    listeners.forEach(fn => fn());
    return () => { entries.delete(owner); if (!entries.size && pending.get(source) === entries) pending.delete(source); listeners.forEach(fn => fn()); };
  }, [source, owner, busy, identity]);
  useLayoutEffect(() => {
    const layer = glass.current, target = content.current;
    if (!layer || !target) return;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const low = navigator.hardwareConcurrency > 0 && navigator.hardwareConcurrency <= 4;
    const width = target.clientWidth, height = target.clientHeight;
    const start = performance.now(); let raf = 0;
    layer.style.backdropFilter = reduced ? 'none' : `blur(${low ? 8 : READER_TRANSITION.blurPx}px)`;
    const tick = (now: number) => {
      const elapsed = reduced ? 1 : clamp((now - start) / (busy ? READER_TRANSITION.spreadMs : READER_TRANSITION.revealMs));
      // Start gently so another selection can supersede the pending render.
      const p = elapsed * elapsed;
      layer.style.opacity = String(busy ? .8 : .8 * (1 - p));
      layer.style.maskImage = diffusionMask(busy ? .06 + .94 * p : 1, width, height, false, low);
      target.style.maskImage = busy || p === 1 ? 'none' : diffusionMask(p, width, height, true, low);
      target.style.filter = busy || p === 1 || reduced ? 'none' : `blur(${READER_TRANSITION.revealBlurPx * (1 - p) ** 2}px)`;
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    tick(start);
    return () => { cancelAnimationFrame(raf); target.style.maskImage = 'none'; target.style.filter = 'none'; };
  }, [identity, busy]);
  return <div className="xrd-canvas-transition" aria-busy={busy}>
    <div ref={content}>{children(ready)}</div>
    <div ref={glass} className="xrd-canvas-glass" aria-hidden="true"/>
    {busy && <span className="xrd-canvas-loading" role="status">正在切换画布…</span>}
  </div>;
}

export function CanvasReady({ ready, children, disabled = false }: { ready(): void; children: ReactNode; disabled?: boolean }) {
  useEffect(() => { if (disabled) return; let second = 0; const first = requestAnimationFrame(() => { second = requestAnimationFrame(ready); }); return () => { cancelAnimationFrame(first); cancelAnimationFrame(second); }; }, [ready, disabled]);
  return <>{children}</>;
}

