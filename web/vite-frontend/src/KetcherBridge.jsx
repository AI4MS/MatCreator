import process from "process";

globalThis.process ||= process;
globalThis.global ||= globalThis;

/** Mount a standalone Ketcher editor and expose its API to the Svelte host. */
export async function mountKetcher(target, options = {}) {
  const { mountKetcherRuntime } = await import("./KetcherRuntime.jsx");
  return mountKetcherRuntime(target, options);
}
