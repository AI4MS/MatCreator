import type { PluginViewProps } from '../sdk';
type Info = Awaited<ReturnType<PluginViewProps['host']['getAgentInfo']>>;
const requests = new Map<string, { time: number; promise: Promise<Info>; pending: boolean }>();
/** The timeline and both canvases consume the same large snapshot. Coalesce reads. */
export function readFrameInfo(source: string, load: () => Promise<Info>): Promise<Info> {
  const previous = requests.get(source);
  if (previous?.pending) return previous.promise;
  const entry = { time: Date.now(), pending: true, promise: Promise.resolve().then(load) };
  requests.set(source, entry);
  entry.promise.then(() => { entry.pending = false; entry.time = Date.now(); }, () => { if (requests.get(source) === entry) requests.delete(source); });
  if (requests.size > 24) { const oldest = [...requests].find(([, item]) => !item.pending); if (oldest) requests.delete(oldest[0]); }
  return entry.promise;
}
