const DEFAULT_EVENT_ESTIMATE = 132;

export function transcriptEventKey(event, meta = {}, fallback = 0) {
  const explicit = event?.id ?? event?.event_id ?? event?.eventId;
  if (explicit !== undefined && explicit !== null && String(explicit)) return String(explicit);
  return meta.cursor || `${event?.author || "event"}:${event?.timestamp ?? event?.createTime ?? fallback}`;
}

function rowRevision(records) {
  const last = records.at(-1);
  return `${last?.meta.cursor || "none"}:${records.length}`;
}

export class TranscriptStore {
  constructor({ pageLimit = 12 } = {}) {
    this.pageLimit = pageLimit;
    this.totalCount = 0;
    this.revision = "";
    this.records = new Map();
    this.pages = new Map();
    this.loadingOffsets = new Set();
    this.focusIndex = 0;
    this.listeners = new Set();
  }

  subscribe(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  notify() {
    this.listeners.forEach((listener) => listener(this));
  }

  reset() {
    this.totalCount = 0;
    this.revision = "";
    this.records.clear();
    this.pages.clear();
    this.loadingOffsets.clear();
    this.focusIndex = 0;
    this.notify();
  }

  beginLoad(offset) {
    if (this.loadingOffsets.has(offset)) return false;
    this.loadingOffsets.add(offset);
    return true;
  }

  endLoad(offset) {
    this.loadingOffsets.delete(offset);
  }

  insertPage(response) {
    const pagination = response?.pagination || {};
    const events = response?.events || [];
    const meta = response?.event_meta || [];
    const start = Number(pagination.start_index ?? Math.max(0, Number(pagination.total_count || 0) - events.length));
    this.totalCount = Number(pagination.total_count ?? Math.max(this.totalCount, start + events.length));
    this.revision = response?.revision || this.revision;
    const indices = [];
    events.forEach((event, offset) => {
      const index = Number(meta[offset]?.index ?? start + offset);
      const record = {
        index,
        event,
        meta: {
          index,
          cursor: meta[offset]?.cursor || "",
          turnId: String(meta[offset]?.turn_id || transcriptEventKey(event, meta[offset], index)),
        },
      };
      this.records.set(index, record);
      indices.push(index);
    });
    const pageKey = `${start}:${events.length}`;
    this.pages.delete(pageKey);
    this.pages.set(pageKey, { start, end: start + events.length, indices });
    this.evictDistantPages();
    this.notify();
    return { start, end: start + events.length, totalCount: this.totalCount };
  }

  setFocusIndex(index) {
    if (!Number.isFinite(index)) return;
    this.focusIndex = Math.max(0, Math.min(this.totalCount - 1, index));
  }

  evictDistantPages() {
    while (this.pages.size > this.pageLimit) {
      const candidates = [...this.pages.entries()];
      candidates.sort(([, left], [, right]) => {
        const leftDistance = Math.min(Math.abs(left.start - this.focusIndex), Math.abs(left.end - this.focusIndex));
        const rightDistance = Math.min(Math.abs(right.start - this.focusIndex), Math.abs(right.end - this.focusIndex));
        return rightDistance - leftDistance;
      });
      const [key, page] = candidates[0];
      this.pages.delete(key);
      page.indices.forEach((index) => {
        const stillCovered = [...this.pages.values()].some((candidate) => index >= candidate.start && index < candidate.end);
        if (!stillCovered) this.records.delete(index);
      });
    }
  }

  rows() {
    if (!this.totalCount) return [];
    const loaded = [...this.records.keys()].sort((left, right) => left - right);
    const rows = [];
    let cursor = 0;
    let block = [];
    const flushBlock = () => {
      if (!block.length) return;
      let assistant = [];
      const flushAssistant = () => {
        if (!assistant.length) return;
        const turnId = assistant[0].meta.turnId;
        rows.push({
          id: `assistant:${turnId}`,
          type: "assistant",
          startIndex: assistant[0].index,
          endIndex: assistant.at(-1).index + 1,
          span: assistant.length,
          records: assistant,
          revision: rowRevision(assistant),
        });
        assistant = [];
      };
      block.forEach((record) => {
        if (record.event?.author === "user") {
          flushAssistant();
          rows.push({
            id: `user:${transcriptEventKey(record.event, record.meta, record.index)}`,
            type: "user",
            startIndex: record.index,
            endIndex: record.index + 1,
            span: 1,
            records: [record],
            revision: rowRevision([record]),
          });
          return;
        }
        if (assistant.length && assistant[0].meta.turnId !== record.meta.turnId) flushAssistant();
        assistant.push(record);
      });
      flushAssistant();
      block = [];
    };

    loaded.forEach((index) => {
      if (index > cursor) {
        flushBlock();
        rows.push({
          id: `gap:${cursor}:${index}`,
          type: "gap",
          startIndex: cursor,
          endIndex: index,
          span: index - cursor,
          revision: `gap:${cursor}:${index}`,
        });
      }
      if (block.length && block.at(-1).index + 1 !== index) flushBlock();
      block.push(this.records.get(index));
      cursor = index + 1;
    });
    flushBlock();
    if (cursor < this.totalCount) {
      rows.push({
        id: `gap:${cursor}:${this.totalCount}`,
        type: "gap",
        startIndex: cursor,
        endIndex: this.totalCount,
        span: this.totalCount - cursor,
        revision: `gap:${cursor}:${this.totalCount}`,
      });
    }
    const idCounts = new Map();
    rows.forEach((row) => idCounts.set(row.id, (idCounts.get(row.id) || 0) + 1));
    return rows.map((row) => idCounts.get(row.id) > 1 ? {
      ...row,
      id: `${row.id}:segment:${row.records?.at(-1)?.meta.cursor || row.endIndex}`,
    } : row);
  }

  estimateRow(row) {
    if (!row) return DEFAULT_EVENT_ESTIMATE;
    if (row.type === "user") return 112;
    if (row.type === "gap") return Math.max(DEFAULT_EVENT_ESTIMATE, row.span * DEFAULT_EVENT_ESTIMATE);
    return Math.max(176, row.span * DEFAULT_EVENT_ESTIMATE);
  }
}
