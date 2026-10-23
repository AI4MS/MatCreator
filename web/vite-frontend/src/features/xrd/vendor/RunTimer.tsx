import { useEffect, useState } from 'react';
export type Timing = { started_at_ms?: number; finished_at_ms?: number; running?: boolean };
export function RunTimer({ timing, stage }: { timing?: Timing; stage?: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { setNow(Date.now()); if (!timing?.running) return; const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, [timing?.started_at_ms, timing?.running]);
  if (!timing?.started_at_ms || (!timing.running && !timing.finished_at_ms)) return null;
  const seconds = Math.max(0, Math.floor(((timing.running ? now : timing.finished_at_ms!) - timing.started_at_ms) / 1000));
  const label = `${Math.floor(seconds / 3600).toString().padStart(2, '0')}:${Math.floor(seconds / 60 % 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
  return <small className="xrd-run-timer" title="本阶段实际运行耗时，包含准备与等待时间">{timing.running ? '运行中' : '耗时'} {label}{stage ? ` · ${stage}` : ''}</small>;
}
