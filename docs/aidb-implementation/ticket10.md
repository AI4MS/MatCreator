# Ticket 10 — MatPES batch labeling

Each prepared source frame has its own input revision, selection and tracked job identity. Complete matches wait for a user decision while independent members continue. Collection uses the shared managed VASP receiver, verifies each archived record and emits the labeled EXTXYZ frames in source order. The manifest records the original source index, record identity, status and output index. It reports failures without moving later labels to the wrong frame.

## Acceptance — 2026-11-13

A scripted model in the normal ADK orchestrator requested labels for three source frames, collected the completed member and then explicitly selected the existing match. The provider boundary used the repository's original NaCl static XML and OUTCAR with synthetic POTCAR headers. That historical run used different settings from a newly executed MatPES preset. The replay verifies orchestration, labeling, identity and delivery. The preparation script has separate input-set tests.

| Source frame | Result |
| --- | --- |
| 0 | Waited for explicit choice, then reused the seeded record without submitting or writing. |
| 1 | Provider fault. Reported failed and omitted from EXTXYZ, with its index retained in the manifest. |
| 2 | Independently submitted, collected, archived and reread by ID. |

ASE reopened the aggregate and found source indices 0 and 2 in order, with both record IDs, energies and per-atom forces intact. Repeated collection left three local provider submissions including the seed, two transfers, and two AIDB rows.

Chromium loaded the saved normal chat events in a local-user acceptance session. It opened, collapsed and reopened the archived member's Details. Two fresh read requests and two actual EXTXYZ downloads passed, along with original XML/OUTCAR downloads and reload to the same completion. The test-session owner was mapped to the local UI's `user` identity in the isolated acceptance copy.

The integration regression passed 26 Python tests and the frontend check and Vite build. Private evidence is under `.workspace/matpes-browser-evidence/test_historical_static_replay_0/`.
