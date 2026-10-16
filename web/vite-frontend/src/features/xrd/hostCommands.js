export function createMultiphaseReview(command) {
  return (runId, combinations) => command('multiphase_review', {run_id: runId, combinations});
}
