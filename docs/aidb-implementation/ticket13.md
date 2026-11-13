# Ticket 13 — Tracked recovery and archive readback

Resume the same tracked job, original input revision, provider identity, output directory and AIDB binding. Batch Job retries the original transfer into private staging because Bohrium exposes a bulk download into an empty directory. Sandbox retains verified downloads and retries the missing files. Both then use the same result receiver.

A failed readback keeps computation status and artifacts available. Retrying looks up the existing frame and verifies it by ID. Explicit save of an already delivered result resolves the original tracked provenance when its path and output digests match. Independent runs can retain distinct records even when their output bytes are identical.

## Acceptance — 2026-11-13

The normal ADK Flash dialogue requested recovery of the original relaxation. The replay used historical Si outputs, a local provider adapter and the installed AIDB in isolated SQLite. An injected failure blocked the first detail read after a successful write. The failure receipt retained computation success and its raw files. The retry reported the record as already present and verified it by ID. No additional submission or record was created.

Chromium loaded the saved recovery dialogue under a local acceptance identity. It opened, collapsed and reopened Details, obtained two freshly read responses and downloaded EXTXYZ twice plus the original XML and OUTCAR. Reload found the same completion, `42f3162181134138b2e153f0a3ec2877`, and the same record, `994ede687270c3326690eaf5652511ba`. Private evidence is in `.workspace/aidb-recovery-browser-evidence/`.

Backend replay covers both providers, partial transfer and stale collection state, readback faults, repeated collection and notification, and explicit save of two independent runs with identical outputs. Existing execution-recovery tests check reattachment to the same original jobs.
