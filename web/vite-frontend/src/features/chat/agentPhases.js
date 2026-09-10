/** Map a tool name to the agent-status phase shown while it runs. */
export function phaseForTool(name = "") {
  const tool = String(name).toLowerCase();
  if (tool.includes("search") || tool.includes("retrieve") || tool.includes("lookup")) return "searching";
  if (tool.includes("plan") || tool.includes("graph") || tool.includes("decompos")) return "planning";
  if (tool.includes("run_") || tool.includes("execute") || tool.includes("submit") || tool.includes("resume")) return "executing";
  if (tool.includes("calc") || tool.includes("simulate") || tool.includes("compute")) return "computing";
  return "working";
}
