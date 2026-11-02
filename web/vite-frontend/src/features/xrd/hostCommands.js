export function createMultiphaseReview(command) {
  return (runId, combinations) => command('multiphase_review', {run_id: runId, combinations});
}

export function archiveDownloadUrl(archive, bindAccountUrl = (url) => url) {
  return archive.startsWith('/api/') ? bindAccountUrl(archive) : `data:application/zip;base64,${archive}`;
}
