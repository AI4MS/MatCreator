# Agent Graph and delegated task performance

Previously, closing a task only hid its fully rendered history. Nested task trees
sat outside the parent disclosure, the feed registry retained virtualized rows,
and graph snapshots/deltas carried growing execution payloads. Render keys also
serialized that history on ordinary updates.

## Task ownership

A mounted card initially contains its avatar and summary only. Opening it fetches
one node detail and mounts input, conversation, tool calls, artifacts, and direct
child summaries. Children load their own detail only when opened. Each open card
allows one request in flight; revisions received during a request are coalesced
into a refresh for the latest revision. Errors expose a retry control.

Closing immediately aborts detail requests in the subtree. After the existing
240 ms disclosure motion interval, the body, descendant cards, and detail objects
are released. Reopening reconstructs them. Completing/cancelling a running task
compacts it once; a subsequently opened historical task stays open across updates.

`VirtualTranscript.releaseRow` calls `StepExecutionFeed.releaseWithin` before row
eviction, replacement, or reset. Delegation row/segment removal and superseded
execution attempts use the same release path. It unregisters cards and launcher
hosts, aborts requests, clears collapse timers and detail references, and retains
only disclosure choices. No document-wide polling is involved. Historical graph
updates do not recreate cards evicted by virtualization; transcript remounting
reconstructs exactly one card in the existing launcher slot.

## Summary and detail API

- `GET /api/agent-graph/{session_id}` and `/events` expose topology, status, labels,
  bounded display summaries, timing, minimal input identifiers, counts and revision.
  Existing `node_id`/`action` filtering still includes descendant summaries.
- `GET /api/agent-graph/{session_id}/nodes/{node_id}` returns one complete stored
  node, including input, conversation, tool calls, state/output and artifacts.
  Missing nodes return 404. Responses and fetches use `no-store`.
- The graph sidebar also loads detail on selection and releases its contents on
  close/session switch. Details never merge into graph snapshots or session caches.
- Logger mutations increment a per-node revision. Old logs without revisions use
  the graph/file generation as a compatibility fallback. Input batch identifiers
  from old logs are lifted into summary metadata to preserve layout grouping.

SSE polls `(path, st_mtime_ns, size, inode)` every 200 ms. Only changed file
generations are read/parsed, and deltas compare summaries. A corrupt/partial
file preserves the last good snapshot and is attempted again only after its
metadata changes. File access runs off the async event loop and works with worker
files in server mode. Only lightweight snapshots survive between polls.

## Animation and storage limits

Small graphs keep the existing 30 fps animation cadence. At 150 nodes, the repaint
interval becomes 100 ms; at 500 it becomes 200 ms and flow uses one particle per
edge. Large graphs sleep between paints. Hidden tabs and non-intersecting graph
viewports stop scheduling; terminal changes still receive a static paint.

The logger uses compact JSON and avoids unchanged input/state/text/completion and
empty cancellation writes. Storage remains whole-file JSON: each real logger
mutation still reads/serializes the graph, and a detail request still parses the
file before selecting its node. Text-event debounce and more granular storage
remain separate follow-ups. An individually opened, very large task can still
create a large body; this change bounds cost by expanded tasks, not by events
inside each expanded task.

## Regression coverage

Run `npm test` and `npm run build` in `web/vite-frontend` with Node 24.14.1+.
Run the Python graph tests with:

```sh
python -m pytest tests/test_agent_graph_performance.py tests/test_graph_logger.py \
  tests/test_web_session_access.py tests/test_web_sse_streaming.py
```

The DOM fixture creates 200 tasks with 100 conversation and 100 tool records each:
zero heavy bodies and zero detail requests while collapsed, and zero registered
cards after eviction. Backend fixtures verify summary size stays essentially
constant as history grows, full detail is available on demand, and unchanged files
are read once across repeated SSE polls. Additional tests cover nested ownership,
revision updates, cancellation, live slots, retries, stale requests, actual session
cache switching, graph visibility/animation, and legacy batch metadata. These are
structural resource regressions, not browser heap/FPS benchmarks.
